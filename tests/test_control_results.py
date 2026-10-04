"""Command metadata stays bounded, private and immune to late completions."""

from datetime import UTC, datetime

import pytest

from custom_components.ninebot.control_results import (
    MAX_CONTROL_RESULTS,
    CommandOutcome,
    ControlResults,
    ReadbackOutcome,
)
from custom_components.ninebot.exceptions import ErrorKind

NOW = datetime(2026, 10, 4, tzinfo=UTC)


def test_late_old_completion_cannot_replace_newer_attempt_and_exports_no_identity():
    results = ControlResults()
    old = results.start("synthetic-private-vehicle", "bell", NOW)
    new = results.start("synthetic-private-vehicle", "bell", NOW)
    new.outcome = CommandOutcome.ACCEPTED
    new.readback = ReadbackOutcome.REFRESHED
    old.outcome = CommandOutcome.UNCERTAIN
    old.error = ErrorKind.CONNECTION
    exported = results.diagnostics("synthetic-private-vehicle")
    assert exported["bell"]["outcome"] == "accepted"
    assert exported["bell"]["error"] is None
    assert exported["bell"]["physical_outcome_verified"] is False
    assert "synthetic-private-vehicle" not in str(exported)
    assert results.diagnostics("other") == {}
    results.clear()
    new.finished_at = NOW
    assert results.diagnostics("synthetic-private-vehicle") == {}


def test_global_bound_eviction_and_unknown_action_cannot_add_records():
    results = ControlResults()
    for index in range(MAX_CONTROL_RESULTS + 1):
        results.start(f"synthetic-{index}", "bell", NOW)
    assert results.diagnostics("synthetic-0") == {}
    assert results.diagnostics(f"synthetic-{MAX_CONTROL_RESULTS}")["bell"]["outcome"] == "pending"
    assert len(results._records) == MAX_CONTROL_RESULTS
    with pytest.raises(ValueError):
        results.start("synthetic", "not-an-action", NOW)
    assert len(results._records) == MAX_CONTROL_RESULTS
