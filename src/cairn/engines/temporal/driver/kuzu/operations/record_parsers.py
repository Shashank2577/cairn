import json
from typing import Any

from cairn.engines.temporal.driver.record_parsers import entity_edge_from_record, entity_node_from_record
from cairn.engines.temporal.edges import EntityEdge
from cairn.engines.temporal.nodes import EntityNode


def parse_kuzu_entity_node(record: Any) -> EntityNode:
    """Parse a Kuzu entity node record, deserializing JSON attributes."""
    if isinstance(record.get('attributes'), str):
        try:
            record['attributes'] = json.loads(record['attributes'])
        except (json.JSONDecodeError, TypeError):
            record['attributes'] = {}
    elif record.get('attributes') is None:
        record['attributes'] = {}
    return entity_node_from_record(record)


def parse_kuzu_entity_edge(record: Any) -> EntityEdge:
    """Parse a Kuzu entity edge record, deserializing JSON attributes."""
    if isinstance(record.get('attributes'), str):
        try:
            record['attributes'] = json.loads(record['attributes'])
        except (json.JSONDecodeError, TypeError):
            record['attributes'] = {}
    elif record.get('attributes') is None:
        record['attributes'] = {}
    return entity_edge_from_record(record)
