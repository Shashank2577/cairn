"""Tool and route table for mounting the temporal graph on Cairn's MCP server and HTTP API.

Every entry maps a public name to a ``TemporalService`` coroutine. ``call_tool`` / ``call_tool_sync``
dispatch by name with a JSON arguments object, so a server can register each tool with one
generic wrapper::

    svc = TemporalService(project, router, brain, keep_open=True)
    for spec in TOOLS:
        register(spec.name, spec.description, lambda args, s=spec: call_tool_sync(svc, s.name, args))
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .service import TemporalService, run_sync, search_recipes


@dataclass(frozen=True)
class ToolSpec:
    name: str
    method: str
    description: str
    params: dict[str, str] = field(default_factory=dict)  # name -> "type (default) description"
    writes: bool = False
    needs_model: bool = False
    http: tuple[str, str] | None = None  # (METHOD, path)


TOOLS: list[ToolSpec] = [
    ToolSpec(
        'cairn_timeline_add_episode', 'add_episode',
        'Add an episode (text, JSON or chat message) to the temporal graph. Entities and facts are '
        'extracted; contradicted facts are invalidated, not deleted. reference_time (ISO-8601) is '
        'when the described events happened.',
        {'name': 'str', 'episode_body': 'str', 'source': 'str (text) text|json|message',
         'source_description': 'str', 'reference_time': 'str|null', 'group_id': 'str|null',
         'uuid': 'str|null', 'excluded_entity_types': 'list[str]|null',
         'custom_extraction_instructions': 'str|null', 'previous_episode_uuids': 'list[str]|null',
         'update_communities': 'bool|null', 'saga': 'str|null', 'saga_previous_episode_uuid': 'str|null'},
        writes=True, needs_model=True, http=('POST', '/api/timeline/episodes'),
    ),
    ToolSpec(
        'cairn_timeline_enqueue_episode', 'enqueue_episode',
        'Queue an episode for background processing (episodes of one group are processed in order); '
        'returns immediately.',
        {'name': 'str', 'episode_body': 'str', 'source': 'str (text)', 'group_id': 'str|null',
         'reference_time': 'str|null', 'source_description': 'str'},
        writes=True, needs_model=True, http=('POST', '/api/timeline/episodes/queue'),
    ),
    ToolSpec(
        'cairn_timeline_add_episodes_bulk', 'add_episodes_bulk',
        'Bulk-ingest many episodes at once (extraction and dedupe across the batch).',
        {'episodes': 'list[{name, content, source, source_description, reference_time, uuid}]',
         'group_id': 'str|null', 'saga': 'str|null'},
        writes=True, needs_model=True, http=('POST', '/api/timeline/episodes/bulk'),
    ),
    ToolSpec(
        'cairn_timeline_add_messages', 'add_messages',
        'Add chat messages, one message episode each ("role(role_type): content").',
        {'messages': 'list[{content, role_type, role, name, uuid, timestamp, source_description}]',
         'group_id': 'str|null', 'queue': 'bool (false)'},
        writes=True, needs_model=True, http=('POST', '/api/timeline/messages'),
    ),
    ToolSpec(
        'cairn_timeline_add_fact', 'add_triplet',
        'Write one fact directly (source entity -[edge_name]-> target entity) without extraction; '
        'endpoints are resolved against existing entities.',
        {'source_node_name': 'str', 'edge_name': 'str', 'fact': 'str', 'target_node_name': 'str',
         'group_id': 'str|null', 'source_node_uuid': 'str|null', 'target_node_uuid': 'str|null',
         'valid_at': 'str|null', 'invalid_at': 'str|null'},
        writes=True, needs_model=True, http=('POST', '/api/timeline/facts'),
    ),
    ToolSpec(
        'cairn_timeline_add_entity', 'add_entity_node',
        'Create an entity directly (no model needed).',
        {'name': 'str', 'uuid': 'str|null', 'group_id': 'str|null', 'summary': 'str'},
        writes=True, http=('POST', '/api/timeline/entities'),
    ),
    ToolSpec(
        'cairn_timeline_search_facts', 'search_facts',
        'Search facts (hybrid BM25 + vector + graph). Each fact carries valid_at / invalid_at. '
        'Filter by fact type or validity window; current_only drops superseded facts.',
        {'query': 'str', 'group_ids': 'str|list[str]|null', 'max_facts': 'int (10)',
         'center_node_uuid': 'str|null', 'edge_types': 'list[str]|null',
         'valid_at_after': 'str|null', 'valid_at_before': 'str|null',
         'invalid_at_after': 'str|null', 'invalid_at_before': 'str|null', 'current_only': 'bool (false)'},
        http=('POST', '/api/timeline/search'),
    ),
    ToolSpec(
        'cairn_timeline_facts_at', 'facts_at',
        'Facts that were true at a point in time ("what was true then").',
        {'when': 'str (ISO-8601)', 'query': 'str', 'group_ids': 'str|list[str]|null', 'max_facts': 'int (10)'},
        http=('POST', '/api/timeline/facts-at'),
    ),
    ToolSpec(
        'cairn_timeline_search_entities', 'search_nodes',
        'Search entities, optionally filtered by entity type and centred on a node.',
        {'query': 'str', 'group_ids': 'str|list[str]|null', 'max_nodes': 'int (10)',
         'entity_types': 'list[str]|null', 'center_node_uuid': 'str|null'},
        http=('POST', '/api/timeline/search/entities'),
    ),
    ToolSpec(
        'cairn_timeline_search', 'search',
        'Advanced search over facts, entities, episodes and communities with a search recipe '
        f'({", ".join(search_recipes())}) or a custom config.',
        {'query': 'str', 'recipe': 'str|object (COMBINED_HYBRID_SEARCH_CROSS_ENCODER)',
         'group_ids': 'str|list[str]|null', 'limit': 'int|null', 'center_node_uuid': 'str|null',
         'bfs_origin_node_uuids': 'list[str]|null', 'search_filter': 'object|null'},
        http=('POST', '/api/timeline/search/advanced'),
    ),
    ToolSpec(
        'cairn_timeline_context', 'context',
        'Facts (with validity dates), entities, episodes and communities for a query, rendered as '
        'one prompt-ready context block.',
        {'query': 'str', 'recipe': 'str|object (COMBINED_HYBRID_SEARCH_RRF)', 'group_ids': 'str|list[str]|null',
         'limit': 'int (10)', 'center_node_uuid': 'str|null'},
        http=('POST', '/api/timeline/context'),
    ),
    ToolSpec(
        'cairn_timeline_get_memory', 'get_memory',
        'Facts relevant to a conversation (messages are composed into one query).',
        {'messages': 'list[{content, role_type, role}]', 'group_id': 'str|null', 'max_facts': 'int (10)',
         'center_node_uuid': 'str|null'},
        http=('POST', '/api/timeline/get-memory'),
    ),
    ToolSpec(
        'cairn_timeline_get_episodes', 'get_episodes',
        'The most recent episodes of a group.',
        {'group_ids': 'str|list[str]|null', 'last_n': 'int (10)', 'reference_time': 'str|null',
         'source': 'str|null', 'saga': 'str|null'},
        http=('GET', '/api/timeline/episodes'),
    ),
    ToolSpec(
        'cairn_timeline_episode_entities', 'get_episode_entities',
        'Provenance: the entities and facts created by specific episodes.',
        {'episode_uuids': 'list[str]'},
        http=('POST', '/api/timeline/episodes/entities'),
    ),
    ToolSpec(
        'cairn_timeline_get_fact', 'get_entity_edge', 'One fact by uuid.', {'uuid': 'str'},
        http=('GET', '/api/timeline/facts/{uuid}'),
    ),
    ToolSpec(
        'cairn_timeline_get_entity', 'get_node', 'One entity and all facts it takes part in.',
        {'uuid': 'str'}, http=('GET', '/api/timeline/entities/{uuid}'),
    ),
    ToolSpec(
        'cairn_timeline_list_facts', 'list_facts', "Page through a group's facts.",
        {'group_id': 'str|null', 'limit': 'int (100)', 'uuid_cursor': 'str|null'},
        http=('GET', '/api/timeline/facts'),
    ),
    ToolSpec(
        'cairn_timeline_list_entities', 'list_entities', "Page through a group's entities.",
        {'group_id': 'str|null', 'limit': 'int (100)', 'uuid_cursor': 'str|null'},
        http=('GET', '/api/timeline/entities'),
    ),
    ToolSpec(
        'cairn_timeline_communities', 'list_communities', "A group's communities.",
        {'group_id': 'str|null'}, http=('GET', '/api/timeline/communities'),
    ),
    ToolSpec(
        'cairn_timeline_build_communities', 'build_communities',
        'Detect communities of related entities and summarize each.',
        {'group_ids': 'str|list[str]|null'}, writes=True, needs_model=True,
        http=('POST', '/api/timeline/communities/build'),
    ),
    ToolSpec(
        'cairn_timeline_sagas', 'list_sagas', "A group's sagas (ordered episode chains).",
        {'group_id': 'str|null'}, http=('GET', '/api/timeline/sagas'),
    ),
    ToolSpec(
        'cairn_timeline_summarize_saga', 'summarize_saga',
        "Generate or refresh a saga's running summary.",
        {'saga_name': 'str', 'group_id': 'str|null'}, writes=True, needs_model=True,
        http=('POST', '/api/timeline/sagas/summarize'),
    ),
    ToolSpec(
        'cairn_timeline_delete_fact', 'delete_entity_edge', 'Delete one fact.', {'uuid': 'str'},
        writes=True, http=('DELETE', '/api/timeline/facts/{uuid}'),
    ),
    ToolSpec(
        'cairn_timeline_delete_episode', 'delete_episode',
        'Delete an episode and the entities/facts only it created.', {'uuid': 'str'},
        writes=True, http=('DELETE', '/api/timeline/episodes/{uuid}'),
    ),
    ToolSpec(
        'cairn_timeline_delete_group', 'delete_group', 'Delete everything in one namespace.',
        {'group_id': 'str'}, writes=True, http=('DELETE', '/api/timeline/groups/{group_id}'),
    ),
    ToolSpec(
        'cairn_timeline_clear', 'clear', 'Clear the given groups (default: this project).',
        {'group_ids': 'str|list[str]|null'}, writes=True, http=('POST', '/api/timeline/clear'),
    ),
    ToolSpec(
        'cairn_timeline_status', 'status', 'Store health and counts.', {},
        http=('GET', '/api/timeline/status'),
    ),
]

TOOL_INDEX: dict[str, ToolSpec] = {t.name: t for t in TOOLS}


async def call_tool(service: TemporalService, name: str, arguments: dict[str, Any] | None = None) -> Any:
    spec = TOOL_INDEX.get(name)
    if spec is None:
        raise KeyError(f'unknown temporal tool {name!r}')
    args = dict(arguments or {})
    method = getattr(service, spec.method)
    positional = {
        'add_episode': ('name', 'episode_body'),
        'enqueue_episode': ('name', 'episode_body'),
        'add_episodes_bulk': ('episodes',),
        'add_messages': ('messages',),
        'add_triplet': ('source_node_name', 'edge_name', 'fact', 'target_node_name'),
        'add_entity_node': ('name',),
        'search_facts': ('query',),
        'facts_at': ('when', 'query'),
        'search_nodes': ('query',),
        'search': ('query',),
        'context': ('query',),
        'get_memory': ('messages',),
        'get_episode_entities': ('episode_uuids',),
        'get_entity_edge': ('uuid',),
        'get_node': ('uuid',),
        'summarize_saga': ('saga_name',),
        'delete_entity_edge': ('uuid',),
        'delete_episode': ('uuid',),
        'delete_group': ('group_id',),
    }.get(spec.method, ())
    pos = [args.pop(p) for p in positional if p in args]
    if spec.method == 'clear':
        return await method(args.pop('group_ids', None))
    if spec.method == 'build_communities':
        return await method(args.pop('group_ids', None))
    return await method(*pos, **args)


def call_tool_sync(service: TemporalService, name: str, arguments: dict[str, Any] | None = None) -> Any:
    return run_sync(call_tool(service, name, arguments))
