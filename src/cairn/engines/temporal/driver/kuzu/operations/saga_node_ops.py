import logging
from typing import Any

from cairn.engines.temporal.driver.driver import GraphProvider
from cairn.engines.temporal.driver.operations.saga_node_ops import SagaNodeOperations
from cairn.engines.temporal.driver.query_executor import QueryExecutor, Transaction
from cairn.engines.temporal.errors import NodeNotFoundError
from cairn.engines.temporal.models.nodes.node_db_queries import SAGA_NODE_RETURN, get_saga_node_save_query
from cairn.engines.temporal.nodes import SagaNode, get_saga_node_from_record

logger = logging.getLogger(__name__)


def _saga_node_from_record(record: Any) -> SagaNode:
    return get_saga_node_from_record(record)


class KuzuSagaNodeOperations(SagaNodeOperations):
    async def save(
        self,
        executor: QueryExecutor,
        node: SagaNode,
        tx: Transaction | None = None,
    ) -> None:
        query = get_saga_node_save_query(GraphProvider.KUZU)
        params: dict[str, Any] = {
            'uuid': node.uuid,
            'name': node.name,
            'group_id': node.group_id,
            'created_at': node.created_at,
            'summary': node.summary,
            'first_episode_uuid': node.first_episode_uuid,
            'last_episode_uuid': node.last_episode_uuid,
            'last_summarized_at': node.last_summarized_at,
            'last_summarized_episode_valid_at': node.last_summarized_episode_valid_at,
        }
        if tx is not None:
            await tx.run(query, **params)
        else:
            await executor.execute_query(query, **params)

        logger.debug(f'Saved Saga Node to Graph: {node.uuid}')

    async def save_bulk(
        self,
        executor: QueryExecutor,
        nodes: list[SagaNode],
        tx: Transaction | None = None,
        batch_size: int = 100,
    ) -> None:
        # Kuzu doesn't support UNWIND - iterate and save individually
        for node in nodes:
            await self.save(executor, node, tx=tx)

    async def delete(
        self,
        executor: QueryExecutor,
        node: SagaNode,
        tx: Transaction | None = None,
    ) -> None:
        query = """
            MATCH (n:Saga {uuid: $uuid})
            DETACH DELETE n
        """
        if tx is not None:
            await tx.run(query, uuid=node.uuid)
        else:
            await executor.execute_query(query, uuid=node.uuid)

        logger.debug(f'Deleted Node: {node.uuid}')

    async def delete_by_group_id(
        self,
        executor: QueryExecutor,
        group_id: str,
        tx: Transaction | None = None,
        batch_size: int = 100,
    ) -> None:
        # Kuzu doesn't support IN TRANSACTIONS OF - simple delete
        query = """
            MATCH (n:Saga {group_id: $group_id})
            DETACH DELETE n
        """
        if tx is not None:
            await tx.run(query, group_id=group_id)
        else:
            await executor.execute_query(query, group_id=group_id)

    async def delete_by_uuids(
        self,
        executor: QueryExecutor,
        uuids: list[str],
        tx: Transaction | None = None,
        batch_size: int = 100,
    ) -> None:
        # Kuzu doesn't support IN TRANSACTIONS OF - simple delete
        query = """
            MATCH (n:Saga)
            WHERE n.uuid IN $uuids
            DETACH DELETE n
        """
        if tx is not None:
            await tx.run(query, uuids=uuids)
        else:
            await executor.execute_query(query, uuids=uuids)

    async def get_by_uuid(
        self,
        executor: QueryExecutor,
        uuid: str,
    ) -> SagaNode:
        query = (
            """
            MATCH (s:Saga {uuid: $uuid})
            RETURN
            """
            + SAGA_NODE_RETURN
        )
        records, _, _ = await executor.execute_query(query, uuid=uuid)
        nodes = [_saga_node_from_record(r) for r in records]
        if len(nodes) == 0:
            raise NodeNotFoundError(uuid)
        return nodes[0]

    async def get_by_uuids(
        self,
        executor: QueryExecutor,
        uuids: list[str],
    ) -> list[SagaNode]:
        query = (
            """
            MATCH (s:Saga)
            WHERE s.uuid IN $uuids
            RETURN
            """
            + SAGA_NODE_RETURN
        )
        records, _, _ = await executor.execute_query(query, uuids=uuids)
        return [_saga_node_from_record(r) for r in records]

    async def get_by_group_ids(
        self,
        executor: QueryExecutor,
        group_ids: list[str],
        limit: int | None = None,
        uuid_cursor: str | None = None,
    ) -> list[SagaNode]:
        cursor_clause = 'AND s.uuid < $uuid' if uuid_cursor else ''
        limit_clause = 'LIMIT $limit' if limit is not None else ''
        query = (
            """
            MATCH (s:Saga)
            WHERE s.group_id IN $group_ids
            """
            + cursor_clause
            + """
            RETURN
            """
            + SAGA_NODE_RETURN
            + """
            ORDER BY s.uuid DESC
            """
            + limit_clause
        )
        records, _, _ = await executor.execute_query(
            query,
            group_ids=group_ids,
            uuid=uuid_cursor,
            limit=limit,
        )
        return [_saga_node_from_record(r) for r in records]
