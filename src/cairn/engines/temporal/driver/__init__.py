"""Graph store drivers.

``KuzuDriver`` (embedded, the default) needs only the ``kuzu`` package. The server drivers are
optional and imported lazily so their client libraries are only needed when configured:
``Neo4jDriver`` (``neo4j``), ``FalkorDriver`` (``falkordb``), ``NeptuneDriver``
(``boto3``, ``langchain-aws``, ``opensearch-py``).
"""

from typing import Any

from .driver import GraphDriver, GraphProvider

__all__ = ['GraphDriver', 'GraphProvider', 'KuzuDriver', 'Neo4jDriver', 'FalkorDriver', 'NeptuneDriver']


def __getattr__(name: str) -> Any:
    if name == 'KuzuDriver':
        from .kuzu_driver import KuzuDriver

        return KuzuDriver
    if name == 'Neo4jDriver':
        from .neo4j_driver import Neo4jDriver

        return Neo4jDriver
    if name == 'FalkorDriver':
        from .falkordb_driver import FalkorDriver

        return FalkorDriver
    if name == 'NeptuneDriver':
        from .neptune_driver import NeptuneDriver

        return NeptuneDriver
    raise AttributeError(name)
