"""Synthetic archive measurements; always a new disposable database, never HA config.

Run with the pinned HA dependencies installed: python -m scripts.profile_history
--output /tmp/profile.json. Only timings, plans and synthetic identifiers leave the
temporary directory. This is a profiler, not a hardware-dependent test threshold.
"""

import argparse
import asyncio
import hashlib
import json
import math
import platform
import resource
import sqlite3
import statistics
import subprocess
import tempfile
import time
from datetime import UTC, datetime, timedelta
from functools import partial
from importlib.metadata import version
from pathlib import Path

from custom_components.ninebot.adapters import travel
from custom_components.ninebot.archive_codec import decoded, ride_data
from custom_components.ninebot.archive_versions import catalog_version
from custom_components.ninebot.detail_codec import packed, unpacked
from custom_components.ninebot.raw import Endpoint, build_record
from custom_components.ninebot.ride_archive import RideArchive, vehicle_key
from custom_components.ninebot.services import ride_response
from custom_components.ninebot.travel import merge_detail, parse_ride

NOW = datetime(2026, 10, 17, 12, tzinfo=UTC)
START = datetime(2021, 10, 1, tzinfo=UTC)
END = datetime(2026, 10, 1, tzinfo=UTC)
OWNER = hashlib.sha256(b"synthetic-profile-owner").hexdigest()
CHUNK = 512  # The packaged history workbench's current point-page size.


def summary(samples):
    values = sorted(samples)
    return {
        "n": len(values),
        "p50_ms": statistics.median(values),
        "p95_ms": values[math.ceil(len(values) * 0.95) - 1],
        "max_ms": values[-1],
    }


def measure(operation, repeats):
    values = []
    for _ in range(repeats):
        started = time.perf_counter()
        operation()
        values.append((time.perf_counter() - started) * 1000)
    return summary(values)


class ProfileArchive(RideArchive):
    """Instrument the actual actor without changing its queue or ownership guards."""

    capture = False

    def __init__(self, path):
        super().__init__(path, OWNER, max_bytes=2 * 1024**3)
        self.samples = []

    async def _run(self, operation, **kwargs):
        if not self.capture:
            return await super()._run(operation, **kwargs)
        queued = time.perf_counter()
        times = {}

        def measured():
            times["entered"] = time.perf_counter()
            try:
                return operation()
            finally:
                times["finished"] = time.perf_counter()

        result = await super()._run(measured, **kwargs)
        returned = time.perf_counter()
        self.samples.append(
            {
                "queue_and_guard_ms": (times["entered"] - queued) * 1000,
                "worker_operation_ms": (times["finished"] - times["entered"]) * 1000,
                "total_ms": (returned - queued) * 1000,
            }
        )
        return result


def month_rows(vehicle, month_index, count):
    index = 2021 * 12 + 9 + month_index
    year, month = divmod(index, 12)
    start = datetime(year, month + 1, 1, tzinfo=UTC)
    key = start.strftime("%Y%m")
    rows = [
        {
            "travel_id": f"synthetic-{vehicle}-{key}-{i:04}",
            "start_time": int(start.timestamp()) + i * 1200 + vehicle * 7,
            "end_time": int(start.timestamp()) + i * 1200 + vehicle * 7 + 600,
            "mileages": "5.50",
            "duration": 600,
            "speed": 48,
            "ec": 120,
        }
        for i in range(count)
    ]
    return key, rows


async def seed(actor, scale):
    started = time.perf_counter()
    base, extra = divmod(scale, 4 * 60)
    final = None
    for vehicle in range(4):
        for month in range(60):
            key, rows = month_rows(vehicle, month, base + (vehicle * 60 + month < extra))
            model = travel({"times": len(rows), "list": rows}, key)
            assert len(model.rides) == len(rows)
            await actor.async_record_month(f"synthetic-vehicle-{vehicle}", model, NOW)
            if vehicle == 0 and month == 59:
                final = model, rows
    assert final is not None
    return (time.perf_counter() - started) * 1000, final


async def timed_reads(actor, operation, repeats):
    actor.samples.clear()
    batches = []
    actor.capture = True
    try:
        for _ in range(repeats):
            started = time.perf_counter()
            await operation()
            batches.append((time.perf_counter() - started) * 1000)
    finally:
        actor.capture = False
    return {
        **{key: summary([row[key] for row in actor.samples]) for key in actor.samples[0]},
        "batch_wall_ms": summary(batches),
    }


async def page(actor, *, vehicle=0, limit=25, cursor=None, recent=False):
    return await actor.async_recorded_page(
        f"synthetic-vehicle-{vehicle}",
        datetime(2026, 9, 1, tzinfo=UTC) if recent else START,
        END,
        NOW,
        limit=limit,
        sort="desc",
        cursor=cursor,
    )


def traced_plans(actor):
    """Capture exact SELECTs outside timed samples, including current metadata reads."""
    from custom_components.ninebot.archive_browse import recorded_page
    from custom_components.ninebot.ride_archive import utc_string

    statements = []
    db = actor._db()
    db.set_trace_callback(statements.append)
    try:
        recorded_page(
            db,
            OWNER,
            vehicle_key("synthetic-vehicle-0"),
            utc_string(START),
            utc_string(END),
            utc_string(NOW),
            25,
            "202110",
            "202609",
            None,
            "desc",
        )
    finally:
        db.set_trace_callback(None)
    unique = list(dict.fromkeys(s for s in statements if s.lstrip().upper().startswith("SELECT")))
    return [
        {"sql": sql, "plan": [list(row) for row in db.execute("EXPLAIN QUERY PLAN " + sql)]}
        for sql in unique
    ]


def trigger_cost(actor):
    """Rollback-only microbenchmark on the disposable database; DDL is restored too."""
    db = actor._db()
    vehicle = vehicle_key("synthetic-vehicle-0")
    identity, original = db.execute(
        "SELECT id,data FROM rides WHERE vehicle=? LIMIT 1", (vehicle,)
    ).fetchone()
    before = catalog_version(db, vehicle)
    result = {}
    for enabled in (True, False):
        db.execute("SAVEPOINT profile_triggers")
        try:
            if not enabled:
                names = [
                    row[0]
                    for row in db.execute(
                        "SELECT name FROM sqlite_temp_master WHERE type='trigger'"
                    )
                ]
                for name in names:
                    assert name.startswith("ninebot_catalog_") and name.replace("_", "").isalnum()
                    db.execute(f'DROP TRIGGER temp."{name}"')
            started = time.perf_counter()
            for i in range(500):
                db.execute(
                    "UPDATE rides SET data=? WHERE vehicle=? AND id=?",
                    (original + (" " if i % 2 == 0 else ""), vehicle, identity),
                )
            result["with_triggers_ms" if enabled else "without_triggers_ms"] = (
                time.perf_counter() - started
            ) * 1000
        finally:
            db.execute("ROLLBACK TO profile_triggers")
            db.execute("RELEASE profile_triggers")
        assert catalog_version(db, vehicle) == before
    result["updates_per_sample"] = 500
    result["scope"] = "one transaction, no fsync; rollback restores TEMP triggers and source rows"
    return result


async def details(actor, final, repeats):
    month, rows = final
    received = NOW + timedelta(seconds=1)
    result = []
    for index, count in enumerate((512, 4096, 20000)):
        raw = {
            **rows[index],
            "is_show_simple_point": False,
            "trail": ";".join(
                f"{120 + i / 100000:.6f},{30 + math.sin(i / 40) / 1000:.6f},{i % 49},1"
                for i in range(count)
            ),
        }
        record = build_record(
            Endpoint.TRIP_DETAIL, raw, received, month.month, backend_version="0.1.7"
        )
        ride = merge_detail(
            month.rides[index], parse_ride(raw, month.month, source=Endpoint.TRIP_DETAIL)
        )
        assert await actor.async_record_detail("synthetic-vehicle-0", ride, received, raw=record)
        digest, body, _ = packed(record)
        canonical = json.dumps(raw, sort_keys=True, separators=(",", ":")).encode()
        metrics = {
            "source_points": count,
            "raw_json_bytes": len(canonical),
            "compressed_bytes": len(body),
            "json_loads": measure(partial(json.loads, canonical), repeats),
            "unpack_integrity_redaction": measure(
                partial(unpacked, body, digest, received, month.month, "0.1.7", None), repeats
            ),
            "parse_point_page": measure(
                partial(
                    parse_ride, raw, month.month, source=Endpoint.TRIP_DETAIL, max_points=CHUNK
                ),
                repeats,
            ),
        }
        for label, offset in (("first", 0), ("last", max(0, count - CHUNK))):
            metrics[label + "_actor_page"] = await timed_reads(
                actor,
                lambda offset=offset, ride=ride: actor.async_private_detail(
                    "synthetic-vehicle-0", ride.ride_id, max_points=CHUNK, point_offset=offset
                ),
                repeats,
            )
        detail = await actor.async_private_detail(
            "synthetic-vehicle-0", ride.ride_id, max_points=CHUNK
        )
        assert detail is not None and detail.ride.total_track_points == count
        assert len(detail.ride.track_points) == min(count, CHUNK)
        metrics["ha_response_json"] = measure(
            lambda detail=detail: json.dumps(
                ride_response(detail.ride, True), separators=(",", ":")
            ),
            repeats,
        )
        result.append(metrics)
    return result


async def profile(scale, repeats, directory):
    path = directory / f"synthetic-{scale}.sqlite3"
    actor = ProfileArchive(path)
    await actor.async_open()
    try:
        seed_ms, final = await seed(actor, scale)
        counts = await actor._run(
            lambda: {
                table: actor._db().execute(f"SELECT count(*) FROM {table}").fetchone()[0]
                for table in ("rides", "months", "membership")
            }
        )
        assert counts["rides"] == counts["membership"] == scale and counts["months"] == 240
        await actor.async_close()
        actor = ProfileArchive(path)
        opening = time.perf_counter()
        await actor.async_open()
        reopen_ms = (time.perf_counter() - opening) * 1000
        first = await timed_reads(actor, lambda: page(actor), 1)
        first_page = await page(actor)
        assert len(first_page.records) == 25 and first_page.next_cursor is not None
        result = {
            "rides": scale,
            "vehicles": 4,
            "months_per_vehicle": 60,
            "counts": counts,
            "seed_normalize_and_actor_ms": seed_ms,
            "reopen_validation_ms": reopen_ms,
            "connection_reopen_first_read_not_os_cold": first,
        }
        for label, operation in (
            ("five_year_page_25", lambda: page(actor)),
            ("five_year_page_100", lambda: page(actor, limit=100)),
            ("recent_month_page_25", lambda: page(actor, recent=True)),
            ("continuation_page_25", lambda: page(actor, cursor=first_page.next_cursor)),
            (
                "four_vehicle_parallel",
                lambda: asyncio.gather(*(page(actor, vehicle=i) for i in range(4))),
            ),
        ):
            result[label] = await timed_reads(actor, operation, repeats)
        result["query_plans"] = await actor._run(lambda: traced_plans(actor))
        sample = json.dumps(ride_data(first_page.records[0].ride))
        result["scalar_json_decode"] = measure(lambda: decoded(sample), repeats)
        result["temp_trigger_updates"] = await actor._run(lambda: trigger_cost(actor))
        result["details"] = await details(actor, final, min(repeats, 5))
        result["database_bytes"] = path.stat().st_size
        result["integrity_check"] = await actor._run(
            lambda: actor._db().execute("PRAGMA integrity_check").fetchone()[0]
        )
        assert result["integrity_check"] == "ok"
        return result
    finally:
        await actor.async_close()


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--scales",
        nargs="+",
        type=int,
        choices=(5000, 50000, 200000),
        default=[5000, 50000, 200000],
    )
    parser.add_argument("--repeats", type=int, choices=range(1, 31), default=12)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = {
        "schema_version": 1,
        "data": "synthetic_only_no_network_no_existing_database",
        "commit": (
            await asyncio.to_thread(
                subprocess.check_output, ["git", "rev-parse", "HEAD"], text=True
            )
        ).strip(),
        "python": platform.python_version(),
        "platform": platform.platform(),
        "sqlite": sqlite3.sqlite_version,
        "ha": version("homeassistant"),
        "ninecli": version("ninecli"),
        "repeats": args.repeats,
        "percentile": "nearest rank; small sample, descriptive not an SLA",
        "cache": "reopened actor and warm repeats; operating-system cache is not flushed",
        "operations": (
            "worker includes SQL, JSON and normalization; queue_and_guard excludes worker time"
        ),
        "results": [],
    }
    with tempfile.TemporaryDirectory(prefix="ninebot-synthetic-profile-") as temporary:
        for scale in args.scales:
            report["results"].append(await profile(scale, args.repeats, Path(temporary)))
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(report, indent=2) + "\n")
            print(f"Completed synthetic {scale} rides", flush=True)
    report["process_peak_rss_platform_units"] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    args.output.write_text(json.dumps(report, indent=2) + "\n")


if __name__ == "__main__":
    asyncio.run(main())
