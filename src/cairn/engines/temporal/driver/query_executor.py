from abc import ABC, abstractmethod
from typing import Any


class Transaction(ABC):
    """Minimal transaction interface yielded by GraphDriver.transaction().

    For drivers with real transaction support (e.g., Neo4j), this wraps a native
    transaction with commit/rollback semantics. For drivers without transaction
    support, this is a thin wrapper where queries execute immediately.
    """

    @abstractmethod
    async def run(self, query: str, **kwargs: Any) -> Any: ...


class QueryExecutor(ABC):
    """Slim interface for executing queries against a graph database.

    GraphDriver extends this. Operations ABCs depend only on QueryExecutor
    (not GraphDriver), which avoids circular imports.
    """

    @abstractmethod
    async def execute_query(self, cypher_query_: str, **kwargs: Any) -> Any: ...
