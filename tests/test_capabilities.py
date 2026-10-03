import pytest

from custom_components.ninebot.capabilities import (
    CapabilityState as State,
)
from custom_components.ninebot.capabilities import (
    ControlCapability,
    VehicleCapabilities,
)


@pytest.mark.parametrize(
    "support,permission,semantics,evidence,expected",
    [
        (State.UNKNOWN, State.ALLOWED, True, "mock", False),
        (State.ALLOWED, State.UNKNOWN, True, "mock", False),
        (State.DENIED, State.ALLOWED, True, "mock", False),
        (State.ALLOWED, State.DENIED, True, "mock", False),
        (State.ALLOWED, State.ALLOWED, False, "mock", False),
        (State.ALLOWED, State.ALLOWED, True, None, False),
        (State.ALLOWED, State.ALLOWED, True, "mock", True),
    ],
)
def test_control_requires_all_evidence(support, permission, semantics, evidence, expected):
    capability = ControlCapability("bell", support, permission, semantics, evidence)
    assert capability.allowed is expected
    assert VehicleCapabilities((capability,)).allows("bell") is expected
    assert not VehicleCapabilities((capability,)).allows("buck")
    assert not VehicleCapabilities((capability, capability)).allows("bell")


def test_decision_reports_both_support_and_permission_without_leaking_evidence():
    from custom_components.ninebot.capabilities import decide_control

    capability = ControlCapability("bell", State.ALLOWED, State.DENIED, True, "private-token-label")
    result = decide_control(
        "bell", VehicleCapabilities((capability,)), (("consent_missing", False),)
    )
    assert result.blockers == ("consent_missing", "permission_denied")
    assert not result.allowed
    assert result.diagnostics()["permission"] == "denied"
    assert result.diagnostics()["evidence_available"] is True
    assert "private-token-label" not in str(result.diagnostics())


@pytest.mark.parametrize("evidence", [None, "", " ", "\n\t"])
def test_empty_evidence_never_opens_gate(evidence):
    from custom_components.ninebot.capabilities import decide_control

    capability = ControlCapability("bell", State.ALLOWED, State.ALLOWED, True, evidence)
    assert not capability.allowed
    result = decide_control("bell", VehicleCapabilities((capability,)), ())
    assert result.blockers == ("evidence_missing",)


def test_ambiguous_and_unknown_actions_cannot_be_authorized():
    from custom_components.ninebot.capabilities import decide_control

    capability = ControlCapability("bell", State.ALLOWED, State.ALLOWED, True, "reviewed")
    duplicate = decide_control("bell", VehicleCapabilities((capability, capability)), ())
    assert "ambiguous_capability" in duplicate.blockers
    assert duplicate.support is State.UNKNOWN
    assert not duplicate.allowed
    unknown = ControlCapability("custom", State.ALLOWED, State.ALLOWED, True, "reviewed")
    assert decide_control("custom", VehicleCapabilities((unknown,)), ()).blockers == (
        "unknown_action",
    )
    assert decide_control("bell", VehicleCapabilities((capability,)), ()).allowed
