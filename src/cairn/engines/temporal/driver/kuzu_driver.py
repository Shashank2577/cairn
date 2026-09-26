"""Embedded graph store (the default): a Kuzu database in a local folder, no server needed.

Full-text search uses Kuzu's bundled FTS extension and vector similarity uses
``array_cosine_similarity``, so hybrid search works offline.
"""

import gc
import logging
from typing import Any

import kuzu

from cairn.engines.temporal.driver.driver import GraphDriver, GraphDriverSession, GraphProvider
from cairn.engines.temporal.driver.kuzu.operations.community_edge_ops import KuzuCommunityEdgeOperations
from cairn.engines.temporal.driver.kuzu.operations.community_node_ops import KuzuCommunityNodeOperations
from cairn.engines.temporal.driver.kuzu.operations.entity_edge_ops import KuzuEntityEdgeOperations
from cairn.engines.temporal.driver.kuzu.operations.entity_node_ops import KuzuEntityNodeOperations
from cairn.engines.temporal.driver.kuzu.operations.episode_node_ops import KuzuEpisodeNodeOperations
from cairn.engines.temporal.driver.kuzu.operations.episodic_edge_ops import KuzuEpisodicEdgeOperations
from cairn.engines.temporal.driver.kuzu.operations.graph_ops import KuzuGraphMaintenanceOperations
from cairn.engines.temporal.driver.kuzu.operations.has_episode_edge_ops import KuzuHasEpisodeEdgeOperations
from cairn.engines.temporal.driver.kuzu.operations.next_episode_edge_ops import (
    KuzuNextEpisodeEdgeOperations,
)
from cairn.engines.temporal.driver.kuzu.operations.saga_node_ops import KuzuSagaNodeOperations
from cairn.engines.temporal.driver.kuzu.operations.search_ops import KuzuSearchOperations
from cairn.engines.temporal.driver.operations.community_edge_ops import CommunityEdgeOperations
from cairn.engines.temporal.driver.operations.community_node_ops import CommunityNodeOperations
from cairn.engines.temporal.driver.operations.entity_edge_ops import EntityEdgeOperations
from cairn.engines.temporal.driver.operations.entity_node_ops import EntityNodeOperations
from cairn.engines.temporal.driver.operations.episode_node_ops import EpisodeNodeOperations
from cairn.engines.temporal.driver.operations.episodic_edge_ops import EpisodicEdgeOperations
from cairn.engines.temporal.driver.operations.graph_ops import GraphMaintenanceOperations
from cairn.engines.temporal.driver.operations.has_episode_edge_ops import HasEpisodeEdgeOperations
from cairn.engines.temporal.driver.operations.next_episode_edge_ops import NextEpisodeEdgeOperations
from cairn.engines.temporal.driver.operations.saga_node_ops import SagaNodeOperations
from cairn.engines.temporal.driver.operations.search_ops import SearchOperations
from cairn.engines.temporal.graph_queries import get_fulltext_indices

logger = logging.getLogger(__name__)

# Kuzu requires an explicit schema.
# As Kuzu currently does not support creating full text indexes on edge properties,
# we work around this by representing (n:Entity)-[:RELATES_TO]->(m:Entity) as
# (n)-[:RELATES_TO]->(e:RelatesToNode_)-[:RELATES_TO]->(m).
SCHEMA_QUERIES = """
    CREATE NODE TABLE IF NOT EXISTS Episodic (
        uuid STRING PRIMARY KEY,
        name STRING,
        group_id STRING,
        created_at TIMESTAMP,
        source STRING,
        source_description STRING,
        content STRING,
        valid_at TIMESTAMP,
        entity_edges STRING[]
    );
    CREATE NODE TABLE IF NOT EXISTS Entity (
        uuid STRING PRIMARY KEY,
        name STRING,
        group_id STRING,
        labels STRING[],
        created_at TIMESTAMP,
        name_embedding FLOAT[],
        summary STRING,
        attributes STRING
    );
    CREATE NODE TABLE IF NOT EXISTS Community (
        uuid STRING PRIMARY KEY,
        name STRING,
        group_id STRING,
        created_at TIMESTAMP,
        name_embedding FLOAT[],
        summary STRING
    );
    CREATE NODE TABLE IF NOT EXISTS RelatesToNode_ (
        uuid STRING PRIMARY KEY,
        group_id STRING,
        created_at TIMESTAMP,
        name STRING,
        fact STRING,
        fact_embedding FLOAT[],
        episodes STRING[],
        expired_at TIMESTAMP,
        valid_at TIMESTAMP,
        invalid_at TIMESTAMP,
        reference_time TIMESTAMP,
        attributes STRING
    );
    CREATE REL TABLE IF NOT EXISTS RELATES_TO(
        FROM Entity TO RelatesToNode_,
        FROM RelatesToNode_ TO Entity
    );
    CREATE REL TABLE IF NOT EXISTS MENTIONS(
        FROM Episodic TO Entity,
        uuid STRING PRIMARY KEY,
        group_id STRING,
        created_at TIMESTAMP
    );
    CREATE REL TABLE IF NOT EXISTS HAS_MEMBER(
        FROM Community TO Entity,
        FROM Community TO Community,
        uuid STRING,
        group_id STRING,
        created_at TIMESTAMP
    );
    CREATE NODE TABLE IF NOT EXISTS Saga (
        uuid STRING PRIMARY KEY,
        name STRING,
        group_id STRING,
        created_at TIMESTAMP,
        summary STRING,
        first_episode_uuid STRING,
        last_episode_uuid STRING,
        last_summarized_at TIMESTAMP,
        last_summarized_episode_valid_at TIMESTAMP
    );
    CREATE REL TABLE IF NOT EXISTS HAS_EPISODE(
        FROM Saga TO Episodic,
        uuid STRING,
        group_id STRING,
        created_at TIMESTAMP
    );
    CREATE REL TABLE IF NOT EXISTS NEXT_EPISODE(
        FROM Episodic TO Episodic,
        uuid STRING,
        group_id STRING,
        created_at TIMESTAMP
    );
    CREATE NODE TABLE IF NOT EXISTS StoreMeta_ (
        key STRING PRIMARY KEY,
        value STRING
    );
"""


class KuzuDriver(GraphDriver):
    provider: GraphProvider = GraphProvider.KUZU
    aoss_client: None = None

    def __init__(
        self,
        db: str = ':memory:',
        max_concurrent_queries: int = 1,
        read_only: bool = False,
    ):
        super().__init__()
        self.path = db
        # Kuzu keeps every group in one database; group ids are a property filter, so the
        # request-scoped "database" is never switched (see TemporalGraph._resolve_request_scope).
        self._database = ''
        self.read_only = read_only
        self.db = kuzu.Database(db, read_only=read_only)

        if not read_only:
            self.setup_schema()

        self.client = kuzu.AsyncConnection(self.db, max_concurrent_queries=max_concurrent_queries)

        # Instantiate Kuzu operations
        self._entity_node_ops = KuzuEntityNodeOperations()
        self._episode_node_ops = KuzuEpisodeNodeOperations()
        self._community_node_ops = KuzuCommunityNodeOperations()
        self._saga_node_ops = KuzuSagaNodeOperations()
        self._entity_edge_ops = KuzuEntityEdgeOperations()
        self._episodic_edge_ops = KuzuEpisodicEdgeOperations()
        self._community_edge_ops = KuzuCommunityEdgeOperations()
        self._has_episode_edge_ops = KuzuHasEpisodeEdgeOperations()
        self._next_episode_edge_ops = KuzuNextEpisodeEdgeOperations()
        self._search_ops = KuzuSearchOperations()
        self._graph_ops = KuzuGraphMaintenanceOperations()

    # --- Operations properties ---

    @property
    def entity_node_ops(self) -> EntityNodeOperations:
        return self._entity_node_ops

    @property
    def episode_node_ops(self) -> EpisodeNodeOperations:
        return self._episode_node_ops

    @property
    def community_node_ops(self) -> CommunityNodeOperations:
        return self._community_node_ops

    @property
    def saga_node_ops(self) -> SagaNodeOperations:
        return self._saga_node_ops

    @property
    def entity_edge_ops(self) -> EntityEdgeOperations:
        return self._entity_edge_ops

    @property
    def episodic_edge_ops(self) -> EpisodicEdgeOperations:
        return self._episodic_edge_ops

    @property
    def community_edge_ops(self) -> CommunityEdgeOperations:
        return self._community_edge_ops

    @property
    def has_episode_edge_ops(self) -> HasEpisodeEdgeOperations:
        return self._has_episode_edge_ops

    @property
    def next_episode_edge_ops(self) -> NextEpisodeEdgeOperations:
        return self._next_episode_edge_ops

    @property
    def search_ops(self) -> SearchOperations:
        return self._search_ops

    @property
    def graph_ops(self) -> GraphMaintenanceOperations:
        return self._graph_ops

    async def execute_query(
        self, cypher_query_: str, **kwargs: Any
    ) -> tuple[list[dict[str, Any]] | list[list[dict[str, Any]]], None, None]:
        params = {k: v for k, v in kwargs.items() if v is not None}
        # Kuzu does not support these parameters.
        params.pop('database_', None)
        params.pop('routing_', None)

        try:
            results = await self.client.execute(cypher_query_, parameters=params)
        except Exception as e:
            params = {k: (v[:5] if isinstance(v, list) else v) for k, v in params.items()}
            logger.error(f'Error executing Kuzu query: {e}\n{cypher_query_}\n{params}')
            raise

        if not results:
            return [], None, None

        # Read everything, then close the results right away: a result object that outlives
        # the database (closed in ``close()``) would crash when it is finally collected.
        batch = results if isinstance(results, list) else [results]
        try:
            rows = [list(result.rows_as_dict()) for result in batch]
        finally:
            for result in batch:
                try:
                    result.close()
                except Exception:
                    pass
        dict_results = rows if isinstance(results, list) else rows[0]
        return dict_results, None, None  # type: ignore

    def session(self, _database: str | None = None) -> GraphDriverSession:
        return KuzuDriverSession(self)

    async def close(self):
        # Release the file lock so the store can be reopened (by this or another process).
        # Collect first so no stray result/connection object outlives the database.
        gc.collect()
        client, db = getattr(self, 'client', None), getattr(self, 'db', None)
        self.client = None  # type: ignore[assignment]
        if client is not None:
            try:
                client.close()
            except Exception:  # already closed
                pass
        if db is not None:
            try:
                db.close()
            except Exception:
                pass
            self.db = None  # type: ignore[assignment]

    async def delete_all_indexes(self, database_: str | None = None):
        for label, name in _existing_fts_indexes(self.db):
            await self.execute_query(f"CALL DROP_FTS_INDEX('{label}', '{name}')")

    async def build_indices_and_constraints(self, delete_existing: bool = False):
        # The node/rel tables are created in setup_schema(); the full-text indices are
        # (re)built here. Kuzu keeps FTS indices up to date on insert/update/delete.
        if delete_existing:
            await self.delete_all_indexes()
        existing = {name for _, name in _existing_fts_indexes(self.db)}
        for query in get_fulltext_indices(GraphProvider.KUZU):
            name = query.split("'")[3]
            if name not in existing:
                await self.execute_query(query)

    def setup_schema(self):
        conn = kuzu.Connection(self.db)
        try:
            _run(conn, SCHEMA_QUERIES)
            existing = {name for _, name in _existing_fts_indexes(self.db, conn)}
            for query in get_fulltext_indices(GraphProvider.KUZU):
                name = query.split("'")[3]
                if name not in existing:
                    _run(conn, query)
        finally:
            conn.close()


def _run(conn: Any, query: str) -> list[list[Any]]:
    """Execute on a sync connection, read all rows and close every result object."""
    results = conn.execute(query)
    batch = results if isinstance(results, list) else [results]
    try:
        return [row for result in batch for row in result.get_all()]
    finally:
        for result in batch:
            try:
                result.close()
            except Exception:
                pass


def _existing_fts_indexes(db: Any, conn: Any = None) -> list[tuple[str, str]]:
    own = conn is None
    conn = conn or kuzu.Connection(db)
    try:
        rows = _run(conn, 'CALL SHOW_INDEXES() RETURN *')
    finally:
        if own:
            conn.close()
    return [(r[0], r[1]) for r in rows if len(r) > 2 and r[2] == 'FTS']


class KuzuDriverSession(GraphDriverSession):
    provider = GraphProvider.KUZU

    def __init__(self, driver: KuzuDriver):
        self.driver = driver

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        # No cleanup needed for Kuzu, but method must exist.
        pass

    async def close(self):
        # Do not close the session here, as we're reusing the driver connection.
        pass

    async def execute_write(self, func, *args, **kwargs):
        # Directly await the provided async function with `self` as the transaction/session
        return await func(self, *args, **kwargs)

    async def run(self, query: str | list, **kwargs: Any) -> Any:
        if isinstance(query, list):
            for cypher, params in query:
                await self.driver.execute_query(cypher, **params)
        else:
            await self.driver.execute_query(query, **kwargs)
        return None
