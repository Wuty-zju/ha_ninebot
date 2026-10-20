"""Read-only release request checks; never execute candidate code or publish anything."""

import argparse
import json
import re
import subprocess
from pathlib import Path

NUMBER = r"(?:0|[1-9][0-9]*)"
VERSION = re.compile(rf"({NUMBER})\.({NUMBER})\.({NUMBER})((a|b|rc){NUMBER})?")
SHA = re.compile(r"[0-9a-f]{40}")


def validate_request(channel: str, version: str, source: str, sha: str) -> None:
    """Keep preview, promoted RC and stable publication roles explicit."""
    match = VERSION.fullmatch(version)
    if not match or not SHA.fullmatch(sha):
        raise ValueError("Use an exact version without v and a full lowercase commit SHA")
    if channel not in {"preview", "candidate", "stable"}:
        raise ValueError("Unknown release channel")
    preview = match.group(4) is not None
    if channel == "preview" and not preview:
        raise ValueError("Preview needs an a/b/rc suffix")
    if channel == "stable" and preview:
        raise ValueError("Stable must not have a prerelease suffix")
    if channel == "candidate" and match.group(5) != "rc":
        raise ValueError("Candidate publication requires an rc version")
    base_version = ".".join(match.group(i) for i in (1, 2, 3))
    allowed = {"dev", f"release/{base_version}"} if channel == "preview" else {"main"}
    if source not in allowed:
        raise ValueError("Source branch does not match the channel and version")


def git(repo: Path, *args: str) -> str:
    """Read Git objects without evaluating shell text or candidate Python."""
    result = subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=False
    )
    if result.returncode:
        raise ValueError("Required Git object is unavailable; fetch branches and tags first")
    return result.stdout.strip()


def check_candidate(repo: Path, channel: str, version: str, source: str, sha: str) -> dict:
    """Validate a source tip, committed manifest and unused tag; make no mutations."""
    validate_request(channel, version, source, sha)
    tip = git(repo, "rev-parse", "--verify", f"refs/remotes/origin/{source}^{{commit}}")
    if tip != sha:
        raise ValueError("Candidate must be the fetched source tip; review a changed branch again")
    manifest = json.loads(git(repo, "show", f"{sha}:custom_components/ninebot/manifest.json"))
    if (
        not isinstance(manifest, dict)
        or manifest.get("domain") != "ninebot"
        or manifest.get("version") != version
    ):
        raise ValueError("Committed Ninebot manifest version must equal the requested version")
    tags = set(git(repo, "tag", "--list").splitlines())
    if {version, f"v{version}"} & tags:
        raise ValueError("Version tag already exists; never move or reuse a published tag")
    return {
        "candidate_sha": sha,
        "tree_sha": git(repo, "rev-parse", f"{sha}^{{tree}}"),
        "runtime_tree_sha": git(repo, "rev-parse", f"{sha}:custom_components/ninebot"),
        "source": source,
        "version": version,
        "tag": f"v{version}",
        "prerelease": channel == "preview",
        "set_latest": channel != "preview",
        "scope": "Request preflight only; CI, packaging and publication still need verification",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--channel", required=True, choices=("preview", "candidate", "stable"))
    parser.add_argument("--version", required=True)
    parser.add_argument("--source", required=True)
    parser.add_argument("--sha", required=True)
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    args = parser.parse_args()
    try:
        result = check_candidate(args.repo, args.channel, args.version, args.source, args.sha)
    except (ValueError, OSError) as err:
        parser.exit(1, f"Release preflight failed: {err}\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
