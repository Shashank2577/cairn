import logging
from typing import Any

from cairn.engines.temporal.driver.driver import GraphProvider
from cairn.engines.temporal.driver.kuzu.operations.record_parsers import parse_kuzu_entity_node
from cairn.engines.temporal.driver.operations.graph_ops import GraphMaintenanceOperations
from cairn.engines.temporal.driver.operations.graph_utils import Neighbor, label_propagation
from cairn.engines.temporal.driver.query_executor import QueryExecutor
from cairn.engines.temporal.driver.record_parsers import community_node_from_record
from cairn.engines.temporal.graph_queries import get_fulltext_indices, get_range_indices
from cairn.engines.temporal.helpers import semaphore_gather
from cairn.engines.temporal.models.nodes.node_db_queries import (
    COMMUNITY_NODE_RETURN,
    get_entity_node_return_query,
)
from cairn.engines.temporal.nodes import CommunityNode, EntityNode, EpisodicNode

logger = logging.getLogger(__name__)


class KuzuGraphMaintenanceOperations(GraphMaintenanceOperations):
    async def clear_data(
        self,
        executor: QueryExecutor,
        group_ids: list[str] | None = None,
    ) -> None:
        if group_ids is None:
            await executor.execute_query('MATCH (n) DETACH DELETE n')
        else:
            # Kuzu requires deleting RelatesToNode_ intermediates in addition to
            # Entity, Episodic, and Community nodes.
            for label in ['RelatesToNode_', 'Entity', 'Episodic', 'Community', 'Saga']:
                await executor.execute_query(
                    f"""
                    MATCH (n:{label})
                    WHERE n.group_id IN $group_ids
                    DETACH DELETE n
                    """,
                    group_ids=group_ids,
                )

    async def build_indices_and_constraints(
        self,
        executor: QueryExecutor,
        delete_existing: bool = False,
    ) -> None:
        # Kuzu schema is static (created in setup_schema), so range indices
        # return an empty list. Only FTS indices need to be created here; the
        # driver skips the ones that already exist (creating one twice is an error).
        build = getattr(executor, 'build_indices_and_constraints', None)
        if build is not None:
            await build(delete_existing)
            return

        if delete_existing:
            await self.delete_all_indexes(executor)
        range_indices = get_range_indices(GraphProvider.KUZU)
        fulltext_indices = get_fulltext_indices(GraphProvider.KUZU)
        index_queries = range_indices + fulltext_indices

        await semaphore_gather(*[executor.execute_query(q) for q in index_queries])

    async def delete_all_indexes(
        self,
        executor: QueryExecutor,
    ) -> None:
        drop = getattr(executor, 'delete_all_indexes', None)
        if drop is not None:
            await drop()

    async def get_community_clusters(
        self,
        executor: QueryExecutor,
        group_ids: list[str] | None = None,
    ) -> list[Any]:
        community_clusters: list[list[EntityNode]] = []

        if group_ids is None:
            group_id_values, _, _ = await executor.execute_query(
                """
                MATCH (n:Entity)
                WHERE n.group_id IS NOT NULL
                RETURN
                    collect(DISTINCT n.group_id) AS group_ids
                """
            )
            group_ids = group_id_values[0]['group_ids'] if group_id_values else []

        resolved_group_ids: list[str] = group_ids or []
        for group_id in resolved_group_ids:
            projection: dict[str, list[Neighbor]] = {}

            # Get all entity nodes for this group
            node_records, _, _ = await executor.execute_query(
                """
                MATCH (n:Entity)
                WHERE n.group_id IN $group_ids
                RETURN
                """
                + get_entity_node_return_query(GraphProvider.KUZU),
                group_ids=[group_id],
            )
            nodes = [parse_kuzu_entity_node(r) for r in node_records]

            for node in nodes:
                # Kuzu edges are modeled through RelatesToNode_ intermediate nodes
                records, _, _ = await executor.execute_query(
                    """
                    MATCH (n:Entity {group_id: $group_id, uuid: $uuid})-[:RELATES_TO]->(:RelatesToNode_)-[:RELATES_TO]-(m:Entity {group_id: $group_id})
                    WITH count(*) AS count, m.uuid AS uuid
                    RETURN
                        uuid,
                        count
                    """,
                    uuid=node.uuid,
                    group_id=group_id,
                )

                projection[node.uuid] = [
                    Neighbor(node_uuid=record['uuid'], edge_count=record['count'])
                    for record in records
                ]

            cluster_uuids = label_propagation(projection)

            # Fetch full node objects for each cluster
            for cluster in cluster_uuids:
                if not cluster:
                    continue
                cluster_records, _, _ = await executor.execute_query(
                    """
                    MATCH (n:Entity)
                    WHERE n.uuid IN $uuids
                    RETURN
                    """
                    + get_entity_node_return_query(GraphProvider.KUZU),
                    uuids=cluster,
                )
                community_clusters.append([parse_kuzu_entity_node(r) for r in cluster_records])

        return community_clusters

    async def determine_entity_community(
        self,
        executor: QueryExecutor,
        entity: EntityNode,
    ) -> None:
        # Check if the node is already part of a community
        records, _, _ = await executor.execute_query(
            """
            MATCH (c:Community)-[:HAS_MEMBER]->(n:Entity {uuid: $entity_uuid})
            RETURN
            """
            + COMMUNITY_NODE_RETURN,
            entity_uuid=entity.uuid,
        )

        if len(records) > 0:
            return

        # If the node has no community, find the mode community of surrounding
        # entities. Kuzu uses RelatesToNode_ as an intermediate for RELATES_TO edges.
        records, _, _ = await executor.execute_query(
            """
            MATCH (c:Community)-[:HAS_MEMBER]->(m:Entity)-[:RELATES_TO]->(:RelatesToNode_)-[:RELATES_TO]-(n:Entity {uuid: $entity_uuid})
            RETURN
            """
            + COMMUNITY_NODE_RETURN,
            entity_uuid=entity.uuid,
        )

    async def get_mentioned_nodes(
        self,
        executor: QueryExecutor,
        episodes: list[EpisodicNode],
    ) -> list[EntityNode]:
        episode_uuids = [episode.uuid for episode in episodes]

        records, _, _ = await executor.execute_query(
            """
            MATCH (episode:Episodic)-[:MENTIONS]->(n:Entity)
            WHERE episode.uuid IN $uuids
            RETURN DISTINCT
            """
            + get_entity_node_return_query(GraphProvider.KUZU),
            uuids=episode_uuids,
        )

        return [parse_kuzu_entity_node(r) for r in records]

    async def get_communities_by_nodes(
        self,
        executor: QueryExecutor,
        nodes: list[EntityNode],
    ) -> list[CommunityNode]:
        node_uuids = [node.uuid for node in nodes]

        records, _, _ = await executor.execute_query(
            """
            MATCH (c:Community)-[:HAS_MEMBER]->(m:Entity)
            WHERE m.uuid IN $uuids
            RETURN DISTINCT
            """
            + COMMUNITY_NODE_RETURN,
            uuids=node_uuids,
        )

        return [community_node_from_record(r) for r in records]
