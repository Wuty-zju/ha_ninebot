"""Authorized local detail plans and a small model-safe preview projection."""

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from homeassistant.core import Context, HomeAssistant
from homeassistant.util import dt as dt_util

from .archive_runtime import ArchiveStatistics
from .assistant_approval import APPROVAL_TTL, DetailPlan
from .assistant_policy import AssistantError, async_authorize
from .assistant_preview import DetailPreview
from .compat import validation as vol
from .const import BUSINESS_TIMEZONE
from .history_budget import configured_budget, request_interval
from .recorded_actions import business_date
from .ride_archive import ArchiveError, ArchiveFailure


@dataclass(frozen=True)
class DetailProposal:
    """Private server plan; only model_data is intended for a tool response."""

    preview: DetailPreview = field(repr=False)
    plan: DetailPlan | None = field(repr=False)
    vehicle_ref: str
    start_date: date
    end_date: date
    as_of: datetime
    created_at: datetime
    request_interval_seconds: float

    def model_data(self) -> dict[str, Any]:
        preview, plan = self.preview, self.plan
        requested: list[str] = []
        month = self.start_date.replace(day=1)
        while month <= self.end_date:
            requested.append(month.strftime("%Y%m"))
            month = (month.replace(day=28) + timedelta(days=4)).replace(day=1)
        stored = {row.month for row in preview.months}
        return {
            "schema_version": 1,
            "operation": "detail_only",
            "vehicle_ref": self.vehicle_ref,
            "source": "local_archive",
            "range": {
                "start_date": self.start_date.isoformat(),
                "end_date": self.end_date.isoformat(),
                "time_zone": BUSINESS_TIMEZONE,
                "selection": "ended_rides_overlapping_interval",
                "as_of": self.as_of.isoformat(),
            },
            "coverage": {
                "known_local_rides": preview.known_count,
                "indexed_details": preview.saved_count,
                "missing_mapped_details": preview.missing_count,
                "unmapped_details": preview.unmapped_count,
                "skipped_conflicting_times": preview.skipped_conflicting_times,
                "missing_months": [month for month in requested if month not in stored],
                "months": [
                    {
                        "month": row.month,
                        "received_at": row.received_at.isoformat(),
                        "observed_list_complete": row.list_complete,
                        "reported_month_count": row.reported_count,
                        "recorded_enumeration": row.enumeration is not None,
                    }
                    for row in preview.months
                ],
                "counts_basis": "local_current_selection",
                "upstream_history_complete": "unverified",
                "detail_payload_integrity_verified": False,
            },
            "selection": {"count": preview.selected_count, "has_more": preview.has_more},
            "requests": {
                "preview_cloud_calls": 0,
                "maximum_after_approval": plan.attempts if plan else 0,
                "account_minimum_interval_seconds": self.request_interval_seconds,
                "shared_account_pacing": True,
            },
            "plan": {
                "digest": plan.digest,
                "approval_expires_at": (plan.created_at + APPROVAL_TTL).isoformat(),
            }
            if plan
            else None,
            "approval_required": plan is not None,
            "task_created": False,
        }


async def async_detail_proposal(
    hass: HomeAssistant,
    context: Context | None,
    vehicle_ref: str,
    start_date: str,
    end_date: str,
) -> DetailProposal:
    """Retain original caller/ownership across a bounded local read; never create a job."""
    scope = await async_authorize(hass, context, vehicle_ref)
    try:
        first, last = (date.fromisoformat(business_date(value)) for value in (start_date, end_date))
    except (ValueError, vol.Invalid):
        raise AssistantError("invalid_arguments") from None
    if not 0 <= (last - first).days <= 30:
        raise AssistantError("invalid_arguments")
    statistics = scope.runtime.coordinator.statistics
    if not isinstance(statistics, ArchiveStatistics) or not statistics.archive_ready:
        raise AssistantError("local_data_missing")
    archive = statistics.archive

    def guard() -> bool:
        scope.assert_current(hass)
        return (
            scope.runtime.coordinator.statistics is statistics
            and statistics.archive is archive
            and statistics.archive_ready
        )

    zone = ZoneInfo(BUSINESS_TIMEZONE)
    now = dt_util.utcnow()
    try:
        preview = await archive.async_detail_preview(
            scope.sn,
            datetime.combine(first, time(), zone),
            datetime.combine(last + timedelta(days=1), time(), zone),
            now,
            guard=guard,
        )
    except ArchiveError as err:
        await scope.async_validate(hass)
        code = {
            ArchiveFailure.OWNER: "not_authorized",
            ArchiveFailure.BUDGET: "response_too_large",
        }.get(err.kind, "retry_later")
        raise AssistantError(code) from None
    await scope.async_validate(hass)
    if not guard() or scope.context.user_id is None:
        raise AssistantError("not_authorized")
    created = dt_util.utcnow()
    plan = (
        DetailPlan(
            scope.context.user_id,
            archive.owner,
            scope.vehicle_ref,
            scope.sn,
            first,
            last,
            preview.scope,
            min(20, preview.selected_count * 2),
            created,
        )
        if preview.scope
        else None
    )
    return DetailProposal(
        preview,
        plan,
        scope.vehicle_ref,
        first,
        last,
        now,
        created,
        request_interval(configured_budget(scope.entry.options)),
    )
