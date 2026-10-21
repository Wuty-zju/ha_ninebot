"""Verify reusable full validation against Git objects and trusted Actions results."""

import hashlib
import json
import re
import subprocess
from pathlib import Path

# Full HA/mypy/Node inputs. Publication/documentation tools have current-SHA
# lint/policy gates; changing those does not require reinstalling both HA stacks.
INPUTS = ("custom_components", "tests", "scripts/ci.py", ".github", "pyproject.toml", "hacs.json")


def git(repo: Path, *args: str) -> bytes:
    return subprocess.check_output(["git", "-C", str(repo), *args])


def input_digest(repo: Path, sha: str) -> str:
    if re.fullmatch(r"[0-9a-f]{40}", sha) is None:
        raise ValueError("Validation requires a full Git commit SHA")
    # Paths, modes and immutable blob IDs all participate; working files do not.
    rows = git(repo, "ls-tree", "-r", "-z", sha, "--", *INPUTS)
    if not rows:
        raise ValueError("Validation inputs are missing")
    return hashlib.sha256(rows).hexdigest()


def verify_full_run(api, repo: Path, sha: str, run_id: int) -> dict:
    if type(run_id) is not int or run_id <= 0:
        raise ValueError("Select a successful full validation run")
    run = api.request(f"actions/runs/{run_id}")
    if not isinstance(run, dict) or (
        run.get("status") != "completed"
        or run.get("conclusion") != "success"
        or run.get("event") != "workflow_dispatch"
        or run.get("path") != ".github/workflows/checks.yaml"
        or run.get("head_repository", {}).get("full_name") != api.repository
    ):
        raise ValueError("Full validation must be a successful maintained Checks dispatch")
    digest = input_digest(repo, sha)
    if input_digest(repo, run["head_sha"]) != digest:
        raise ValueError("Full validation inputs differ from the candidate")
    config = json.loads(git(repo, "show", f"{sha}:.github/ci.json"))
    required = {"Core lint"} | {
        f"HA suite ({boundary['ha']})" for boundary in config["boundaries"].values()
    }
    jobs = {}
    for page in range(1, 11):
        result = api.request(f"actions/runs/{run_id}/jobs?filter=latest&per_page=100&page={page}")
        if not isinstance(result, dict) or not isinstance(result.get("jobs"), list):
            raise ValueError("Cannot read full validation jobs")
        rows = result["jobs"]
        for job in rows:
            if job["name"] in required and job.get("head_sha") == run["head_sha"]:
                if job["id"] > jobs.get(job["name"], {}).get("id", -1):
                    jobs[job["name"]] = job
        if len(rows) < 100:
            break
    else:
        raise ValueError("Full validation jobs exceed the pagination bound")
    if set(jobs) != required or any(
        job.get("status") != "completed" or job.get("conclusion") != "success"
        for job in jobs.values()
    ):
        raise ValueError("Full validation needs both pinned HA suites and frontend checks")
    for name, job in jobs.items():
        required_steps = (
            {"Run node --test tests/frontend/*.mjs"}
            if name == "Core lint"
            else {
                "Install and verify selected boundary",
                "Run mypy custom_components/ninebot --follow-imports=silent",
                "Run the complete offline suite",
            }
        )
        passed = {
            step["name"] for step in job.get("steps", []) if step.get("conclusion") == "success"
        }
        if not required_steps <= passed:
            raise ValueError("Full validation is missing successful full-suite steps")
    return {
        "run_id": run_id,
        "head_sha": run["head_sha"],
        "input_sha256": digest,
        "url": run["html_url"],
        "jobs": sorted(jobs),
        "scope": "HA suites, mypy and frontend; tooling gates remain candidate-specific",
    }


def find_full_run(api, repo: Path, sha: str) -> dict | None:
    """Promotion-only lookup; lack of evidence keeps the normal regression plan."""
    result = api.request(
        "actions/workflows/checks.yaml/runs?event=workflow_dispatch&status=success&per_page=20"
    )
    if not isinstance(result, dict):
        return None
    for run in result.get("workflow_runs", []):
        try:
            return verify_full_run(api, repo, sha, run["id"])
        except (ValueError, KeyError, subprocess.CalledProcessError):
            continue
    return None
