"""Cairn's temporal fact graph ("Timeline" facts).

Episodes (text, JSON or chat messages) become entities and facts with validity windows; new
information invalidates contradicted facts instead of deleting them, so the graph answers both
"what is true now" and "what was true then". Hybrid search (BM25 + vectors + graph traversal,
with rerankers and recipes), communities, sagas, custom entity/fact types and namespaces
(``group_id``) are all supported.

Entry points
  TemporalService   per-project facade used by the CLI, MCP tools and HTTP API (``service.py``)
  TemporalGraph     the engine itself (``engine.py``)
  api               tool table + sync helpers for mounting on MCP / HTTP
"""

from .edges import CommunityEdge, EntityEdge, EpisodicEdge, HasEpisodeEdge, NextEpisodeEdge
from .engine import AddBulkEpisodeResults, AddEpisodeResults, AddTripletResults, TemporalGraph
from .errors import (
    EdgeNotFoundError,
    GroupIdValidationError,
    NodeNotFoundError,
    TemporalGraphError,
)
from .nodes import CommunityNode, EntityNode, EpisodeType, EpisodicNode, SagaNode
from .search.search_config import SearchConfig, SearchResults
from .search.search_filters import ComparisonOperator, DateFilter, SearchFilters
from .service import (
    ModelUnavailableError,
    StoreBusyError,
    TemporalService,
    TemporalSettings,
    run_sync,
    search_recipes,
)
from .utils.bulk_utils import RawEpisode

__all__ = [
    'TemporalGraph',
    'TemporalService',
    'TemporalSettings',
    'AddEpisodeResults',
    'AddBulkEpisodeResults',
    'AddTripletResults',
    'EpisodeType',
    'EpisodicNode',
    'EntityNode',
    'CommunityNode',
    'SagaNode',
    'EntityEdge',
    'EpisodicEdge',
    'CommunityEdge',
    'HasEpisodeEdge',
    'NextEpisodeEdge',
    'RawEpisode',
    'SearchConfig',
    'SearchResults',
    'SearchFilters',
    'DateFilter',
    'ComparisonOperator',
    'TemporalGraphError',
    'EdgeNotFoundError',
    'NodeNotFoundError',
    'GroupIdValidationError',
    'ModelUnavailableError',
    'StoreBusyError',
    'run_sync',
    'search_recipes',
]
