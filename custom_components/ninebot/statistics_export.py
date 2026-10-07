"""Explicit, bounded imports of closed business periods into native Recorder.

State is the period value; sum is the cumulative sum of known period values.
Missing periods stay absent. Corrections rebuild the entire existing suffix.
No cloud access, raw payload, location or per-ride state is needed here.
"""

import math
from asyncio import timeout
from datetime import UTC, datetime
from functools import partial
from hashlib import sha256
from typing import Any, Literal
from zoneinfo import ZoneInfo

from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.models import (
    StatisticData,
    StatisticMeanType,
    StatisticMetaData,
)
from homeassistant.components.recorder.statistics import (
    StatisticsRow,
    async_add_external_statistics,
    get_metadata,
    statistics_during_period,
)
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers.recorder import DATA_INSTANCE
from homeassistant.util import dt as dt_util

from .compat import validation as vol
from .const import BUSINESS_TIMEZONE, DOMAIN
from .services import assert_query_scope, resolve_vehicle, validation_error
from .statistics_actions import METRICS, STATISTICS_SCHEMA, async_statistics_query

IMPORT_SCHEMA = STATISTICS_SCHEMA.extend({vol.Optional("refresh", default=False): False})
MAX_SERIES_POINTS = 12_000
MAX_PENDING_IMPORTS = 2
UNITS: dict[str, tuple[str | None, str | None]] = {
    "distance_km": ("distance", "km"),
    "energy_wh": ("energy", "Wh"),
    "ride_count": (None, None),
    "duration_s": ("duration", "s"),
}
LABELS = {
    "distance_km": "Distance",
    "energy_wh": "Energy",
    "ride_count": "Rides",
    "duration_s": "Duration",
}
CHINESE_LABELS = {
    "distance_km": "里程",
    "energy_wh": "能耗",
    "ride_count": "骑行次数",
    "duration_s": "骑行时长",
}
type Grain = Literal["day", "month"]


def statistic_id(entry_id: str, sn: str, grain: Grain, metric: str) -> str:
    identity = sha256(f"{entry_id}\0{sn}".encode()).hexdigest()[:32]
    return f"{DOMAIN}:{identity}_{grain}_{metric}"


def bucket(value: str, grain: Grain) -> datetime:
    """Only explicit calendar period starts, independent of global dt timezone."""
    parsed = datetime.strptime(value, "%Y-%m-%d" if grain == "day" else "%Y%m")
    return parsed.replace(tzinfo=ZoneInfo(BUSINESS_TIMEZONE))


def valid_value(value: object, metric: str) -> bool:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return False
    try:
        return (
            math.isfinite(value) and value >= 0 and (metric != "ride_count" or int(value) == value)
        )
    except OverflowError:
        return False


def merge_periods(
    existing: list[StatisticsRow],
    incoming: dict[datetime, float],
    grain: Grain,
    metric: str,
    now: datetime,
) -> list[StatisticData]:
    """Validate before queueing anything; never silently repair foreign data."""
    if len(existing) > MAX_SERIES_POINTS:
        raise validation_error("statistics_import_data")
    values: dict[datetime, float] = {}
    today = now.astimezone(ZoneInfo(BUSINESS_TIMEZONE)).date()
    for row in existing:
        start, value = row.get("start"), row.get("state")
        if (
            not isinstance(start, (int, float))
            or isinstance(start, bool)
            or not math.isfinite(start)
        ):
            raise validation_error("statistics_import_data")
        try:
            stamp = datetime.fromtimestamp(start, ZoneInfo(BUSINESS_TIMEZONE))
        except (ValueError, OverflowError, OSError) as err:
            raise validation_error("statistics_import_data") from err
        if (
            stamp.year < 2000
            or stamp.date() >= today
            or any((stamp.hour, stamp.minute, stamp.second, stamp.microsecond))
            or (
                grain == "month"
                and (stamp.day != 1 or stamp.strftime("%Y%m") >= today.strftime("%Y%m"))
            )
            or stamp in values
            or not valid_value(value, metric)
        ):
            raise validation_error("statistics_import_data")
        assert value is not None
        values[stamp] = value
    values.update(incoming)
    if len(values) > MAX_SERIES_POINTS:
        raise validation_error("statistics_import_data")
    total = 0.0
    result: list[StatisticData] = []
    for stamp, value in sorted(values.items()):
        total += value
        if not math.isfinite(total):
            raise validation_error("statistics_import_data")
        result.append({"start": stamp, "state": value, "sum": total})
    return result


def prepare_import(
    hass: HomeAssistant,
    entry_id: str,
    sn: str,
    name: str,
    response: dict[str, Any],
    now: datetime,
    chinese: bool = False,
) -> tuple[list[tuple[StatisticMetaData, list[StatisticData]]], list[dict[str, Any]]]:
    """Recorder executor only; immutable response, bounded own series reads."""
    plans: list[tuple[StatisticMetaData, list[StatisticData]]] = []
    descriptions: list[dict[str, Any]] = []
    for grain in ("day", "month"):
        rows = response["days" if grain == "day" else "months"]
        for metric in METRICS:
            sid = statistic_id(entry_id, sn, grain, metric)
            incoming = {
                bucket(row["date" if grain == "day" else "month"], grain): float(row[metric])
                for row in rows
                if row["period_closed"]
                and valid_value(row[metric], metric)
                and (grain == "day" or row["observed_after_period_end"])
            }
            description: dict[str, Any] = {
                "statistic_id": sid,
                "grain": grain,
                "metric": metric,
                "source_points": len(incoming),
                "skipped_points": len(rows) - len(incoming),
                "queued_points": 0,
            }
            descriptions.append(description)
            if not incoming:
                continue
            unit_class, unit = UNITS[metric]
            metadata: StatisticMetaData = {
                "statistic_id": sid,
                "source": DOMAIN,
                "name": (
                    f"{name}：{'每日' if grain == 'day' else '每月'}{CHINESE_LABELS[metric]}"
                    if chinese
                    else f"{name}: {grain} {LABELS[metric]}"
                ),
                "mean_type": StatisticMeanType.NONE,
                "has_sum": True,
                "unit_class": unit_class,
                "unit_of_measurement": unit,
            }
            old_meta = get_metadata(hass, statistic_ids={sid}).get(sid)
            if old_meta and any(
                old_meta[1].get(key) != metadata[key]
                for key in (
                    "source",
                    "mean_type",
                    "has_sum",
                    "unit_class",
                    "unit_of_measurement",
                )
            ):
                raise validation_error("statistics_import_data")
            existing = statistics_during_period(
                hass,
                datetime(2000, 1, 1, tzinfo=UTC),
                None,
                {sid},
                "hour",
                None,
                {"state"},
            ).get(sid, [])
            points = merge_periods(existing, incoming, grain, metric, now)
            description["queued_points"] = len(points)
            description["name"] = metadata["name"]
            plans.append((metadata, points))
    return plans, descriptions


async def async_import_statistics(hass: HomeAssistant, call: ServiceCall) -> dict[str, Any]:
    """A write action: explicit source range, no upstream refresh or auto-import."""
    entry, sn = resolve_vehicle(hass, call.data["device_id"], require_live=False)
    if DATA_INSTANCE not in hass.data:
        raise validation_error("statistics_recorder")
    if hass.config.time_zone != BUSINESS_TIMEZONE:
        raise validation_error("statistics_timezone")
    runtime = entry.runtime_data
    if runtime.statistics_import_pending >= MAX_PENDING_IMPORTS:
        raise validation_error("busy")
    runtime.statistics_import_pending += 1
    try:
        async with runtime.statistics_import_lock:
            assert_query_scope(
                hass, call.data["device_id"], entry.entry_id, sn, False, require_live=False
            )
            recorder = get_instance(hass)
            # A second import must read committed values from the first, even if
            # its caller was cancelled after queueing the previous write.
            try:
                async with timeout(30):
                    await recorder.async_block_till_done()
            except TimeoutError as err:
                raise validation_error("statistics_recorder") from err
            response = await async_statistics_query(hass, call)
            now = dt_util.utcnow()
            name = runtime.coordinator.data[sn].profile.name
            plans, series = await recorder.async_add_executor_job(
                partial(
                    prepare_import,
                    hass,
                    entry.entry_id,
                    sn,
                    name,
                    response,
                    now,
                    hass.config.language.startswith("zh"),
                )
            )
            assert_query_scope(
                hass, call.data["device_id"], entry.entry_id, sn, False, require_live=False
            )
            if hass.config.time_zone != BUSINESS_TIMEZONE:
                raise validation_error("statistics_timezone")
            # No await in this block: ownership cannot change between plans.
            for metadata, points in plans:
                async_add_external_statistics(hass, metadata, points)
            return {
                "schema_version": 1,
                "status": "queued" if plans else "no_closed_data",
                "business_timezone": BUSINESS_TIMEZONE,
                "scope": response["scope"],
                "series": series,
                "dashboard_cards": [
                    {
                        "type": "statistics-graph",
                        "title": item["name"],
                        "period": item["grain"],
                        "chart_type": "bar",
                        "stat_types": ["change"],
                        "days_to_show": min(
                            730,
                            (
                                now.date()
                                - bucket(response["scope"]["start_month"], "month").date()
                            ).days
                            + 2,
                        ),
                        "entities": [{"entity": item["statistic_id"], "name": item["name"]}],
                    }
                    for item in series
                    if item["queued_points"]
                ],
                "warnings": [
                    "recorder_queue_is_not_a_durable_commit_acknowledgement",
                    "missing_periods_are_not_zero_previous_verified_points_are_retained",
                    "use_day_or_wider_for_day_series_and_month_for_month_series",
                    "no_hourly_distribution",
                    "dashboard_cards_show_a_rolling_window_of_at_most_730_days",
                    "upstream_pagination_unverified",
                ],
            }
    finally:
        runtime.statistics_import_pending -= 1
