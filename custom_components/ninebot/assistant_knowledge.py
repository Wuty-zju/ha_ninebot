"""Small versioned public help corpus, independent of HA and private archives."""

import hashlib
import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

CATALOG = Path(__file__).with_name("assistant_knowledge") / "catalog.json"
MAX_CATALOG_BYTES = 128 * 1024
MAX_HELP_BYTES = 4 * 1024
SOURCE_PREFIX = "https://github.com/Wuty-zju/ha_ninebot/blob/"
STOP_WORDS = {"a", "an", "the", "is", "are", "why", "how", "my", "of", "to", "for", "and"}
READ_TOOLS = {
    "get_vehicles",
    "get_vehicle_status",
    "get_statistics",
    "get_recorded_trips",
    "get_trip_detail",
    "get_sync_health",
    "search_help",
}


class KnowledgeUnavailable(ValueError):
    """Public help is unavailable; other Assistant reads remain independent."""


def release_tuple(value: object) -> tuple[int, ...]:
    """Only the project's published numeric release contract is applicable."""
    if not isinstance(value, str) or not re.fullmatch(r"\d{1,4}\.\d{1,4}\.\d{1,4}", value):
        raise KnowledgeUnavailable("unsupported_knowledge_version")
    return tuple(int(part) for part in value.split("."))


def content_hash(entry: dict[str, Any]) -> str:
    body = {key: value for key, value in entry.items() if key != "content_sha256"}
    return hashlib.sha256(
        json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _text(value: object, maximum: int) -> bool:
    return isinstance(value, str) and 0 < len(value.encode()) <= maximum


def _valid_source(source: Any) -> bool:
    return (
        set(source) == {"path", "commit", "sha256", "url"}
        and bool(re.fullmatch(r"[0-9a-f]{40}", source["commit"]))
        and bool(re.fullmatch(r"[0-9a-f]{64}", source["sha256"]))
        and bool(re.fullmatch(r"docs/[a-z_]+\.md", source["path"]))
        and source["url"].startswith(f"{SOURCE_PREFIX}{source['commit']}/{source['path']}#")
        and bool(re.fullmatch(r"[a-z0-9-]+", source["url"].split("#")[-1]))
    )


def _bounds(versions: Any) -> tuple[tuple[int, ...], tuple[int, ...]]:
    if set(versions) != {"min_inclusive", "max_exclusive"}:
        raise ValueError
    low, high = (release_tuple(versions[key]) for key in ("min_inclusive", "max_exclusive"))
    if low >= high:
        raise ValueError
    return low, high


def validate_catalog(catalog: Any) -> dict[str, Any]:
    """Reject corrupted, unreviewed or ambiguous catalog metadata atomically."""
    try:
        if (
            not isinstance(catalog, dict)
            or set(catalog)
            != {
                "schema_version",
                "corpus_version",
                "source_version",
                "entries",
                "procedures",
            }
            or type(catalog["schema_version"]) is not int
            or catalog["schema_version"] != 2
            or not isinstance(catalog["entries"], list)
            or not 1 <= len(catalog["entries"]) <= 32
        ):
            raise ValueError
        release_tuple(catalog["corpus_version"])
        release_tuple(catalog["source_version"])
        if not isinstance(catalog["procedures"], list) or len(catalog["procedures"]) > 16:
            raise ValueError
        procedures = {}
        for procedure in catalog["procedures"]:
            if (
                set(procedure)
                != {
                    "id",
                    "source",
                    "applicable_versions",
                    "evidence_status",
                    "reviewed_at",
                    "allowed_tools",
                    "max_calls",
                    "translations",
                    "content_sha256",
                }
                or not re.fullmatch(r"SK0[1-8]", procedure["id"])
                or procedure["id"] in procedures
                or procedure["evidence_status"] != "reviewed_procedure"
                or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", procedure["reviewed_at"])
                or not _valid_source(procedure["source"])
                or content_hash(procedure) != procedure["content_sha256"]
                or type(procedure["max_calls"]) is not int
                or not 1 <= procedure["max_calls"] <= 4
                or not isinstance(procedure["allowed_tools"], list)
                or not procedure["allowed_tools"]
                or not set(procedure["allowed_tools"]) <= READ_TOOLS
                or len(set(procedure["allowed_tools"])) != len(procedure["allowed_tools"])
                or set(procedure["translations"]) != {"en", "zh-Hans"}
            ):
                raise ValueError
            _bounds(procedure["applicable_versions"])
            for translation in procedure["translations"].values():
                if (
                    set(translation)
                    != {"title", "trigger", "required_inputs", "steps", "stop", "verify"}
                    or any(
                        not _text(translation[key], 300)
                        for key in ("title", "trigger", "stop", "verify")
                    )
                    or any(
                        not isinstance(translation[key], list)
                        or not 1 <= len(translation[key]) <= 4
                        or any(not _text(text, 300) for text in translation[key])
                        for key in ("required_inputs", "steps")
                    )
                ):
                    raise ValueError
            procedures[procedure["id"]] = procedure
        identifiers = set()
        for entry in catalog["entries"]:
            if not isinstance(entry, dict) or set(entry) != {
                "id",
                "applicable_versions",
                "evidence_status",
                "reviewed_at",
                "supersedes",
                "source",
                "translations",
                "content_sha256",
                "procedure_id",
            }:
                raise ValueError
            identifier = entry["id"]
            if (
                not isinstance(identifier, str)
                or not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", identifier)
                or identifier in identifiers
                or entry["evidence_status"] != "first_party_contract"
                or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", entry["reviewed_at"])
                or not isinstance(entry["supersedes"], list)
                or len(entry["supersedes"]) > 16
                or any(not _text(old, 64) for old in entry["supersedes"])
                or content_hash(entry) != entry["content_sha256"]
            ):
                raise ValueError
            identifiers.add(identifier)
            low, high = _bounds(entry["applicable_versions"])
            procedure_id = entry["procedure_id"]
            if procedure_id is not None:
                if procedure_id not in procedures:
                    raise ValueError
                start, end = _bounds(procedures[procedure_id]["applicable_versions"])
                if not start <= low < high <= end:
                    raise ValueError
            if not _valid_source(entry["source"]):
                raise ValueError
            if set(entry["translations"]) != {"en", "zh-Hans"}:
                raise ValueError
            for translation in entry["translations"].values():
                if (
                    set(translation) != {"title", "text", "keywords"}
                    or not _text(translation["title"], 160)
                    or not _text(translation["text"], 1200)
                    or not isinstance(translation["keywords"], list)
                    or not 1 <= len(translation["keywords"]) <= 16
                    or any(not _text(word, 64) for word in translation["keywords"])
                ):
                    raise ValueError
        if any(entry["id"] in entry["supersedes"] for entry in catalog["entries"]):
            raise ValueError
    except (KeyError, TypeError, AttributeError, ValueError, RecursionError) as err:
        raise KnowledgeUnavailable("invalid_knowledge_catalog") from err
    return catalog


@lru_cache(maxsize=1)
def load_catalog() -> dict[str, Any]:
    """Lazy package-only read; callers run it in HA's executor, never at setup."""
    try:
        with CATALOG.open("rb") as stream:
            data = stream.read(MAX_CATALOG_BYTES + 1)
        if len(data) > MAX_CATALOG_BYTES:
            raise KnowledgeUnavailable("knowledge_catalog_too_large")
        return validate_catalog(json.loads(data))
    except (OSError, UnicodeError, ValueError, RecursionError) as err:
        raise KnowledgeUnavailable("knowledge_unavailable") from err


def terms(text: str) -> set[str]:
    """English/code tokens and Han bigrams; no model or index dependency."""
    result = set(re.findall(r"[a-z0-9_]+", text.casefold())) - STOP_WORDS
    for phrase in re.findall(r"[\u3400-\u9fff]+", text):
        result.update(phrase[index : index + 2] for index in range(len(phrase) - 1))
    return result


def search_help(query: str, language: str, installed_version: str) -> dict[str, Any]:
    """Retrieve at most three applicable evidence chunks in a 4 KiB body."""
    if not isinstance(query, str) or not 1 <= len(query.strip()) <= 240:
        raise KnowledgeUnavailable("invalid_help_query")
    catalog = load_catalog()
    version = release_tuple(installed_version)
    language = "zh-Hans" if language.casefold().startswith("zh") else "en"
    query_terms = terms(query)
    applicable = [
        entry
        for entry in catalog["entries"]
        if release_tuple(entry["applicable_versions"]["min_inclusive"])
        <= version
        < release_tuple(entry["applicable_versions"]["max_exclusive"])
    ]
    retired = {old for entry in applicable for old in entry["supersedes"]}
    ranked = []
    for entry in applicable:
        if entry["id"] in retired:
            continue
        texts = entry["translations"].values()
        keywords = terms(" ".join(word for text in texts for word in text["keywords"]))
        prose = terms(" ".join(text["title"] + " " + text["text"] for text in texts))
        score = 3 * len(query_terms & keywords) + len(query_terms & prose)
        if score:
            ranked.append((score, entry["id"], entry))
    ranked.sort(key=lambda row: (-row[0], row[1]))
    result: dict[str, Any] = {
        "corpus_version": catalog["corpus_version"],
        "corpus_sha256": hashlib.sha256(
            json.dumps(catalog, ensure_ascii=False, sort_keys=True).encode()
        ).hexdigest(),
        "retrieval_contract": "lexical-v1",
        "procedure_contract": "read-workflows-v1",
        "installed_version": installed_version,
        "language": language,
        "matches": [],
        "procedures": [],
        "match_status": "matched"
        if ranked
        else ("no_match" if applicable else "no_applicable_knowledge"),
        "more_available": False,
    }
    for _, _, entry in ranked[:3]:
        text = entry["translations"][language]
        match = {
            "source_id": entry["id"],
            "title": text["title"],
            "text": text["text"],
            "source": entry["source"],
            "content_sha256": entry["content_sha256"],
            "applicable_versions": entry["applicable_versions"],
            "evidence_status": entry["evidence_status"],
            "reviewed_at": entry["reviewed_at"],
            "procedure_id": entry["procedure_id"],
        }
        result["matches"].append(match)
        new_procedure = entry["procedure_id"] is not None and all(
            row["id"] != entry["procedure_id"] for row in result["procedures"]
        )
        if new_procedure:
            procedure = next(
                row for row in catalog["procedures"] if row["id"] == entry["procedure_id"]
            )
            result["procedures"].append(
                {
                    **{key: value for key, value in procedure.items() if key != "translations"},
                    **procedure["translations"][language],
                    "allowed_tools": [f"ninebot__{key}" for key in procedure["allowed_tools"]],
                    "advisory_only": True,
                }
            )
        if len(json.dumps(result, ensure_ascii=False).encode()) > MAX_HELP_BYTES:
            result["matches"].pop()
            if new_procedure:
                result["procedures"].pop()
            break
    result["more_available"] = len(ranked) > len(result["matches"])
    if ranked and not result["matches"]:
        result["match_status"] = "no_match_within_limit"
    return result
