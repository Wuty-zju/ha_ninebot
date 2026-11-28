"""Scalar-only archive comparisons and temporary, user-scoped selections for the HA map."""

from collections.abc import Callable
from copy import deepcopy
from dataclasses import dataclass
from functools import partial
from time import monotonic
from typing import Any
from uuid import uuid4

from homeassistant.components import websocket_api
from homeassistant.core import Context, HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError, Unauthorized

from .assistant_errors import AssistantError
from .assistant_policy import AssistantScope
from .assistant_views import AssistantView, numeric
from .compat import validation as vol
from .const import DOMAIN
from .ride_archive import ArchiveError, ArchiveFailure, RideArchive
from .services import assert_query_scope, check_user_history_access, iso
from .travel_statistics import energy_statistics

VIEWS = "ninebot_assistant_ride_views"
VIEW_LIFETIME = 600
MAX_VIEWS = 64
METRICS = {"energy_wh": "Wh", "energy_intensity_wh_per_km": "Wh/km"}


async def async_archive_ride_row(
    archive: RideArchive,
    sn: str,
    query_month: str | None,
    ride_id: str,
    *,
    device_id: str,
    vehicle_label: str,
    guard: Callable[[], bool],
) -> dict[str, Any] | None:
    """Project canonical local membership and scalar metrics, including summary-only rides."""
    if query_month is None:
        ride = await archive.async_current_detail_summary(sn, ride_id, guard=guard)
    else:
        ride = await archive.async_detail_summary(sn, ride_id, query_month, guard=guard)
    if not guard():
        raise ArchiveError(ArchiveFailure.OWNER)
    if ride is None:
        return None
    saved = await archive.async_saved_detail_ids(sn, (ride_id,))
    if not guard():
        raise ArchiveError(ArchiveFailure.OWNER)
    distance = ride.distance_m
    energy = energy_statistics(
        distance / 1000 if distance is not None else None, ride.energy_raw, basis="ride_metrics"
    )
    conflict = "duration_time_difference" in ride.issues
    return {
        "device_id": device_id,
        "vehicle_label": vehicle_label,
        "ride_id": ride_id,
        "query_month": ride.query_month,
        "source": ride.source.value,
        "start_time": iso(ride.started_at),
        "end_time": iso(ride.ended_at),
        "distance_m": distance,
        "duration_s": ride.duration_s,
        "max_speed_m_s": ride.server_max_speed_m_s,
        "average_speed_m_s": None if conflict else ride.average_speed_m_s,
        "energy_wh": energy["energy_wh"],
        "energy_intensity_wh_per_km": energy["energy_intensity_wh_per_km"],
        "detail_saved": ride_id in saved,
        "precision": {k: v for k, v in ride.precision if k in {"mileages", "ec"}},
        "warnings": ["duration_time_difference"] if conflict else [],
    }


@dataclass(frozen=True)
class RideView:
    user_id: str
    expires: float
    scopes: tuple[AssistantScope, ...]
    rides: tuple[dict[str, Any], ...]


async def async_check_ride_scopes(
    hass: HomeAssistant, context: Context, scopes: tuple[AssistantScope, ...], *, track: bool
) -> None:
    """One fresh user lookup, then validate every grant and registry entry without yielding."""
    user = await hass.auth.async_get_user(context.user_id) if context.user_id else None
    if user is None or not user.is_active or not user.is_admin:
        raise Unauthorized(context=context)
    for scope in scopes:
        scope.assert_current(hass)
        if context.user_id != scope.context.user_id or (
            track and not scope.runtime.coordinator.statistics.archive_ready
        ):
            raise Unauthorized(context=context)
        call = ServiceCall(
            hass, DOMAIN, "get_recorded_trips", {"device_id": scope.vehicle_ref}, context=context
        )
        check_user_history_access(hass, call, user, scope.entry.entry_id, include_track=track)
        assert_query_scope(
            hass, scope.vehicle_ref, scope.entry.entry_id, scope.sn, track, require_live=False
        )


def _current(scope: AssistantScope, hass: HomeAssistant) -> bool:
    scope.assert_current(hass)
    return True


def _store_view(hass: HomeAssistant, view: RideView) -> str:
    views: dict[str, RideView] = hass.data.setdefault(VIEWS, {})
    now = monotonic()
    for key in tuple(views):
        if views[key].expires <= now:
            del views[key]
    if len(views) >= MAX_VIEWS:
        del views[next(iter(views))]
    token = uuid4().hex
    views[token] = view
    return f"/ninebot-archive?view={token}"


async def async_validate_ride_view(
    hass: HomeAssistant, context: Context, token: str, devices: set[str]
) -> None:
    """Revalidate the original requester's exact map selection without touching the archive."""
    views = hass.data.get(VIEWS, {})
    view = views.get(token)
    if (
        view is None
        or context.user_id != view.user_id
        or view.expires <= monotonic()
        or devices != {scope.vehicle_ref for scope in view.scopes}
    ):
        raise Unauthorized(context=context)
    try:
        await async_check_ride_scopes(hass, context, view.scopes, track=True)
    except (AssistantError, HomeAssistantError) as err:
        views.pop(token, None)
        raise Unauthorized(context=context) from err
    if views.get(token) is not view or view.expires <= monotonic():
        raise Unauthorized(context=context)


@websocket_api.websocket_command(
    {
        vol.Required("type"): "ninebot/open_ride_view",
        vol.Required("view"): vol.Match("^[0-9a-f]{32}$"),
        vol.Optional("validate_only", default=False): bool,
    }
)
@websocket_api.async_response
async def async_open_ride_view(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, message: dict[str, Any]
) -> None:
    token = message["view"]
    view = hass.data.get(VIEWS, {}).get(token)
    try:
        if view is None:
            raise Unauthorized()
        await async_validate_ride_view(
            hass, connection.context(message), token, {scope.vehicle_ref for scope in view.scopes}
        )
    except Unauthorized:
        connection.send_error(message["id"], "unauthorized", "Ride view unavailable")
        return
    connection.send_result(
        message["id"],
        {"valid": True} if message.get("validate_only") else {"rides": list(view.rides)},
    )


async def async_compare_rides(
    hass: HomeAssistant,
    context: Context,
    scopes: dict[str, AssistantScope],
    labels: dict[str, str],
    selections: list[dict[str, str]],
) -> AssistantView:
    selected_scopes = tuple(scopes.values())
    rows = []
    try:
        await async_check_ride_scopes(hass, context, selected_scopes, track=False)
        for selection in selections:
            scope = scopes[selection["vehicle_ref"]]
            archive = scope.runtime.coordinator.statistics
            if not archive.archive_ready:
                raise AssistantError("local_data_missing")
            row = await async_archive_ride_row(
                archive.archive,
                scope.sn,
                selection["query_month"],
                selection["ride_id"],
                device_id=scope.vehicle_ref,
                vehicle_label=labels[scope.vehicle_ref],
                guard=partial(_current, scope, hass),
            )
            if row is None:
                raise AssistantError("local_data_missing")
            rows.append(row)
        await async_check_ride_scopes(hass, context, selected_scopes, track=False)
    except ArchiveError as err:
        if err.kind is ArchiveFailure.OWNER:
            raise AssistantError("not_authorized") from err
        raise AssistantError("retry_later") from err

    counts = {key: sum(row[key] is not None for row in rows) for key in METRICS}
    total = len(rows)
    energy = (
        numeric(sum(row["energy_wh"] for row in rows)) if counts["energy_wh"] == total else None
    )
    intensity = None
    if counts["energy_intensity_wh_per_km"] == total:
        distance_km = sum(row["distance_m"] for row in rows) / 1000
        intensity = energy_statistics(distance_km, energy, basis="selected_rides")[
            "energy_intensity_wh_per_km"
        ]
    lowest = {}
    for key, count in counts.items():
        minimum = min(row[key] for row in rows) if count == total else None
        lowest[key] = (
            [i for i, row in enumerate(rows, 1) if row[key] == minimum]
            if minimum is not None
            else []
        )
    link = None
    try:
        await async_check_ride_scopes(hass, context, selected_scopes, track=True)
    except (HomeAssistantError, AssistantError):
        # Scalar consent is independent of tracker access. Recheck it before disclosing results.
        await async_check_ride_scopes(hass, context, selected_scopes, track=False)
    else:
        assert context.user_id is not None
        link = _store_view(
            hass,
            RideView(
                context.user_id, monotonic() + VIEW_LIFETIME, selected_scopes, tuple(deepcopy(rows))
            ),
        )
    return AssistantView(
        data={
            "rides": rows,
            "units": METRICS,
            "lowest_ride_positions": lowest,
            "combined": {"energy_wh": energy, "energy_intensity_wh_per_km": intensity},
            "map_url": link,
        },
        quality={"selected": total, "known": counts},
        evidence={
            "source_kind": "account_archive",
            "selection_basis": "selected_recorded_rides",
            "energy_intensity_basis": "sum_energy_wh_divided_by_sum_distance_km",
        },
    )
