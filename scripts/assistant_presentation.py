"""Deterministic display of bounded local Ninebot receipts, independent of HA/models."""

import html
import json
import math
import re
from datetime import date, datetime

SUPPORTED_TOOLS = frozenset(
    f"ninebot__{name}" for name in ("get_statistics", "search_help", "get_sync_health")
)
LIMIT = 16 * 1024
ERRORS = frozenset(
    "not_authorized invalid_arguments local_data_missing retry_later timeout "
    "auth_required unsupported_capability response_too_large".split()
)
METRICS = {
    "distance_km": ("里程", "Distance", "km"),
    "ride_count": ("行程次数", "Ride count", None),
    "duration_s": ("总时长", "Total duration", "s"),
    "energy_wh": ("耗电", "Energy", "Wh"),
    "energy_intensity_wh_per_km": ("平均能耗", "Energy intensity", "Wh/km"),
}
WORDS = frozenset(
    "available missing stale partial unknown not_reported missing_month "
    "day_not_observed missing_daily_chart incomplete_month_list missing_metric zero_distance "
    "adjacent_month_not_observed unknown_reason upstream_complete local_reconciled incomplete "
    "server_month_summary business_day_projection account_archive server_daily_chart "
    "returned_unique_rides whole_ride_end_business_date server_month unverified "
    "not_started disabled paused discovering preserving verifying incremental waiting_budget "
    "waiting_retry waiting_connection needs_login storage_full storage_error complete with_gaps "
    "reconciled idle querying discover preserve verify synced".split()
)


class PresentationError(ValueError):
    """Fixed failure kind; never include untrusted payloads or exception details."""


def require(condition):
    if not condition:
        raise PresentationError("unsupported_receipt")


def matched(value, pattern):
    require(isinstance(value, str) and re.fullmatch(pattern, value))
    return value


def word(value):
    require(isinstance(value, str) and value in WORDS)
    return value


def stamp(value):
    if value is None:
        return None
    require(isinstance(value, str) and len(value) <= 64)
    try:
        require(datetime.fromisoformat(value).tzinfo is not None)
    except ValueError:
        raise PresentationError("unsupported_receipt") from None
    return value


def scalar(value):
    require(value is None or type(value) in (bool, int, float))
    if type(value) in (int, float):
        try:
            require(math.isfinite(value))
        except OverflowError:
            raise PresentationError("unsupported_receipt") from None
    return json.dumps(value, allow_nan=False)


def prose(value):
    require(isinstance(value, str) and 0 < len(value.encode()) <= 1200)
    require(all(char.isprintable() or char == "\n" for char in value))
    require(not re.search(r"https?://|www\.", value, re.I))
    return html.escape(value).replace("[", "\\[").replace("]", "\\]")


def source(value):
    require(isinstance(value, dict))
    url, commit, path = value.get("url"), value.get("commit"), value.get("path")
    matched(commit, r"[0-9a-f]{40}")
    matched(
        path,
        r"(?:docs/[A-Za-z0-9_/-]+\.md|(?:custom_components/ninebot|scripts)/"
        r"[A-Za-z0-9_/-]+\.(?:py|js|json))",
    )
    return matched(
        url,
        re.escape(f"https://github.com/Wuty-zju/ha_ninebot/blob/{commit}/{path}")
        + r"(?:#[A-Za-z0-9_-]+)?",
    )


def version(value):
    return matched(value, r"\d{1,4}\.\d{1,4}\.\d{1,4}")


def statistics(payload, args, zh):
    query, data, quality, evidence = (payload[key] for key in "query data quality evidence".split())
    require(
        query.get("period")
        == args.get("period")
        in {"today", "yesterday", "this_month", "last_month"}
    )
    require(query.get("business_timezone") == "Asia/Shanghai")
    for key in ("start_date", "end_date"):
        require(isinstance(query.get(key), str))
        try:
            require(date.fromisoformat(query[key]).isoformat() == query[key])
        except ValueError:
            raise PresentationError("unsupported_receipt") from None
    require(query["start_date"] <= query["end_date"])
    lines = [f"{query['start_date']} — {query['end_date']} (Asia/Shanghai)"]
    require(isinstance(quality.get("metrics"), dict) and isinstance(data.get("units"), dict))
    for key, (cn, en, unit) in METRICS.items():
        require(key in data and data["units"].get(key, "absent") == unit)
        require(data[key] is None or type(data[key]) in (int, float))
        metric = quality["metrics"].get(key)
        require(isinstance(metric, dict))
        require(metric.get("status") in {"available", "missing", "stale", "partial"})
        require((data[key] is None) == (metric["status"] == "missing"))
        lines.append(
            f"{cn if zh else en}: {scalar(data[key])} {unit or ''} "
            f"[status={metric['status']}; reason={word(metric.get('reason'))}]"
        )
    for key in ("period_closed", "ride_window_complete", "observed_after_period_end"):
        require(key in quality and (quality[key] is None or type(quality[key]) is bool))
        lines.append(f"{key}: {scalar(quality[key])}")
    lines.append(f"completeness_basis: {word(quality.get('completeness_basis'))}")
    for key in ("received_at", "adjacent_received_at"):
        require(key in quality)
        lines.append(f"{key}: {stamp(quality[key]) or ('未记录' if zh else 'not recorded')}")
    for key in (
        "source_kind coverage_basis distance_basis ride_date_assignment energy_intensity_basis"
    ).split():
        lines.append(f"{key}: {word(evidence.get(key))}")
    lines.append(
        "available仅表示指标可用，不证明完整总量。"
        if zh
        else "available means the metric is available, not proof of a complete total."
    )
    if any(data[key] is None for key in METRICS):
        lines.append(
            "null表示缺少数据，不能当作0。" if zh else "null means missing data, not zero."
        )
    return lines, True


def help_text(payload, args, zh):
    data = payload["data"]
    require(payload["evidence"].get("source_kind") == "bundled_public_help")
    corpus, installed = version(data.get("corpus_version")), version(data.get("installed_version"))
    require(data.get("retrieval_contract") == "lexical-v1")
    require(data.get("procedure_contract") == "read-workflows-v1")
    matches, procedures = data.get("matches"), data.get("procedures")
    require(isinstance(matches, list) and len(matches) <= 3 and isinstance(procedures, list))
    require(type(args.get("include_procedures", False)) is bool)
    require(not procedures or args.get("include_procedures") is True)
    require(len(procedures) <= 3)
    require(len(json.dumps(data, ensure_ascii=False).encode()) <= 4096)
    require(
        data.get("match_status")
        in {"matched", "no_match", "no_applicable_knowledge", "no_match_within_limit"}
    )
    require(bool(matches) == (data["match_status"] == "matched"))
    lines = [f"KB {corpus}; installed_version={installed}; lexical-v1; read-workflows-v1"]
    for match in matches:
        require(isinstance(match, dict) and isinstance(match.get("applicable_versions"), dict))
        bounds = match["applicable_versions"]
        low, high = version(bounds.get("min_inclusive")), version(bounds.get("max_exclusive"))
        require(
            tuple(map(int, low.split(".")))
            <= tuple(map(int, installed.split(".")))
            < tuple(map(int, high.split(".")))
        )
        lines.extend(
            [
                prose(match.get("text")),
                f"[Source]({source(match.get('source'))}) (KB {corpus})",
                f"applicable_versions: [{low}, {high})",
            ]
        )
    procedure_ids = set()
    for procedure in procedures:
        require(isinstance(procedure, dict) and procedure.get("advisory_only") is True)
        matched(procedure.get("id"), r"SK(?:0[1-9]|10)")
        require(
            procedure["id"] not in procedure_ids
            and any(m.get("procedure_id") == procedure["id"] for m in matches)
        )
        procedure_ids.add(procedure["id"])
        lines.append(
            f"{procedure['id']}: "
            + ("辅助清单，不执行操作。" if zh else "Advisory checklist; no operation executed.")
        )
        for key in ("trigger", "stop", "verify"):
            lines.append(f"{key}: {prose(procedure.get(key))}")
        for key in ("required_inputs", "steps"):
            values = procedure.get(key)
            require(isinstance(values, list) and len(values) <= 8)
            lines.extend(f"{key}: {prose(value)}" for value in values)
        tools = procedure.get("allowed_tools")
        require(isinstance(tools, list) and 1 <= len(tools) <= 8)
        require(
            all(
                isinstance(tool, str) and re.fullmatch(r"ninebot__[a-z_]{1,64}", tool)
                for tool in tools
            )
        )
        require(type(procedure.get("max_calls")) is int and 1 <= procedure["max_calls"] <= 4)
        lines.append(f"advisory_tools: {', '.join(tools)}; max_calls: {procedure['max_calls']}")
    if not matches:
        lines.append("没有适用的帮助证据。" if zh else "No applicable help evidence.")
    return lines, bool(matches)


def sync_health(payload, args, zh):
    data, quality = payload["data"], payload["quality"]
    lines = [f"state: {word(data.get('state'))}; phase: {word(data.get('phase'))}"]
    for key in ("next_request_at", "retry_at", "last_success", "last_completed"):
        require(key in data)
        lines.append(f"{key}: {stamp(data[key]) or ('未记录' if zh else 'not recorded')}")
    for key in (
        "scanned_months total_months verified_months gap_months scan_progress "
        "detail_progress discovery_complete paused background_enabled "
        "automatic_sync_enabled"
    ).split():
        require(key in data)
        flags = {"discovery_complete", "paused", "background_enabled", "automatic_sync_enabled"}
        allowed = (bool,) if key in flags else (int, float)
        require(data[key] is None or type(data[key]) in allowed)
        lines.append(f"{key}: {scalar(data[key])}")
    policy = data.get("request_policy")
    require(isinstance(policy, dict) and policy.get("basis") == "paced_account_requests")
    for key in ("configured_requests_per_hour", "minimum_interval_s"):
        require(key in policy)
        require(policy[key] is None or type(policy[key]) in (int, float))
        lines.append(f"{key}: {scalar(policy[key])}")
    require(
        quality.get("upstream_history_complete") == "unverified"
        and payload["evidence"].get("source_kind") == "account_archive"
    )
    lines.extend(
        [
            "budget_basis: paced_account_requests",
            f"upstream_history_complete: {word(quality.get('upstream_history_complete'))}",
            f"source_kind: {word(payload['evidence'].get('source_kind'))}",
            "等待额度是本地调度，不代表云端授权或未知数据的原因。"
            if zh
            else "Budget wait alone establishes neither cloud permission nor missing-data causes.",
        ]
    )
    return lines, True


def render_answer(receipts: list[dict], language: str = "zh-Hans") -> dict:
    """Display actual receipts only; callers must supply their own trusted call ledger."""
    require(
        isinstance(receipts, list)
        and len(receipts) <= 4
        and isinstance(language, str)
        and language in {"zh-Hans", "en"}
    )
    zh, checked = language == "zh-Hans", []
    try:
        for receipt in receipts:
            require(isinstance(receipt, dict) and set(receipt) == {"name", "arguments", "result"})
            require(receipt["name"] in SUPPORTED_TOOLS and isinstance(receipt["arguments"], dict))
            require(len(json.dumps(receipt, ensure_ascii=False, allow_nan=False).encode()) <= LIMIT)
            payload = receipt["result"]
            require(
                isinstance(payload, dict)
                and type(payload.get("schema_version")) is int
                and payload["schema_version"] == 1
            )
            matched(payload.get("request_id"), r"[0-9a-f]{32}")
            if "error" in payload:
                require(
                    isinstance(payload["error"], dict) and payload["error"].get("code") in ERRORS
                )
            else:
                for key in ("query", "data", "quality", "evidence"):
                    require(isinstance(payload.get(key), dict))
                require(isinstance(payload.get("warnings"), list))
                require(
                    payload["evidence"].get("adapter_contract") == "ninebot-assistant-v1"
                    and payload["evidence"].get("access_mode") == "local_only"
                )
                if receipt["name"] != "ninebot__search_help":
                    ref = receipt["arguments"].get("vehicle_ref")
                    matched(ref, r"[A-Za-z0-9_-]{1,128}")
                    require(ref == payload.get("vehicle_ref"))
            checked.append(receipt)
        denied = any(r["result"].get("error", {}).get("code") == "not_authorized" for r in checked)
        lines, ids, evidence = [], [], False
        if denied:
            lines = [
                "访问被拒绝；本轮结果已撤回。请检查Home Assistant权限。"
                if zh
                else "Access denied; results withdrawn. Check Home Assistant permissions."
            ]
        for receipt in [] if denied else checked:
            payload, name, args = receipt["result"], receipt["name"], receipt["arguments"]
            if "error" in payload:
                prefix = "读取失败：" if zh else "Read failed: "
                lines.append(f"{prefix}{name}: {payload['error']['code']}")
                continue
            body, supported = {
                "ninebot__get_statistics": statistics,
                "ninebot__search_help": help_text,
                "ninebot__get_sync_health": sync_health,
            }[name](payload, args, zh)
            lines.extend([name, *body])
            if "vehicle_ref" in args:
                lines.append(f"vehicle_ref: {args['vehicle_ref']}")
            if supported:
                ids.append(payload["request_id"])
                evidence = True
        if not checked:
            lines = [
                "缺少工具证据；请明确要查询的问题、车辆或日期。"
                if zh
                else "No tool evidence; specify the question, vehicle or dates needed."
            ]
        text = "\n".join(lines)
        answer = {
            "text": text,
            "outcome": "denied"
            if denied
            else "evidence"
            if evidence
            else "unavailable"
            if checked
            else "needs_input",
            "receipt_count": len(checked),
            "evidence_request_ids": ids,
        }
        require(len(json.dumps(answer, ensure_ascii=False).encode()) <= LIMIT)
        return answer
    except (TypeError, KeyError, ValueError, RecursionError):
        raise PresentationError("unsupported_receipt") from None
