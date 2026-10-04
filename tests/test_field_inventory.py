"""Every recorded business shape must have an explicit reviewed disposition."""

import json
from pathlib import Path

FIXTURES = Path(__file__).parent / "fixtures/ninecli/0.1.7"
ROOT = Path(__file__).parents[1]


def field_types(value, path="$", observed=None):
    if observed is None:
        observed = {}
    observed.setdefault(path, set()).add(type(value).__name__)
    if isinstance(value, dict):
        for key, child in value.items():
            field_types(child, f"{path}.{key}", observed)
    elif isinstance(value, list):
        for child in value:
            field_types(child, f"{path}[]", observed)
    return observed


def test_current_inventory_covers_all_recorded_paths_types_and_explains_each_use():
    inventory = json.loads((ROOT / "docs/evidence/v2x-current-field-usage.json").read_text())
    indexed = {}
    for group in inventory["observed_endpoints"]:
        for row in group["fields"]:
            key = group["endpoint"], row["path"]
            assert key not in indexed
            indexed[key] = row
            assert set(row["classification"].split("/")) <= set("ABCDEFGHIJ")
            assert all(row[key] for key in ("meaning", "current_use", "unit", "evidence"))
            assert row["sources"]
            assert all((ROOT / source).is_file() for source in row["sources"])
    assert len(indexed) == inventory["field_path_count"]
    metadata = json.loads((FIXTURES / "metadata.json").read_text())
    for record in metadata["records"] + metadata["selected_shape_fixtures"]:
        data = json.loads((FIXTURES / record["file"]).read_text())
        for path, types in field_types(data).items():
            row = indexed[record["endpoint"], path]
            assert types <= set(row["types"])
            assert f"tests/fixtures/ninecli/0.1.7/{record['file']}" in row["sources"]
    # Source/candidate aliases must not masquerade as real recorded fields.
    assert ("battery", "$.battery_list[].battery_sn") not in indexed
    assert ("trip_detail", "$.gps") not in indexed


def test_recorded_command_shape_is_classified_separately_from_business_telemetry():
    inventory = json.loads((ROOT / "docs/evidence/v2x-current-field-usage.json").read_text())
    metadata = json.loads((FIXTURES / "metadata.json").read_text())
    recorded = metadata["control_response_fixtures"][0]
    paths = field_types(json.loads((FIXTURES / recorded["file"]).read_text()))
    endpoints = inventory["observed_command_responses"]
    assert {g["endpoint"] for g in endpoints} == set(recorded["endpoints"])
    assert sum(len(g["fields"]) for g in endpoints) == inventory["command_response_path_count"]
    for endpoint in endpoints:
        assert endpoint["http_status"] == recorded["http_status"] == 200
        assert endpoint["evidence"] and endpoint["sources"]
        assert all((ROOT / path).is_file() for path in endpoint["sources"])
        assert {row["path"]: set(row["types"]) for row in endpoint["fields"]} == paths
        assert all(row["meaning"] and row["current_use"] for row in endpoint["fields"])
        assert all(
            set(row["classification"].split("/")) <= set("ABCDEFGHIJ") for row in endpoint["fields"]
        )
