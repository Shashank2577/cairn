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
