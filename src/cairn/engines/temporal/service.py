"""Per-project facade over the temporal graph: the functions Cairn's CLI, MCP tools and HTTP API call.

``TemporalService(project, router, brain)`` wires the engine the Cairn way:

  * store: embedded Kuzu at ``.cairn/temporal/graph.kuzu`` by default, or a team server
    (Neo4j ``bolt://``, FalkorDB ``falkor://``, Neptune ``neptune-db://``) from ``temporal.url``;
  * model: ``RouterLLMClient`` over ``cairn.router.Router`` (no provider SDK is called directly);
  * embeddings: ``LocalEmbedder`` (shared local model, no key); reranker: local, or model-judged
    with ``temporal.reranker = "model"``.

Every public coroutine returns plain JSON-ready data. Reads (search, episodes, facts) work without
a model; writes that need extraction raise ``ModelUnavailableError`` when ``router.available`` is
False. The embedded store is opened per call and closed afterwards (another process — a git hook
sync, the server — may need it); set ``keep_open=True`` in long-lived processes that own it.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
import time
from collections.abc import AsyncIterator, Coroutine
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, TypeVar
from uuid import uuid4

from pydantic import BaseModel

from . import formatting, ontology
from .cross_encoder.client import CrossEncoderClient
from .cross_encoder.local import LocalRerankerClient
from .cross_encoder.router_reranker import RouterRerankerClient
from .driver.driver import GraphDriver, GraphProvider
from .edges import EntityEdge
from .embedder.client import EmbedderClient
from .embedder.local import LocalEmbedder
from .engine import TemporalGraph
from .errors import GroupsEdgesNotFoundError, TemporalGraphError
from .helpers import validate_group_id
from .llm_client.router_client import RouterLLMClient
from .migrations import ensure_embedding_space, migrate
from .nodes import CommunityNode, EntityNode, EpisodeType, SagaNode
from .queue import EpisodeQueue
from .search import search_config_recipes as recipes
from .search.search_config import SearchConfig
from .search.search_filters import ComparisonOperator, DateFilter, SearchFilters
from .search.search_helpers import search_results_to_context_string
from .utils.bulk_utils import RawEpisode
from .utils.maintenance.graph_data_operations import clear_data

logger = logging.getLogger(__name__)
T = TypeVar('T')

BACKENDS = ('kuzu', 'neo4j', 'falkordb', 'neptune')
STORE_FILE = 'graph.kuzu'


class ModelUnavailableError(TemporalGraphError):
    """Raised by operations that need a model when none is configured."""

    def __init__(self, operation: str):
        self.message = f'{operation} needs a model (configure models.provider or sign in to Claude Code)'
        super().__init__(self.message)


class StoreBusyError(TemporalGraphError):
    """The embedded store is locked by another process for longer than the wait allows."""


# ---- settings ------------------------------------------------------------------------------------
@dataclass
class TemporalSettings:
    backend: str = 'kuzu'
    path: Path = Path('.cairn/temporal')
    url: str = ''
    user: str = ''
    password: str = ''
    database: str = ''
    search_host: str = ''  # Neptune: the full-text search endpoint
    group_id: str = 'default'
    reranker: str = 'local'  # local | model | bge
    concurrency: int = 4
    llm_cache: bool = False
    store_raw_episode_content: bool = True
    update_communities: bool = False
    entity_types: list[Any] = field(default_factory=list)
    edge_types: list[Any] = field(default_factory=list)
    edge_type_map: list[Any] = field(default_factory=list)
    custom_extraction_instructions: str = ''
    local_extractor: bool = False  # entity extraction on a local NER model (gliner2 package)
    lock_wait_seconds: float = 30.0

    @classmethod
    def from_project(cls, project: Any) -> TemporalSettings:
        cfg = project.cfg
        url = str(cfg('temporal.url', '') or cfg('deep.graph_url', '') or '')
        backend = str(cfg('temporal.backend', '') or '').lower() or _backend_for_url(url)
        custom = cfg('temporal.custom_types', {}) or {}
        entity_types = list(cfg('temporal.entity_types', []) or [])
        entity_types += [{'name': k, 'description': v} for k, v in dict(custom).items()]
        return cls(
            backend=backend,
            path=Path(project.dir) / 'temporal',
            url=url,
            user=str(cfg('temporal.user', '') or os.environ.get('CAIRN_TEMPORAL_USER', '')),
            password=os.environ.get('CAIRN_TEMPORAL_PASSWORD', ''),
            database=str(cfg('temporal.database', '') or ''),
            search_host=str(cfg('temporal.search_host', '') or ''),
            group_id=str(cfg('temporal.group_id', '') or project.id),
            reranker=str(cfg('temporal.reranker', 'local') or 'local'),
            concurrency=int(cfg('temporal.concurrency', 4) or 4),
            llm_cache=bool(cfg('temporal.llm_cache', False)),
            store_raw_episode_content=bool(cfg('temporal.store_raw_episode_content', True)),
            update_communities=bool(cfg('temporal.update_communities', False)),
            entity_types=entity_types,
            edge_types=list(cfg('temporal.edge_types', []) or []),
            edge_type_map=list(cfg('temporal.edge_type_map', []) or []),
            custom_extraction_instructions=str(cfg('temporal.extraction_instructions', '') or ''),
            local_extractor=bool(cfg('temporal.local_extractor', False)),
        )


def _backend_for_url(url: str) -> str:
    if url.startswith(('bolt://', 'bolt+s://', 'neo4j://', 'neo4j+s://')):
        return 'neo4j'
    if url.startswith(('falkor://', 'falkors://', 'redis://', 'rediss://')):
        return 'falkordb'
    if url.startswith(('neptune-db://', 'neptune-graph://')):
        return 'neptune'
    return 'kuzu'


# ---- shared embedded-store handles (one per path per process) -----------------------------------
_handles_lock = threading.Lock()
_handles: dict[str, list[Any]] = {}  # path -> [driver, refcount]
_migrated: set[str] = set()


def _open_kuzu(path: Path, wait: float) -> GraphDriver:
    from .driver.kuzu_driver import KuzuDriver

    key = str(path.resolve())
    deadline = time.monotonic() + max(0.0, wait)
    delay = 0.05
    while True:
        with _handles_lock:
            entry = _handles.get(key)
            if entry is not None:
                entry[1] += 1
                return entry[0]
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                driver = KuzuDriver(db=str(path))
                _handles[key] = [driver, 1]
                return driver
            except RuntimeError as exc:
                if 'lock' not in str(exc).lower():
                    raise
                if time.monotonic() >= deadline:
                    raise StoreBusyError(
                        f'the temporal store {path} is in use by another process'
                    ) from exc
        time.sleep(delay)
        delay = min(delay * 2, 1.0)


async def _release_kuzu(path: Path, driver: GraphDriver, keep_open: bool) -> None:
    key = str(path.resolve())
    close = False
    with _handles_lock:
        entry = _handles.get(key)
        if entry is not None and entry[0] is driver:
            entry[1] -= 1
            if entry[1] <= 0 and not keep_open:
                del _handles[key]
                close = True
    if close:
        await driver.close()


def run_sync(coro: Coroutine[Any, Any, T]) -> T:
    """Run a coroutine from synchronous code, even when an event loop is already running."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    box: dict[str, Any] = {}

    def target() -> None:
        try:
            box['value'] = asyncio.run(coro)
        except BaseException as exc:  # re-raised in the caller's thread
            box['error'] = exc

    thread = threading.Thread(target=target, daemon=True)
    thread.start()
    thread.join()
    if 'error' in box:
        raise box['error']
    return box['value']


def _source(source: str | EpisodeType | None) -> EpisodeType:
    if isinstance(source, EpisodeType):
        return source
    try:
        return EpisodeType[(source or 'text').lower()]
    except KeyError:
        logger.warning('unknown episode source %r; using text', source)
        return EpisodeType.text


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ---- the service ---------------------------------------------------------------------------------
class TemporalService:
    def __init__(
        self,
        project: Any,
        router: Any,
        brain: Any = None,
        *,
        settings: TemporalSettings | None = None,
        budget: Any = None,
        keep_open: bool = False,
        driver: GraphDriver | None = None,
        embedder: EmbedderClient | None = None,
        cross_encoder: CrossEncoderClient | None = None,
    ):
        self.project = project
        self.router = router
        self.brain = brain
        self.settings = settings or TemporalSettings.from_project(project)
        self.budget = budget
        self.keep_open = keep_open
        self._driver_override = driver
        self._embedder = embedder
        self._cross_encoder = cross_encoder
        self._engine: TemporalGraph | None = None
        self._driver: GraphDriver | None = None
        self._depth = 0
        self._locks: dict[int, asyncio.Lock] = {}
        self._state_lock = threading.Lock()  # guards open/depth/close across threads (each has its own loop)
        self.queue = EpisodeQueue()

    # ---- wiring ----------------------------------------------------------------------------------
    @property
    def available(self) -> bool:
        """True when a model is available (needed for ingestion, communities, saga summaries)."""
        return bool(getattr(self.router, 'available', False))

    @property
    def store_path(self) -> Path:
        return Path(self.settings.path) / STORE_FILE

    @property
    def default_group(self) -> str:
        return self.settings.group_id

    def entity_types(self) -> dict[str, type[BaseModel]] | None:
        return ontology.build_entity_types(self.settings.entity_types)

    def edge_types(self) -> dict[str, type[BaseModel]] | None:
        return ontology.build_edge_types(self.settings.edge_types)

    def edge_type_map(self) -> dict[tuple[str, str], list[str]] | None:
        return ontology.build_edge_type_map(self.settings.edge_type_map)

    def _llm(self) -> RouterLLMClient:
        cache_dir = str(Path(self.settings.path) / 'llm-cache') if self.settings.llm_cache else None
        return RouterLLMClient(
            self.router,
            budget=self.budget,
            cache=self.settings.llm_cache,
            cache_dir=cache_dir,
            max_concurrency=self.settings.concurrency,
        )

    def _extraction_client(self, llm: RouterLLMClient) -> Any:
        """The model client the engine uses: the router adapter, optionally fronted by the local
        entity extractor (``temporal.local_extractor = true``; needs the gliner2 package)."""
        if not self.settings.local_extractor:
            return llm
        try:
            from .llm_client.local_extractor_client import LocalEntityExtractorClient

            client = LocalEntityExtractorClient(llm_client=llm)
            client.available = llm.available  # type: ignore[attr-defined]
            return client
        except ImportError as exc:
            logger.warning('%s; entity extraction stays on the model router', exc)
            return llm

    def _server_driver(self) -> GraphDriver:
        s = self.settings
        if s.backend == 'neo4j':
            from .driver.neo4j_driver import Neo4jDriver

            return Neo4jDriver(s.url, s.user or 'neo4j', s.password, database=s.database or 'neo4j')
        if s.backend == 'falkordb':
            from .driver.falkordb_driver import FalkorDriver

            rest = s.url.split('://', 1)[-1] if s.url else 'localhost:6379'
            host, _, port = rest.partition(':')
            return FalkorDriver(
                host=host or 'localhost',
                port=int((port or '6379').split('/')[0]),
                username=s.user or None,
                password=s.password or None,
                database=s.database or s.group_id,
            )
        if s.backend == 'neptune':
            from .driver.neptune_driver import NeptuneDriver

            return NeptuneDriver(host=s.url, aoss_host=s.search_host)
        raise ValueError(f'unknown temporal backend {s.backend!r} (use one of {", ".join(BACKENDS)})')

    def _reranker(self, llm: RouterLLMClient, embedder: EmbedderClient) -> CrossEncoderClient:
        choice = self.settings.reranker
        if choice == 'model' and self.available:
            return RouterRerankerClient(llm, LocalRerankerClient(embedder))
        if choice == 'bge':
            try:
                from .cross_encoder.bge_reranker_client import BGERerankerClient

                return BGERerankerClient()
            except ImportError as exc:
                logger.warning('%s; using the local reranker', exc)
        return LocalRerankerClient(embedder)

    async def _open(self) -> TemporalGraph:
        if self._driver_override is not None:
            driver = self._driver_override
            key = f'override:{id(driver)}'
        elif self.settings.backend == 'kuzu':
            driver = await asyncio.to_thread(
                _open_kuzu, self.store_path, self.settings.lock_wait_seconds
            )
            key = str(self.store_path.resolve())
        else:
            driver = self._server_driver()
            key = f'{self.settings.backend}:{self.settings.url}:{self.settings.database}'
        self._driver = driver
        embedder = self._embedder or LocalEmbedder()
        llm = self._llm()
        cross_encoder = self._cross_encoder or self._reranker(llm, embedder)
        engine = TemporalGraph(
            graph_driver=driver,
            llm_client=self._extraction_client(llm),
            embedder=embedder,
            cross_encoder=cross_encoder,
            store_raw_episode_content=self.settings.store_raw_episode_content,
            max_coroutines=self.settings.concurrency,
        )
        if key not in _migrated:
            await migrate(driver)
            await ensure_embedding_space(driver, embedder)
            _migrated.add(key)
        return engine

    async def _close(self) -> None:
        driver, self._driver, self._engine = self._driver, None, None
        if driver is None or self._driver_override is not None:
            return
        if self.settings.backend == 'kuzu':
            await _release_kuzu(self.store_path, driver, self.keep_open)
        else:
            await driver.close()

    def _lock(self) -> asyncio.Lock:
        loop_id = id(asyncio.get_running_loop())
        lock = self._locks.get(loop_id)
        if lock is None:
            self._locks = {loop_id: asyncio.Lock()}
            lock = self._locks[loop_id]
        return lock

    @asynccontextmanager
    async def session(self) -> AsyncIterator[TemporalGraph]:
        """Open the store (reentrant, safe for concurrent tasks) and yield the engine; closes it
        when the last user leaves unless ``keep_open`` is set."""
        async with self._lock():
            await asyncio.to_thread(self._state_lock.acquire)
            try:
                if self._engine is None:
                    self._engine = await self._open()
                self._depth += 1
                engine = self._engine
            finally:
                self._state_lock.release()
        try:
            yield engine
        finally:
            async with self._lock():
                await asyncio.to_thread(self._state_lock.acquire)
                try:
                    self._depth -= 1
                    if self._depth == 0 and not self.keep_open:
                        await self._close()
                finally:
                    self._state_lock.release()

    async def close(self) -> None:
        await self.queue.stop()
        self.keep_open = False
        if self._depth == 0:
            await self._close()

    async def __aenter__(self) -> TemporalService:
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.close()

    def _need_model(self, operation: str) -> None:
        if not self.available:
            raise ModelUnavailableError(operation)

    def _groups(self, group_ids: str | list[str] | None) -> list[str]:
        groups = ontology.coerce_group_ids(group_ids)
        return groups if groups is not None else [self.default_group]

    # ---- ingest ----------------------------------------------------------------------------------
    async def add_episode(
        self,
        name: str,
        episode_body: str | dict | list,
        *,
        source: str | EpisodeType = 'text',
        source_description: str = '',
        reference_time: str | datetime | None = None,
        group_id: str | None = None,
        uuid: str | None = None,
        update_communities: bool | None = None,
        entity_types: dict[str, type[BaseModel]] | None = None,
        excluded_entity_types: list[str] | None = None,
        previous_episode_uuids: list[str] | None = None,
        edge_types: dict[str, type[BaseModel]] | None = None,
        edge_type_map: dict[tuple[str, str], list[str]] | None = None,
        custom_extraction_instructions: str | None = None,
        saga: str | None = None,
        saga_previous_episode_uuid: str | None = None,
    ) -> dict[str, Any]:
        """Add one episode (``source``: text | json | message) and return what it produced."""
        self._need_model('adding an episode')
        body = episode_body if isinstance(episode_body, str) else json.dumps(episode_body)
        group = group_id or self.default_group
        validate_group_id(group)
        async with self.session() as engine:
            res = await engine.add_episode(
                name=name,
                episode_body=body,
                source_description=source_description,
                reference_time=ontology.parse_reference_time(reference_time) or _now(),
                source=_source(source),
                group_id=group,
                uuid=uuid,
                update_communities=(
                    self.settings.update_communities if update_communities is None else update_communities
                ),
                entity_types=entity_types if entity_types is not None else self.entity_types(),
                excluded_entity_types=excluded_entity_types,
                previous_episode_uuids=previous_episode_uuids,
                edge_types=edge_types if edge_types is not None else self.edge_types(),
                edge_type_map=edge_type_map if edge_type_map is not None else self.edge_type_map(),
                custom_extraction_instructions=(
                    custom_extraction_instructions or self.settings.custom_extraction_instructions or None
                ),
                saga=saga,
                saga_previous_episode_uuid=saga_previous_episode_uuid,
            )
        return {
            'episode': formatting.episode_result(res.episode, include_content=False),
            'nodes': [formatting.node_result(n) for n in res.nodes],
            'facts': [formatting.fact_result(e) for e in res.edges],
            'invalidated': [formatting.fact_result(e) for e in res.edges if e.invalid_at is not None],
            'communities': [formatting.community_result(c) for c in res.communities],
        }

    async def enqueue_episode(self, name: str, episode_body: str | dict | list, **kwargs: Any) -> dict:
        """Queue an episode for background processing (per-group ordering); returns immediately."""
        self._need_model('adding an episode')
        group = kwargs.get('group_id') or self.default_group
        validate_group_id(group)
        kwargs['group_id'] = group

        async def process() -> dict:
            return await self.add_episode(name, episode_body, **kwargs)

        position = await self.queue.add(group, process, label=name)
        return {'message': f"Episode '{name}' queued for processing in group '{group}'",
                'group_id': group, 'position': position}

    async def add_episodes_bulk(
        self,
        episodes: list[dict[str, Any]],
        *,
        group_id: str | None = None,
        saga: str | None = None,
        excluded_entity_types: list[str] | None = None,
        custom_extraction_instructions: str | None = None,
    ) -> dict[str, Any]:
        """Bulk ingest: ``episodes`` are ``{name, content|episode_body, source, source_description,
        reference_time, uuid}``; extraction and dedupe run across the whole batch."""
        self._need_model('bulk ingest')
        group = group_id or self.default_group
        validate_group_id(group)
        raw = [
            RawEpisode(
                name=e.get('name') or f'episode-{i}',
                uuid=e.get('uuid'),
                content=(lambda c: c if isinstance(c, str) else json.dumps(c))(
                    e.get('content', e.get('episode_body', ''))
                ),
                source_description=e.get('source_description', ''),
                source=_source(e.get('source')),
                reference_time=ontology.parse_reference_time(e.get('reference_time')) or _now(),
            )
            for i, e in enumerate(episodes)
        ]
        async with self.session() as engine:
            res = await engine.add_episode_bulk(
                raw,
                group_id=group,
                entity_types=self.entity_types(),
                excluded_entity_types=excluded_entity_types,
                edge_types=self.edge_types(),
                edge_type_map=self.edge_type_map(),
                custom_extraction_instructions=(
                    custom_extraction_instructions or self.settings.custom_extraction_instructions or None
                ),
                saga=saga,
            )
        return {
            'episodes': [formatting.episode_result(e, include_content=False) for e in res.episodes],
            'nodes': [formatting.node_result(n) for n in res.nodes],
            'facts': [formatting.fact_result(e) for e in res.edges],
        }

    async def add_messages(
        self,
        messages: list[dict[str, Any]],
        *,
        group_id: str | None = None,
        queue: bool = False,
    ) -> dict[str, Any]:
        """One ``message`` episode per chat message ``{content, role_type, role, name, uuid,
        timestamp, source_description}``, formatted ``role(role_type): content``."""
        self._need_model('adding messages')
        results = []
        for m in messages:
            kwargs = {
                'source': EpisodeType.message,
                'source_description': m.get('source_description', ''),
                'reference_time': m.get('timestamp'),
                'group_id': group_id,
                'uuid': m.get('uuid'),
            }
            body = f"{m.get('role') or ''}({m.get('role_type', 'user')}): {m.get('content', '')}"
            name = m.get('name') or f'message-{uuid4().hex[:8]}'
            if queue:
                results.append(await self.enqueue_episode(name, body, **kwargs))
            else:
                results.append(await self.add_episode(name, body, **kwargs))
        return {'message': f'{len(messages)} message(s) {"queued" if queue else "added"}', 'results': results}

    async def add_triplet(
        self,
        source_node_name: str,
        edge_name: str,
        fact: str,
        target_node_name: str,
        *,
        group_id: str | None = None,
        source_node_uuid: str | None = None,
        target_node_uuid: str | None = None,
        valid_at: str | datetime | None = None,
        invalid_at: str | datetime | None = None,
    ) -> dict[str, Any]:
        """Write one fact directly (no extraction); endpoints are resolved against existing
        entities and the fact is deduplicated / checked for contradictions."""
        self._need_model('adding a fact')
        group = group_id or self.default_group
        validate_group_id(group)
        now = _now()
        source = EntityNode(uuid=source_node_uuid or str(uuid4()), name=source_node_name,
                            group_id=group, created_at=now)
        target = EntityNode(uuid=target_node_uuid or str(uuid4()), name=target_node_name,
                            group_id=group, created_at=now)
        edge = EntityEdge(name=edge_name, fact=fact, group_id=group, source_node_uuid=source.uuid,
                          target_node_uuid=target.uuid, created_at=now,
                          valid_at=ontology.parse_reference_time(valid_at),
                          invalid_at=ontology.parse_reference_time(invalid_at))
        async with self.session() as engine:
            res = await engine.add_triplet(source, edge, target)
        return {'nodes': [formatting.node_result(n) for n in res.nodes],
                'facts': [formatting.fact_result(e) for e in res.edges]}

    async def add_entity_node(
        self, name: str, *, uuid: str | None = None, group_id: str | None = None, summary: str = ''
    ) -> dict[str, Any]:
        """Create (or overwrite) an entity directly; needs no model."""
        group = group_id or self.default_group
        validate_group_id(group)
        node = EntityNode(name=name, uuid=uuid or str(uuid4()), group_id=group, summary=summary,
                          created_at=_now())
        async with self.session() as engine:
            await node.generate_name_embedding(engine.embedder)
            await node.save(engine.driver)
        return formatting.node_result(node)

    # ---- retrieve --------------------------------------------------------------------------------
    async def search_facts(
        self,
        query: str,
        *,
        group_ids: str | list[str] | None = None,
        max_facts: int = 10,
        center_node_uuid: str | None = None,
        edge_types: list[str] | None = None,
        valid_at_after: str | datetime | None = None,
        valid_at_before: str | datetime | None = None,
        invalid_at_after: str | datetime | None = None,
        invalid_at_before: str | datetime | None = None,
        current_only: bool = False,
    ) -> list[dict[str, Any]]:
        """Hybrid (BM25 + vector + graph) fact search with validity-window filters."""
        if max_facts <= 0:
            raise ValueError('max_facts must be a positive integer')
        search_filter = ontology.build_fact_search_filters(
            edge_types, valid_at_after, valid_at_before, invalid_at_after, invalid_at_before,
            current_only=current_only,
        )
        async with self.session() as engine:
            edges = await engine.search(
                query,
                center_node_uuid=center_node_uuid,
                group_ids=self._groups(group_ids),
                num_results=max_facts,
                search_filter=search_filter,
            )
        return [formatting.fact_result(e) for e in edges]

    async def facts_at(
        self,
        when: str | datetime,
        query: str,
        *,
        group_ids: str | list[str] | None = None,
        max_facts: int = 10,
    ) -> list[dict[str, Any]]:
        """Facts that were true at ``when``: valid_at <= when and (no invalid_at or invalid_at > when)."""
        t = ontology.parse_reference_time(when)
        search_filter = SearchFilters(
            valid_at=[[DateFilter(date=t, comparison_operator=ComparisonOperator.less_than_equal)]],
            invalid_at=[
                [DateFilter(comparison_operator=ComparisonOperator.is_null)],
                [DateFilter(date=t, comparison_operator=ComparisonOperator.greater_than)],
            ],
        )
        async with self.session() as engine:
            edges = await engine.search(query, group_ids=self._groups(group_ids),
                                        num_results=max_facts, search_filter=search_filter)
        return [formatting.fact_result(e) for e in edges]

    async def search_nodes(
        self,
        query: str,
        *,
        group_ids: str | list[str] | None = None,
        max_nodes: int = 10,
        entity_types: list[str] | None = None,
        center_node_uuid: str | None = None,
    ) -> list[dict[str, Any]]:
        """Entity search (hybrid, optionally filtered by type and reranked around a node)."""
        config = (recipes.NODE_HYBRID_SEARCH_NODE_DISTANCE if center_node_uuid
                  else recipes.NODE_HYBRID_SEARCH_RRF).model_copy(deep=True)
        config.limit = max_nodes
        async with self.session() as engine:
            results = await engine.search_(
                query, config=config, group_ids=self._groups(group_ids),
                center_node_uuid=center_node_uuid, search_filter=SearchFilters(node_labels=entity_types),
            )
        return [formatting.node_result(n) for n in results.nodes[:max_nodes]]

    async def search(
        self,
        query: str,
        *,
        recipe: str | SearchConfig | dict = 'COMBINED_HYBRID_SEARCH_CROSS_ENCODER',
        group_ids: str | list[str] | None = None,
        limit: int | None = None,
        center_node_uuid: str | None = None,
        bfs_origin_node_uuids: list[str] | None = None,
        search_filter: SearchFilters | dict | None = None,
    ) -> dict[str, Any]:
        """Advanced search over facts, entities, episodes and communities with any recipe
        (``search_recipes()`` lists them) or a custom ``SearchConfig``."""
        config = self._recipe(recipe)
        if limit is not None:
            config.limit = limit
        if isinstance(search_filter, dict):
            search_filter = SearchFilters.model_validate(search_filter)
        async with self.session() as engine:
            res = await engine.search_(query, config=config, group_ids=self._groups(group_ids),
                                       center_node_uuid=center_node_uuid,
                                       bfs_origin_node_uuids=bfs_origin_node_uuids,
                                       search_filter=search_filter)
        return {
            'facts': [formatting.fact_result(e) for e in res.edges],
            'fact_scores': list(res.edge_reranker_scores),
            'nodes': [formatting.node_result(n) for n in res.nodes],
            'node_scores': list(res.node_reranker_scores),
            'episodes': [formatting.episode_result(e) for e in res.episodes],
            'episode_scores': list(res.episode_reranker_scores),
            'communities': [formatting.community_result(c) for c in res.communities],
            'community_scores': list(res.community_reranker_scores),
        }

    async def context(
        self,
        query: str,
        *,
        recipe: str | SearchConfig | dict = 'COMBINED_HYBRID_SEARCH_RRF',
        group_ids: str | list[str] | None = None,
        limit: int = 10,
        center_node_uuid: str | None = None,
    ) -> dict[str, Any]:
        """Facts (with validity dates), entities, episodes and communities for ``query`` rendered
        as one prompt-ready context block."""
        config = self._recipe(recipe)
        config.limit = limit
        async with self.session() as engine:
            res = await engine.search_(query, config=config, group_ids=self._groups(group_ids),
                                       center_node_uuid=center_node_uuid)
        return {'context': search_results_to_context_string(res).strip(),
                'facts': len(res.edges), 'entities': len(res.nodes), 'episodes': len(res.episodes),
                'communities': len(res.communities)}

    @staticmethod
    def _recipe(recipe: str | SearchConfig | dict) -> SearchConfig:
        if isinstance(recipe, SearchConfig):
            return recipe.model_copy(deep=True)
        if isinstance(recipe, dict):
            return SearchConfig.model_validate(recipe)
        config = getattr(recipes, str(recipe).upper(), None)
        if not isinstance(config, SearchConfig):
            raise ValueError(f'unknown search recipe {recipe!r}; one of {", ".join(search_recipes())}')
        return config.model_copy(deep=True)

    async def get_memory(
        self,
        messages: list[dict[str, Any]],
        *,
        group_id: str | None = None,
        max_facts: int = 10,
        center_node_uuid: str | None = None,
    ) -> list[dict[str, Any]]:
        """Facts relevant to a conversation: the messages are composed into one query."""
        query = ''.join(
            f"{m.get('role_type') or ''}({m.get('role') or ''}): {m.get('content', '')}\n" for m in messages
        )
        return await self.search_facts(query, group_ids=[group_id or self.default_group],
                                       max_facts=max_facts, center_node_uuid=center_node_uuid)

    async def get_episodes(
        self,
        *,
        group_ids: str | list[str] | None = None,
        last_n: int = 10,
        reference_time: str | datetime | None = None,
        source: str | None = None,
        saga: str | None = None,
    ) -> list[dict[str, Any]]:
        """The most recent episodes (before ``reference_time``, default now), newest first."""
        async with self.session() as engine:
            episodes = await engine.retrieve_episodes(
                ontology.parse_reference_time(reference_time) or _now(),
                last_n=last_n,
                group_ids=self._groups(group_ids),
                source=_source(source) if source else None,
                saga=saga,
            )
        episodes = sorted(episodes, key=lambda e: e.valid_at, reverse=True)
        return [formatting.episode_result(e) for e in episodes]

    async def get_episode_entities(self, episode_uuids: list[str]) -> dict[str, Any]:
        """Provenance: the entities and facts produced by the given episodes."""
        if not episode_uuids:
            raise ValueError('episode_uuids must contain at least one uuid')
        async with self.session() as engine:
            res = await engine.get_nodes_and_edges_by_episode(episode_uuids)
        return {'nodes': [formatting.node_result(n) for n in res.nodes],
                'facts': [formatting.fact_result(e) for e in res.edges]}

    async def get_entity_edge(self, uuid: str) -> dict[str, Any]:
        """One fact by uuid (raises EdgeNotFoundError)."""
        async with self.session() as engine:
            edge = await EntityEdge.get_by_uuid(engine.driver, uuid)
        return formatting.fact_result(edge)

    async def get_node(self, uuid: str) -> dict[str, Any]:
        """One entity with every fact it takes part in (raises NodeNotFoundError)."""
        async with self.session() as engine:
            node = await EntityNode.get_by_uuid(engine.driver, uuid)
            edges = await EntityEdge.get_by_node_uuid(engine.driver, uuid)
        edges.sort(key=lambda e: (e.valid_at or e.created_at), reverse=True)
        return {**formatting.node_result(node), 'facts': [formatting.fact_result(e) for e in edges]}

    async def list_facts(
        self, *, group_id: str | None = None, limit: int = 100, uuid_cursor: str | None = None
    ) -> list[dict[str, Any]]:
        """Page through a group's facts (uuid order, newest-looking first)."""
        async with self.session() as engine:
            try:
                edges = await EntityEdge.get_by_group_ids(
                    engine.driver, [group_id or self.default_group], limit=limit, uuid_cursor=uuid_cursor
                )
            except GroupsEdgesNotFoundError:
                edges = []
        return [formatting.fact_result(e) for e in edges]

    async def list_entities(
        self, *, group_id: str | None = None, limit: int = 100, uuid_cursor: str | None = None
    ) -> list[dict[str, Any]]:
        async with self.session() as engine:
            nodes = await EntityNode.get_by_group_ids(
                engine.driver, [group_id or self.default_group], limit=limit, uuid_cursor=uuid_cursor
            )
        return [formatting.node_result(n) for n in nodes]

    async def list_communities(self, *, group_id: str | None = None) -> list[dict[str, Any]]:
        async with self.session() as engine:
            communities = await CommunityNode.get_by_group_ids(engine.driver, [group_id or self.default_group])
        return [formatting.community_result(c) for c in communities]

    async def list_sagas(self, *, group_id: str | None = None) -> list[dict[str, Any]]:
        async with self.session() as engine:
            sagas = await SagaNode.get_by_group_ids(engine.driver, [group_id or self.default_group])
        return [formatting.saga_result(s) for s in sagas]

    # ---- maintenance -----------------------------------------------------------------------------
    async def delete_entity_edge(self, uuid: str) -> dict[str, Any]:
        async with self.session() as engine:
            edge = await EntityEdge.get_by_uuid(engine.driver, uuid)
            await edge.delete(engine.driver)
        return {'message': f'Entity edge {uuid} deleted', 'success': True}

    async def delete_episode(self, uuid: str) -> dict[str, Any]:
        """Delete an episode and the entities/facts only it created."""
        async with self.session() as engine:
            await engine.remove_episode(uuid)
        return {'message': f'Episode {uuid} deleted', 'success': True}

    async def delete_group(self, group_id: str) -> dict[str, Any]:
        """Delete every fact, entity, episode, community and saga of one namespace."""
        validate_group_id(group_id)
        async with self.session() as engine:
            await clear_data(engine.driver, group_ids=[group_id])
        return {'message': f'Group {group_id} deleted', 'success': True}

    async def clear(self, group_ids: str | list[str] | None = None, *, everything: bool = False) -> dict:
        """Clear the given groups (default: this project's group), or the whole store."""
        async with self.session() as engine:
            if everything:
                await clear_data(engine.driver)
                await engine.build_indices_and_constraints()
                return {'message': 'Graph cleared', 'success': True}
            groups = self._groups(group_ids)
            await clear_data(engine.driver, group_ids=groups)
        return {'message': f'Graph data cleared for {", ".join(groups)}', 'success': True}

    async def build_communities(self, group_ids: str | list[str] | None = None) -> dict[str, Any]:
        """Cluster entities (label propagation) and summarize each community."""
        self._need_model('building communities')
        async with self.session() as engine:
            communities, edges = await engine.build_communities(group_ids=self._groups(group_ids))
        return {'communities': [formatting.community_result(c) for c in communities],
                'community_count': len(communities), 'edge_count': len(edges)}

    async def summarize_saga(self, saga_name: str, *, group_id: str | None = None) -> dict[str, Any]:
        """Refresh the running summary of a saga (an ordered chain of episodes)."""
        self._need_model('summarizing a saga')
        group = group_id or self.default_group
        async with self.session() as engine:
            sagas = await SagaNode.get_by_group_ids(engine.driver, [group])
            match = next((s for s in sagas if s.name == saga_name), None)
            if match is None:
                raise ValueError(f"No saga named '{saga_name}' found in group '{group}'")
            saga = await engine.summarize_saga(match.uuid)
        return formatting.saga_result(saga)

    async def reembed(self, group_ids: str | list[str] | None = None) -> dict[str, Any]:
        """Recompute stored vectors with the current embedder."""
        from .migrations import reembed

        async with self.session() as engine:
            return await reembed(engine.driver, engine.embedder,
                                 ontology.coerce_group_ids(group_ids))

    async def status(self) -> dict[str, Any]:
        """Store health and counts for the default group."""
        out: dict[str, Any] = {'backend': self.settings.backend, 'group_id': self.default_group,
                               'model': self.available}
        if self.settings.backend == 'kuzu':
            out['path'] = str(self.store_path)
        try:
            async with self.session() as engine:
                out['counts'] = await _counts(engine.driver, self.default_group)
            out['status'] = 'ok'
        except Exception as exc:
            out['status'] = 'error'
            out['message'] = f'{type(exc).__name__}: {exc}'[:300]
        return out


async def _counts(driver: GraphDriver, group_id: str) -> dict[str, int]:
    queries = {
        'episodes': 'MATCH (n:Episodic) WHERE n.group_id = $g RETURN count(n) AS c',
        'entities': 'MATCH (n:Entity) WHERE n.group_id = $g RETURN count(n) AS c',
        'communities': 'MATCH (n:Community) WHERE n.group_id = $g RETURN count(n) AS c',
        'sagas': 'MATCH (n:Saga) WHERE n.group_id = $g RETURN count(n) AS c',
    }
    if driver.provider == GraphProvider.KUZU:
        facts = 'MATCH (e:RelatesToNode_) WHERE e.group_id = $g RETURN count(e) AS c'
        current = ('MATCH (e:RelatesToNode_) WHERE e.group_id = $g AND e.invalid_at IS NULL '
                   'AND e.expired_at IS NULL RETURN count(e) AS c')
    else:
        facts = 'MATCH ()-[e:RELATES_TO]->() WHERE e.group_id = $g RETURN count(e) AS c'
        current = ('MATCH ()-[e:RELATES_TO]->() WHERE e.group_id = $g AND e.invalid_at IS NULL '
                   'AND e.expired_at IS NULL RETURN count(e) AS c')
    queries['facts'] = facts
    queries['current_facts'] = current
    out = {}
    for name, q in queries.items():
        records, _, _ = await driver.execute_query(q, g=group_id, routing_='r')
        out[name] = int(records[0]['c']) if records else 0
    return out


def search_recipes() -> list[str]:
    """Names of the built-in search recipes (usable as ``recipe=`` in ``search``)."""
    return sorted(k for k, v in vars(recipes).items() if isinstance(v, SearchConfig))
