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


def test_latest_command_uses_dispatch_order_instead_of_timestamp_ties():
    results = ControlResults()
    start = results.start("vehicle", "engine/start", NOW)
    stop = results.start("vehicle", "engine/stop", NOW)
    stop.outcome = CommandOutcome.ACCEPTED
    start.outcome = CommandOutcome.UNCERTAIN
    assert results.latest("vehicle", ("engine/start", "engine/stop"))["outcome"] == "accepted"
    assert results.latest("other", ("engine/start", "engine/stop")) is None


def test_support_result_keeps_safe_response_metadata_and_logs_no_identity(caplog):
    import logging

    from custom_components.ninebot.exceptions import NinebotError

    secret = "synthetic-account-token-location-secret"
    error = NinebotError(ErrorKind.SERVICE)
    error.observe_response(502, {"error": {"code": "upstream_error", "message": secret}})
    result = ControlResults().start(secret, "bell", NOW)
    result.outcome = CommandOutcome.UNCERTAIN
    result.error = error.kind
    result.failure_context = error.context
    result.finished_at = NOW
    with caplog.at_level(logging.DEBUG, logger="custom_components.ninebot.control_results"):
        result.log("bell")
    row = result.diagnostics()
    assert len(row["support_id"]) == 8
    assert row["proxy_http_status"] == 502 and row["envelope_code"] == "upstream_error"
    assert row["outcome"] == "uncertain" and row["physical_outcome_verified"] is False
    assert f"support={row['support_id']}" in caplog.text
    assert "action=bell" in caplog.text and "outcome=uncertain" in caplog.text
    assert secret not in caplog.text and secret not in str(row)
    caplog.clear()
    with caplog.at_level(logging.DEBUG, logger="custom_components.ninebot.control_results"):
        result.log(secret)
    assert not caplog.records
