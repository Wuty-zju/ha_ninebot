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
