"""JSON-ready shapes for the temporal graph's public API (CLI, MCP tools, HTTP, UI).

Embedding vectors are always dropped; datetimes are ISO-8601 UTC strings.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from .edges import EntityEdge
from .nodes import CommunityNode, EntityNode, EpisodicNode, SagaNode


def iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def _clean_attributes(attributes: dict[str, Any] | None) -> dict[str, Any]:
    return {k: v for k, v in (attributes or {}).items() if 'embedding' not in k.lower()}


def node_result(node: EntityNode) -> dict[str, Any]:
    return {
        'uuid': node.uuid,
        'name': node.name,
        'labels': list(node.labels or []),
        'summary': node.summary,
        'group_id': node.group_id,
        'created_at': iso(node.created_at),
        'attributes': _clean_attributes(node.attributes),
    }


def fact_result(edge: EntityEdge) -> dict[str, Any]:
    """A fact with its validity window: ``valid_at`` (became true), ``invalid_at`` (stopped being
    true), ``expired_at`` (when the graph learned it stopped being true)."""
    return {
        'uuid': edge.uuid,
        'name': edge.name,
        'fact': edge.fact,
        'source_node_uuid': edge.source_node_uuid,
        'target_node_uuid': edge.target_node_uuid,
        'group_id': edge.group_id,
        'episodes': list(edge.episodes or []),
        'created_at': iso(edge.created_at),
        'valid_at': iso(edge.valid_at),
        'invalid_at': iso(edge.invalid_at),
        'expired_at': iso(edge.expired_at),
        'reference_time': iso(edge.reference_time),
        'attributes': _clean_attributes(edge.attributes),
        'current': edge.invalid_at is None and edge.expired_at is None,
    }


def episode_result(episode: EpisodicNode, include_content: bool = True) -> dict[str, Any]:
    source = episode.source.value if hasattr(episode.source, 'value') else str(episode.source)
    out = {
        'uuid': episode.uuid,
        'name': episode.name,
        'group_id': episode.group_id,
        'source': source,
        'source_description': episode.source_description,
        'created_at': iso(episode.created_at),
        'valid_at': iso(episode.valid_at),
        'entity_edges': list(episode.entity_edges or []),
    }
    if include_content:
        out['content'] = episode.content
    return out


def community_result(community: CommunityNode) -> dict[str, Any]:
    return {
        'uuid': community.uuid,
        'name': community.name,
        'group_id': community.group_id,
        'summary': community.summary,
        'created_at': iso(community.created_at),
    }


def saga_result(saga: SagaNode) -> dict[str, Any]:
    return {
        'uuid': saga.uuid,
        'name': saga.name,
        'group_id': saga.group_id,
        'summary': saga.summary,
        'created_at': iso(saga.created_at),
        'first_episode_uuid': saga.first_episode_uuid,
        'last_episode_uuid': saga.last_episode_uuid,
        'last_summarized_at': iso(saga.last_summarized_at),
        'last_summarized_episode_valid_at': iso(saga.last_summarized_episode_valid_at),
    }
