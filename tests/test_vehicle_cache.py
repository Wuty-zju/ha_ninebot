"""Cache integrity, privacy and cancellation at the filesystem boundary."""

import asyncio
import threading
import traceback

import pytest

from custom_components.ninebot.exceptions import ErrorKind, NinebotError
from custom_components.ninebot.vehicle_cache import cache_io, merge_partial_cache, read_cache


async def test_cancelled_cache_worker_finishes_before_its_caller_returns():
    entered, release, finished = (threading.Event() for _ in range(3))

    def worker():
        entered.set()
        release.wait(2)
        finished.set()
        raise OSError("synthetic-private-path")

    task = asyncio.create_task(cache_io(worker))
    for _ in range(100):
        if entered.is_set():
            break
        await asyncio.sleep(0.01)
    assert entered.is_set()
    task.cancel()
    await asyncio.sleep(0)
    task.cancel()
    await asyncio.sleep(0)
    assert not task.done()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert finished.is_set()


async def test_cache_filesystem_error_cannot_expose_a_private_path():
    def worker():
        raise OSError("synthetic-private-path")

    with pytest.raises(NinebotError) as error:
        await cache_io(worker)
    assert error.value.kind is ErrorKind.SERVICE
    assert "synthetic-private-path" not in "".join(traceback.format_exception(error.value))


def test_partial_cache_rejects_unreviewed_routes_and_symlinks(tmp_path):
    original = b'{"vehicles":[{"wnumber":"synthetic","business_line":"unreviewed"}]}'
    (tmp_path / "vehicles.json").write_bytes(original)
    with pytest.raises(NinebotError) as error:
        merge_partial_cache(tmp_path, None)
    assert error.value.kind is ErrorKind.PROTOCOL
    assert (tmp_path / "vehicles.json").read_bytes() == original
    (tmp_path / "vehicles.json").unlink()
    outside = tmp_path / "private-other-file"
    outside.write_bytes(original)
    (tmp_path / "vehicles.json").symlink_to(outside)
    with pytest.raises(NinebotError):
        read_cache(tmp_path)
    assert outside.read_bytes() == original


@pytest.mark.parametrize("observed", [frozenset(), frozenset({"one"})])
def test_silent_omission_preserves_verified_routes(tmp_path, observed):
    import json

    from custom_components.ninebot.vehicle_cache import reconcile_cache

    old = {
        "vehicles": [
            {"wnumber": "one", "business_line": "ebike"},
            {"wnumber": "two", "business_line": "motor"},
        ]
    }
    current = {"vehicles": [row for row in old["vehicles"] if row["wnumber"] in observed]}
    previous = json.dumps(old).encode()
    (tmp_path / "vehicles.json").write_text(json.dumps(current))
    assert not reconcile_cache(tmp_path, previous, observed, True)
    assert json.loads((tmp_path / "vehicles.json").read_bytes()) == old


def test_first_empty_account_is_not_a_removed_known_vehicle(tmp_path):
    from custom_components.ninebot.vehicle_cache import reconcile_cache

    assert reconcile_cache(tmp_path, None, frozenset(), True)
