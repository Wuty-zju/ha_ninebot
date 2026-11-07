"""Frozen detail plans and one-use confirmation; not an AI execution endpoint."""

import hashlib
import hmac
import json
import secrets
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime, timedelta
from uuid import uuid4

from homeassistant.core import Context, HomeAssistant
from homeassistant.util import dt as dt_util

from .assistant_policy import AssistantError, async_authorize
from .preservation import DetailScope

APPROVAL_TTL = timedelta(minutes=10)
EXECUTION_TTL = timedelta(hours=24)


def aware(value: datetime) -> bool:
    return isinstance(value, datetime) and value.utcoffset() is not None


@dataclass(frozen=True)
class DetailPlan:
    """Server-created bindings; private identity never belongs in model responses."""

    user_id: str = field(repr=False)
    account_key: str = field(repr=False)
    vehicle_ref: str = field(repr=False)
    sn: str = field(repr=False)
    start_date: date
    end_date: date
    scope: DetailScope = field(repr=False)
    attempts: int
    created_at: datetime

    def __post_init__(self) -> None:
        if (
            any(
                not isinstance(value, str) or not 1 <= len(value) <= 128
                for value in (self.user_id, self.vehicle_ref, self.sn)
            )
            or not isinstance(self.account_key, str)
            or len(self.account_key) != 64
            or set(self.account_key) - set("0123456789abcdef")
            or type(self.start_date) is not date
            or type(self.end_date) is not date
            or not 0 <= (self.end_date - self.start_date).days <= 30
            or not isinstance(self.scope, DetailScope)
            or type(self.attempts) is not int
            or not len(self.scope.rides) <= self.attempts <= 20
            or not aware(self.created_at)
        ):
            raise ValueError("Invalid frozen detail plan")

    @property
    def digest(self) -> str:
        body = {
            "version": 1,
            "user": self.user_id,
            "account": self.account_key,
            "device": self.vehicle_ref,
            "vehicle": self.sn,
            "start": self.start_date.isoformat(),
            "end": self.end_date.isoformat(),
            "rides": sorted(self.scope.rides),
            "attempts": self.attempts,
            "created": self.created_at.astimezone(UTC).isoformat(),
            "operation": "detail_only",
        }
        return hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()


@dataclass(frozen=True)
class ApprovalTicket:
    """Persist only the digest of the UI-only nonce; consume under the task-store lock."""

    task_id: str
    plan: DetailPlan = field(repr=False)
    nonce_hash: str = field(repr=False)
    approved_at: datetime | None = None

    def __post_init__(self) -> None:
        if (
            not isinstance(self.task_id, str)
            or len(self.task_id) != 32
            or set(self.task_id) - set("0123456789abcdef")
            or not isinstance(self.plan, DetailPlan)
            or not isinstance(self.nonce_hash, str)
            or (
                self.approved_at is None
                and (len(self.nonce_hash) != 64 or set(self.nonce_hash) - set("0123456789abcdef"))
            )
            or (
                self.approved_at is not None
                and (
                    not aware(self.approved_at)
                    or self.nonce_hash != ""
                    or not self.plan.created_at
                    <= self.approved_at
                    < self.plan.created_at + APPROVAL_TTL
                )
            )
        ):
            raise ValueError("Invalid approval ticket")

    @classmethod
    def issue(cls, plan: DetailPlan, now: datetime) -> tuple["ApprovalTicket", str]:
        if not aware(now) or not plan.created_at <= now < plan.created_at + APPROVAL_TTL:
            raise AssistantError("approval_expired")
        task_id, nonce = uuid4().hex, secrets.token_urlsafe(32)
        digest = cls._nonce_digest(task_id, plan.digest, nonce)
        return cls(task_id, plan, digest), nonce

    @staticmethod
    def _nonce_digest(task_id: str, plan_hash: str, nonce: str) -> str:
        return hashlib.sha256(f"{task_id}:{plan_hash}:{nonce}".encode()).hexdigest()

    def approve(self, user_id: str, nonce: str, plan_hash: str, now: datetime) -> "ApprovalTicket":
        if self.approved_at is not None:
            raise AssistantError("approval_used")
        if not aware(now) or not self.plan.created_at <= now < self.plan.created_at + APPROVAL_TTL:
            raise AssistantError("approval_expired")
        if (
            user_id != self.plan.user_id
            or plan_hash != self.plan.digest
            or not isinstance(nonce, str)
            or not nonce.isascii()
            or not 16 <= len(nonce) <= 128
            or not hmac.compare_digest(
                self.nonce_hash, self._nonce_digest(self.task_id, self.plan.digest, nonce)
            )
        ):
            raise AssistantError("not_authorized")
        return replace(self, nonce_hash="", approved_at=now)

    def execution_valid(self, now: datetime) -> bool:
        """Temporal eligibility only; callers must reauthorize current ownership and consent."""
        return bool(
            self.approved_at is not None
            and aware(now)
            and self.approved_at <= now < self.approved_at + EXECUTION_TTL
        )


async def async_approve_ticket(
    hass: HomeAssistant,
    ticket: ApprovalTicket,
    context: Context | None,
    nonce: str,
    plan_hash: str,
) -> ApprovalTicket:
    """Validate a proposed transition; caller must persist it under its task-store lock."""
    scope = await async_authorize(hass, context, ticket.plan.vehicle_ref)
    archive = scope.runtime.coordinator.statistics.archive
    if archive.owner != ticket.plan.account_key or scope.sn != ticket.plan.sn:
        raise AssistantError("not_authorized")
    await scope.async_validate(hass)
    scope.assert_current(hass)
    user_id = scope.context.user_id
    if user_id is None:
        raise AssistantError("not_authorized")
    # No await between final authorization and the immutable state transition.
    return ticket.approve(user_id, nonce, plan_hash, dt_util.utcnow())
