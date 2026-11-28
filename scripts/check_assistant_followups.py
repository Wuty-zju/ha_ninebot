"""Synthetic daily statistics shared by the native Action and optional model smoke."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

PERIODS = json.loads(
    (
        Path(__file__).resolve().parents[1] / "tests/fixtures/assistant/evaluation-fixtures.json"
    ).read_text()
)["periods"]


async def check(hass, entry, co, primary, other, ask, *, model_smoke=False):
    from homeassistant.core import SupportsResponse
    from homeassistant.helpers import device_registry as dr
    from homeassistant.util import dt as dt_util

    from custom_components.ninebot.assistant_access import (
        READ_STATISTICS,
        READ_STATUS,
        AssistantAccess,
        VehicleAccess,
        with_assistant_access,
    )
    from custom_components.ninebot.models import VehicleProfile, VehicleSnapshot

    first_date = PERIODS["yesterday"]["start_date"]
    midnight = datetime.fromisoformat(first_date).replace(tzinfo=UTC, hour=16) + timedelta(days=1)
    after = midnight + timedelta(seconds=1)
    next_date = midnight.date().isoformat()
    full, zero = PERIODS["yesterday"]["ride_count"], PERIODS["verified_empty"]["ride_count"]
    counts = {primary.id: {first_date: full, next_date: zero}, other.id: {next_date: full}}
    statistics = VehicleAccess(frozenset({READ_STATISTICS}))
    status = VehicleAccess(frozenset({READ_STATUS}))
    reads = []

    def grants(**vehicles):
        hass.config_entries.async_update_entry(
            entry, options=with_assistant_access(entry.options, AssistantAccess(True, vehicles))
        )

    async def archive(call):
        target = call.data["device_id"]
        assert call.context.user_id == "synthetic-admin"
        assert call.data["refresh"] is False and call.data["include_daily"] is True
        reads.append(target)
        return {
            "days": [
                {
                    "date": day,
                    "ride_count": count,
                    "period_closed": True,
                    "ride_window_complete": True,
                    "completeness_basis": "upstream_complete",
                    "received_at": dt_util.utcnow().isoformat(),
                }
                for day, count in counts[target].items()
            ],
        }

    hass.services.async_register(
        "ninebot", "get_statistics", archive, supports_response=SupportsResponse.ONLY
    )
    both = {"synthetic-sn": statistics, "second-synthetic-sn": statistics}
    grants(**both)
    context = None
    summary = []
    for question, args, instant, target, date, count in (
        (
            "这辆车昨天骑了几趟？",
            {"period": "yesterday", "metric": "ride_count"},
            midnight - timedelta(seconds=1),
            primary.id,
            first_date,
            full,
        ),
        ("那昨天呢？", {"period": "yesterday"}, after, primary.id, next_date, zero),
        (
            "另一辆呢？",
            {"other_vehicle": True},
            after + timedelta(days=1),
            other.id,
            next_date,
            full,
        ),
    ):
        selection = {"device_id": primary.id} if context is None else {"query_context": context}
        with patch.object(dt_util, "utcnow", return_value=instant):
            response = await ask(question, args, selection)
        assert response["outcome"] == "evidence", response
        context = {"device_id": target, "date": date, "metric": "ride_count"}
        assert response["query_context"] == context, response
        (receipt,) = response["results"]
        payload = receipt["result"]
        assert payload["data"]["ride_count"] == count
        assert payload["evidence"]["source_kind"] == "account_archive"
        assert response["answer"]["request_ids"] == [payload["request_id"]]
        answer = response["answer"]["text"]
        assert all(
            fragment in answer
            for fragment in (target, date, f"Ride count: {count}", "Asia/Shanghai")
        )
        summary.append(
            {
                "turn": question,
                "vehicle": "A" if target == primary.id else "B",
                "date": date,
                "count": count,
                "fresh_receipt": True,
            }
        )

    # Yesterday's successful B read cannot be replayed after B loses its grant.
    grants(**{**both, "second-synthetic-sn": status})
    denied = await ask("那昨天呢？", {}, {"query_context": context})
    assert denied["outcome"] == "denied" and denied["results"] == []
    assert "query_context" not in denied and "Ride count:" not in denied["answer"]["text"]
    assert reads == [primary.id, primary.id, other.id], reads
    if model_smoke:
        return summary

    anchor = {**context, "device_id": primary.id}
    denied = await ask("另一辆呢？", {"other_vehicle": True}, {"query_context": anchor})
    assert denied["outcome"] == "denied" and denied["results"] == []
    grants(**{"synthetic-sn": statistics})
    absent = await ask("另一辆呢？", {"other_vehicle": True}, {"query_context": anchor})
    assert absent["stop_reason"] == "no_other_vehicle"
    assert absent["next_step"] == {"type": "no_other_vehicle"} and absent["results"] == []
    third_sn = "third-synthetic-sn"
    co.data[third_sn] = VehicleSnapshot(VehicleProfile(third_sn, "Vehicle A", "Synthetic"))
    third = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id, identifiers={("ninebot", third_sn)}, name="Vehicle A"
    )
    grants(**{**both, "second-synthetic-sn": status, third_sn: statistics})
    ambiguous = await ask("另一辆呢？", {"other_vehicle": True}, {"query_context": anchor})
    assert ambiguous["outcome"] == "needs_input" and ambiguous["results"] == [], ambiguous
    assert {choice["device_id"] for choice in ambiguous["next_step"]["choices"]} == {
        other.id,
        third.id,
    }
    assert reads == [primary.id, primary.id, other.id]
    return summary
