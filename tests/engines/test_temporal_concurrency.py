"""The embedded fact store is a single-writer file: it must be released after every call, even when one
service object is used from several threads at once (each thread runs its own event loop)."""
from __future__ import annotations

import threading

import pytest

pytest.importorskip("kuzu")


def test_shared_service_releases_store_under_concurrency(cairn):
    from cairn.engines.temporal import TemporalService, run_sync
    from cairn.engines.temporal import service as svcmod
    shared = TemporalService(cairn.project, cairn.router, cairn.brain)
    calls = [shared.status, lambda: shared.list_facts(limit=5), lambda: shared.list_entities(limit=5)]
    errors: list[str] = []

    def call(i: int) -> None:
        try:
            run_sync(calls[i % 3]())
        except Exception as exc:  # noqa: BLE001 — collected and asserted below
            errors.append(f"{type(exc).__name__}: {exc}")
    threads = [threading.Thread(target=call, args=(i,)) for i in range(9)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    assert shared._depth == 0
    assert str(shared.store_path.resolve()) not in svcmod._handles  # closed, so another process can open it


def test_semaphore_gather_stops_queued_calls_when_one_fails(recwarn):
    """One failed call (a budget stop mid-episode) cancels the queue: nothing left unawaited, no more spend."""
    import asyncio
    import gc

    from cairn.engines.temporal.helpers import semaphore_gather
    started: list[int] = []

    async def resolve_extracted_edge(i: int) -> int:
        started.append(i)
        await asyncio.sleep(0.01 if i else 0)
        if i == 0:
            raise RuntimeError("budget reached")
        return i

    async def main() -> None:
        with pytest.raises(RuntimeError, match="budget reached"):
            await semaphore_gather(*[resolve_extracted_edge(i) for i in range(8)], max_coroutines=2)

    asyncio.run(main())
    gc.collect()
    assert not [w for w in recwarn if "never awaited" in str(w.message)]
    assert len(started) < 8  # the tail of the queue never ran

    async def ok() -> list[int]:
        return await semaphore_gather(*[resolve_extracted_edge(i) for i in range(1, 5)], max_coroutines=2)
    assert asyncio.run(ok()) == [1, 2, 3, 4]  # results keep their order on success


def test_semaphore_gather_cancelled_before_it_starts_leaves_nothing_unawaited(recwarn):
    """A timeout or Ctrl-C right after the calls are scheduled: every coroutine is still closed."""
    import asyncio
    import gc

    from cairn.engines.temporal.helpers import semaphore_gather

    async def resolve_extracted_edge(i: int) -> int:
        await asyncio.sleep(1)
        return i

    async def main() -> None:
        outer = asyncio.ensure_future(semaphore_gather(*[resolve_extracted_edge(i) for i in range(5)],
                                                       max_coroutines=2))
        await asyncio.sleep(0)
        outer.cancel()
        with pytest.raises(asyncio.CancelledError):
            await outer

    asyncio.run(main())
    gc.collect()
    assert not [w for w in recwarn if "never awaited" in str(w.message)]
