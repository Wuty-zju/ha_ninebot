"""Render bounded Ninebot answers from host-recorded native tool results."""

import json
import math
import re
from datetime import date, datetime
from html import escape
from typing import Any, TypeGuard

from .archive_timeline import SELECTION_BASIS
from .assistant_views import RIDE_UNITS, numeric
from .const import BUSINESS_TIMEZONE
from .travel import opaque_id

CONTRACT = "ninebot-evidence-answer-v1"
MAX_ANSWER_BYTES = 16 * 1024
MAX_RECEIPTS = 4
DENIED_TEXT = (
    "Access was denied. Check the selected vehicle reference and Home Assistant permissions. "
    "The result does not identify which check failed or establish what data exists."
)
ERROR_TEXT = {
    "auth_required": (
        "The local read reports that the account needs reauthentication in Home Assistant. "
        "The reason for the authentication failure is unverified."
    ),
    "local_data_missing": (
        "The requested data is unavailable or unverified locally. The result remains unknown."
    ),
    "retry_later": "The local read could not finish. Use any retry delay supplied in the result.",
    "timeout": "The local read timed out. The result remains unknown.",
    "invalid_arguments": "The supplied arguments do not match this tool's input.",
    "unsupported_capability": "The local operation or returned result shape is unsupported.",
    "response_too_large": "The result exceeded the response limit. Narrow the local query range.",
}
METRIC_LABELS = {
    "distance_km": ("Distance", "km"),
    "ride_count": ("Ride count", None),
    "duration_s": ("Duration", "s"),
    "energy_wh": ("Energy", "Wh"),
    "energy_intensity_wh_per_km": ("Energy intensity", "Wh/km"),
}
RIDE_LABELS = {
    "distance_m": "Distance",
    "duration_s": "Duration",
    "energy_wh": "Energy",
    "max_speed_m_s": "Maximum speed",
    "average_speed_m_s": "Average speed (distance/duration)",
    "energy_intensity_wh_per_km": "Energy intensity",
}
STATUS = frozenset(("available", "missing", "partial", "stale"))
PINNED_GUIDE = re.compile(
    r"https://github\.com/Wuty-zju/ha_ninebot/blob/[a-f0-9]{40}/docs/[a-z_]+\.md#[a-z0-9-]+"
)
VERSION = re.compile(r"[0-9]{1,4}\.[0-9]{1,4}\.[0-9]{1,4}")
CODE = re.compile(r"[a-z][a-z0-9_]{0,47}")
REQUEST_ID = re.compile(r"[0-9a-f]{32}")
QUERY_MONTH = re.compile(r"20[0-9]{2}(0[1-9]|1[0-2])")


def _object(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _matches(pattern: re.Pattern[str], value: Any) -> TypeGuard[str]:
    return isinstance(value, str) and pattern.fullmatch(value) is not None


def _prose(value: str) -> str:
    return re.sub(r"([\\`*_\[\]])", r"\\\1", escape(value, quote=False))


def _help(payload: dict[str, Any]) -> str:
    data = _object(payload.get("data"))
    corpus, installed = data.get("corpus_version"), data.get("installed_version")
    matches = data.get("matches")
    if (
        _object(payload.get("evidence")).get("source_kind") != "bundled_public_help"
        or not _matches(VERSION, corpus)
        or not _matches(VERSION, installed)
        or data.get("language") not in ("en", "zh-Hans")
        or not isinstance(matches, list)
        or not 1 <= len(matches) <= 3
        or data.get("match_status") != "matched"
    ):
        return ""
    topics = []
    for match in matches:
        match = _object(match)
        title, text = match.get("title"), match.get("text")
        url = _object(match.get("source")).get("url")
        citation = match.get("citation")
        if (
            not isinstance(title, str)
            or not 1 <= len(title.encode()) <= 160
            or not isinstance(text, str)
            or not 1 <= len(text.encode()) <= 1200
            or not _matches(PINNED_GUIDE, url)
            or citation != f"[Source]({url}) (KB {corpus})"
        ):
            return ""
        topics.append(f"**{_prose(title)}**\n\n{_prose(text)} {citation}")
    intro = (
        f"Ninebot {installed} 公开帮助。车辆状况须结合该车的观测证据。"
        if data["language"] == "zh-Hans"
        else f"Public help for Ninebot {installed}. A vehicle diagnosis requires its own evidence."
    )
    return "\n\n".join((intro, *topics))


def _date(value: Any) -> date | None:
    if isinstance(value, str) and len(value) == 10:
        try:
            parsed = date.fromisoformat(value)
        except ValueError:
            return None
        if parsed.isoformat() == value:
            return parsed
    return None


def _observation(value: Any) -> str:
    if isinstance(value, str) and len(value) <= 64:
        try:
            stamp = datetime.fromisoformat(value)
        except ValueError:
            return "unrecorded"
        if stamp.tzinfo is not None:
            return stamp.isoformat()
    return "unrecorded"


def _statistics(payload: dict[str, Any], selected_metric: str | None = None) -> str:
    query = _object(payload.get("query"))
    data, quality = _object(payload.get("data")), _object(payload.get("quality"))
    evidence = _object(payload.get("evidence"))
    start, end = _date(query.get("start_date")), _date(query.get("end_date"))
    source, coverage = evidence.get("source_kind"), evidence.get("coverage_basis")
    if (
        start is None
        or end is None
        or start > end
        or query.get("business_timezone") != "Asia/Shanghai"
        or (source, coverage)
        not in (
            ("account_archive", "business_day_projection"),
            ("server_month_summary", "server_month_summary"),
        )
    ):
        return ""
    label = f"{start}" if start == end else f"{start} through {end}"
    lines = [f"**Local statistics for {label} (Asia/Shanghai)**"]
    metrics = [selected_metric] if selected_metric is not None else list(METRIC_LABELS)
    complete_period = quality.get("period_closed") is True and (
        quality.get("ride_window_complete") is True
        if source == "account_archive"
        else quality.get("observed_after_period_end") is True
    )
    qualified_metric = False
    for metric in metrics:
        name, unit = METRIC_LABELS[metric]
        measured = _object(_object(quality.get("metrics")).get(metric))
        value, status, reason = data.get(metric), measured.get("status"), measured.get("reason")
        if (
            not isinstance(status, str)
            or status not in STATUS
            or not _matches(CODE, reason)
            or metric not in data
            or _object(data.get("units")).get(metric, "") != unit
            or (value is None) != (status == "missing")
            or (value is not None and type(value) not in (int, float))
            or (type(value) is float and not math.isfinite(value))
        ):
            return ""
        if value is None:
            displayed = "unknown"
        else:
            displayed = f"{value} {unit}" if unit else str(value)
        lines.append(f"- {name}: {displayed} ({status}, recorded reason `{reason}`).")
        qualified_metric |= status != "available"
    lines.append(f"- Local source `{source}` with coverage basis `{coverage}`.")
    lines.append(f"- Observation time: {_observation(quality.get('received_at'))}.")
    if (basis := quality.get("completeness_basis")) in (
        "upstream_complete",
        "local_reconciled",
        "incomplete",
        "unknown",
        "server_month_summary",
    ):
        lines.append(f"- Reported completeness basis `{basis}`.")
    if not complete_period:
        lines.append("- Reported coverage leaves the complete period total unverified.")
    if not complete_period or qualified_metric:
        lines.append("- The cause of any data gap remains unverified.")
    return lines[0] + "\n\n" + "\n".join(lines[1:])


def _local_archive(evidence: dict[str, Any]) -> bool:
    return (
        evidence.get("source_kind") == "account_archive"
        and evidence.get("access_mode") == "local_only"
        and evidence.get("adapter_contract") == "ninebot-assistant-v1"
    )


def _ride_fields(ride: dict[str, Any]) -> str:
    identifier, month = ride.get("ride_id"), ride.get("query_month")
    if (
        not isinstance(identifier, str)
        or opaque_id(identifier) != identifier
        or not _matches(QUERY_MONTH, month)
    ):
        return ""
    fields = [
        f"Started: {_observation(ride.get('started_at'))}",
        f"Ended: {_observation(ride.get('ended_at'))}",
        f"Archive query month: {month}",
    ]
    for key, label in RIDE_LABELS.items():
        if key not in ride:
            return ""
        value = ride[key]
        if value is not None and (numeric(value) is None or value < 0):
            return ""
        fields.append(
            f"{label}: unknown" if value is None else f"{label}: {value} {RIDE_UNITS[key]}"
        )
    return " · ".join(fields)


def _month_observations(quality: dict[str, Any], start: date, end: date) -> list[str] | None:
    months = quality.get("months")
    if not isinstance(months, list) or len(months) > 3:
        return None
    lines, seen = [], set()
    for row in months:
        row = _object(row)
        month, complete = row.get("month"), row.get("observed_list_complete")
        if (
            not _matches(QUERY_MONTH, month)
            or not start.strftime("%Y%m") <= month <= end.strftime("%Y%m")
            or month in seen
            or (complete is not None and type(complete) is not bool)
        ):
            return None
        seen.add(month)
        coverage = (
            "complete" if complete is True else "incomplete" if complete is False else "unknown"
        )
        observed = _observation(row.get("received_at"))
        lines.append(f"- {month} list: {coverage}. Archive observation: {observed}.")
    return lines


def _recorded_trips(payload: dict[str, Any]) -> str:
    query, data = _object(payload.get("query")), _object(payload.get("data"))
    quality, evidence = _object(payload.get("quality")), _object(payload.get("evidence"))
    start, end = _date(query.get("start_date")), _date(query.get("end_date"))
    rides, returned, limit = data.get("rides"), data.get("returned"), query.get("limit")
    cursor, has_more = data.get("next_cursor"), data.get("has_more")
    if (
        not _local_archive(evidence)
        or evidence.get("selection_basis") != SELECTION_BASIS
        or evidence.get("sort") != "desc"
        or query.get("sort") != "desc"
        or query.get("business_timezone") != BUSINESS_TIMEZONE
        or start is None
        or end is None
        or not (2000 <= start.year <= end.year <= 2099 and 0 <= (end - start).days < 31)
        or ("cursor" in query and not _matches(REQUEST_ID, query["cursor"]))
        or not isinstance(rides, list)
        or type(limit) is not int
        or not 1 <= limit <= 20
        or type(returned) is not int
        or returned != len(rides)
        or returned > limit
        or (cursor is not None and not _matches(REQUEST_ID, cursor))
        or has_more is not (cursor is not None)
        or data.get("units") != RIDE_UNITS
        or quality.get("upstream_history_complete") != "unverified"
    ):
        return ""
    if (months := _month_observations(quality, start, end)) is None:
        return ""
    missing = quality.get("missing_month_count")
    if missing is not None and (type(missing) is not int or not 0 <= missing <= 3):
        return ""
    page = "Continuation page" if "cursor" in query else "First page"
    remaining = (
        "Another local page is available." if has_more else "No further local page is indicated."
    )
    lines = [
        f"**Recorded trips for {start} through {end} ({BUSINESS_TIMEZONE})**\n",
        f"{page}: {returned} returned (limit {limit}). {remaining} "
        "This page count leaves the period total unverified.",
        "- Source: local account archive, latest lists and retained partial observations. "
        "Cloud completeness is unverified.",
        f"- Page selection cutoff: {_observation(quality.get('page_as_of'))}. "
        f"Tool query time: {_observation(query.get('as_of'))}.",
        f"- Months without a recorded list: {'unknown' if missing is None else missing}.",
        *months,
    ]
    for row in rides:
        ride = _object(row)
        fields, saved = _ride_fields(ride), ride.get("detail_saved")
        if not fields or (saved is not None and type(saved) is not bool):
            return ""
        detail = "saved" if saved is True else "not saved" if saved is False else "unknown"
        lines.append(
            f"- {fields} · Detail: {detail} · Archive observation: "
            f"{_observation(ride.get('received_at'))}."
        )
    return "\n".join(lines)


def _saved_detail(payload: dict[str, Any]) -> str:
    query, ride = _object(payload.get("query")), _object(payload.get("data"))
    quality, evidence = _object(payload.get("quality")), _object(payload.get("evidence"))
    if (
        not _local_archive(evidence)
        or query.get("business_timezone") != BUSINESS_TIMEZONE
        or query.get("ride_id") != ride.get("ride_id")
        or query.get("query_month") != ride.get("query_month")
        or ride.get("detail_saved") is not True
        or ride.get("units") != RIDE_UNITS
        or not (fields := _ride_fields(ride))
    ):
        return ""
    partial = quality.get("partial_payload")
    integrity = (
        "partial"
        if partial is True
        else "no partial payload reported"
        if partial is False
        else "unknown"
    )
    return (
        f"**Saved trip detail ({BUSINESS_TIMEZONE} business dates)**\n\n"
        f"{fields}\n\n"
        "- Source: local account archive.\n"
        f"- Archive observation: {_observation(quality.get('received_at'))}.\n"
        f"- Saved payload integrity: {integrity}."
    )


def _error(row: dict[str, Any]) -> Any:
    return (
        _object(_object(row.get("result")).get("error")).get("code")
        if row.get("error") is True
        else None
    )


def _workflow(payload: dict[str, Any]) -> str:
    data = _object(payload.get("data"))
    steps, metric = data.get("steps"), data.get("metric")
    if (
        _object(payload.get("evidence")).get("source_kind") != "local_statistics_check"
        or not isinstance(steps, list)
        or not 1 <= len(steps) <= 3
        or not isinstance(metric, str)
        or metric not in METRIC_LABELS
    ):
        return ""
    sections = []
    for step in steps:
        step = _object(step)
        result = _object(step.get("result"))
        tool = step.get("tool")
        if isinstance(code := _error(step), str) and code in ERROR_TEXT:
            sections.append(ERROR_TEXT[code])
        elif step.get("error") is False and tool == "ninebot__get_statistics":
            sections.append(_statistics(result, metric))
        elif step.get("error") is False and tool == "ninebot__search_help":
            sections.append(_help(result))
        elif step.get("error") is False and tool == "ninebot__get_sync_health":
            state = _object(result.get("data")).get("state")
            if _matches(CODE, state):
                sections.append(f"Local synchronization reported the state `{state}`.")
    if _object(payload.get("quality")).get("partial") is True:
        sections.append("The local statistics check returned incomplete evidence.")
    if sections:
        sections.append("These observations leave the underlying cause unverified.")
    return "\n\n".join(filter(None, sections))


def _answer(text: str, request_ids: list[str]) -> dict[str, Any]:
    return {"contract": CONTRACT, "text": text, "request_ids": request_ids}


def render_evidence_answer(
    results: list[dict[str, Any]], *, denied: bool = False
) -> dict[str, Any] | None:
    """Render a host's authorized read receipts. Model-supplied rows cannot establish provenance."""
    if denied:
        return _answer(DENIED_TEXT, [])
    if len(results) > MAX_RECEIPTS:
        return None
    sections, request_ids = [], []
    for row in results:
        payload = _object(row.get("result"))
        tool = row.get("tool")
        code = _error(row)
        if code == "not_authorized":
            return _answer(DENIED_TEXT, [])
        request_id = payload.get("request_id")
        if type(payload.get("schema_version")) is not int or payload["schema_version"] != 1:
            continue
        if not isinstance(request_id, str) or REQUEST_ID.fullmatch(request_id) is None:
            continue
        if isinstance(code, str):
            text = ERROR_TEXT.get(code, "")
        elif row.get("error") is not False or "error" in payload:
            continue
        elif tool == "ninebot__search_help":
            text = _help(payload)
        elif tool == "ninebot__get_statistics":
            text = _statistics(payload)
        elif tool == "ninebot__get_recorded_trips":
            text = _recorded_trips(payload)
        elif tool == "ninebot__get_trip_detail":
            text = _saved_detail(payload)
        elif tool == "ninebot__explain_statistics":
            text = _workflow(payload)
        else:
            continue
        if text:
            sections.append(text)
            request_ids.append(request_id)
    if not sections:
        return None
    answer = _answer("\n\n".join(sections), request_ids)
    return (
        answer if len(json.dumps(answer, ensure_ascii=False).encode()) <= MAX_ANSWER_BYTES else None
    )
