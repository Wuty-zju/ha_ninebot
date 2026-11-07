"""Small nonce-free task metadata shared by model tools and human review."""

from typing import Any

from homeassistant.util import dt as dt_util

from .assistant_approval import APPROVAL_TTL, EXECUTION_TTL, ApprovalTicket
from .const import BUSINESS_TIMEZONE


def ticket_view(ticket: ApprovalTicket) -> dict[str, Any]:
    """Small reviewed fields; no account, SN, user identity or stored nonce digest."""
    plan = ticket.plan
    now = dt_util.utcnow()
    state = "approved" if ticket.approved_at else "awaiting_approval"
    if (ticket.approved_at and not ticket.execution_valid(now)) or (
        ticket.approved_at is None and now >= plan.created_at + APPROVAL_TTL
    ):
        state = "expired"
    return {
        "task_id": ticket.task_id,
        "vehicle_ref": plan.vehicle_ref,
        "state": state,
        "plan_hash": plan.digest,
        "operation": "detail_only",
        "start_date": plan.start_date.isoformat(),
        "end_date": plan.end_date.isoformat(),
        "time_zone": BUSINESS_TIMEZONE,
        "selected_count": len(plan.scope.rides),
        "maximum_detail_attempts": plan.attempts,
        "approval_expires_at": (plan.created_at + APPROVAL_TTL).isoformat(),
        "execution_expires_at": (ticket.approved_at + EXECUTION_TTL).isoformat()
        if ticket.approved_at
        else None,
    }
