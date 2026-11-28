"""Synthetic statistics shared by the native Action and optional model smoke."""

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
        ("那昨天呢？", {"change": "yesterday"}, after, primary.id, next_date, zero),
        (
            "另一辆呢？",
            {"change": "other_vehicle"},
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
    denied = await ask("另一辆呢？", {"change": "other_vehicle"}, {"query_context": anchor})
    assert denied["outcome"] == "denied" and denied["results"] == []
    grants(**{"synthetic-sn": statistics})
    absent = await ask("另一辆呢？", {"change": "other_vehicle"}, {"query_context": anchor})
    assert absent["stop_reason"] == "no_other_vehicle"
    assert absent["next_step"] == {"type": "no_other_vehicle"} and absent["results"] == []
    third_sn = "third-synthetic-sn"
    co.data[third_sn] = VehicleSnapshot(VehicleProfile(third_sn, "Vehicle A", "Synthetic"))
    third = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id, identifiers={("ninebot", third_sn)}, name="Vehicle A"
    )
    grants(**{**both, "second-synthetic-sn": status, third_sn: statistics})
    ambiguous = await ask("另一辆呢？", {"change": "other_vehicle"}, {"query_context": anchor})
    assert ambiguous["outcome"] == "needs_input" and ambiguous["results"] == [], ambiguous
    assert {choice["device_id"] for choice in ambiguous["next_step"]["choices"]} == {
        other.id,
        third.id,
    }
    assert reads == [primary.id, primary.id, other.id]
    return summary


async def check_period_metrics(hass, entry, primary, other, ask, *, model_smoke=False):
    """Replay the same-name vehicle, month and measure across separate business months."""
    from homeassistant.core import SupportsResponse
    from homeassistant.util import dt as dt_util

    from custom_components.ninebot.assistant_access import (
        READ_STATISTICS,
        READ_STATUS,
        AssistantAccess,
        VehicleAccess,
        with_assistant_access,
    )

    def grant(read):
        hass.config_entries.async_update_entry(
            entry,
            options=with_assistant_access(
                entry.options,
                AssistantAccess(
                    True,
                    {
                        "synthetic-sn": VehicleAccess(frozenset({READ_STATISTICS})),
                        "second-synthetic-sn": VehicleAccess(frozenset({read})),
                    },
                ),
            ),
        )

    full = PERIODS["last_month"]
    month = full["start_date"][:7]
    november = datetime.fromisoformat(PERIODS["yesterday"]["start_date"]).replace(tzinfo=UTC)
    december = november.replace(month=12)
    reads = []
    revoke_in_flight = False
    energy = full["energy_wh"]

    async def archive(call):
        assert call.context.user_id == "synthetic-admin"
        assert call.data["refresh"] is False and call.data["include_daily"] is False
        assert call.data["start_month"] == call.data["end_month"] == month.replace("-", "")
        target = call.data["device_id"]
        reads.append(target)
        sample = full if target == other.id else PERIODS["verified_empty"]
        if revoke_in_flight:
            grant(READ_STATUS)
        return {
            "months": [
                {
                    "month": call.data["start_month"],
                    "ride_count": sample["ride_count"],
                    "energy_wh": energy if target == other.id else sample["energy_wh"],
                    "period_closed": True,
                    "observed_after_period_end": True,
                    "received_at": november.isoformat(),
                }
            ]
        }

    hass.services.async_register(
        "ninebot", "get_statistics", archive, supports_response=SupportsResponse.ONLY
    )
    grant(READ_STATISTICS)
    context = {"device_id": other.id, "date": november.date().isoformat(), "metric": "ride_count"}
    steps = []
    for text, arguments, instant, metric, value in (
        ("那上个月呢？", {"change": "last_month"}, november, "ride_count", full["ride_count"]),
        ("耗电量呢？", {"change": "energy_wh"}, december, "energy_wh", energy),
    ):
        with patch.object(dt_util, "utcnow", return_value=instant):
            result = await ask(text, arguments, {"query_context": context})
        assert result["outcome"] == "evidence", result
        context = {
            "device_id": other.id,
            "month": month,
            "through_date": full["end_date"],
            "metric": metric,
        }
        assert result["query_context"] == context, result
        (receipt,) = result["results"]
        payload = receipt["result"]
        assert payload["data"] == {
            metric: value,
            "units": {metric: "Wh" if metric == "energy_wh" else None},
        }
        assert payload["query"]["start_date"] == full["start_date"]
        assert payload["query"]["end_date"] == full["end_date"]
        assert payload["evidence"]["source_kind"] == "server_month_summary"
        assert payload["quality"]["metrics"][metric]["status"] == "available"
        assert result["answer"]["request_ids"] == [payload["request_id"]]
        assert all(
            fragment in result["answer"]["text"]
            for fragment in (full["start_date"], full["end_date"], str(value), other.id)
        )
        steps.append(
            {
                "turn": text,
                "vehicle": "B",
                "month": month,
                "through_date": full["end_date"],
                "metric": metric,
                "value": value,
            }
        )
    assert reads == [other.id, other.id]
    grant(READ_STATUS)
    denied = await ask("耗电量呢？", {"change": "energy_wh"}, {"query_context": context})
    assert denied["outcome"] == "denied" and not denied["results"] and "query_context" not in denied
    assert str(energy) not in denied["answer"]["text"] and reads == [other.id, other.id]
    if model_smoke:
        return steps

    grant(READ_STATISTICS)
    from custom_components.ninebot.assistant_ask import QUERY_CONTEXT
    from custom_components.ninebot.compat import validation as vol

    for bad in (
        {**context, "through_date": november.date().isoformat()},
        {**context, "date": november.date().isoformat()},
        {key: val for key, val in context.items() if key != "through_date"},
    ):
        try:
            QUERY_CONTEXT(bad)
        except vol.Invalid:
            pass
        else:
            raise AssertionError("An inconsistent monthly receipt was accepted")
    open_month = {
        **context,
        "month": november.strftime("%Y-%m"),
        "through_date": november.date().isoformat(),
    }
    for args in ({"change": "energy_wh"}, {"change": open_month["month"]}):
        with patch.object(dt_util, "utcnow", return_value=december):
            changed = await ask("耗电量呢？", args, {"query_context": open_month})
        assert changed["outcome"] == "failed" and "query_context" not in changed
        assert changed["results"][0]["result"]["error"]["code"] == "period_changed"
    assert reads == [other.id, other.id]

    revoke_in_flight = True
    denied = await ask("耗电量呢？", {"change": "energy_wh"}, {"query_context": context})
    assert (
        denied["outcome"] == "denied" and denied["results"] == [] and "query_context" not in denied
    )
    revoke_in_flight = False
    grant(READ_STATISTICS)
    energy = PERIODS["partial"]["energy_wh"]
    refreshed = await ask("耗电量呢？", {"change": "energy_wh"}, {"query_context": context})
    assert refreshed["outcome"] == "evidence", refreshed
    payload = refreshed["results"][0]["result"]
    assert (
        payload["data"]["energy_wh"] is None
        and payload["quality"]["metrics"]["energy_wh"]["status"] == "missing"
    )
    assert f"Energy: {full['energy_wh']} Wh" not in refreshed["answer"]["text"], refreshed[
        "answer"
    ]["text"]
    assert reads == [other.id] * 4
    return steps
