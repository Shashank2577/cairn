from abc import ABC, abstractmethod

from cairn.engines.temporal.driver.query_executor import QueryExecutor, Transaction
from cairn.engines.temporal.edges import NextEpisodeEdge


class NextEpisodeEdgeOperations(ABC):
    @abstractmethod
    async def save(
        self,
        executor: QueryExecutor,
        edge: NextEpisodeEdge,
        tx: Transaction | None = None,
    ) -> None: ...

    @abstractmethod
    async def save_bulk(
        self,
        executor: QueryExecutor,
        edges: list[NextEpisodeEdge],
        tx: Transaction | None = None,
        batch_size: int = 100,
    ) -> None: ...

    @abstractmethod
    async def delete(
        self,
        executor: QueryExecutor,
        edge: NextEpisodeEdge,
        tx: Transaction | None = None,
    ) -> None: ...

    @abstractmethod
    async def delete_by_uuids(
        self,
        executor: QueryExecutor,
        uuids: list[str],
        tx: Transaction | None = None,
    ) -> None: ...

    @abstractmethod
    async def get_by_uuid(
        self,
        executor: QueryExecutor,
        uuid: str,
    ) -> NextEpisodeEdge: ...

    @abstractmethod
    async def get_by_uuids(
        self,
        executor: QueryExecutor,
        uuids: list[str],
    ) -> list[NextEpisodeEdge]: ...

    @abstractmethod
    async def get_by_group_ids(
        self,
        executor: QueryExecutor,
        group_ids: list[str],
        limit: int | None = None,
        uuid_cursor: str | None = None,
    ) -> list[NextEpisodeEdge]: ...
