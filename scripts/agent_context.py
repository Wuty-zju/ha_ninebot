"""Offline, bounded agent navigation and public-document integrity checks."""

import argparse
import hashlib
import json
import re
import subprocess
from pathlib import Path
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parents[1]
CATALOG = "docs/agent/catalog.json"
EVIDENCE_INDEX = "docs/agent/evidence-index.json"
LINK = re.compile(r"(?<!!)\[[^\]\n]*\]\((<[^>\n]+>|[^)\n]+)\)")


def repository_file(root: Path, value: str) -> Path:
    """Reject escapes including symlinks before reading any catalog target."""
    if not value or Path(value).is_absolute():
        raise ValueError("Repository-relative file required")
    path = (root / value).resolve()
    if not path.is_relative_to(root.resolve()) or not path.is_file():
        raise ValueError("Missing file or path outside repository")
    return path


def load_catalog(root: Path) -> dict:
    data = json.loads(repository_file(root, CATALOG).read_text())
    if data.get("schema_version") != 1:
        raise ValueError("Unsupported catalog schema")
    return data


def evidence_kind(name: str) -> str:
    if "validation" in name:
        return "validation_summary_not_a_new_test_run"
    if "live" in name or "production-readonly" in name:
        return "recorded_operation_summary_not_physical_completion"
    if "inventory" in name or "field" in name or "schema" in name:
        return "dated_field_or_schema_inventory"
    if name == "nineplus-source-review.json":
        return "source_review_and_offline_replay"
    return "review_or_protocol_evidence_check_original_scope"


def build_evidence_index(root: Path) -> dict:
    """Index only public files/metadata; never copy payload or arbitrary scope."""
    paths = sorted((root / "docs/evidence").rglob("*.json"))
    metadata = sorted((root / "tests/fixtures").rglob("metadata.json"))
    records = []
    for path in [*paths, *metadata]:
        relative = path.relative_to(root).as_posix()
        content = repository_file(root, relative).read_bytes()
        data = json.loads(content)
        item = {
            "path": relative,
            "kind": "fixture_provenance_manifest" if path in metadata else evidence_kind(path.name),
            "sha256": hashlib.sha256(content).hexdigest(),
            "bytes": len(content),
        }
        # Only accept date/version-shaped strings, never arbitrary source values.
        for key in ("date", "recorded_at_utc", "reviewed_at_utc"):
            value = data.get(key)
            if isinstance(value, str) and re.fullmatch(r"[0-9TtZz:+. \-]{10,40}", value):
                item["date"] = value
                break
        for key in ("version", "implementation_version", "product_version"):
            value = data.get(key)
            if isinstance(value, str) and re.fullmatch(r"[v0-9.abrc]{3,30}", value):
                item["version"] = value
                break
        records.append(item)
    return {
        "schema_version": 1,
        "scope": "Public evidence metadata only; no raw values",
        "records": records,
    }


def check(root: Path) -> list[str]:
    """Validate local references, coverage and reproducible evidence metadata."""
    errors = []
    catalog = load_catalog(root)
    documented = set()
    for row in catalog["documents"]:
        value = row["path"]
        try:
            repository_file(root, value)
        except ValueError:
            errors.append(f"Invalid document target: {value}")
        if value in documented:
            errors.append(f"Duplicate catalog target: {value}")
        documented.add(value)
        for replacement in row.get("overridden_by", []):
            try:
                repository_file(root, replacement)
            except ValueError:
                errors.append(f"Invalid override target: {replacement}")
    actual = {path.relative_to(root).as_posix() for path in (root / "docs").rglob("*.md")}
    for value in sorted(actual - documented):
        errors.append(f"Unclassified document: {value}")
    for row in catalog["documents"]:
        for replacement in row.get("overridden_by", []):
            if replacement not in documented:
                errors.append(f"Override document not classified: {replacement}")
    for name, topic in catalog["topics"].items():
        for group in ("docs", "code", "tests", "evidence"):
            for value in topic.get(group, []):
                try:
                    repository_file(root, value)
                except ValueError:
                    errors.append(f"Invalid {name}/{group} target: {value}")
        for value in topic.get("docs", []):
            if value not in documented:
                errors.append(f"Topic document not classified: {value}")
    for path in (root / "docs").rglob("*.md"):
        for match in LINK.finditer(path.read_text()):
            value = match.group(1).strip("<>")
            if "://" in value or value.startswith(("#", "mailto:")):
                continue
            value = re.sub(r":\d+$", "", unquote(value.partition("#")[0]))
            target = (path.parent / value).resolve()
            if not target.is_relative_to(root.resolve()) or not target.exists():
                line = path.read_text()[: match.start()].count("\n") + 1
                errors.append(f"Invalid public link: {path.relative_to(root)}:{line}")
    indexed = json.loads(repository_file(root, EVIDENCE_INDEX).read_text())
    if indexed != build_evidence_index(root):
        errors.append("Evidence index stale: run --refresh-index")
    return errors


def git_value(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=root, text=True, capture_output=True, timeout=5, check=False
    )
    return result.stdout.strip() if result.returncode == 0 else "unavailable"


def context(root: Path, topic_name: str | None) -> str:
    catalog = load_catalog(root)
    if topic_name is not None and topic_name not in catalog["topics"]:
        raise ValueError("Unknown topic; choose " + ", ".join(catalog["topics"]))
    manifest = json.loads(
        repository_file(root, "custom_components/ninebot/manifest.json").read_text()
    )
    status = git_value(root, "status", "--porcelain")
    lines = [
        "Offline agent context (paths only, no raw/private content)",
        "Branch: " + git_value(root, "branch", "--show-current"),
        "HEAD: " + git_value(root, "rev-parse", "--short", "HEAD"),
        "Dirty entries: " + str(len(status.splitlines()) if status else 0),
        "Integration: " + str(manifest["version"]),
        "Requirements: " + ", ".join(manifest["requirements"]),
        "First: docs/agent/START_HERE.md; docs/agent/CURRENT_STATE.md",
    ]
    if topic_name is None:
        lines.append("Topics: " + ", ".join(catalog["topics"]))
        return "\n".join(lines)
    topic = catalog["topics"][topic_name]
    lines.append("Focus: " + topic["focus"])
    for group in ("docs", "code", "tests", "evidence"):
        lines.append(group + ":")
        for value in topic.get(group, []):
            path = repository_file(root, value)
            lines.append(f"  {value} ({path.stat().st_size} bytes)")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--topic")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true")
    mode.add_argument("--refresh-index", action="store_true")
    args = parser.parse_args()
    if args.refresh_index:
        (ROOT / EVIDENCE_INDEX).write_text(
            json.dumps(build_evidence_index(ROOT), ensure_ascii=False, indent=2) + "\n"
        )
        print("Refreshed public evidence index; source evidence unchanged")
    elif args.check:
        errors = check(ROOT)
        if errors:
            print("\n".join(errors))
            raise SystemExit(1)
        print("Public catalog, local references and evidence index: passed")
    else:
        print(context(ROOT, args.topic))


if __name__ == "__main__":
    main()
