"""Synthetic bootstrap checkpoints, separate from the legacy range job."""

from dataclasses import asdict

import pytest

from custom_components.ninebot.history_plan import HistoryPlan, HistoryState, month_count, restored

NOW = "2035-01-01T00:00:00+00:00"


def plan(start="200001", end="203501"):
    return HistoryPlan.create("a" * 32, "b" * 64, start, end, NOW)


def test_long_history_not_restricted_to_legacy_action_range():
    root = plan()
    assert month_count(root.start_month, root.end_month) == 421
    assert root.next_month == "203501"
    assert not root.discovery_complete


def test_page_and_month_checkpoints_are_distinct_and_gaps_are_retained():
    root = plan("203412", "203501")
    page = root.page_committed(NOW, finished=False, verified=False)
    assert page.next_page == 2 and page.scanned_months == 0
    january = page.page_committed(NOW, finished=True, verified=True)
    assert january.next_month == "203412" and january.next_page == 1
    december = january.page_committed(NOW, finished=True, verified=False)
    assert december.discovery_complete and december.state is HistoryState.PRESERVE
    assert december.scanned_months == 2 and december.verified_months == 1
    assert december.gap_months == 1
    data = asdict(december)
    data["state"] = "complete"
    with pytest.raises(ValueError):
        restored(data)


@pytest.mark.parametrize(
    "changes",
    [
        {"vehicle": "foreign"},
        {"contract": True},
        {"scanned_months": True},
        {"next_page": 0},
        {"next_month": None},
        {"discovery_complete": True},
        {"state": "complete"},
        {"state": "waiting"},
        {"reason": "token=private"},
        {"retry_at": NOW},
        {"cutoff": "2034-12-01T00:00:00+00:00"},
        {"provider": "arbitrary-host"},
        {"unexpected": "field"},
    ],
)
def test_invalid_or_legacy_metadata_cannot_become_complete(changes):
    with pytest.raises(ValueError):
        restored({**asdict(plan()), **changes})


def test_roundtrip_does_not_advance_checkpoint():
    root = plan().page_committed(NOW, finished=False, verified=False)
    assert restored(asdict(root)) == root
