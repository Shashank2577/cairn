"""Store migrations: bring an existing graph store up to the current layout.

Each store records its layout version and the embedding space its vectors were made with in
``StoreMeta_`` rows (a table in the embedded store, plain nodes on graph servers). ``migrate``
applies the pending steps in order; ``ensure_embedding_space`` re-embeds every entity name, fact
and community when the embedder changed (for example the local model became available after the
hashing fallback was used), because vectors from different spaces must never be compared.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from ..driver.driver import GraphDriver, GraphProvider
from ..edges import EntityEdge
from ..errors import GroupsEdgesNotFoundError
from ..nodes import CommunityNode, EntityNode

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 2
META_LABEL = 'StoreMeta_'


async def get_meta(driver: GraphDriver, key: str) -> str | None:
    records, _, _ = await driver.execute_query(
        f'MATCH (m:{META_LABEL} {{key: $key}}) RETURN m.value AS value', key=key, routing_='r'
    )
    return records[0]['value'] if records else None


async def set_meta(driver: GraphDriver, key: str, value: str) -> None:
    await driver.execute_query(
        f'MERGE (m:{META_LABEL} {{key: $key}}) SET m.value = $value', key=key, value=value
    )


# ---- steps ---------------------------------------------------------------------------------------
async def _v1_indices(driver: GraphDriver) -> None:
    """Full-text and range indices (the embedded store builds its FTS indices here too)."""
    await driver.build_indices_and_constraints()


async def _v2_saga_columns(driver: GraphDriver) -> None:
    """Saga summaries: stores created before sagas carried summaries get the columns added."""
    if driver.provider != GraphProvider.KUZU:
        return  # schemaless servers need nothing
    for column, kind in (
        ('summary', 'STRING'),
        ('first_episode_uuid', 'STRING'),
        ('last_episode_uuid', 'STRING'),
        ('last_summarized_at', 'TIMESTAMP'),
        ('last_summarized_episode_valid_at', 'TIMESTAMP'),
    ):
        await driver.execute_query(f'ALTER TABLE Saga ADD IF NOT EXISTS {column} {kind}')


STEPS: list[tuple[int, str, Callable[[GraphDriver], Awaitable[None]]]] = [
    (1, 'indices', _v1_indices),
    (2, 'saga columns', _v2_saga_columns),
]


async def migrate(driver: GraphDriver) -> list[str]:
    """Apply every step newer than the store's recorded version. Returns the applied step names."""
    try:
        current = int(await get_meta(driver, 'schema_version') or 0)
    except Exception:  # meta table missing on a very old embedded store
        current = 0
    applied: list[str] = []
    for version, name, step in STEPS:
        if version <= current:
            continue
        await step(driver)
        await set_meta(driver, 'schema_version', str(version))
        applied.append(name)
        logger.info('temporal store migrated to v%d (%s)', version, name)
    return applied


# ---- embedding space -----------------------------------------------------------------------------
async def _group_ids(driver: GraphDriver) -> list[str]:
    records, _, _ = await driver.execute_query(
        'MATCH (n:Entity) RETURN DISTINCT n.group_id AS group_id', routing_='r'
    )
    groups = {r['group_id'] for r in records if r.get('group_id') is not None}
    records, _, _ = await driver.execute_query(
        'MATCH (n:Community) RETURN DISTINCT n.group_id AS group_id', routing_='r'
    )
    groups |= {r['group_id'] for r in records if r.get('group_id') is not None}
    return sorted(groups)


async def reembed(driver: GraphDriver, embedder: Any, group_ids: list[str] | None = None) -> dict:
    """Recompute every stored vector (entity names, facts, community names) with ``embedder``."""
    groups = group_ids if group_ids is not None else await _group_ids(driver)
    counts = {'nodes': 0, 'edges': 0, 'communities': 0}
    for group_id in groups:
        nodes = await EntityNode.get_by_group_ids(driver, [group_id])
        for node in nodes:
            await node.generate_name_embedding(embedder)
            await node.save(driver)
        counts['nodes'] += len(nodes)
        try:
            edges = await EntityEdge.get_by_group_ids(driver, [group_id])
        except GroupsEdgesNotFoundError:
            edges = []
        for edge in edges:
            await edge.generate_embedding(embedder)
            await edge.save(driver)
        counts['edges'] += len(edges)
        communities = await CommunityNode.get_by_group_ids(driver, [group_id])
        for community in communities:
            await community.generate_name_embedding(embedder)
            await community.save(driver)
        counts['communities'] += len(communities)
    return counts


async def ensure_embedding_space(driver: GraphDriver, embedder: Any) -> dict | None:
    """Re-embed the store when the embedder identity changed. Returns counts when it did."""
    space = str(getattr(embedder, 'embedder_id', '') or type(embedder).__name__)
    recorded = await get_meta(driver, 'embedder')
    if recorded == space:
        return None
    counts = None
    if recorded is not None:
        logger.info('embedding space changed (%s -> %s); re-embedding the temporal store', recorded, space)
        counts = await reembed(driver, embedder)
    await set_meta(driver, 'embedder', space)
    return counts
