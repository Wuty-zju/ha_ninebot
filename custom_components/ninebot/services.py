"""Bounded, device-scoped history queries with explicit response data."""

from datetime import datetime
from functools import partial
from typing import Any, cast

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse, callback
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr
from homeassistant.util import dt as dt_util

from . import adapters
from .compat import device_entry_ids, is_child_device
from .compat import validation as vol
from .const import CONF_COORDINATES, DEFAULT_COORDINATES, DOMAIN
from .coordinator import NinebotCoordinator
from .exceptions import ErrorKind, NinebotError
from .models import TravelMonth
from .parsing import previous_month
from .raw import Endpoint, RawRecord
from .ride_models import Ride, RideDetail
from .runtime import NinebotConfigEntry
from .travel import MAX_TRACK_POINTS, merge_detail, opaque_id, parse_ride
from .travel_statistics import energy_statistics, returned_statistics

MAX_DETAIL_QUERIES = 5


def query_month(value: object) -> str:
    """Only supported, non-future business months; no implicit history scan."""
    if not isinstance(value, str) or not value.isascii():
        raise vol.Invalid("Use YYYYMM")
    try:
        previous_month(value)
    except ValueError as err:
        raise vol.Invalid("Use YYYYMM") from err
    if not "200001" <= value <= adapters.month_at(dt_util.utcnow()):
        raise vol.Invalid("Month outside the supported range")
    return value


def bounded_integer(value: object, maximum: int) -> int:
    """Do not silently truncate floats or accept booleans as page numbers."""
    if type(value) is not int or not 1 <= value <= maximum:
        raise vol.Invalid("Integer outside the supported range")
    return value


def ride_identifier(value: object) -> str:
    if not isinstance(value, str) or not (result := opaque_id(value)):
        raise vol.Invalid("Invalid ride ID")
    return result


COMMON = {
    vol.Required("device_id"): cv.string,
    vol.Optional("include_track", default=False): cv.boolean,
}
TRIPS_SCHEMA = vol.Schema(
    {
        **COMMON,
        vol.Required("month"): query_month,
        vol.Optional("page", default=1): partial(bounded_integer, maximum=1000),
        vol.Optional("limit", default=20): partial(bounded_integer, maximum=100),
        vol.Optional("include_detail", default=False): cv.boolean,
    }
)
DETAIL_SCHEMA = vol.Schema(
    {
        **COMMON,
        vol.Required("ride_id"): ride_identifier,
        vol.Optional("query_month"): query_month,
        vol.Optional("max_points", default=500): partial(bounded_integer, maximum=MAX_TRACK_POINTS),
    }
)


def validation_error(key: str) -> ServiceValidationError:
    return ServiceValidationError(translation_domain=DOMAIN, translation_key=key)


@callback
def resolve_vehicle(hass: HomeAssistant, device_id: str) -> tuple[NinebotConfigEntry, str]:
    """Resolve only one loaded account's current, present vehicle, not an entity."""
    device = dr.async_get(hass).async_get(device_id)
    if device is None or is_child_device(device) or device.disabled_by is not None:
        raise validation_error("query_device")
    identifiers = {identifier for domain, identifier in device.identifiers if domain == DOMAIN}
    if len(identifiers) != 1:
        raise validation_error("query_device")
    sn = next(iter(identifiers))
    owners = [
        entry
        for owner in device_entry_ids(device)
        if (entry := hass.config_entries.async_get_entry(owner)) and entry.domain == DOMAIN
    ]
    if len(owners) != 1:
        raise validation_error("query_device")
    entry = cast(NinebotConfigEntry, owners[0])
    if entry.state is not ConfigEntryState.LOADED or not entry.runtime_data.coordinator.fresh(
        sn, "profile"
    ):
        raise validation_error("query_unavailable")
    return entry, sn


def iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def ride_response(ride: Ride, include_track: bool = False) -> dict[str, Any]:
    """Whitelist the domain model. Never serialize raw objects or signed URLs."""
    result: dict[str, Any] = {
        "ride_id": ride.ride_id,
        "detail_id": ride.detail_id,
        "query_month": ride.query_month,
        "source": ride.source.value,
        "start_time": iso(ride.started_at),
        "end_time": iso(ride.ended_at),
        "distance_m": ride.distance_m,
        "duration_s": ride.duration_s,
        "max_speed_m_s": ride.server_max_speed_m_s,
        "average_speed_m_s": (
            None if "duration_time_difference" in ride.issues else ride.average_speed_m_s
        ),
        "energy_raw": ride.energy_raw,
        "energy_wh": ride.energy_raw,
        "energy_intensity_wh_per_km": energy_statistics(
            ride.distance_m / 1000 if ride.distance_m is not None else None,
            ride.energy_raw,
            basis="ride_metrics",
        )["energy_intensity_wh_per_km"],
        "energy_unit": "Wh",
        "energy_unit_evidence": "maintainer_confirmed",
        "used_electricity_raw": ride.used_electricity_raw,
        "server_average_speed_raw": ride.server_average_speed_raw,
        "raw_units": "unknown",
        "parser_contract": ride.parser_contract,
        "field_provenance": dict(ride.field_provenance),
        "field_states": {name: state.value for name, state in ride.field_states},
        "field_sources": dict(ride.field_sources),
        "detail_field_states": {name: state.value for name, state in ride.detail_field_states},
        "warnings": list(ride.issues),
        "speed_samples": [
            {"sequence": sample.sequence, "speed_raw": sample.raw, "unit": "unknown"}
            for sample in ride.speed_samples
        ],
    }
    if include_track:
        result["coordinate_system"] = "unknown"
        result["track"] = [
            {
                "latitude": point.latitude,
                "longitude": point.longitude,
                "sequence": point.sequence,
                "speed_raw": point.speed_raw,
                "distance_delta_raw": point.distance_delta_raw,
                "raw_units": "unknown",
            }
            for point in ride.track_points
        ]
        result["track_pagination"] = {
            "returned": len(ride.track_points),
            "total_known": ride.total_track_points,
            "truncated": ride.track_truncated,
        }
    return result


def month_data(record: RawRecord) -> TravelMonth:
    assert record.query_month is not None
    return adapters.travel(record.payload(), record.query_month)


def month_response(travel: TravelMonth) -> dict[str, Any]:
    """Small aggregates plus a bounded daily series, never historical state."""
    summary = travel.summary
    assert summary is not None
    return {
        "month_mileage_km": summary.mileage_km,
        "month_energy_raw": summary.energy_wh,
        "month_energy_wh": summary.energy_wh,
        "month_energy_unit": "Wh",
        "energy_unit_evidence": "maintainer_confirmed",
        "month_ride_count": summary.ride_count,
        "month_duration_s": summary.duration_s,
        "daily_mileage": [
            {"date": point.day.isoformat(), "distance_km": point.distance_km}
            for point in summary.daily_mileage
        ],
        "daily_mileage_status": summary.chart_status,
        "statistics": {
            "server_month": energy_statistics(
                summary.mileage_km, summary.energy_wh, basis="server_month_summary"
            ),
            "returned_rides": returned_statistics(travel.rides),
        },
        "coverage": {
            "reported_count": summary.ride_count,
            "returned_count": summary.returned_count,
            "unique_ride_count": summary.unique_ride_count,
            "fraction": summary.coverage,
            "list_complete": summary.list_complete,
            "returned_distance_m": summary.returned_distance_m,
            "returned_duration_s": summary.returned_duration_s,
            "returned_energy_wh": summary.returned_energy_wh,
        },
    }


async def detail_ride(
    hass: HomeAssistant, co: NinebotCoordinator, sn: str, summary: Ride, max_points: int
) -> tuple[RideDetail, RawRecord]:
    if summary.detail_id is None:
        raise validation_error("query_ride")
    record = await co.async_query_detail(sn, summary.detail_id, summary.query_month)
    reference = co.raw.reference(
        Endpoint.TRIP_DETAIL,
        sn,
        summary.detail_id,
        now=dt_util.utcnow(),
        query_month=summary.query_month,
    )
    ride = await hass.async_add_executor_job(detail_data, record, summary, max_points)
    if reference is not None and co.raw.resolve(reference, now=dt_util.utcnow()) is not record:
        reference = None
    return RideDetail(
        summary.detail_id,
        summary.query_month,
        record.received_at,
        ride,
        record.backend_version,
        record.endpoint_version,
        reference,
    ), record


def detail_data(record: RawRecord, summary: Ride, max_points: int) -> Ride:
    """Decode and normalize bounded raw JSON away from the event loop."""
    if record.endpoint is not Endpoint.TRIP_DETAIL or record.query_month != summary.query_month:
        raise NinebotError(ErrorKind.PROTOCOL)
    detail = parse_ride(
        record.payload(), summary.query_month, source=Endpoint.TRIP_DETAIL, max_points=max_points
    )
    # The observed detail lacks an ID. Check available timestamps rather than
    # merging a demonstrably different trip into a legitimate month index.
    if not any((detail.started_at, detail.ended_at, detail.distance_m, detail.duration_s)):
        raise NinebotError(ErrorKind.PROTOCOL)
    for field in ("started_at", "ended_at"):
        before, after = getattr(summary, field), getattr(detail, field)
        if before is not None and after is not None and before != after:
            raise NinebotError(ErrorKind.PROTOCOL)
    return merge_detail(summary, detail)


@callback
def assert_query_scope(
    hass: HomeAssistant, device_id: str, entry_id: str, sn: str, include_track: bool
) -> None:
    """Do not return in-flight data after unload, device removal or ownership change."""
    entry, current_sn = resolve_vehicle(hass, device_id)
    if entry.entry_id != entry_id or current_sn != sn:
        raise validation_error("query_device")
    if include_track and entry.options.get(CONF_COORDINATES, DEFAULT_COORDINATES) is not True:
        raise validation_error("query_coordinates")


async def async_query(hass: HomeAssistant, call: ServiceCall) -> dict[str, Any]:
    entry, sn = resolve_vehicle(hass, call.data["device_id"])
    co = entry.runtime_data.coordinator
    include_track = call.data["include_track"]
    if include_track and entry.options.get(CONF_COORDINATES, DEFAULT_COORDINATES) is not True:
        raise validation_error("query_coordinates")
    is_list = call.service == "get_trips"
    if is_list:
        if call.data["include_detail"] and call.data["limit"] > MAX_DETAIL_QUERIES:
            raise validation_error("query_detail_limit")
        if include_track and not call.data["include_detail"]:
            raise validation_error("query_track_detail")
        month = call.data["month"]
    else:
        month = call.data.get("query_month", adapters.month_at(dt_util.utcnow()))
    try:
        record = await co.async_query_month(sn, month)
        travel = await hass.async_add_executor_job(month_data, record)
        rides = travel.rides
        if is_list:
            # Stable local order when verified times exist; retain original order
            # for rows without times rather than inventing server ordering.
            rides = tuple(
                sorted(
                    rides,
                    key=lambda ride: iso(ride.ended_at or ride.started_at) or "",
                    reverse=True,
                )
            )
            page, limit = call.data["page"], call.data["limit"]
            start = (page - 1) * limit
            selected = rides[start : start + limit]
            if call.data["include_detail"]:
                # Serial backend and explicit <=5 fanout; no background downloads.
                enriched = []
                for summary in selected:
                    if (
                        summary.ride_id is None
                        or sum(ride.ride_id == summary.ride_id for ride in rides) != 1
                    ):
                        raise validation_error("query_ride")
                    enriched.append((await detail_ride(hass, co, sn, summary, 500))[0].ride)
                selected = tuple(enriched)
            response = {
                "schema_version": 2,
                "query_month": month,
                "received_at": iso(record.received_at),
                "source": "ninecli",
                "backend_version": record.backend_version,
                **month_response(travel),
                "rides": [ride_response(ride, include_track) for ride in selected],
                "pagination": {
                    "page": page,
                    "limit": limit,
                    "returned": len(selected),
                    "available_in_response": len(rides),
                    "total_known": travel.reported_ride_count,
                    "has_more": start + limit < len(rides),
                    "upstream_complete": (
                        travel.summary.list_complete
                        if travel.summary and travel.summary.list_complete is not None
                        else "unknown"
                    ),
                },
                "warnings": [
                    "upstream_pagination_unverified",
                    *(travel.summary.warnings if travel.summary else ()),
                ],
            }
        else:
            matches = [ride for ride in rides if ride.ride_id == call.data["ride_id"]]
            if len(matches) != 1:
                raise validation_error("query_ride")
            detail, detail_record = await detail_ride(
                hass, co, sn, matches[0], call.data["max_points"]
            )
            response = {
                "schema_version": 2,
                "query_month": month,
                "received_at": iso(detail_record.received_at),
                "source": "ninecli",
                "backend_version": detail_record.backend_version,
                "ride": ride_response(detail.ride, include_track),
                "detail_context": {
                    "requested_detail_id": detail.requested_detail_id,
                    "query_month": detail.query_month,
                    "received_at": iso(detail.received_at),
                    "backend_version": detail.backend_version,
                    "endpoint_version": detail.endpoint_version,
                },
            }
    except NinebotError as err:
        raise HomeAssistantError(translation_domain=DOMAIN, translation_key=err.kind.value) from err
    assert_query_scope(hass, call.data["device_id"], entry.entry_id, sn, include_track)
    return response


@callback
def async_register_actions(hass: HomeAssistant) -> None:
    """Actions remain available in editors when no account is loaded."""
    from .history_actions import HISTORY_SCHEMA, async_history_query
    from .statistics_actions import STATISTICS_SCHEMA, async_statistics_query
    from .statistics_export import IMPORT_SCHEMA, async_import_statistics

    hass.services.async_register(
        DOMAIN,
        "import_statistics",
        partial(async_import_statistics, hass),
        schema=IMPORT_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )

    hass.services.async_register(
        DOMAIN,
        "get_statistics",
        partial(async_statistics_query, hass),
        schema=STATISTICS_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )

    hass.services.async_register(
        DOMAIN,
        "get_history",
        partial(async_history_query, hass),
        schema=HISTORY_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )
    for name, schema in (("get_trips", TRIPS_SCHEMA), ("get_trip_detail", DETAIL_SCHEMA)):
        hass.services.async_register(
            DOMAIN,
            name,
            partial(async_query, hass),
            schema=schema,
            supports_response=SupportsResponse.ONLY,
        )
