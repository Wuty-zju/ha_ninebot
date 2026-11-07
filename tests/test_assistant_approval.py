"""One-use plan confirmation; no model, cloud, execution or private records."""

from dataclasses import replace
from datetime import UTC, date, datetime, timedelta

import pytest

from custom_components.ninebot.assistant_approval import ApprovalTicket, DetailPlan
from custom_components.ninebot.assistant_policy import AssistantError
from custom_components.ninebot.preservation import DetailScope

NOW = datetime(2026, 11, 7, tzinfo=UTC)


def plan():
    return DetailPlan(
        "fixture-user",
        "a" * 64,
        "fixture-device",
        "fixture-sn",
        date(2026, 11, 5),
        date(2026, 11, 6),
        DetailScope((("ride-one", "b" * 64),)),
        2,
        NOW,
    )


def test_nonce_is_hashed_bound_and_consumed():
    p = plan()
    ticket, nonce = ApprovalTicket.issue(p, NOW)
    assert nonce not in repr(ticket) and p.user_id not in repr(ticket)
    assert nonce != ticket.nonce_hash
    approved = ticket.approve(p.user_id, nonce, p.digest, NOW)
    assert approved.nonce_hash == "" and approved.approved_at == NOW
    with pytest.raises(AssistantError, match="^approval_used$"):
        approved.approve(p.user_id, nonce, p.digest, NOW)
    assert approved.execution_valid(NOW + timedelta(hours=23))
    assert not approved.execution_valid(NOW + timedelta(hours=24))
    assert not ticket.execution_valid(NOW)


@pytest.mark.parametrize("offset", [-1, 600, 601])
def test_expired_or_future_confirmation_is_not_accepted(offset):
    p = plan()
    ticket, nonce = ApprovalTicket.issue(p, NOW)
    with pytest.raises(AssistantError, match="^approval_expired$"):
        ticket.approve(p.user_id, nonce, p.digest, NOW + timedelta(seconds=offset))


def test_another_user_ticket_or_plan_cannot_reuse_the_nonce():
    p = plan()
    ticket, nonce = ApprovalTicket.issue(p, NOW)
    other, _ = ApprovalTicket.issue(p, NOW)
    for target, user, submitted in (
        (ticket, "another-user", p.digest),
        (other, p.user_id, p.digest),
        (replace(ticket, plan=replace(p, attempts=3)), p.user_id, p.digest),
        (ticket, p.user_id, "0" * 64),
    ):
        with pytest.raises(AssistantError, match="^not_authorized$"):
            target.approve(user, nonce, submitted, NOW)


@pytest.mark.parametrize("nonce", ["confirmed", "x" * 129, "同意" * 20, "\ud800" * 20])
def test_chat_confirmation_and_invalid_nonce_do_not_grant_permission(nonce):
    p = plan()
    ticket, _ = ApprovalTicket.issue(p, NOW)
    with pytest.raises(AssistantError, match="^not_authorized$"):
        ticket.approve(p.user_id, nonce, p.digest, NOW)


@pytest.mark.parametrize(
    "field,value",
    [
        ("start_date", date(2026, 11, 7)),
        ("attempts", True),
        ("attempts", 21),
        ("attempts", 0),
        ("account_key", "invalid"),
        ("created_at", NOW.replace(tzinfo=None)),
    ],
)
def test_invalid_plan_is_rejected_before_confirmation(field, value):
    with pytest.raises(ValueError):
        replace(plan(), **{field: value})


def test_equivalent_scope_order_and_time_zone_have_one_digest():
    p = plan()
    p = replace(p, scope=DetailScope((("one", "1" * 64), ("two", "2" * 64))))
    assert p.digest == replace(p, scope=DetailScope(tuple(reversed(p.scope.rides)))).digest
    assert p.digest != replace(p, end_date=date(2026, 11, 7)).digest
