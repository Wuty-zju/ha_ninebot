"""Raw retention is private, bounded and independent of entity normalization."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from custom_components.ninebot import adapters
from custom_components.ninebot.raw import Endpoint, RawLimitError, RawStore, build_record

NOW = datetime(2026, 10, 4, tzinfo=UTC)
FIXTURES = Path(__file__).parent / "fixtures/ninecli/0.1.7"


def test_private_payload_copy_and_schema_do_not_leak_values_or_unknown_keys():
    payload = {
        "dump_energy": "79",
        "password": "sensitive-password",
        "blue_secret": "secret-value",
        "owner_user_phone": "private-phone",
        "loc": {"lat": 31.12345, "lon": 121.12345},
        "private-user-in-key": {"score": 7},
        "unknown_business": {"future_value": 9},
        "battery_list": [{"score": 0}, {"score": None}],
    }
    record = build_record(Endpoint.STATUS, payload, NOW)
    payload["dump_energy"] = 1
    copy = record.payload()
    assert copy["dump_energy"] == "79"
    assert copy["password"] == copy["blue_secret"] == copy["owner_user_phone"] == "[redacted]"
    assert copy["unknown_business"]["future_value"] == 9
    copy["unknown_business"]["future_value"] = 100
    assert record.payload()["unknown_business"]["future_value"] == 9
    diag = json.dumps(record.diagnostics())
    for forbidden in (
        "sensitive-password",
        "secret-value",
        "private-phone",
        "31.12345",
        "121.12345",
        "private-user-in-key",
        "unknown_business",
        "future_value",
    ):
        assert forbidden not in diag
    fields = {field.path: field for field in record.schema}
    assert fields["battery_list[].score"].types == ("null", "number")
    assert fields["battery_list[].score"].occurrences == 2
    assert record.redacted_fields == 3
    assert record.unknown_fields > 0
    changed = build_record(Endpoint.STATUS, {"dump_energy": "80"}, NOW)
    equivalent = build_record(Endpoint.STATUS, {"dump_energy": "10"}, NOW)
    assert changed.schema_fingerprint == equivalent.schema_fingerprint


@pytest.mark.parametrize(
    "payload",
    [float("nan"), float("inf"), object(), {"a": "x" * 1048577}, [None] * 25001, {"x" * 257: 1}],
)
def test_rejects_unbounded_or_non_json_records(payload):
    with pytest.raises(RawLimitError):
        build_record(Endpoint.STATUS, payload, NOW)


def test_depth_size_and_aware_timestamp_limits():
    value = 1
    for _ in range(13):
        value = [value]
    with pytest.raises(RawLimitError):
        build_record(Endpoint.STATUS, value, NOW)
    with pytest.raises(RawLimitError):
        build_record(Endpoint.STATUS, {"x": "a" * 1048570}, NOW)
    with pytest.raises(ValueError):
        build_record(Endpoint.STATUS, {}, datetime(2026, 10, 4))
    for month in ("private-month", "202613", "２０２６１０"):
        with pytest.raises(ValueError):
            build_record(Endpoint.TRAVEL, {}, NOW, month)
    tree = 1
    for _ in range(8):
        tree = {"status": tree, "duration": tree}
    with pytest.raises(RawLimitError):
        build_record(Endpoint.STATUS, tree, NOW)


def test_lru_detail_count_expiry_global_budget_and_replacement():
    store = RawStore(max_details=2, detail_ttl=5, max_records=3)
    detail = build_record(Endpoint.TRIP_DETAIL, {"duration": 1}, NOW)
    for key in ("one", "two"):
        assert store.put(detail, "private-vehicle", key)
    assert store.get(Endpoint.TRIP_DETAIL, "private-vehicle", "one", now=NOW) is detail
    store.put(detail, "private-vehicle", "three")
    assert store.get(Endpoint.TRIP_DETAIL, "private-vehicle", "two", now=NOW) is None
    assert store.get(Endpoint.TRIP_DETAIL, "private-vehicle", "one", now=NOW) is detail
    assert (
        store.get(Endpoint.TRIP_DETAIL, "private-vehicle", "one", now=NOW + timedelta(seconds=5))
        is None
    )
    status = build_record(Endpoint.STATUS, {"dump_energy": 10}, NOW)
    for sn in ("a", "b", "c", "d"):
        store.put(status, sn)
    assert store.get(Endpoint.STATUS, "a", now=NOW) is None
    before = store.retained_bytes
    store.put(status, "b")
    assert store.retained_bytes == before
    bounded = RawStore(max_bytes=status.retained_bytes)
    bounded.put(status, "a")
    bounded.put(status, "b")
    assert bounded.get(Endpoint.STATUS, "a", now=NOW) is None
    assert bounded.retained_bytes <= bounded.max_bytes
    assert not bounded.put(detail, "a" * 257)
    assert bounded.rejected == 1
    too_small = RawStore(max_bytes=1)
    assert not too_small.put(status)
    with pytest.raises(ValueError):
        RawStore(max_records=0)
    assert "private-vehicle" not in json.dumps(store.diagnostics(NOW))
    store.clear()
    assert store.diagnostics(NOW)["record_count"] == store.retained_bytes == 0


def test_sanitized_recorded_fixtures_replay_and_provenance():
    metadata = json.loads((FIXTURES / "metadata.json").read_text())
    assert metadata["new_cloud_requests"] == 5
    assert not metadata["missing"]
    for row in metadata["records"]:
        assert row["provenance"] == "recorded_sanitized"
        payload = json.loads((FIXTURES / row["file"]).read_text())
        endpoint = Endpoint(row["endpoint"])
        record = build_record(endpoint, payload, NOW)
        assert record.schema
        if endpoint is Endpoint.VEHICLES:
            profiles = adapters.profiles(payload)
            assert len(profiles) == 2
            assert all(p.sn.startswith("fixture-") for p in profiles)
        elif endpoint is Endpoint.STATUS:
            status = adapters.status(payload)
            assert 0 <= status.battery <= 100
            assert status.latitude == status.longitude == 0  # Explicit synthetic substitution.
            assert not status.capabilities.allows("bell")
        elif endpoint is Endpoint.BATTERY:
            packs = adapters.batteries(payload)
            assert len(packs.batteries) == 1
            assert not packs.batteries[0].identified
            assert packs.batteries[0].cycles is None
        elif endpoint is Endpoint.TRAVEL:
            travel = adapters.travel(payload, payload["month"])
            if row["file"] == "travel-nonempty.json":
                assert len(travel.rides) == 20
                assert travel.last_ride.ride_id == "fixture-ride-01"
            else:
                assert travel.last_ride is None
                assert travel.mileage == 0
        else:
            from custom_components.ninebot.travel import parse_ride

            ride = parse_ride(payload, "202609", source=endpoint)
            assert len(ride.track_points) == 5
            assert all(point.latitude >= 30 for point in ride.track_points)
