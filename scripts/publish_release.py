"""Explicit, draft-first publication from the maintained main workflow.

Candidate code is read as Git objects, never executed. Mutations are single-shot;
an interrupted run can resume a matching draft without replacing any asset.
"""

import argparse
import hashlib
import json
import os
import re
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from scripts.build_release import FILENAME, build_package, git_bytes
from scripts.check_release import validate_request
from scripts.ci_evidence import verify_full_run

REQUIRED_CHECKS = {"Core lint", "Release policy", "docs", "HACS validation", "Hassfest validation"}
APP_ID = 15368  # GitHub Actions, also required by the repository branch protections.


class GitHub:
    def __init__(self, repository: str, token: str) -> None:
        if not re.fullmatch(r"[\w.-]+/[\w.-]+", repository) or not token:
            raise ValueError("A repository and authenticated GitHub token are required")
        self.repository = repository
        self.token = token

    def request(
        self,
        path: str,
        *,
        method: str = "GET",
        payload: dict | None = None,
        data: bytes | None = None,
        content_type: str = "application/json",
    ):
        host = "uploads.github.com" if data is not None else "api.github.com"
        request = Request(
            f"https://{host}/repos/{self.repository}/{path}",
            data=data if data is not None else json.dumps(payload).encode() if payload else None,
            method=method,
            headers={
                "Authorization": f"Bearer {self.token}",
                "Accept": "application/vnd.github+json",
                "Content-Type": content_type,
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
        attempts = 3 if method == "GET" else 1
        for attempt in range(attempts):
            try:
                with urlopen(request, timeout=45) as response:
                    return json.load(response)
            except HTTPError as error:
                if method == "GET" and error.code == 404:
                    return None
                reason = f"HTTP {error.code}"
                retry = error.code >= 500
            except (URLError, TimeoutError):
                reason = "network timeout or connection failure"
                retry = True
            if not retry or attempt + 1 == attempts:
                raise RuntimeError(
                    f"GitHub {method} {path.split('?', 1)[0]} failed ({reason}); "
                    "inspect or resume the draft"
                )
            time.sleep(attempt + 1)
        raise RuntimeError("Unreachable GitHub request state")


def releases(api: GitHub) -> list[dict]:
    result = []
    for page in range(1, 21):
        rows = api.request(f"releases?per_page=100&page={page}")
        if not isinstance(rows, list):
            raise ValueError("Cannot read the release catalog")
        result.extend(rows)
        if len(rows) < 100:
            return result
    raise ValueError("Release catalog exceeds the reviewed pagination bound")


def version_key(tag: str) -> tuple | None:
    match = re.fullmatch(r"v(\d+)\.(\d+)(?:\.(\d+))?(?:(a|b|rc)(\d+))?", tag)
    if not match:
        return None
    return (
        int(match[1]),
        int(match[2]),
        int(match[3] or 0),
        {"a": 0, "b": 1, "rc": 2, None: 3}[match[4]],
        int(match[5] or 0),
    )


def release_body(
    repository: str,
    tag: str,
    channel: str,
    catalog: list[dict],
    english: str,
    chinese: str,
    upgrade_en: str = "",
    upgrade_zh: str = "",
) -> str:
    if (
        not english.strip()
        or not chinese.strip()
        or bool(upgrade_en.strip()) != bool(upgrade_zh.strip())
    ):
        raise ValueError("Provide both language versions of changes and any upgrade instructions")
    if re.search(r"[\u3400-\u9fff]", english + upgrade_en):
        raise ValueError("Keep Chinese text inside the translated section")
    all_text = english + chinese + upgrade_en + upgrade_zh
    if len(all_text) > 20000 or any(
        t in all_text for t in ("/Users/", "/tmp/", "## Compatibility")
    ):
        raise ValueError("Release notes contain private paths or unsupported sections")
    current = version_key(tag)
    older = [
        (version_key(r["tag_name"]), r["tag_name"])
        for r in catalog
        if not r["draft"] and (channel in {"preview", "candidate"} or not r["prerelease"])
    ]
    previous = max(
        (pair for pair in older if pair[0] is not None and pair[0] < current), default=None
    )
    body = "## What's Changed\n\n" + english.strip()
    if upgrade_en.strip():
        body += "\n\n## Upgrade Notes\n\n" + upgrade_en.strip()
    body += "\n\n<details>\n<summary>简体中文</summary>\n\n### 更新内容\n\n" + chinese.strip()
    if upgrade_zh.strip():
        body += "\n\n### 升级说明\n\n" + upgrade_zh.strip()
    body += "\n\n</details>\n"
    if previous:
        comparison = f"{previous[1]}...{tag}"
        body += f"\n**Full Changelog**: [{comparison}](https://github.com/{repository}/compare/{comparison})\n"
    return body


def tag_commit(api: GitHub, tag: str) -> str | None:
    ref = api.request(f"git/ref/tags/{quote(tag, safe='')}")
    if ref is None:
        return None
    obj = ref["object"]
    for _ in range(4):
        if obj["type"] == "commit":
            return obj["sha"]
        if obj["type"] != "tag":
            break
        obj = api.request(f"git/tags/{obj['sha']}")["object"]
    raise ValueError("Tag does not resolve to a supported commit")


def candidate_gate(api: GitHub, source: str, sha: str) -> None:
    ref = api.request(f"git/ref/heads/{quote(source, safe='')}")
    if ref is None or ref["object"]["sha"] != sha:
        raise ValueError("Candidate must still equal the source branch tip")
    checks = {}
    for page in range(1, 6):
        result = api.request(f"commits/{sha}/check-runs?per_page=100&page={page}")
        if not isinstance(result, dict):
            raise ValueError("Cannot read candidate checks")
        rows = result["check_runs"]
        for row in rows:
            name = row["name"]
            if (
                name in REQUIRED_CHECKS
                and row.get("app", {}).get("id") == APP_ID
                and row.get("head_sha") == sha
            ):
                if row["id"] > checks.get(name, {}).get("id", -1):
                    checks[name] = row
        if len(rows) < 100:
            break
    else:
        raise ValueError("Candidate checks exceed the reviewed pagination bound")
    if not REQUIRED_CHECKS.issubset(checks) or any(
        r["status"] != "completed" or r["conclusion"] != "success" for r in checks.values()
    ):
        raise ValueError(
            "Candidate needs successful GitHub Actions lint/policy/docs/HACS/Hassfest checks"
        )


def check_release(release: dict, tag: str, sha: str, preview: bool, body: str) -> None:
    if (
        release["tag_name"] != tag
        or release["name"] != tag
        or release["target_commitish"] != sha
        or release["prerelease"] != preview
        or release["body"] != body
    ):
        raise ValueError(
            "Existing release does not match the reviewed request; it will not be overwritten"
        )


def check_asset(asset: dict, content: bytes) -> None:
    if (
        asset.get("state") != "uploaded"
        or asset.get("size") != len(content)
        or asset.get("digest") != "sha256:" + hashlib.sha256(content).hexdigest()
    ):
        raise ValueError(
            "Asset content/state differs; preserve it for inspection instead of overwriting"
        )


def publish(
    api: GitHub,
    repo: Path,
    output: Path,
    channel: str,
    version: str,
    source: str,
    sha: str,
    english: str,
    chinese: str,
    upgrade_en: str = "",
    upgrade_zh: str = "",
    *,
    apply: bool = False,
    expected_digest: str | None = None,
    validation_run: int | None = None,
) -> dict:
    validate_request(channel, version, source, sha)
    local_tip = (
        git_bytes(repo, "rev-parse", f"refs/remotes/origin/{source}^{{commit}}").decode().strip()
    )
    if local_tip != sha:
        raise ValueError("Fetched candidate source tip changed")
    candidate_gate(api, source, sha)
    validation = None
    if validation_run is not None or channel != "preview" or "rc" in version:
        validation = verify_full_run(api, repo, sha, validation_run or 0)
    tag = f"v{version}"
    catalog = releases(api)
    body = release_body(
        api.repository, tag, channel, catalog, english, chinese, upgrade_en, upgrade_zh
    )
    matches = [r for r in catalog if r["tag_name"] == tag]
    if len(matches) > 1:
        raise ValueError("Ambiguous release identity")
    existing = matches[0] if matches else None
    remote_tag = tag_commit(api, tag)
    if remote_tag is not None and (existing is None or remote_tag != sha):
        raise ValueError("Tag already exists outside this matching release; never move or reuse it")
    if existing:
        check_release(existing, tag, sha, channel == "preview", body)
    package = build_package(repo, sha, version, output)
    if expected_digest is not None and package["sha256"] != expected_digest:
        raise ValueError("Package differs from the preparation job")
    assets = {name: (output / name).read_bytes() for name in (FILENAME, "SHA256SUMS")}
    report = {
        "tag": tag,
        "candidate_sha": sha,
        "package_sha256": package["sha256"],
        "filename": FILENAME,
        "bytes": package["bytes"],
        "member_count": len(package["members"]),
        "status": "prepared",
        "full_validation": validation,
        "prerelease": channel == "preview",
    }
    if not apply:
        return report
    latest = api.request("releases/latest")
    if existing is None:
        payload = {
            "tag_name": tag,
            "target_commitish": sha,
            "name": tag,
            "body": body,
            "draft": True,
            "prerelease": channel == "preview",
            "make_latest": "false",
        }
        try:
            existing = api.request("releases", method="POST", payload=payload)
        except RuntimeError:
            # A request can succeed before the connection is lost. Inspect once.
            existing = next((r for r in releases(api) if r["tag_name"] == tag), None)
            if existing is None:
                raise
        check_release(existing, tag, sha, channel == "preview", body)
    if existing.get("immutable") and existing["draft"]:
        raise ValueError("Cannot populate an immutable draft")
    for name, content in assets.items():
        current_assets = api.request(f"releases/{existing['id']}/assets?per_page=100")
        if not isinstance(current_assets, list) or len(current_assets) >= 100:
            raise ValueError("Invalid or oversized release asset list")
        matching = [a for a in current_assets if a["name"] == name]
        if len(matching) > 1:
            raise ValueError("Duplicate asset names")
        if matching:
            check_asset(matching[0], content)
            continue
        if not existing["draft"]:
            raise ValueError("Published releases cannot receive missing or replacement assets")
        try:
            asset = api.request(
                f"releases/{existing['id']}/assets?name={quote(name, safe='')}",
                method="POST",
                data=content,
                content_type="application/zip" if name == FILENAME else "text/plain",
            )
        except RuntimeError:
            asset = next(
                (
                    a
                    for a in api.request(f"releases/{existing['id']}/assets?per_page=100")
                    if a["name"] == name
                ),
                None,
            )
            if asset is None:
                raise
        check_asset(asset, content)
    final_assets = api.request(f"releases/{existing['id']}/assets?per_page=100")
    if {a["name"] for a in final_assets} != set(assets):
        raise ValueError("Unexpected release assets; inspect the draft")
    for asset in final_assets:
        check_asset(asset, assets[asset["name"]])
    if existing["draft"]:
        candidate_gate(api, source, sha)
        if validation is not None:
            verify_full_run(api, repo, sha, validation["run_id"])
        # Recheck the live draft and tag immediately before public visibility.
        check_release(
            api.request(f"releases/{existing['id']}"), tag, sha, channel == "preview", body
        )
        current_tag = tag_commit(api, tag)
        if current_tag is not None and current_tag != sha:
            raise ValueError("Tag changed while preparing the draft")
        try:
            api.request(
                f"releases/{existing['id']}",
                method="PATCH",
                payload={
                    "draft": False,
                    "make_latest": "false" if channel == "preview" else "true",
                },
            )
        except RuntimeError:
            pass  # Read the outcome; never blindly replay publication.
    final = api.request(f"releases/{existing['id']}")
    check_release(final, tag, sha, channel == "preview", body)
    if final["draft"] or tag_commit(api, tag) != sha:
        raise ValueError("Publication not confirmed; resume the matching draft")
    after_latest = api.request("releases/latest")
    if channel == "preview":
        if (latest or {}).get("id") != (after_latest or {}).get("id"):
            raise ValueError("Latest changed during preview publication")
    elif (after_latest or {}).get("id") != final["id"]:
        raise ValueError("Release Latest was not confirmed")
    return report | {
        "status": "published_verified",
        "release_id": final["id"],
        "url": final["html_url"],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--channel", required=True, choices=("preview", "candidate", "stable"))
    parser.add_argument("--version", required=True)
    parser.add_argument("--source", required=True)
    parser.add_argument("--sha", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--publish", action="store_true")
    args = parser.parse_args()
    if args.publish and (
        os.environ.get("GITHUB_ACTIONS") != "true"
        or os.environ.get("GITHUB_REF") != "refs/heads/main"
    ):
        parser.error("Publish only through the maintained main workflow")
    api = GitHub(os.environ["GITHUB_REPOSITORY"], os.environ["GH_TOKEN"])
    result = publish(
        api,
        Path.cwd(),
        args.output,
        args.channel,
        args.version,
        args.source,
        args.sha,
        os.environ["RELEASE_NOTES_EN"],
        os.environ["RELEASE_NOTES_ZH"],
        os.environ.get("UPGRADE_NOTES_EN", ""),
        os.environ.get("UPGRADE_NOTES_ZH", ""),
        apply=args.publish,
        expected_digest=os.environ.get("EXPECTED_PACKAGE_SHA") or None,
        validation_run=int(os.environ["VALIDATION_RUN_ID"])
        if os.environ.get("VALIDATION_RUN_ID")
        else None,
    )
    (args.output / "publication.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
