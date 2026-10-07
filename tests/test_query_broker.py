"""Deterministic account scheduling; no cloud or native processes."""

import asyncio
from datetime import UTC, datetime

import pytest

from custom_components.ninebot.exceptions import ErrorKind, NinebotAuthError, NinebotError
from custom_components.ninebot.query_broker import Priority, QueryBroker, QueryKey

NOW = datetime(2026, 10, 8, tzinfo=UTC)


async def until(predicate):
    for _ in range(100):
        if predicate():
            return
        await asyncio.sleep(0)
    assert predicate(), "Task did not reach the expected scheduling boundary"


def key(scope="one", vehicle="private-vehicle"):
    return QueryKey(0, vehicle, "status", scope)


@pytest.fixture
async def broker():
    value = QueryBroker(asyncio.Semaphore(2), lambda: NOW)
    yield value
    await value.async_close()


@pytest.mark.parametrize("failed", [False, True])
async def test_same_flight_shares_receipt_or_failure(broker, failed):
    started, release = asyncio.Event(), asyncio.Event()
    count = 0
    failure = NinebotError(ErrorKind.SERVICE)

    async def operation():
        nonlocal count
        count += 1
        started.set()
        await release.wait()
        if failed:
            raise failure
        return {"value": 1}

    tasks = [
        asyncio.create_task(broker.async_read(key(), operation, lambda: True)) for _ in range(3)
    ]
    await started.wait()
    await until(lambda: broker.diagnostics()["waiters"] == 3)
    release.set()
    results = await asyncio.gather(*tasks, return_exceptions=True)
    assert count == 1 and results[0] is results[1] is results[2]
    if failed:
        assert results[0] is failure and failure.request_revision == 1
    else:
        assert results[0].received_at == results[0].started_at == NOW
        assert results[0].revision == 1
    assert broker.diagnostics()["counts"]["coalesced"] == 2
    assert not broker._flights and not broker._jobs


async def test_partial_cancel_keeps_other_consumer_last_cancel_reclaims_work(broker):
    started, release, cancelled = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def operation():
        started.set()
        try:
            await release.wait()
        finally:
            cancelled.set()
        return 2

    first = asyncio.create_task(broker.async_read(key(), operation, lambda: True))
    await started.wait()
    other = asyncio.create_task(broker.async_read(key(), operation, lambda: True))
    await until(lambda: broker.diagnostics()["waiters"] == 2)
    first.cancel()
    await asyncio.gather(first, return_exceptions=True)
    assert not cancelled.is_set()
    other.cancel()
    await asyncio.gather(other, return_exceptions=True)
    await cancelled.wait()
    await until(lambda: broker.diagnostics()["pending"] == 0)
    assert broker.diagnostics()["waiters"] == 0
    assert broker.diagnostics()["counts"]["cancelled"] == 1


async def test_cancelled_queued_job_releases_reserved_read_budget(broker):
    started, release = asyncio.Event(), asyncio.Event()
    calls = []

    async def operation():
        started.set()
        await release.wait()
        calls.append("active")

    async def unused():
        calls.append("unused")

    active = asyncio.create_task(broker.async_read(key(), operation, lambda: True))
    await started.wait()
    queued = asyncio.create_task(broker.async_read(key("unused"), unused, lambda: True))
    await until(lambda: broker.diagnostics()["pending"] == 2)
    queued.cancel()
    await asyncio.gather(queued, return_exceptions=True)
    assert broker.diagnostics()["pending"] == 1
    release.set()
    await active
    assert calls == ["active"]


async def test_priority_and_background_turn_do_not_starve_old_work(broker):
    started, release = asyncio.Event(), asyncio.Event()
    order = []

    async def initial():
        started.set()
        await release.wait()

    async def record(value):
        order.append(value)

    active = asyncio.create_task(broker.async_read(key(), initial, lambda: True))
    await started.wait()
    jobs = [("background", Priority.BACKGROUND), ("interactive", Priority.INTERACTIVE)]
    jobs += [(f"status{i}", Priority.STATUS) for i in range(5)]
    tasks = [
        asyncio.create_task(
            broker.async_read(key(value), lambda value=value: record(value), lambda: True, priority)
        )
        for value, priority in jobs
    ]
    await until(lambda: broker.diagnostics()["pending"] == 8)
    release.set()
    await asyncio.gather(active, *tasks)
    assert order[:3] == ["status0", "status1", "background"]
    assert set(order) == {value for value, _ in jobs}


async def test_same_priority_alternates_vehicles(broker):
    started, release = asyncio.Event(), asyncio.Event()
    order = []

    async def initial():
        started.set()
        await release.wait()

    async def record(vehicle):
        order.append(vehicle)

    active = asyncio.create_task(broker.async_read(key(vehicle="a"), initial, lambda: True))
    await started.wait()
    tasks = [
        asyncio.create_task(
            broker.async_read(key(str(i), vehicle), lambda v=vehicle: record(v), lambda: True)
        )
        for i, vehicle in enumerate(("a", "a", "b", "b"))
    ]
    await until(lambda: broker.diagnostics()["pending"] == 5)
    release.set()
    await asyncio.gather(active, *tasks)
    assert order == ["b", "a", "b", "a"]


async def test_account_one_wire_global_two_and_independent_slow_account():
    gate = asyncio.Semaphore(2)
    brokers = [QueryBroker(gate, lambda: NOW) for _ in range(3)]
    releases = [asyncio.Event() for _ in brokers]
    started = [asyncio.Event() for _ in brokers]
    running = maximum = 0

    async def operation(index):
        nonlocal running, maximum
        running += 1
        maximum = max(maximum, running)
        started[index].set()
        try:
            await releases[index].wait()
        finally:
            running -= 1
        return index

    tasks = [
        asyncio.create_task(b.async_read(key(), lambda i=i: operation(i), lambda: True))
        for i, b in enumerate(brokers)
    ]
    try:
        await started[0].wait()
        await started[1].wait()
        assert not started[2].is_set()
        releases[1].set()
        await started[2].wait()
        assert not tasks[0].done() and maximum == 2
        releases[0].set()
        releases[2].set()
        await asyncio.gather(*tasks)
    finally:
        for b in brokers:
            await b.async_close()
        await asyncio.gather(*tasks, return_exceptions=True)


async def test_commands_never_coalesce_and_keep_ack_after_owner_loss(broker):
    calls = 0
    allowed = True

    async def command():
        nonlocal calls, allowed
        calls += 1
        allowed = False

    receipt = await broker.async_command("private-vehicle", command, lambda: allowed)
    assert receipt.value is None and calls == 1
    allowed = True
    await broker.async_command("private-vehicle", command, lambda: allowed)
    assert calls == 2
    with pytest.raises(NinebotError) as caught:
        await broker.async_command("private-vehicle", command, lambda: allowed)
    assert caught.value.kind is ErrorKind.CLOSED and calls == 2


@pytest.mark.parametrize("before", [False, True])
async def test_read_guard_before_and_after_wire(broker, before):
    allowed = not before
    calls = 0

    async def operation():
        nonlocal calls, allowed
        calls += 1
        allowed = False

    with pytest.raises(NinebotError) as caught:
        await broker.async_read(key(), operation, lambda: allowed)
    assert caught.value.kind is ErrorKind.CLOSED
    assert calls == (0 if before else 1)


async def test_auth_callback_invalidates_following_queued_wire():
    allowed = True
    started, release = asyncio.Event(), asyncio.Event()
    calls = []

    def invalidate():
        nonlocal allowed
        allowed = False

    broker = QueryBroker(asyncio.Semaphore(2), lambda: NOW, on_auth_failure=invalidate)

    async def operation():
        started.set()
        await release.wait()
        raise NinebotAuthError()

    async def unused():
        calls.append("unexpected")

    first = asyncio.create_task(broker.async_read(key(), operation, lambda: allowed))
    await started.wait()
    queued = asyncio.create_task(broker.async_read(key("next"), unused, lambda: allowed))
    await until(lambda: broker.diagnostics()["pending"] == 2)
    release.set()
    results = await asyncio.gather(first, queued, return_exceptions=True)
    assert isinstance(results[0], NinebotAuthError)
    assert results[1].kind is ErrorKind.CLOSED and not calls
    await broker.async_close()


@pytest.mark.parametrize("kind,retry", [(ErrorKind.CONNECTION, None), (ErrorKind.SERVICE, 7)])
async def test_circuit_cooldown_does_not_extend_on_rejection_and_recovers(broker, kind, retry):
    calls = 0

    async def failure():
        nonlocal calls
        calls += 1
        raise NinebotError(kind, retry_after=retry)

    for _ in range(2 if retry is None else 1):
        with pytest.raises(NinebotError):
            await broker.async_read(key(), failure, lambda: True)
    deadline = broker._cooldown_until
    with pytest.raises(NinebotError) as caught:
        await broker.async_read(key(), failure, lambda: True)
    assert caught.value.retry_after > 0 and broker._cooldown_until == deadline
    assert calls == (2 if retry is None else 1)
    broker._cooldown_until = 0

    async def recover():
        return "recovered"

    assert (await broker.async_read(key(), recover, lambda: True)).value == "recovered"
    assert broker._connection_failures == 0


async def test_protocol_failure_does_not_break_account_and_unknown_failure_transfers(broker):
    for failure in (NinebotError(ErrorKind.PROTOCOL), RuntimeError("synthetic")):

        async def operation(failure=failure):
            raise failure

        with pytest.raises(NinebotError) as caught:
            await broker.async_read(key(), operation, lambda: True)
        assert caught.value.kind is ErrorKind.PROTOCOL
        assert "synthetic" not in str(caught.value)
    assert broker._cooldown_until == 0
    assert "private-vehicle" not in str(broker.diagnostics())


async def test_read_command_queue_budgets_and_waiter_limits(broker):
    release, started = asyncio.Event(), asyncio.Event()

    async def hold():
        started.set()
        await release.wait()

    tasks = [
        asyncio.create_task(broker.async_read(key(str(i)), hold, lambda: True)) for i in range(12)
    ]
    await started.wait()
    await until(lambda: broker.diagnostics()["pending"] == 12)
    with pytest.raises(NinebotError) as caught:
        await broker.async_read(key("excess"), hold, lambda: True)
    assert caught.value.kind is ErrorKind.BUSY
    commands = [
        asyncio.create_task(broker.async_command("v", hold, lambda: True)) for _ in range(4)
    ]
    await until(lambda: broker.diagnostics()["pending"] == 16)
    with pytest.raises(NinebotError):
        await broker.async_command("v", hold, lambda: True)
    await broker.async_close()
    results = await asyncio.gather(*tasks, *commands, return_exceptions=True)
    assert all(isinstance(value, asyncio.CancelledError) for value in results)
    assert broker.diagnostics()["pending"] == broker.diagnostics()["waiters"] == 0


async def test_coalesced_waiter_bounds_and_closed_admission(broker):
    release = asyncio.Event()

    async def hold():
        await release.wait()

    first = [asyncio.create_task(broker.async_read(key(), hold, lambda: True)) for _ in range(32)]
    await until(lambda: broker.diagnostics()["waiters"] == 32)
    with pytest.raises(NinebotError):
        await broker.async_read(key(), hold, lambda: True)
    other = [
        asyncio.create_task(broker.async_read(key("other"), hold, lambda: True)) for _ in range(32)
    ]
    await until(lambda: broker.diagnostics()["waiters"] == 64)
    with pytest.raises(NinebotError):
        await broker.async_read(key("third"), hold, lambda: True)
    await broker.async_close()
    await asyncio.gather(*first, *other, return_exceptions=True)
    with pytest.raises(NinebotError) as caught:
        await broker.async_read(key(), hold, lambda: True)
    assert caught.value.kind is ErrorKind.CLOSED


async def test_close_while_waiting_global_gate_reclaims_worker():
    gate = asyncio.Semaphore(0)
    broker = QueryBroker(gate, lambda: NOW)

    async def unused():
        pytest.fail("No global wire admission")

    task = asyncio.create_task(broker.async_read(key(), unused, lambda: True))
    await until(lambda: broker._active is not None)
    await broker.async_close()
    assert isinstance(
        (await asyncio.gather(task, return_exceptions=True))[0], asyncio.CancelledError
    )
    assert broker._worker.done() and not broker._flights
