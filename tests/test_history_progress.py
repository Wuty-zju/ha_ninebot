"""Progress is a safe projection, not a cloud-completeness claim."""

import json
from dataclasses import asdict, replace
from datetime import UTC, datetime, timedelta

import pytest

from custom_components.ninebot.detail_health import HistoryHealth
from custom_components.ninebot.history_plan import HistoryPlan, HistoryState
from custom_components.ninebot.history_progress import HistoryEvidence, project_progress

NOW = datetime(2026, 10, 14, tzinfo=UTC)
PLAN = HistoryPlan.create("a" * 32, "b" * 64, "202609", "202610", NOW.isoformat())
HEALTH = HistoryHealth(2, 2, 0, 0, 0, 0, 0, 0, 0)


def view(evidence, **kwargs):
    return project_progress(
        evidence,
        NOW,
        enabled=kwargs.get("enabled", True),
        paused=kwargs.get("paused", False),
        authenticated=kwargs.get("authenticated", True),
        storage_error=kwargs.get("storage_error"),
    )


def test_saved_details_100_percent_does_not_complete_discovery():
    value = view(HistoryEvidence(PLAN, HEALTH))
    assert value.detail_percent == 100
    assert value.scan_percent == 0 and not value.discovery_complete
    assert value.state == "discovering"
    assert value.last_success is None
    assert not any(key in json.dumps(asdict(value)) for key in ("plan_id", "vehicle", "trail"))


def test_no_denominator_is_unknown_instead_of_false_zero_or_success():
    value = view(HistoryEvidence())
    assert value.state == "not_started"
    assert value.scan_percent is value.detail_percent is None
    assert (
        view(HistoryEvidence(PLAN, replace(HEALTH, discovered=0, saved=0))).detail_percent is None
    )


def test_scanned_failed_month_is_not_verified_progress():
    plan = replace(PLAN, scanned_months=1, verified_months=0, gap_months=1, next_month="202609")
    value = view(HistoryEvidence(plan, HEALTH))
    assert value.scanned_months == 1 and value.scan_percent == 0


@pytest.mark.parametrize(
    "settings,state",
    [
        ({"enabled": False}, "disabled"),
        ({"paused": True}, "paused"),
        ({"authenticated": False}, "needs_login"),
        ({"storage_error": "capacity"}, "storage_full"),
        ({"storage_error": "storage"}, "storage_error"),
    ],
)
def test_operational_overlays_keep_saved_counts(settings, state):
    value = view(HistoryEvidence(PLAN, HEALTH), **settings)
    assert value.state == state and value.saved == 2


def test_budget_retry_and_incremental_are_distinct_from_success():
    deadline = NOW + timedelta(seconds=120)
    assert view(HistoryEvidence(PLAN, HEALTH, budget_at=deadline)).state == "waiting_budget"
    waiting = replace(
        PLAN, state=HistoryState.WAITING, reason="connection", retry_at=deadline.isoformat()
    )
    assert view(HistoryEvidence(waiting, HEALTH, budget_at=deadline)).state == "waiting_retry"
    complete = replace(
        PLAN,
        state=HistoryState.COMPLETE,
        discovery_complete=True,
        next_month=None,
        scanned_months=2,
        verified_months=2,
    )
    incremental = HistoryPlan.create("c" * 32, "b" * 64, "202611", "202611", NOW.isoformat())
    value = view(HistoryEvidence(complete, HEALTH, (incremental,), NOW))
    assert value.state == "incremental" and value.scan_percent == 100
    assert value.last_success == NOW and value.active_months == 1
    gaps = replace(complete, state=HistoryState.WITH_GAPS, verified_months=1, gap_months=1)
    assert view(HistoryEvidence(gaps, HEALTH)).state == "with_gaps"
    assert view(HistoryEvidence(gaps, HEALTH)).scan_percent == 50
