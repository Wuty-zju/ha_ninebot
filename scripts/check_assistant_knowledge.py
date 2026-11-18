"""Offline corpus/source verification without importing Home Assistant."""

import argparse
import hashlib
import importlib.util
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "ninebot_public_knowledge", ROOT / "custom_components/ninebot/assistant_knowledge.py"
)
assert SPEC is not None and SPEC.loader is not None
knowledge = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(knowledge)


def verify_sources() -> int:
    """Compare immutable first-party source blobs; no network or live checkout trust."""
    from scripts.check_docs import anchors

    catalog = knowledge.load_catalog()
    records = [*catalog["entries"], *catalog["procedures"]]
    sources = {
        (entry["source"]["commit"], entry["source"]["path"], entry["source"]["sha256"])
        for entry in records
    }
    for commit, path, expected_hash in sources:
        data = subprocess.check_output(["git", "show", f"{commit}:{path}"], cwd=ROOT)
        if hashlib.sha256(data).hexdigest() != expected_hash:
            raise ValueError("Public source hash mismatch")
        fragments = anchors(data.decode())
        for entry in records:
            source = entry["source"]
            if source["commit"] == commit and source["path"] == path:
                if source["url"].split("#")[-1] not in fragments:
                    raise ValueError("Public source anchor missing")
    return len(sources)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verify-sources", action="store_true")
    args = parser.parse_args()
    print(f"Validated {len(knowledge.load_catalog()['entries'])} bilingual help topics")
    if args.verify_sources:
        print(f"Verified {verify_sources()} immutable source blobs and citation anchors")
