"""Offline identity scenarios; named pack rows are synthetic, not recorded SN evidence."""

import hashlib
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from custom_components.ninebot.adapters import batteries
from custom_components.ninebot.battery import battery_signature, battery_summary
from custom_components.ninebot.compat import child_registry_api_available
from custom_components.ninebot.exceptions import NinebotError
from custom_components.ninebot.models import VehicleProfile, VehicleSnapshot
from custom_components.ninebot.sensor import battery_descriptions, legacy_battery_description


def snapshot(*rows):
    return VehicleSnapshot(
        VehicleProfile("vehicle", "name", "model"), battery=batteries({"battery_list": list(rows)})
    )


def pack(identity, voltage=72, support=True):
    return {
        "sn": identity,
        "bms_volt": voltage,
        "bat_temp": 25,
        "bms_cycle": 10,
        "have_bms_cycle_support": support,
    }


def test_vehicle_measurements_do_not_follow_a_primary_pack_into_multiple_rows():
    one = snapshot(pack("a"))
    descriptions = [
        d
        for d in battery_descriptions(one)
        if d.translation_key in {"bms_voltage", "batt_temp", "bms_cycles"}
    ]
    assert [d.key for d in descriptions] == ["bms_voltage", "batt_temp", "bms_cycles"]
    for changed in (snapshot(pack("a"), pack("b")), snapshot(), snapshot({}, {})):
        assert all(d.value(changed) is None for d in descriptions)
        assert all(legacy_battery_description(d.key).value(changed) is None for d in descriptions)
    replacement = snapshot(pack("replacement", 75))
    assert [d.value(replacement) for d in descriptions] == [75, 25, 10]
    assert [legacy_battery_description(d.key).value(replacement) for d in descriptions] == [
        75,
        25,
        10,
    ]
    unsupported = snapshot(pack("replacement", support=False))
    assert descriptions[-1].value(unsupported) is None


def test_pack_entities_survive_reorder_but_not_replacement_or_identity_loss():
    original = snapshot(pack("slot_0"), pack("b", 73))
    descriptions = [
        d
        for d in battery_descriptions(original)
        if d.translation_key in {"bms_voltage", "batt_temp", "bms_cycles"}
    ]
    first = descriptions[0]
    assert first.key == f"battery_{hashlib.sha256(b'slot_0').hexdigest()[:12]}_bms_voltage"
    assert [
        d.key
        for d in battery_descriptions(snapshot(pack("b", 73), pack("slot_0")))
        if d.translation_key in {"bms_voltage", "batt_temp", "bms_cycles"}
    ] == [d.key for d in descriptions[3:]] + [d.key for d in descriptions[:3]]
    assert first.value(snapshot(pack("b", 73), pack("slot_0", 74))) == 74
    assert first.value(snapshot(pack("slot_0", 75))) == 75
    assert first.value(snapshot(pack("replacement"), pack("b"))) is None
    assert first.value(snapshot({"bms_volt": 90}, pack("b"))) is None
    assert first.value(snapshot({"bms_volt": 90})) is None
    # A genuine serial spelling a placeholder must coexist with an unknown row.
    mixed = snapshot({"bms_volt": 90}, pack("slot_0", 76))
    assert first.value(mixed) == 76
    assert len(battery_descriptions(mixed)) == 6


def test_conflicting_identity_aliases_are_not_silently_attached_to_existing_history():
    with pytest.raises(NinebotError):
        snapshot({"battery_sn": "a", "sn": "b"})
    assert snapshot({"battery_sn": "a", "sn": " a "}).battery.batteries[0].key == "a"


def test_signature_separates_namespaces_and_delimiters_and_ignores_order():
    one = snapshot(pack("a,b"), pack("c")).battery
    two = snapshot(pack("a"), pack("b,c")).battery
    assert battery_signature(one) != battery_signature(two)
    assert battery_signature(one) == battery_signature(snapshot(pack("c"), pack("a,b")).battery)
    assert battery_signature(snapshot({}).battery) != battery_signature(
        snapshot(pack("unidentified")).battery
    )
    assert battery_signature(snapshot({}).battery) != battery_signature(snapshot({}, {}).battery)
    # Unknown same-count replacements cannot be detected: do not claim otherwise.
    assert battery_signature(snapshot({"bms_volt": 70}).battery) == battery_signature(
        snapshot({"bms_volt": 80}).battery
    )


def test_battery_diagnostics_include_shape_and_policy_without_identity_or_measurements():
    summary = battery_summary(snapshot(pack("private-sn"), {"bms_volt": 75}).battery)
    assert summary == {
        "reported_row_count": 2,
        "identified_row_count": 1,
        "unidentified_row_count": 1,
        "vehicle_measurement_unambiguous": False,
        "device_assignment": "vehicle",
        "component_model": "unverified",
    }


@pytest.mark.parametrize(
    "present,create,lookup,expected",
    [
        (False, True, True, False),
        (True, False, True, False),
        (True, True, False, False),
        (True, True, True, True),
    ],
)
def test_child_registry_capability_requires_the_public_operations(
    present, create, lookup, expected
):
    registry = SimpleNamespace(
        DeviceRegistry=SimpleNamespace(
            async_get_or_create_child=(lambda: None) if create else None,
            async_get_child_device_by_identifier=(lambda: None) if lookup else None,
        )
    )
    if present:
        registry.ChildDeviceInfo = dict
    with patch("custom_components.ninebot.compat.dr", registry):
        assert child_registry_api_available() is expected
