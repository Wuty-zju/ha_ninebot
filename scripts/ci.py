"""Shared CI pins and conservative, deduplicated offline test selection."""

import argparse
import fnmatch
import json
import os
import re
import subprocess
import sys
from importlib.metadata import version
from pathlib import Path
from urllib.error import URLError

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
CONFIG = ROOT / ".github/ci.json"


def matches(path: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatchcase(path, pattern) for pattern in patterns)


def select(paths: list[str], config: dict, available: set[str]) -> dict:
    """Unknown runtime paths select all tests; known scopes form one union."""
    selected: set[str] = set()
    reasons: set[str] = set()
    boundaries = ["minimum"]
    frontend = False
    for path in paths:
        if matches(path, config["critical_paths"]):
            boundaries = ["minimum", "current"]
        if matches(
            path, ["custom_components/ninebot/frontend/**", "tests/frontend/**", "tests/browser/**"]
        ):
            frontend = True
            continue
        if matches(path, config["shared_paths"]):
            selected.update(available)
            reasons.add("shared-test-input")
            continue
        if path in {"scripts/ci.py", ".github/ci.json"} or path.startswith(".github/workflows/"):
            selected.update(config["pipeline_tests"])
            reasons.add("pipeline-canary")
            continue
        grouped = False
        for name, group in config["groups"].items():
            if matches(path, group["paths"]) or path in group["tests"]:
                selected.update(group["tests"])
                reasons.add(name)
                grouped = True
        if path in available:
            selected.add(path)
            reasons.add("changed-test")
        if path.startswith("custom_components/ninebot/") and not grouped:
            selected.update(available)
            reasons.add("unmapped-runtime")
    tests = sorted(selected & available)
    return {
        "tests": tests,
        "reasons": sorted(reasons),
        "frontend": frontend,
        "matrix": {
            "include": [{"boundary": name, **config["boundaries"][name]} for name in boundaries]
        },
    }


def changed_paths(base: str, head: str) -> list[str]:
    if not all(re.fullmatch(r"[0-9a-fA-F]{40}", sha) for sha in (base, head)):
        raise ValueError("Use full commit SHAs for the selected diff")
    result = subprocess.check_output(
        ["git", "diff", "--name-only", "--no-renames", "-z", f"{base}...{head}"], cwd=ROOT
    )
    return [path.decode() for path in result.split(b"\0") if path]


def install(config: dict, boundary: str, profile: bool = False) -> None:
    selected = config["boundaries"][boundary]
    expected = {"homeassistant": selected["ha"], "ninecli": config["tools"]["ninecli"]}
    if not profile:
        expected.update(
            {
                "pytest-homeassistant-custom-component": selected["plugin"],
                "mypy": config["tools"]["mypy"],
            }
        )
    subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "install",
            *(f"{key}=={value}" for key, value in expected.items()),
        ],
        check=True,
    )
    subprocess.run([sys.executable, "-m", "pip", "check"], check=True)
    for name, expected_version in expected.items():
        if version(name) != expected_version:
            raise ValueError(f"Installed {name} does not match the selected CI boundary")


def pytest_command(tests: list[str], available: set[str]) -> list[str]:
    """Complete selected suites produce candidate coverage in the same run."""
    if not tests or not isinstance(tests, list) or not set(tests) <= available:
        raise ValueError("The CI plan must name existing offline test files")
    coverage = (
        [
            "--cov=custom_components.ninebot",
            "--cov-branch",
            "--cov-fail-under=91",
            "--cov-report=term-missing",
            "--cov-report=xml",
        ]
        if set(tests) == available
        else []
    )
    return [
        sys.executable,
        "-m",
        "pytest",
        "-q",
        "--maxfail=3",
        "--junitxml=test-results.xml",
        "-o",
        "faulthandler_timeout=60",
        *coverage,
        *tests,
    ]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--plan", action="store_true")
    action.add_argument("--matrix", action="store_true")
    action.add_argument("--install", choices=("minimum", "current"))
    action.add_argument("--run-tests", type=Path)
    parser.add_argument("--profile", action="store_true")
    parser.add_argument("--base")
    parser.add_argument("--head")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    config = json.loads(CONFIG.read_text())
    available = {str(path.relative_to(ROOT)) for path in (ROOT / "tests").rglob("test_*.py")}
    if args.install:
        install(config, args.install, args.profile)
        return
    if args.run_tests:
        tests = json.loads(args.run_tests.read_text())["tests"]
        subprocess.run(pytest_command(tests, available), cwd=ROOT, check=True)
        return
    if args.matrix:
        result = {
            "include": [{"boundary": name, **value} for name, value in config["boundaries"].items()]
        }
        if output := os.environ.get("GITHUB_OUTPUT"):
            with open(output, "a") as stream:
                stream.write(f"matrix={json.dumps(result)}\n")
    else:
        if not args.base or not args.head:
            parser.error("--plan requires --base and --head")
        result = select(changed_paths(args.base, args.head), config, available)
        if os.environ.get("CI_REUSE_FULL_VALIDATION") == "true":
            from scripts.ci_evidence import find_full_run
            from scripts.publish_release import GitHub

            try:
                evidence = find_full_run(
                    GitHub(os.environ["GITHUB_REPOSITORY"], os.environ["GH_TOKEN"]), ROOT, args.head
                )
            except (RuntimeError, ValueError, KeyError, URLError):
                evidence = None
            if evidence:
                result["reused_full_validation"] = evidence
                result["tests"] = []
                result["reasons"].append("verified-full-validation-reuse")
        output = os.environ.get("GITHUB_OUTPUT")
        if output:
            with open(output, "a") as stream:
                stream.write(f"runtime={str(bool(result['tests'])).lower()}\n")
                stream.write(f"frontend={str(result['frontend']).lower()}\n")
                stream.write(f"matrix={json.dumps(result['matrix'])}\n")
                stream.write(f"plan={json.dumps(result)}\n")
    text = json.dumps(result, indent=2) + "\n"
    if args.output:
        args.output.write_text(text)
    print(text, end="")


if __name__ == "__main__":
    main()
