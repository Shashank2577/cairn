"""Background episode queue: episodes for one group are processed strictly in order.

Adding an episode reads the latest graph state (previous episodes, existing entities and facts),
so two episodes for the same group must never be processed concurrently. Servers enqueue and
return immediately; one worker per group drains its queue. Different groups run in parallel.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any

logger = logging.getLogger(__name__)


class EpisodeQueue:
    def __init__(self, history: int = 200):
        self._queues: dict[str, asyncio.Queue] = {}
        self._workers: dict[str, asyncio.Task] = {}
        self._running: dict[str, bool] = {}
        self._history = history
        self.results: list[dict[str, Any]] = []  # recent outcomes, newest last

    async def add(
        self,
        group_id: str,
        process: Callable[[], Awaitable[Any]],
        label: str = '',
    ) -> int:
        """Queue ``process`` for ``group_id``; returns the queue position."""
        queue = self._queues.get(group_id)
        if queue is None:
            queue = self._queues[group_id] = asyncio.Queue()
        await queue.put((label, process))
        worker = self._workers.get(group_id)
        if worker is None or worker.done():
            self._workers[group_id] = asyncio.create_task(self._drain(group_id))
        return queue.qsize()

    async def _drain(self, group_id: str) -> None:
        queue = self._queues[group_id]
        self._running[group_id] = True
        logger.info('episode worker started for group %s', group_id)
        try:
            while True:
                label, process = await queue.get()
                started = time.time()
                try:
                    result = await process()
                    self._record(group_id, label, 'ok', started, result=result)
                except Exception as exc:  # one bad episode never stops the queue
                    logger.error('queued episode %s for group %s failed: %s', label, group_id, exc)
                    self._record(group_id, label, 'error', started, error=f'{type(exc).__name__}: {exc}')
                finally:
                    queue.task_done()
        except asyncio.CancelledError:
            logger.info('episode worker for group %s cancelled', group_id)
        finally:
            self._running[group_id] = False

    def _record(self, group_id: str, label: str, status: str, started: float, **extra: Any) -> None:
        self.results.append({'group_id': group_id, 'label': label, 'status': status,
                             'seconds': round(time.time() - started, 3), **extra})
        del self.results[: -self._history]

    def size(self, group_id: str) -> int:
        queue = self._queues.get(group_id)
        return queue.qsize() if queue else 0

    def is_running(self, group_id: str) -> bool:
        return self._running.get(group_id, False)

    def status(self) -> dict[str, Any]:
        return {g: {'pending': q.qsize(), 'running': self._running.get(g, False)}
                for g, q in self._queues.items()}

    async def join(self, group_id: str | None = None) -> None:
        """Wait until the queue (one group, or all) is empty."""
        groups = [group_id] if group_id else list(self._queues)
        for g in groups:
            queue = self._queues.get(g)
            if queue is not None:
                await queue.join()

    async def stop(self) -> None:
        for task in self._workers.values():
            task.cancel()
        for task in self._workers.values():
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._workers.clear()
        for queue in self._queues.values():
            while not queue.empty():
                queue.get_nowait()
                queue.task_done()
