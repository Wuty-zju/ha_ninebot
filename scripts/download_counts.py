"""Audit canonical GitHub package downloads across the complete release catalog."""

import argparse
import json
import os
from datetime import UTC, datetime
from pathlib import Path

from scripts.build_release import FILENAME
from scripts.publish_release import GitHub, releases


def package_counts(catalog: list[dict]) -> dict:
    """Exclude drafts/checksums and count each uploaded package asset once."""
    seen_releases: set[int] = set()
    seen_assets: set[int] = set()
    rows = []
    for release in catalog:
        if release.get("draft") is True:
            continue
        identity = release.get("id")
        if type(identity) is not int or identity <= 0:
            raise ValueError("Missing release identity")
        if identity in seen_releases:
            continue
        seen_releases.add(identity)
        assets = release.get("assets")
        if not isinstance(assets, list):
            raise ValueError("Missing release assets")
        for asset in assets:
            if asset.get("name") != FILENAME:
                continue
            asset_id, count = asset.get("id"), asset.get("download_count")
            if (
                type(asset_id) is not int
                or asset_id <= 0
                or type(count) is not int
                or count < 0
                or asset.get("state") != "uploaded"
            ):
                raise ValueError("Incomplete package download metadata")
            if asset_id in seen_assets:
                continue
            seen_assets.add(asset_id)
            rows.append(
                {
                    "tag": release["tag_name"],
                    "release_id": identity,
                    "asset_id": asset_id,
                    "downloads": count,
                }
            )
    return {
        "asset": FILENAME,
        "release_count": len(seen_releases),
        "package_release_count": len(rows),
        "downloads": sum(row["downloads"] for row in rows),
        "releases": rows,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    started = datetime.now(UTC)
    api = GitHub(args.repository, os.environ.get("GH_TOKEN", ""))
    result = package_counts(releases(api))
    result.update(
        repository=args.repository,
        started_at=started.isoformat(),
        completed_at=datetime.now(UTC).isoformat(),
        scope="GitHub ninebot.zip requests, including updates and repeat downloads",
    )
    # A failed read/validation leaves the previous report intact.
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(
        f"{result['downloads']} package downloads across {result['package_release_count']} releases"
    )


if __name__ == "__main__":
    main()
