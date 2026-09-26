"""The self-reconciling memory engine.

``Memory.add`` turns messages into durable memories. With inference on, a model extracts facts and
then reconciles each against the most similar stored memories, deciding ADD / UPDATE / DELETE / NONE
(``add_mode="reconcile"``, the default) — or extracts linked ADD-only memories in one call with hash
de-duplication (``add_mode="additive"``). Every change is written to a history log. Search is hybrid:
semantic similarity, BM25 keyword scores and entity-link boosts, with metadata filters, thresholds and
optional reranking. Memories are scoped by project / team / user / agent / run identifiers.
"""
import asyncio
import concurrent.futures
import hashlib
import json
import logging
import os
import uuid
import warnings
from contextlib import ExitStack, contextmanager
from copy import deepcopy
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional

from pydantic import ValidationError

from cairn.engines.memstore.base import MemoryBase
from cairn.engines.memstore.configs.base import MemoryConfig, MemoryItem
from cairn.engines.memstore.configs.enums import MemoryType
from cairn.engines.memstore.exceptions import LLMError, VectorStoreError
from cairn.engines.memstore.exceptions import ValidationError as MemoryValidationError
from cairn.engines.memstore.graph import GraphMemory
from cairn.engines.memstore.prompts import (
    ADDITIVE_EXTRACTION_PROMPT,
    AGENT_CONTEXT_SUFFIX,
    MEMORY_ANSWER_PROMPT,
    PROCEDURAL_MEMORY_SYSTEM_PROMPT,
    generate_additive_extraction_prompt,
    get_update_memory_messages,
)
from cairn.engines.memstore.scopes import ENTITY_PARAMS, SCOPE_KEYS, scope_ids
from cairn.engines.memstore.storage import SQLiteManager
from cairn.engines.memstore.utils.entity_extraction import extract_entities, extract_entities_batch
from cairn.engines.memstore.utils.factory import (
    EmbedderFactory,
    LlmFactory,
    RerankerFactory,
    VectorStoreFactory,
)
from cairn.engines.memstore.utils.lemmatization import lemmatize_for_bm25
from cairn.engines.memstore.utils.messages import (
    ensure_json_instruction,
    extract_json,
    get_fact_retrieval_messages,
    normalize_facts,
    parse_messages,
    parse_vision_messages,
    remove_code_blocks,
)
from cairn.engines.memstore.utils.text import (
    ABOUT_MEMORY,
    CARRIES_FACT,
    _contained,
    _norm as _norm_text,
    _overlap,
    same_fact,
)
from cairn.engines.memstore.utils.scoring import (
    ENTITY_BOOST_WEIGHT,
    get_bm25_params,
    normalize_bm25,
    score_and_rank,
)
from cairn.engines.memstore.vector_stores.base import VectorStoreBase

# Suppress SWIG deprecation warnings globally
warnings.filterwarnings("ignore", category=DeprecationWarning, message=".*SwigPy.*")
warnings.filterwarnings("ignore", category=DeprecationWarning, message=".*swigvarlink.*")

logger = logging.getLogger(__name__)


def _vector_store_list_rows(listed):
    if isinstance(listed, (list, tuple)) and listed and isinstance(listed[0], list):
        return listed[0]
    if isinstance(listed, (list, tuple)):
        return listed
    return []


# Fields that hold runtime auth/connection objects and must be preserved.
# These are non-serializable objects (e.g. AWSV4SignerAuth, RequestsHttpConnection)
# needed by clients like OpenSearch — not sensitive strings to redact.
_RUNTIME_FIELDS = frozenset({
    "http_auth",
    "auth",
    "connection_class",
    "ssl_context",
    "client",
})

# Fields that are known to contain sensitive secrets and must be redacted.
_SENSITIVE_FIELDS_EXACT = frozenset({
    "api_key",
    "secret_key",
    "private_key",
    "access_key",
    "password",
    "credentials",
    "credential",
    "secret",
    "token",
    "access_token",
    "refresh_token",
    "auth_token",
    "session_token",
    "client_secret",
    "auth_client_secret",
    "azure_client_secret",
    "service_account_json",
    "aws_session_token",
})

# Suffixes that indicate a field likely holds a secret value.
_SENSITIVE_SUFFIXES = (
    "_password",
    "_secret",
    "_token",
    "_credential",
    "_credentials",
)

DELETE_ALL_BATCH_SIZE = 1000
_UNSET = object()

# Tenant-scoping fields that caller-supplied metadata must never set, on either the
# creation or the update path.
_IDENTITY_KEYS = ENTITY_PARAMS | {"actor_id"}

# Payload fields surfaced at the top level of a memory item.
PROMOTED_PAYLOAD_KEYS = [
    *SCOPE_KEYS,
    "actor_id",
    "role",
    "attributed_to",
    "expiration_date",
]
_CORE_AND_PROMOTED_KEYS = {
    "data", "hash", "created_at", "updated_at", "id", "text_lemmatized", "attributed_to", *PROMOTED_PAYLOAD_KEYS,
}


def _strip_identity_keys(
    metadata: Dict[str, Any],
    existing_payload: Dict[str, Any],
    *,
    context: str = "update()",
) -> Dict[str, Any]:
    """Drop identity keys from caller metadata; scope is set by the entity params, not metadata.

    On the update path `existing_payload` carries the memory's current scope, so
    re-sending an identical value is silently accepted; only a changed value warns.
    On the creation path there is no prior payload, so pass an empty dict and every
    identity key present in `metadata` is dropped with a warning.
    """
    clean = {}
    for key, value in metadata.items():
        if key not in _IDENTITY_KEYS:
            clean[key] = value
        elif value != existing_payload.get(key):
            logger.warning(f"{context}: ignoring metadata['{key}'] - identity fields cannot be set through metadata")
    return clean


def _reject_top_level_entity_params(kwargs: Dict[str, Any], method_name: str) -> None:
    """Reject top-level entity parameters - must use filters instead."""
    invalid_keys = ENTITY_PARAMS & set(kwargs.keys())
    if invalid_keys:
        raise ValueError(
            f"Top-level entity parameters {invalid_keys} are not supported in {method_name}(). "
            f"Use filters={{'project_id': '...'}} instead."
        )
    if kwargs:
        logger.warning(f"{method_name}(): ignoring unsupported arguments {sorted(kwargs)}")


def _validate_and_trim_entity_id(value: Optional[Any], name: str) -> Optional[str]:
    """
    Validates and normalizes an entity ID.
    - Coerces non-string values (e.g. integer ids) to str
    - Trims leading/trailing whitespace
    - Rejects empty or whitespace-only strings
    - Rejects strings containing internal whitespace

    Args:
        value: The entity ID value to validate
        name: The parameter name (for error messages)

    Returns:
        The trimmed entity ID, or None if input is None

    Raises:
        ValueError: If entity ID is invalid
    """
    if value is None:
        return None
    # Callers commonly pass integer ids (e.g. a database primary key). Coerce
    # to str at this single validation point so scoping stays consistent across
    # add/search/get_all/delete_all instead of crashing on `.strip()`.
    if not isinstance(value, str):
        value = str(value)
    trimmed = value.strip()
    if trimmed == "":
        raise ValueError(
            f"Invalid {name}: cannot be empty or whitespace-only. Provide a valid identifier."
        )
    if any(c.isspace() for c in trimmed):
        raise ValueError(
            f"Invalid {name}: cannot contain whitespace. Provide a valid identifier without spaces."
        )
    return trimmed


def _validate_search_params(threshold: Optional[float] = None, top_k: Optional[int] = None) -> None:
    """
    Validates search parameters.

    Args:
        threshold: Similarity threshold (must be between 0 and 1)
        top_k: Number of results to return (must be non-negative integer)

    Raises:
        ValueError: If threshold or top_k are invalid
    """
    if threshold is not None:
        if not isinstance(threshold, (int, float)):
            raise ValueError("threshold must be a valid number")
        if threshold < 0 or threshold > 1:
            raise ValueError(
                f"Invalid threshold: {threshold}. Must be between 0 and 1 (inclusive)."
            )
    if top_k is not None:
        if not isinstance(top_k, int) or isinstance(top_k, bool):
            raise ValueError("top_k must be a valid integer")
        if top_k < 0:
            raise ValueError(
                f"Invalid top_k: {top_k}. Must be a non-negative integer."
            )


def _validate_and_trim_search_query(query: str) -> str:
    """
    Validates and normalizes a search query before embedding/vector search.

    Raises:
        ValueError: If query is not a string or is empty/whitespace-only.
    """
    if not isinstance(query, str):
        raise ValueError("Invalid query: must be a non-empty string.")
    trimmed = query.strip()
    if not trimmed:
        raise ValueError("Invalid query: cannot be empty or whitespace-only.")
    return trimmed


def _is_sensitive_field(field_name: str) -> bool:
    """Check if a config field holds a secret (used when cloning configs and when showing them).

    Uses a layered approach:
    1. Runtime fields (allowlist) — always preserved, highest priority.
    2. Exact deny list — known secret field names.
    3. Suffix deny list — catches patterns like db_password, auth_secret, etc.
    """
    name = field_name.lower().strip()
    if name in _RUNTIME_FIELDS:
        return False
    if name in _SENSITIVE_FIELDS_EXACT:
        return True
    return any(name.endswith(suffix) for suffix in _SENSITIVE_SUFFIXES)


def redact_config(value: Any, key: Optional[str] = None) -> Any:
    """A copy of a config structure with secret values replaced by '[redacted]'."""
    if isinstance(value, dict):
        return {k: redact_config(v, k) for k, v in value.items()}
    if isinstance(value, list):
        return [redact_config(v, key) for v in value]
    if key is not None and _is_sensitive_field(key):
        return "[redacted]" if value else value
    if not isinstance(value, (str, int, float, bool, type(None))):
        return f"<{type(value).__name__}>"
    return value


def _safe_deepcopy_config(config):
    """Safely deepcopy config, falling back to dict-based cloning for non-serializable objects."""
    try:
        return deepcopy(config)
    except Exception as e:
        logger.debug(f"Deepcopy failed, using dict-based cloning: {e}")

        config_class = type(config)

        if hasattr(config, "model_dump"):
            try:
                clone_dict = config.model_dump()
            except Exception:
                clone_dict = dict(config.__dict__)
        else:
            clone_dict = dict(config.__dict__)

        # Restore runtime fields, redact sensitive ones
        for field_name in list(clone_dict.keys()):
            if field_name in _RUNTIME_FIELDS and hasattr(config, field_name):
                clone_dict[field_name] = getattr(config, field_name)
            elif _is_sensitive_field(field_name):
                clone_dict[field_name] = None

        try:
            return config_class(**clone_dict)
        except Exception:
            logger.debug("Config reconstruction failed, returning shallow dict clone")
            return type("Config", (), clone_dict)()


def _normalize_iso_timestamp_to_utc(timestamp: Optional[str]) -> Optional[str]:
    """Normalize timezone-aware ISO timestamps to UTC without rewriting naive values."""
    if not timestamp:
        return timestamp
    try:
        parsed = datetime.fromisoformat(timestamp)
    except ValueError:
        return timestamp
    if parsed.tzinfo is None:
        return timestamp
    return parsed.astimezone(timezone.utc).isoformat()


def _normalize_timestamp(value: Any) -> Optional[str]:
    """When a memory was observed: unix seconds, a datetime/date or an ISO string -> UTC ISO string."""
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError("timestamp must be a unix time, a datetime or an ISO-8601 string.")
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(float(value), tz=timezone.utc).isoformat()
    if isinstance(value, datetime):
        value = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).isoformat()
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day, tzinfo=timezone.utc).isoformat()
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("timestamp must be a unix time, a datetime or an ISO-8601 string.") from exc
        parsed = parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc).isoformat()
    raise ValueError("timestamp must be a unix time, a datetime or an ISO-8601 string.")


def _build_filters_and_metadata(
    *,  # Enforce keyword-only arguments
    user_id: Optional[str] = None,
    agent_id: Optional[str] = None,
    run_id: Optional[str] = None,
    project_id: Optional[str] = None,
    team_id: Optional[str] = None,
    actor_id: Optional[str] = None,  # For query-time filtering
    input_metadata: Optional[Dict[str, Any]] = None,
    input_filters: Optional[Dict[str, Any]] = None,
) -> tuple[Dict[str, Any], Dict[str, Any]]:
    """
    Constructs metadata for storage and filters for querying based on scope and actor identifiers.

    Supports several scope identifiers (`project_id`, `team_id`, `user_id`, `agent_id` and/or `run_id`)
    and optionally narrows queries to a specific `actor_id`. It returns two dicts:

    1. `base_metadata_template`: Used as a template for metadata when storing new memories.
       It includes all provided scope identifier(s) and any `input_metadata`. Identity
       scope is set from the entity params only; identity keys in `input_metadata` are
       dropped, so freeform metadata cannot place a memory into an unrequested scope.
    2. `effective_query_filters`: Used for querying existing memories. It includes all
       provided scope identifier(s), any `input_filters`, and a resolved actor
       identifier for targeted filtering if specified by any actor-related inputs.

    Actor filtering precedence: explicit `actor_id` arg → `filters["actor_id"]`
    This resolved actor ID is used for querying but is not added to `base_metadata_template`,
    as the actor for storage is typically derived from message content at a later stage.

    Returns:
        tuple[Dict[str, Any], Dict[str, Any]]: (base_metadata_template, effective_query_filters)
    """
    base_metadata_template = (
        _strip_identity_keys(deepcopy(input_metadata), {}, context="add()") if input_metadata else {}
    )
    effective_query_filters = deepcopy(input_filters) if input_filters else {}

    ids = {
        "user_id": _validate_and_trim_entity_id(user_id, "user_id"),
        "agent_id": _validate_and_trim_entity_id(agent_id, "agent_id"),
        "run_id": _validate_and_trim_entity_id(run_id, "run_id"),
        "project_id": _validate_and_trim_entity_id(project_id, "project_id"),
        "team_id": _validate_and_trim_entity_id(team_id, "team_id"),
    }
    session_ids_provided = []
    for key in SCOPE_KEYS:
        if ids[key]:
            base_metadata_template[key] = ids[key]
            effective_query_filters[key] = ids[key]
            session_ids_provided.append(key)

    if not session_ids_provided:
        raise MemoryValidationError(
            message="At least one of 'project_id', 'team_id', 'user_id', 'agent_id' or 'run_id' must be provided.",
            error_code="VALIDATION_001",
            details={"provided_ids": ids},
            suggestion="Please provide at least one identifier to scope the memory operation."
        )

    # ---------- optional actor filter ----------
    resolved_actor_id = actor_id or effective_query_filters.get("actor_id")
    if resolved_actor_id:
        effective_query_filters["actor_id"] = resolved_actor_id

    return base_metadata_template, effective_query_filters


def _escape_scope_value(val: Any) -> str:
    """Escape the structural delimiters of the session scope key."""
    return str(val).replace("%", "%25").replace("&", "%26").replace("=", "%3D")


def _build_session_scope(filters):
    """Build deterministic session scope string from entity IDs."""
    parts = []
    for key in sorted(SCOPE_KEYS):
        val = filters.get(key)
        if val:
            parts.append(f"{key}={_escape_scope_value(val)}")
    return "&".join(parts)


def _entity_collection_name(provider: str, collection_name: str) -> str:
    return f"{collection_name}_entities"


def _normalize_expiration_date(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, str):
        try:
            return date.fromisoformat(value).isoformat()
        except ValueError as exc:
            raise ValueError("expiration_date must be a valid date in YYYY-MM-DD format.") from exc
    raise ValueError("expiration_date must be a date string in YYYY-MM-DD format.")


def _payload_is_expired(payload: Optional[Dict[str, Any]]) -> bool:
    if not payload:
        return False
    expiration_date = payload.get("expiration_date")
    if not expiration_date:
        return False
    try:
        return date.fromisoformat(str(expiration_date)) < datetime.now(timezone.utc).date()
    except ValueError:
        return False


def _format_memory(mem_id: str, payload: Dict[str, Any], score: Optional[float] = None,
                   include_score: bool = True) -> Dict[str, Any]:
    """Shape a stored record as a memory item: core fields, promoted scope fields, then metadata."""
    item = MemoryItem(
        id=mem_id,
        memory=payload.get("data", ""),
        hash=payload.get("hash"),
        created_at=payload.get("created_at"),
        updated_at=payload.get("updated_at"),
        score=score,
    ).model_dump(exclude=None if include_score else {"score"})
    for key in PROMOTED_PAYLOAD_KEYS:
        if key in payload:
            item[key] = payload[key]
    additional_metadata = {k: v for k, v in payload.items() if k not in _CORE_AND_PROMOTED_KEYS}
    if additional_metadata:
        item["metadata"] = additional_metadata
    return item


def _parse_llm_json(response: Any, key: str) -> List[Any]:
    """Read ``key`` (a list) from a model's JSON reply; [] when the reply is empty or unusable."""
    try:
        response = remove_code_blocks(response)
        if not response or not response.strip():
            return []
        try:
            value = json.loads(response, strict=False).get(key, [])
        except json.JSONDecodeError:
            value = json.loads(extract_json(response), strict=False).get(key, [])
        return value if isinstance(value, list) else []
    except Exception as e:
        logger.error(f"Error parsing model response: {e}")
        return []


def _clean_filters(filters: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    effective = dict(filters) if filters else {}
    for key in SCOPE_KEYS:
        if key in effective:
            effective[key] = _validate_and_trim_entity_id(effective[key], key)
    if not any(effective.get(key) for key in SCOPE_KEYS):
        raise ValueError(
            "filters must contain at least one of: " + ", ".join(SCOPE_KEYS) + ". "
            "Example: filters={'project_id': 'my-repo'}"
        )
    return effective


class Memory(MemoryBase):
    def __init__(self, config: Optional[MemoryConfig] = None, *, router: Any = None):
        self.config = config if config is not None else MemoryConfig()
        if router is not None:
            self.config.llm.config = {**(self.config.llm.config or {}), "router": router}

        self.embedding_model = EmbedderFactory.create(
            self.config.embedder.provider,
            self.config.embedder.config,
            self.config.vector_store.config,
        )
        store_config = self.config.vector_store.config
        space = getattr(self.embedding_model, "space", None)
        if space and hasattr(store_config, "embedder_id"):
            store_config.embedder_id = space
        dims = getattr(self.embedding_model.config, "embedding_dims", None)
        if (dims and "embedding_model_dims" in getattr(type(store_config), "model_fields", {})
                and "embedding_model_dims" not in store_config.model_fields_set):
            store_config.embedding_model_dims = dims  # size the store for the embedder unless told otherwise
        self.vector_store = VectorStoreFactory.create(
            self.config.vector_store.provider, self.config.vector_store.config
        )
        self.llm = LlmFactory.create(self.config.llm.provider, self.config.llm.config)
        history_dir = os.path.dirname(self.config.history_db_path)
        if history_dir and self.config.history_db_path != ":memory:":
            os.makedirs(history_dir, exist_ok=True)
        self.db = SQLiteManager(self.config.history_db_path)
        self.collection_name = self.config.vector_store.config.collection_name
        self.api_version = self.config.version
        self.custom_instructions = self.config.custom_instructions

        # Initialize reranker if configured
        self.reranker = None
        if self.config.reranker:
            self.reranker = RerankerFactory.create(
                self.config.reranker.provider,
                self.config.reranker.config,
                llm=self.llm,
            )

        # Entity store is initialized lazily on first use
        self._entity_store = None

        # Relationship memory lives in the temporal fact graph
        self.graph = None
        if self.config.graph_store and self.config.graph_store.enabled:
            self.graph = GraphMemory(self.config.graph_store, router_getter=lambda: getattr(self.llm, "router", None))
        self.enable_graph = self.graph is not None

        if getattr(type(self.vector_store), "keyword_search", None) is VectorStoreBase.keyword_search:
            logger.info(
                "The '%s' vector store does not support keyword search; search uses semantic similarity "
                "and entity boosts only.",
                self.config.vector_store.provider,
            )
        self._reindex_if_stale(self.vector_store)

    # ---- construction ----------------------------------------------------------------------------
    @classmethod
    def from_config(cls, config_dict: Dict[str, Any], *, router: Any = None):
        try:
            config = MemoryConfig(**config_dict)
        except ValidationError as e:
            logger.error(f"Configuration validation error: {e}")
            raise
        return cls(config, router=router)

    @contextmanager
    def batch(self):
        """Group many writes: stores that support it persist once at the end instead of per change."""
        with ExitStack() as stack:
            stores = [self.vector_store]
            if self.config.entity_linking:
                stores.append(self.entity_store)
            for store in stores:
                if hasattr(store, "deferred"):
                    stack.enter_context(store.deferred())
            yield self

    def _reindex_if_stale(self, store) -> None:
        """Re-embed a local store whose vectors came from a different embedding space."""
        if not getattr(store, "stale", False) or not hasattr(store, "reindex"):
            return
        try:
            n = store.reindex(lambda texts: self.embedding_model.embed_batch(texts, "update"))
            logger.info(f"Re-embedded {n} records of '{getattr(store, 'collection_name', '')}' for the current model")
        except Exception as e:
            logger.warning(f"Re-embedding stale vectors failed: {e}")

    # ---- entity store ----------------------------------------------------------------------------
    @property
    def entity_store(self):
        """Lazily initialize entity store on first use."""
        if self._entity_store is None:
            entity_config = _safe_deepcopy_config(self.config.vector_store.config)
            entity_collection = _entity_collection_name(self.config.vector_store.provider, self.collection_name)
            # Set collection name on the cloned config
            if hasattr(entity_config, 'collection_name'):
                entity_config.collection_name = entity_collection
            elif isinstance(entity_config, dict):
                entity_config['collection_name'] = entity_collection
            # For Qdrant, share the existing client to avoid RocksDB lock contention
            # when using embedded mode (path=...). QdrantConfig.client takes precedence
            # over host/port/path.
            if self.config.vector_store.provider == "qdrant" and hasattr(self.vector_store, "client"):
                if hasattr(entity_config, "client"):
                    entity_config.client = self.vector_store.client
                elif isinstance(entity_config, dict):
                    entity_config["client"] = self.vector_store.client
            self._entity_store = VectorStoreFactory.create(
                self.config.vector_store.provider, entity_config
            )
            self._reindex_if_stale(self._entity_store)
        return self._entity_store

    @staticmethod
    def _normalize_entity_text(value: str) -> str:
        return " ".join(value.strip().lower().split())

    def _existing_entities_by_text(self, filters):
        """Return existing entity rows keyed by normalized payload data."""
        try:
            listed = self.entity_store.list(filters=filters, top_k=10000)
        except Exception as e:
            logger.debug(f"Exact entity lookup failed, falling back to semantic dedup: {e}")
            return {}

        rows_by_text = {}
        for row in _vector_store_list_rows(listed):
            payload = getattr(row, "payload", None) or {}
            text = payload.get("data")
            if not isinstance(text, str):
                continue
            normalized = self._normalize_entity_text(text)
            if normalized and normalized not in rows_by_text:
                rows_by_text[normalized] = row
        return rows_by_text

    def _upsert_entity(self, entity_text, entity_type, memory_id, filters):
        """Upsert an entity into the entity store, linking it to a memory."""
        try:
            entity_embedding = self.embedding_model.embed(entity_text, "add")
            search_filters = scope_ids(filters)
            exact_match = self._existing_entities_by_text(search_filters).get(self._normalize_entity_text(entity_text))

            existing = []
            if exact_match is None:
                existing = self.entity_store.search(
                    query=entity_text,
                    vectors=entity_embedding,
                    top_k=1,
                    filters=search_filters,
                )

            semantic_match = existing[0] if existing and existing[0].score >= 0.95 else None
            match = exact_match or semantic_match
            if match:
                # Update existing entity's linked_memory_ids
                payload = match.payload or {}
                linked_ids = payload.get("linked_memory_ids", [])
                if memory_id not in linked_ids:
                    linked_ids.append(memory_id)
                    payload["linked_memory_ids"] = linked_ids
                    self.entity_store.update(
                        vector_id=match.id,
                        vector=None,
                        payload=payload,
                    )
            else:
                # Create new entity
                entity_id = str(uuid.uuid4())
                entity_payload = {
                    "data": entity_text,
                    "entity_type": entity_type,
                    "linked_memory_ids": [memory_id],
                    **search_filters,
                }
                self.entity_store.insert(
                    vectors=[entity_embedding],
                    ids=[entity_id],
                    payloads=[entity_payload],
                )
        except Exception as e:
            logger.warning(f"Entity upsert failed for '{entity_text}': {e}")

    def _remove_memory_from_entity_store(self, memory_id, filters):
        """Strip `memory_id` from every entity record scoped to `filters`.

        For each entity whose `linked_memory_ids` contains `memory_id`:
          - remove the id; if the list becomes empty, delete the entity record.
          - otherwise re-embed the entity text and update the payload.

        No-op if the entity store has never been initialized in this process.
        Errors are swallowed so the primary delete/update path is never broken by entity cleanup.
        """
        if self._entity_store is None:
            return
        search_filters = scope_ids(filters)
        try:
            listed = self.entity_store.list(filters=search_filters, top_k=10000)
            for row in _vector_store_list_rows(listed) or []:
                try:
                    payload = getattr(row, "payload", None) or {}
                    linked = payload.get("linked_memory_ids", [])
                    if not isinstance(linked, list) or memory_id not in linked:
                        continue
                    remaining = [mid for mid in linked if mid != memory_id]
                    if not remaining:
                        try:
                            self.entity_store.delete(vector_id=row.id)
                        except Exception as e:
                            logger.debug(f"Entity delete failed for id={row.id}: {e}")
                    else:
                        entity_text = payload.get("data")
                        if not isinstance(entity_text, str) or not entity_text:
                            logger.debug(f"Entity id={row.id} missing 'data'; skipping update during cleanup")
                            continue
                        try:
                            vec = self.embedding_model.embed(entity_text, "update")
                        except Exception as e:
                            logger.debug(f"Entity re-embed failed for '{entity_text}': {e}")
                            continue
                        new_payload = {**payload, "linked_memory_ids": remaining}
                        try:
                            self.entity_store.update(
                                vector_id=row.id,
                                vector=vec,
                                payload=new_payload,
                            )
                        except Exception as e:
                            logger.debug(f"Entity update failed for id={row.id}: {e}")
                except Exception as e:
                    logger.debug(f"Entity cleanup error: {e}")
        except Exception as e:
            logger.warning(f"Entity store cleanup failed for memory_id={memory_id}: {e}")

    def _link_entities_for_memory(self, memory_id, text, filters):
        """Extract entities from `text` and link them to `memory_id` in the entity store, scoped to
        `filters`. Per-entity search-then-update-or-insert via `_upsert_entity`. Non-fatal on failure.
        """
        if not self.config.entity_linking:
            return
        try:
            entities = extract_entities(text)
            if not entities:
                return
            seen = set()
            for entity_type, entity_text in entities:
                key = self._normalize_entity_text(entity_text)
                if not key or key in seen:
                    continue
                seen.add(key)
                try:
                    self._upsert_entity(entity_text, entity_type, memory_id, filters)
                except Exception as e:
                    logger.debug(f"Entity link failed for '{entity_text}': {e}")
        except Exception as e:
            logger.warning(f"Entity linking failed for memory_id={memory_id}: {e}")

    def _link_entities_batch(self, records, search_filters):
        """Batch entity linking for freshly stored memories: extract, de-duplicate across the batch,
        embed once, then update matching entities or insert new ones. ``records`` are
        (memory_id, text, embedding, payload) tuples."""
        if not self.config.entity_linking or not records:
            return
        try:
            all_texts = [r[1] for r in records]
            all_entities = extract_entities_batch(all_texts)

            # Global dedup — collect unique entities across all memories
            global_entities = {}  # normalized_key -> [entity_type, entity_text, set of memory_ids]
            for idx, (memory_id, text, embedding, payload) in enumerate(records):
                entities = all_entities[idx] if idx < len(all_entities) else []
                for entity_type, entity_text in entities:
                    key = self._normalize_entity_text(entity_text)
                    if not key:
                        continue
                    if key in global_entities:
                        global_entities[key][2].add(memory_id)
                    else:
                        global_entities[key] = [entity_type, entity_text, {memory_id}]

            if not global_entities:
                return
            ordered_keys = list(global_entities.keys())
            entity_texts = [global_entities[k][1] for k in ordered_keys]

            # Single batch embed for all unique entities
            try:
                entity_embeddings = self.embedding_model.embed_batch(entity_texts, "add")
            except Exception:
                entity_embeddings = []
                for t in entity_texts:
                    try:
                        entity_embeddings.append(self.embedding_model.embed(t, "add"))
                    except Exception:
                        entity_embeddings.append(None)

            if len(entity_embeddings) != len(ordered_keys):
                logger.warning(
                    "embed_batch returned %d vectors for %d entity texts — padding/truncating",
                    len(entity_embeddings),
                    len(ordered_keys),
                )
                entity_embeddings = list(entity_embeddings[: len(ordered_keys)])
                entity_embeddings += [None] * (len(ordered_keys) - len(entity_embeddings))

            valid = [(i, k) for i, k in enumerate(ordered_keys) if entity_embeddings[i] is not None]
            if not valid:
                return
            valid_indices, valid_keys = zip(*valid)
            valid_vectors = [entity_embeddings[i] for i in valid_indices]
            exact_matches = self._existing_entities_by_text(search_filters)

            # Batch search for existing entities
            valid_texts = [global_entities[k][1] for k in valid_keys]
            existing_matches = self.entity_store.search_batch(
                queries=valid_texts,
                vectors_list=valid_vectors,
                top_k=1,
                filters=search_filters,
            )

            # Separate into inserts vs updates
            to_insert_vectors, to_insert_ids, to_insert_payloads = [], [], []
            for j, key in enumerate(valid_keys):
                entity_type, entity_text, memory_ids = global_entities[key]
                matches = existing_matches[j] if j < len(existing_matches) else []
                exact_match = exact_matches.get(key)

                semantic_match = matches[0] if matches and matches[0].score >= 0.95 else None
                match = exact_match or semantic_match
                if match:
                    payload = match.payload or {}
                    linked = set(payload.get("linked_memory_ids", []))
                    linked |= memory_ids
                    payload["linked_memory_ids"] = sorted(linked)
                    try:
                        self.entity_store.update(
                            vector_id=match.id,
                            vector=None,
                            payload=payload,
                        )
                    except Exception as e:
                        logger.debug(f"Entity update failed for '{entity_text}': {e}")
                else:
                    to_insert_vectors.append(valid_vectors[j])
                    to_insert_ids.append(str(uuid.uuid4()))
                    to_insert_payloads.append({
                        "data": entity_text,
                        "entity_type": entity_type,
                        "linked_memory_ids": sorted(memory_ids),
                        **search_filters,
                    })

            # Single batch insert for all new entities
            if to_insert_vectors:
                try:
                    self.entity_store.insert(
                        vectors=to_insert_vectors,
                        ids=to_insert_ids,
                        payloads=to_insert_payloads,
                    )
                except Exception as e:
                    logger.warning(f"Batch entity insert failed: {e}")
        except Exception as e:
            logger.warning(f"Batch entity linking failed: {e}")

    def _should_use_agent_memory_extraction(self, messages, metadata):
        """Determine whether to use agent memory extraction based on the logic:
        - If agent_id is present and messages contain assistant role -> True
        - Otherwise -> False

        Args:
            messages: List of message dictionaries
            metadata: Metadata containing user_id, agent_id, etc.

        Returns:
            bool: True if should use agent memory extraction, False for user memory extraction
        """
        has_agent_id = metadata.get("agent_id") is not None
        has_assistant_messages = any(msg.get("role") == "assistant" for msg in messages)
        return has_agent_id and has_assistant_messages

    # ---- add -------------------------------------------------------------------------------------
    def add(
        self,
        messages,
        *,
        user_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        run_id: Optional[str] = None,
        project_id: Optional[str] = None,
        team_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        timestamp: Optional[Any] = None,
        expiration_date: Optional[Any] = None,
        infer: bool = True,
        memory_type: Optional[str] = None,
        prompt: Optional[str] = None,
        llm=None,
    ):
        """
        Create new memories.

        Adds memories scoped to at least one of `project_id`, `team_id`, `user_id`, `agent_id` or `run_id`.

        Args:
            messages (str or List[Dict[str, str]]): The message content or list of messages
                (e.g., `[{"role": "user", "content": "Hello"}, {"role": "assistant", "content": "Hi"}]`)
                to be processed and stored.
            user_id / agent_id / run_id / project_id / team_id (str, optional): Scope identifiers.
            metadata (dict, optional): Metadata to store with the memory. Defaults to None.
            timestamp (Any, optional): When the information was observed (unix seconds, datetime or ISO
                string). Stored as the memory's creation time. Defaults to now.
            expiration_date (Any, optional): Date in YYYY-MM-DD format. Expired memories are hidden
                from search and get_all unless show_expired is True.
            infer (bool, optional): If True (default), a model extracts key facts from 'messages' and
                decides whether to add, update, or delete related memories. If False, 'messages' are
                added as raw memories directly.
            memory_type (str, optional): Pass `MemoryType.PROCEDURAL.value` ("procedural_memory") with an
                `agent_id` to store a procedural summary of an agent's run. Otherwise, memories are treated
                as general factual memories.
            prompt (str, optional): Extraction instructions for this call (overrides custom_instructions).
            llm (optional): A LangChain chat model to write the procedural summary with instead of the router.

        Note:
            `search()` and `get_all()` scope queries via `filters={"project_id": "...", ...}` — they reject
            top-level scope arguments.

        Returns:
            dict: `{"results": [{"id": "...", "memory": "...", "event": "ADD"}, ...]}` (plus `"relations"`
            when graph memory is enabled).

        Raises:
            MemoryValidationError: If input validation fails (invalid memory_type, messages format, etc.).
            VectorStoreError: If vector store operations fail.
            LLMError: If model operations fail.
        """
        normalized_timestamp = _normalize_timestamp(timestamp)
        normalized_expiration_date = _normalize_expiration_date(expiration_date)
        processed_metadata, effective_filters = _build_filters_and_metadata(
            user_id=user_id,
            agent_id=agent_id,
            run_id=run_id,
            project_id=project_id,
            team_id=team_id,
            input_metadata=metadata,
        )
        if normalized_expiration_date is not None:
            processed_metadata["expiration_date"] = normalized_expiration_date
        if normalized_timestamp is not None:
            processed_metadata["created_at"] = normalized_timestamp

        if memory_type is not None and memory_type != MemoryType.PROCEDURAL.value:
            raise MemoryValidationError(
                message=f"Invalid 'memory_type'. Please pass {MemoryType.PROCEDURAL.value} to create procedural memories.",
                error_code="VALIDATION_002",
                details={"provided_type": memory_type, "valid_type": MemoryType.PROCEDURAL.value},
                suggestion=f"Use '{MemoryType.PROCEDURAL.value}' to create procedural memories."
            )

        if isinstance(messages, str):
            messages = [{"role": "user", "content": messages}]

        elif isinstance(messages, dict):
            messages = [messages]

        elif not isinstance(messages, list):
            raise MemoryValidationError(
                message="messages must be str, dict, or list[dict]",
                error_code="VALIDATION_003",
                details={"provided_type": type(messages).__name__, "valid_types": ["str", "dict", "list[dict]"]},
                suggestion="Convert your input to a string, dictionary, or list of dictionaries."
            )

        if agent_id is not None and memory_type == MemoryType.PROCEDURAL.value:
            return self._create_procedural_memory(messages, metadata=processed_metadata, prompt=prompt, llm=llm)

        llm_config = self.config.llm.config or {}
        if llm_config.get("enable_vision"):
            messages = parse_vision_messages(messages, self.llm, llm_config.get("vision_details"))
        else:
            messages = parse_vision_messages(messages)

        if self.graph is None:
            vector_store_result = self._add_to_vector_store(
                messages, processed_metadata, effective_filters, infer, prompt=prompt)
            return {"results": vector_store_result}

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            vector_future = pool.submit(
                self._add_to_vector_store, messages, processed_metadata, effective_filters, infer, prompt)
            graph_future = pool.submit(self._add_to_graph, messages, effective_filters, normalized_timestamp)
            vector_store_result = vector_future.result()
            graph_result = graph_future.result()
        return {"results": vector_store_result, "relations": graph_result}

    def add_facts(
        self,
        facts,
        *,
        user_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        run_id: Optional[str] = None,
        project_id: Optional[str] = None,
        team_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        timestamp: Optional[Any] = None,
        expiration_date: Optional[Any] = None,
        reconcile: bool = True,
    ):
        """Store already-distilled facts, skipping extraction.

        With ``reconcile`` (and a model), each fact is compared with its most similar memories and the
        model decides ADD / UPDATE / DELETE / NONE; NONE decisions are returned too, so callers can tell
        a duplicate from a new memory. Without a model — or with ``reconcile=False`` — facts are stored
        directly, and an identical fact already in scope (same hash) is reported as NONE.

        Returns:
            dict: `{"results": [{"id", "memory", "event", ...}, ...]}`
        """
        if isinstance(facts, str):
            facts = [facts]
        facts = [" ".join(str(f).split()) for f in facts if f and str(f).strip()]
        normalized_timestamp = _normalize_timestamp(timestamp)
        normalized_expiration_date = _normalize_expiration_date(expiration_date)
        processed_metadata, effective_filters = _build_filters_and_metadata(
            user_id=user_id, agent_id=agent_id, run_id=run_id, project_id=project_id, team_id=team_id,
            input_metadata=metadata,
        )
        if normalized_expiration_date is not None:
            processed_metadata["expiration_date"] = normalized_expiration_date
        if normalized_timestamp is not None:
            processed_metadata["created_at"] = normalized_timestamp
        if not facts:
            return {"results": []}
        if reconcile and getattr(self.llm, "available", True):
            results = self._reconcile_facts(facts, processed_metadata, effective_filters, include_noop=True)
        else:
            results = self._store_facts_directly(facts, processed_metadata, effective_filters)
        if self.graph is not None:
            return {"results": results,
                    "relations": self._add_to_graph([{"role": "user", "content": f} for f in facts],
                                                    effective_filters, normalized_timestamp)}
        return {"results": results}

    def _store_facts_directly(self, facts, metadata, filters):
        """Deterministic write path: hash de-duplication within scope, then insert."""
        search_filters = scope_ids(filters)
        results = []
        for fact in facts:
            fact_hash = hashlib.md5(fact.encode()).hexdigest()
            existing = _vector_store_list_rows(self.vector_store.list(filters={**search_filters, "hash": fact_hash},
                                                                      top_k=1))
            if existing:
                results.append({"id": existing[0].id, "memory": fact, "event": "NONE"})
                continue
            memory_id = self._create_memory(fact, {}, deepcopy(metadata))
            self._link_entities_for_memory(memory_id, fact, search_filters)
            results.append({"id": memory_id, "memory": fact, "event": "ADD"})
        return results

    def _add_to_graph(self, messages, filters, timestamp=None):
        empty = {"deleted_entities": [], "added_entities": []}
        if self.graph is None:
            return empty
        data = "\n".join(
            msg["content"] for msg in messages
            if isinstance(msg, dict) and isinstance(msg.get("content"), str) and msg.get("role") != "system"
        )
        try:
            return self.graph.add(data, scope_ids(filters), timestamp=timestamp)
        except Exception as e:
            logger.warning(f"Graph memory add failed: {e}")
            return empty

    def _add_to_vector_store(self, messages, metadata, filters, infer, prompt=None):
        if not infer:
            returned_memories = []
            for message_dict in messages:
                if (
                    not isinstance(message_dict, dict)
                    or message_dict.get("role") is None
                    or message_dict.get("content") is None
                ):
                    logger.warning(f"Skipping invalid message format: {message_dict}")
                    continue

                if message_dict["role"] == "system":
                    continue

                per_msg_meta = deepcopy(metadata)
                per_msg_meta["role"] = message_dict["role"]

                actor_name = message_dict.get("name")
                if actor_name:
                    per_msg_meta["actor_id"] = actor_name

                msg_content = message_dict["content"]
                msg_embeddings = self.embedding_model.embed(msg_content, "add")
                mem_id = self._create_memory(msg_content, {msg_content: msg_embeddings}, per_msg_meta)
                self._link_entities_for_memory(mem_id, msg_content, scope_ids(filters))

                returned_memories.append(
                    {
                        "id": mem_id,
                        "memory": msg_content,
                        "event": "ADD",
                        "actor_id": actor_name if actor_name else None,
                        "role": message_dict["role"],
                    }
                )
            return returned_memories

        if self.config.add_mode == "additive":
            return self._add_additive(messages, metadata, filters, prompt=prompt)
        return self._add_reconcile(messages, metadata, filters, prompt=prompt)

    # ---- reconcile pipeline ----------------------------------------------------------------------
    def _extract_facts(self, messages, metadata, prompt=None):
        """Phase 1 of reconciliation: the model lists the facts worth remembering in ``messages``."""
        parsed_messages = parse_messages(messages)
        custom_prompt = prompt or self.config.custom_fact_extraction_prompt
        if custom_prompt:
            system_prompt = custom_prompt
            user_prompt = f"Input:\n{parsed_messages}"
        else:
            is_agent_memory = self._should_use_agent_memory_extraction(messages, metadata)
            system_prompt, user_prompt = get_fact_retrieval_messages(parsed_messages, is_agent_memory)
            if self.custom_instructions:
                system_prompt += f"\n\nAdditional instructions:\n{self.custom_instructions}"
        system_prompt, user_prompt = ensure_json_instruction(system_prompt, user_prompt)
        try:
            response = self.llm.generate_response(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                response_format={"type": "json_object"},
            )
        except Exception as e:
            logger.error(f"Fact extraction failed: {e}")
            raise LLMError(f"Fact extraction failed: {e}") from e
        return normalize_facts(_parse_llm_json(response, "facts"))

    def _add_reconcile(self, messages, metadata, filters, prompt=None):
        session_scope = _build_session_scope(filters)
        new_retrieved_facts = self._extract_facts(messages, metadata, prompt=prompt)
        if not new_retrieved_facts:
            logger.debug("No new facts retrieved from input. Skipping memory update model call.")
            self.db.save_messages(messages, session_scope)
            return []
        returned_memories = self._reconcile_facts(new_retrieved_facts, metadata, filters)
        self.db.save_messages(messages, session_scope)
        return returned_memories

    def _reconcile_facts(self, facts, metadata, filters, include_noop=False):
        """Phase 2 of reconciliation: compare each fact with its nearest stored memories and let the
        model decide ADD / UPDATE / DELETE / NONE, then apply the decisions with history."""
        search_filters = scope_ids(filters)
        retrieved_old_memory = []
        new_message_embeddings = {}
        embeddings = self.embedding_model.embed_batch(facts, "add")
        for new_mem, messages_embeddings in zip(facts, embeddings):
            new_message_embeddings[new_mem] = messages_embeddings
            existing_memories = self.vector_store.search(
                query=new_mem,
                vectors=messages_embeddings,
                top_k=self.config.reconcile_top_k,
                filters=search_filters,
            )
            for mem in existing_memories:
                if _payload_is_expired(mem.payload):
                    continue
                retrieved_old_memory.append({"id": mem.id, "text": mem.payload.get("data", "")})

        unique_data = {}
        for item in retrieved_old_memory:
            unique_data[item["id"]] = item
        retrieved_old_memory = list(unique_data.values())
        logger.debug(f"Total existing memories: {len(retrieved_old_memory)}")

        # mapping UUIDs with integers for handling UUID hallucinations
        temp_uuid_mapping = {}
        for idx, item in enumerate(retrieved_old_memory):
            temp_uuid_mapping[str(idx)] = item["id"]
            retrieved_old_memory[idx]["id"] = str(idx)

        function_calling_prompt = get_update_memory_messages(
            retrieved_old_memory, facts, self.config.custom_update_memory_prompt
        )
        try:
            response = self.llm.generate_response(
                messages=[{"role": "user", "content": function_calling_prompt}],
                response_format={"type": "json_object"},
            )
        except Exception as e:
            logger.error(f"Memory reconciliation failed: {e}")
            raise LLMError(f"Memory reconciliation failed: {e}") from e
        actions = _parse_llm_json(response, "memory")
        old_text = {temp_uuid_mapping[item["id"]]: item["text"] for item in retrieved_old_memory}
        plan = self._check_actions(actions, facts, temp_uuid_mapping, old_text)

        returned_memories = []
        added_records = []
        for event_type, memory_id, action_text, previous in plan:
            try:
                if event_type == "ADD":
                    memory_id = self._create_memory(
                        data=action_text,
                        existing_embeddings=new_message_embeddings,
                        metadata=deepcopy(metadata),
                    )
                    added_records.append((memory_id, action_text, None, None))
                    returned_memories.append({"id": memory_id, "memory": action_text, "event": event_type})
                elif event_type == "UPDATE":
                    self._update_memory(
                        memory_id=memory_id,
                        data=action_text,
                        existing_embeddings=new_message_embeddings,
                        metadata=deepcopy(metadata),
                    )
                    returned_memories.append(
                        {"id": memory_id, "memory": action_text, "event": event_type, "previous_memory": previous}
                    )
                elif event_type == "DELETE":
                    self._delete_memory(memory_id=memory_id)
                    returned_memories.append({"id": memory_id, "memory": action_text, "event": event_type})
                elif event_type == "NONE":
                    # The fact is already stored: record the new session/agent it was seen in.
                    if metadata.get("agent_id") or metadata.get("run_id"):
                        existing_memory = self.vector_store.get(vector_id=memory_id)
                        if existing_memory is not None:
                            updated_metadata = deepcopy(existing_memory.payload)
                            if metadata.get("agent_id"):
                                updated_metadata["agent_id"] = metadata["agent_id"]
                            if metadata.get("run_id"):
                                updated_metadata["run_id"] = metadata["run_id"]
                            updated_metadata["updated_at"] = datetime.now(timezone.utc).isoformat()
                            self.vector_store.update(vector_id=memory_id, vector=None, payload=updated_metadata)
                            logger.debug(f"Updated session IDs for memory {memory_id}")
                    if include_noop:
                        returned_memories.append({"id": memory_id, "memory": action_text, "event": "NONE"})
            except Exception as e:
                logger.error(f"Error applying memory action {event_type} {memory_id}: {e}")

        self._link_entities_batch(added_records, search_filters)
        return returned_memories

    @staticmethod
    def _check_actions(actions, facts, id_map, old_text):
        """Check the model's decisions against the new facts before anything is written.

        The model sees every similar memory and sometimes rewrites or deletes one that has nothing to do
        with the new fact, or lists everything as NONE and forgets to add the fact. Only these survive:
          ADD     a text derived from the new facts (it carries one, or is part of one)
          UPDATE  a text that still carries a new fact, of a memory that fact is about
          DELETE  of a memory a new fact is about
          NONE    of a memory that states the same thing as a new fact (a duplicate)
        A memory changes at most once. Every new fact not covered by an accepted ADD, UPDATE or duplicate
        is added as its own memory. Returns the plan as (event, memory_id, text, previous_text) tuples.
        """
        plan, adds = [], []
        covered: set = set()
        touched: set = set()
        for resp in actions:
            if not isinstance(resp, dict):
                continue
            event = str(resp.get("event", "")).upper()
            text = _norm_text(resp.get("text"))
            if event == "ADD":
                if text and any(_contained(text, f) >= CARRIES_FACT or _contained(f, text) >= CARRIES_FACT
                                for f in facts):
                    if not any(same_fact(text, a) for a in adds):
                        adds.append(text)
                        plan.append(("ADD", None, text, None))
                else:
                    logger.info(f"Ignored an ADD unrelated to the new facts: {text[:80]!r}")
                continue
            memory_id = id_map.get(str(resp.get("id")))
            if memory_id is None:
                if event in ("UPDATE", "DELETE"):
                    logger.warning(f"Reconciliation referenced an unknown memory id: {resp.get('id')}")
                continue
            before = old_text.get(memory_id, "")
            if event == "UPDATE":
                fits = [i for i, f in enumerate(facts)
                        if _contained(f, text) >= CARRIES_FACT and _overlap(f, before) >= ABOUT_MEMORY]
                if not text or not fits or memory_id in touched:
                    logger.info(f"Ignored an UPDATE of memory {memory_id} that does not carry a new fact about it")
                    continue
                covered.update(fits)
                touched.add(memory_id)
                if _norm_text(before) == text:  # "updated" to what it already says: the fact is there already
                    plan.append(("NONE", memory_id, before, None))
                else:
                    plan.append(("UPDATE", memory_id, text, before))
            elif event == "DELETE":
                if memory_id in touched or not any(_overlap(f, before) >= ABOUT_MEMORY for f in facts):
                    logger.info(f"Ignored a DELETE of memory {memory_id} that no new fact is about")
                    continue
                touched.add(memory_id)
                plan.append(("DELETE", memory_id, before, None))
            elif event == "NONE":
                dup = [i for i, f in enumerate(facts) if same_fact(f, before)]
                if dup and memory_id not in touched:
                    covered.update(dup)
                    touched.add(memory_id)
                    plan.append(("NONE", memory_id, before, None))
                # any other NONE only says "compared and unrelated": nothing to do
        joined = " ".join(adds)
        deleted = {mid for ev, mid, _, _ in plan if ev == "DELETE"}
        for i, fact in enumerate(facts):
            if i in covered or (adds and _contained(fact, joined) >= CARRIES_FACT):
                continue
            dup = next((mid for mid, t in old_text.items()
                        if mid not in deleted and mid not in touched and same_fact(fact, t)), None)
            if dup is not None:  # already stored, even if the model did not say so
                touched.add(dup)
                plan.append(("NONE", dup, old_text[dup], None))
            elif not any(same_fact(fact, a) for a in adds):
                adds.append(fact)
                plan.append(("ADD", None, fact, None))  # the model left this fact out
        return plan

    # ---- additive pipeline -----------------------------------------------------------------------
    def _add_additive(self, messages, metadata, filters, prompt=None):
        # Phase 0: Context gathering
        session_scope = _build_session_scope(filters)
        last_messages = self.db.get_last_messages(session_scope, limit=10)
        parsed_messages = parse_messages(messages)

        # Phase 1: Existing memory retrieval
        search_filters = scope_ids(filters)
        query_embedding = self.embedding_model.embed(parsed_messages, "search")
        existing_results = self.vector_store.search(
            query=parsed_messages,
            vectors=query_embedding,
            top_k=10,
            filters=search_filters,
        )

        # Map UUIDs to integers (anti-hallucination)
        existing_memories = []
        uuid_mapping = {}
        for idx, mem in enumerate(existing_results):
            uuid_mapping[str(idx)] = mem.id
            existing_memories.append({"id": str(idx), "text": mem.payload.get("data", "")})

        # Phase 2: model extraction (single call)
        is_agent_scoped = bool(filters.get("agent_id")) and not filters.get("user_id")
        system_prompt = ADDITIVE_EXTRACTION_PROMPT
        if is_agent_scoped:
            system_prompt += AGENT_CONTEXT_SUFFIX

        custom_instr = prompt or self.custom_instructions

        user_prompt = generate_additive_extraction_prompt(
            existing_memories=existing_memories,
            new_messages=parsed_messages,
            last_k_messages=last_messages,
            custom_instructions=custom_instr,
        )

        try:
            response = self.llm.generate_response(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                response_format={"type": "json_object"},
            )
        except Exception as e:
            # Re-raise so callers can tell "model unavailable" from "nothing extracted".
            logger.error(f"Extraction failed: {e}")
            raise LLMError(f"Extraction failed: {e}") from e

        extracted_memories = [m for m in _parse_llm_json(response, "memory") if isinstance(m, dict)]

        if not extracted_memories:
            # Save messages even if nothing extracted
            self.db.save_messages(messages, session_scope)
            return []

        # Phase 3: Batch embed all extracted memory texts
        mem_texts = [m.get("text", "") for m in extracted_memories if m.get("text")]
        try:
            mem_embeddings_list = self.embedding_model.embed_batch(mem_texts, "add")
            embed_map = dict(zip(mem_texts, mem_embeddings_list))
        except Exception:
            embed_map = {}
            for text in mem_texts:
                try:
                    embed_map[text] = self.embedding_model.embed(text, "add")
                except Exception as e:
                    logger.warning(f"Failed to embed memory text: {e}")

        # Phase 4: Per-memory processing + Phase 5: Hash dedup
        existing_hashes = set()
        for mem in existing_results:
            h = mem.payload.get("hash") if hasattr(mem, "payload") and mem.payload else None
            if h:
                existing_hashes.add(h)

        records = []  # (memory_id, text, embedding, payload)
        seen_hashes = set()  # dedup within the current batch
        for mem in extracted_memories:
            text = mem.get("text")
            if not text or text not in embed_map:
                continue

            mem_hash = hashlib.md5(text.encode()).hexdigest()
            if mem_hash in existing_hashes or mem_hash in seen_hashes:
                logger.debug(f"Skipping duplicate memory (hash match): {text[:50]}")
                continue
            seen_hashes.add(mem_hash)

            memory_id = str(uuid.uuid4())
            mem_metadata = deepcopy(metadata)
            mem_metadata["data"] = text
            mem_metadata["text_lemmatized"] = lemmatize_for_bm25(text)
            mem_metadata["hash"] = mem_hash
            if "created_at" not in mem_metadata:
                mem_metadata["created_at"] = datetime.now(timezone.utc).isoformat()
            mem_metadata["updated_at"] = mem_metadata["created_at"]
            if mem.get("attributed_to"):
                mem_metadata["attributed_to"] = mem["attributed_to"]
            linked = [uuid_mapping[str(i)] for i in (mem.get("linked_memory_ids") or []) if str(i) in uuid_mapping]
            if linked:
                mem_metadata["linked_memory_ids"] = linked

            records.append((memory_id, text, embed_map[text], mem_metadata))

        if not records:
            self.db.save_messages(messages, session_scope)
            return []

        # Phase 6: Batch persist. Only records confirmed stored are reported, logged and linked.
        persisted_records = []
        try:
            self.vector_store.insert(
                vectors=[r[2] for r in records],
                ids=[r[0] for r in records],
                payloads=[r[3] for r in records],
            )
            persisted_records = records
        except Exception:
            for rec in records:
                try:
                    self.vector_store.insert(vectors=[rec[2]], ids=[rec[0]], payloads=[rec[3]])
                    persisted_records.append(rec)
                except Exception as e:
                    logger.error(f"Failed to insert memory {rec[0]}: {e}")

        if not persisted_records:
            self.db.save_messages(messages, session_scope)
            raise VectorStoreError(
                f"Failed to insert any of the {len(records)} extracted memories into the vector store"
            )

        # Batch history
        history_records = [
            {
                "memory_id": r[0],
                "old_memory": None,
                "new_memory": r[1],
                "event": "ADD",
                "created_at": r[3].get("created_at"),
                "is_deleted": 0,
            }
            for r in persisted_records
        ]
        try:
            self.db.batch_add_history(history_records)
        except Exception:
            for hr in history_records:
                try:
                    self.db.add_history(hr["memory_id"], None, hr["new_memory"], "ADD", created_at=hr.get("created_at"))
                except Exception as e:
                    logger.error(f"Failed to add history for {hr['memory_id']}: {e}")

        # Phase 7: Batch entity linking
        self._link_entities_batch(persisted_records, search_filters)

        # Phase 8: Save messages + return
        self.db.save_messages(messages, session_scope)
        return [{"id": r[0], "memory": r[1], "event": "ADD"} for r in persisted_records]

    # ---- reads -----------------------------------------------------------------------------------
    def get(self, memory_id):
        """
        Retrieve a memory by ID.

        Args:
            memory_id (str): ID of the memory to retrieve.

        Returns:
            dict: Retrieved memory, or None.
        """
        memory = self.vector_store.get(vector_id=memory_id)
        if not memory:
            return None
        return _format_memory(memory.id, memory.payload or {})

    def get_all(
        self,
        *,
        filters: Optional[Dict[str, Any]] = None,
        top_k: int = 20,
        show_expired: bool = False,
        **kwargs,
    ):
        """
        List memories in a scope.

        Args:
            filters (dict): Scope ids (at least one of project_id, team_id, user_id, agent_id, run_id)
                plus optional metadata filters. Example: filters={"project_id": "p1", "kind": "decision"}
            top_k (int, optional): The maximum number of memories to return. Defaults to 20.
            show_expired (bool, optional): Include expired memories. Defaults to False.

        Returns:
            dict: `{"results": [{"id": "...", "memory": "...", ...}]}` (plus `"relations"` with graph memory).
        """
        _reject_top_level_entity_params(kwargs, "get_all")
        _validate_search_params(top_k=top_k)
        effective_filters = _clean_filters(filters)
        if self._has_advanced_operators(effective_filters):
            effective_filters = self._apply_advanced_filters(effective_filters)

        limit = top_k
        fetch_limit = limit if show_expired else max(limit * 4, 60)
        all_memories_result = self._get_all_from_vector_store(effective_filters, fetch_limit, show_expired, limit)
        if self.graph is not None:
            return {"results": all_memories_result,
                    "relations": self._graph_call("get_all", scope_ids(effective_filters), limit=limit)}
        return {"results": all_memories_result}

    def _get_all_from_vector_store(self, filters, limit, show_expired=False, output_limit=None):
        memories_result = self.vector_store.list(filters=filters, top_k=limit)

        formatted_memories = []
        for mem in _vector_store_list_rows(memories_result):
            if not show_expired and _payload_is_expired(mem.payload):
                continue
            formatted_memories.append(_format_memory(mem.id, mem.payload or {}, include_score=False))
            if output_limit is not None and len(formatted_memories) >= output_limit:
                break
        return formatted_memories

    def search(
        self,
        query: str,
        *,
        top_k: int = 20,
        filters: Optional[Dict[str, Any]] = None,
        threshold: float = 0.1,
        rerank: bool = False,
        explain: bool = False,
        reference_date: Optional[Any] = None,
        show_expired: bool = False,
        **kwargs,
    ):
        """
        Searches for memories based on a query.

        Args:
            query (str): Query to search for.
            top_k (int, optional): Maximum number of results to return. Defaults to 20.
            filters (dict): Scope ids (at least one of project_id, team_id, user_id, agent_id, run_id)
                plus optional metadata filters:
                - {"key": "value"} - exact match
                - {"key": {"eq": "value"}} - equals
                - {"key": {"ne": "value"}} - not equals
                - {"key": {"in": ["val1", "val2"]}} - in list
                - {"key": {"nin": ["val1", "val2"]}} - not in list
                - {"key": {"gt": 10}} / {"gte": 10} / {"lt": 10} / {"lte": 10} - comparisons
                - {"key": {"contains": "text"}} / {"icontains": "text"} - substring match
                - {"key": "*"} - wildcard match (any value)
                - {"AND": [filter1, filter2]} / {"OR": [...]} / {"NOT": [...]} - logic
            threshold (float, optional): Minimum semantic score for a memory to be included. Defaults to 0.1.
            rerank (bool, optional): Whether to rerank results with the configured reranker. Defaults to False.
            explain (bool, optional): Whether to include score_details for each result. Defaults to False.
            reference_date (Any, optional): Not supported here — point-in-time questions go to the
                temporal fact graph.
            show_expired (bool, optional): Include expired memories. Defaults to False.

        Returns:
            dict: `{"results": [{"id": "...", "memory": "...", "score": 0.8, ...}]}` (plus `"relations"`).
        """
        if reference_date is not None:
            raise ValueError(
                "reference_date is not supported by memory search; ask the temporal fact graph for "
                "point-in-time answers."
            )

        _reject_top_level_entity_params(kwargs, "search")
        _validate_search_params(threshold=threshold, top_k=top_k)
        query = _validate_and_trim_search_query(query)
        effective_filters = _clean_filters(filters)

        limit = top_k
        if self._has_advanced_operators(effective_filters):
            effective_filters = self._apply_advanced_filters(effective_filters)

        original_memories = self._search_vector_store(
            query, effective_filters, limit, threshold, explain=explain, show_expired=show_expired
        )

        # Apply reranking if enabled and reranker is available
        if rerank and self.reranker and original_memories:
            try:
                original_memories = self.reranker.rerank(query, original_memories, limit)
            except Exception as e:
                logger.warning(f"Reranking failed, using original results: {e}")

        if self.graph is not None:
            return {"results": original_memories,
                    "relations": self._graph_call("search", query, scope_ids(effective_filters), limit=limit)}
        return {"results": original_memories}

    def _graph_call(self, method, *args, **kwargs):
        try:
            return getattr(self.graph, method)(*args, **kwargs)
        except Exception as e:
            logger.warning(f"Graph memory {method} failed: {e}")
            return []

    def _apply_advanced_filters(self, effective_filters: Dict[str, Any]) -> Dict[str, Any]:
        processed_filters = self._process_metadata_filters(effective_filters)
        effective_filters = dict(effective_filters)
        for logical_key in ("AND", "OR", "NOT"):
            effective_filters.pop(logical_key, None)
        for fk in list(effective_filters.keys()):
            if fk not in SCOPE_KEYS and (isinstance(effective_filters.get(fk), dict) or effective_filters[fk] == "*"):
                effective_filters.pop(fk, None)
        effective_filters.update(processed_filters)
        return effective_filters

    def _process_metadata_filters(self, metadata_filters: Dict[str, Any]) -> Dict[str, Any]:
        """
        Process enhanced metadata filters and convert them to vector store compatible format.

        Args:
            metadata_filters: Enhanced metadata filters with operators

        Returns:
            Dict of processed filters compatible with vector store
        """
        processed_filters = {}

        def process_condition(key: str, condition: Any) -> Dict[str, Any]:
            if not isinstance(condition, dict):
                # Simple equality: {"key": "value"}
                if condition == "*":
                    # Wildcard: match everything for this field (implementation depends on vector store)
                    return {key: "*"}
                return {key: condition}

            result = {}
            for operator, value in condition.items():
                # Map operators to a universal format each vector store translates
                operator_map = {
                    "eq": "eq", "ne": "ne", "gt": "gt", "gte": "gte",
                    "lt": "lt", "lte": "lte", "in": "in", "nin": "nin",
                    "contains": "contains", "icontains": "icontains"
                }

                if operator in operator_map:
                    result.setdefault(key, {})[operator_map[operator]] = value
                else:
                    raise ValueError(f"Unsupported metadata filter operator: {operator}")
            return result

        def merge_filters(target: Dict[str, Any], source: Dict[str, Any]) -> None:
            """Merge source into target, deep-merging nested operator dicts for the same key."""
            for key, value in source.items():
                if key in target and isinstance(target[key], dict) and isinstance(value, dict):
                    target[key].update(value)
                else:
                    target[key] = value

        for key, value in metadata_filters.items():
            if key == "AND":
                if not isinstance(value, list):
                    raise ValueError("AND operator requires a list of conditions")
                for condition in value:
                    for sub_key, sub_value in condition.items():
                        merge_filters(processed_filters, process_condition(sub_key, sub_value))
            elif key == "OR":
                if not isinstance(value, list) or not value:
                    raise ValueError("OR operator requires a non-empty list of conditions")
                processed_filters["$or"] = []
                for condition in value:
                    or_condition = {}
                    for sub_key, sub_value in condition.items():
                        merge_filters(or_condition, process_condition(sub_key, sub_value))
                    processed_filters["$or"].append(or_condition)
            elif key == "NOT":
                if not isinstance(value, list) or not value:
                    raise ValueError("NOT operator requires a non-empty list of conditions")
                processed_filters["$not"] = []
                for condition in value:
                    not_condition = {}
                    for sub_key, sub_value in condition.items():
                        merge_filters(not_condition, process_condition(sub_key, sub_value))
                    processed_filters["$not"].append(not_condition)
            else:
                merge_filters(processed_filters, process_condition(key, value))

        return processed_filters

    def _has_advanced_operators(self, filters: Dict[str, Any]) -> bool:
        """
        Check if filters contain advanced operators that need special processing.

        Args:
            filters: Dictionary of filters to check

        Returns:
            bool: True if advanced operators are detected
        """
        if not isinstance(filters, dict):
            return False

        for key, value in filters.items():
            if key in ["AND", "OR", "NOT"]:
                return True
            if isinstance(value, dict):
                for op in value.keys():
                    if op in ["eq", "ne", "gt", "gte", "lt", "lte", "in", "nin", "contains", "icontains"]:
                        return True
            if value == "*":
                return True
        return False

    def _search_vector_store(self, query, filters, limit, threshold=0.1, explain=False, show_expired=False):
        if threshold is None:
            threshold = 0.1

        # Step 1: Preprocess query
        query_lemmatized = lemmatize_for_bm25(query)
        query_entities = extract_entities(query) if self.config.entity_linking else []

        # Step 2: Embed query
        embeddings = self.embedding_model.embed(query, "search")

        # Step 3: Semantic search (over-fetch for scoring pool)
        internal_limit = max(limit * 4, 60)
        semantic_results = self.vector_store.search(
            query=query, vectors=embeddings, top_k=internal_limit, filters=filters
        )

        # Step 4: Keyword search (if store supports it)
        keyword_results = self.vector_store.keyword_search(
            query=query_lemmatized, top_k=internal_limit, filters=filters
        )

        # Step 5: Compute BM25 scores from keyword results
        bm25_scores = {}
        if keyword_results is not None:
            midpoint, steepness = get_bm25_params(query, lemmatized=query_lemmatized)
            for mem in keyword_results:
                mem_id = str(mem.id) if hasattr(mem, 'id') else str(mem.get('id', ''))
                raw_score = mem.score if hasattr(mem, 'score') else mem.get('score', 0)
                if raw_score and raw_score > 0:
                    bm25_scores[mem_id] = normalize_bm25(raw_score, midpoint, steepness)

        # Step 6: Compute entity boosts
        entity_boosts = {}
        if query_entities:
            entity_boosts = self._compute_entity_boosts(query_entities, filters)

        # Step 7: Build candidate set from semantic results
        candidates = []
        for mem in semantic_results:
            payload = mem.payload if hasattr(mem, 'payload') else {}
            if not show_expired and _payload_is_expired(payload):
                continue
            candidates.append({
                "id": str(mem.id),
                "score": mem.score,
                "payload": payload,
            })

        # Step 8: Score and rank
        scored_results = score_and_rank(
            semantic_results=candidates,
            bm25_scores=bm25_scores,
            entity_boosts=entity_boosts,
            threshold=threshold,
            top_k=limit,
            explain=explain,
        )

        # Step 9: Format results
        original_memories = []
        for scored in scored_results:
            payload = scored.get("payload") or {}
            if not payload.get("data"):
                continue  # Skip candidates with no payload data
            memory_item_dict = _format_memory(scored["id"], payload, score=scored["score"])
            if explain and "score_details" in scored:
                memory_item_dict["score_details"] = scored["score_details"]
            original_memories.append(memory_item_dict)

        return original_memories

    def _compute_entity_boosts(self, query_entities, filters):
        """Compute per-memory entity boosts from entity store search.

        For each extracted entity from the query:
        1. Embed the entity text
        2. Search the entity store (threshold >= 0.5)
        3. For each matched entity, boost its linked memories

        Returns:
            Dict mapping memory_id (str) -> max entity boost [0, 0.5].
        """
        seen = set()
        deduped = []
        for entity_type, entity_text in query_entities[:8]:
            key = self._normalize_entity_text(entity_text)
            if key and key not in seen:
                seen.add(key)
                deduped.append((entity_type, entity_text))

        if not deduped:
            return {}

        search_filters = scope_ids(filters)
        memory_boosts = {}

        try:
            entity_texts = [text for _, text in deduped]
            embeddings = self.embedding_model.embed_batch(entity_texts, "search")

            if len(embeddings) != len(entity_texts):
                logger.warning(
                    "embed_batch returned %d vectors for %d texts — skipping entity boost",
                    len(embeddings),
                    len(entity_texts),
                )
                return memory_boosts

            entity_store = self.entity_store

            def _search_entity(entity_text, embedding):
                return entity_store.search(
                    query=entity_text, vectors=embedding, top_k=500, filters=search_filters
                )

            with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
                futures = {
                    pool.submit(_search_entity, text, emb): text
                    for text, emb in zip(entity_texts, embeddings)
                }

                for future in concurrent.futures.as_completed(futures):
                    try:
                        matches = future.result()
                    except Exception as e:
                        logger.warning("Entity boost search failed for one entity: %s", e)
                        continue

                    for match in matches:
                        similarity = match.score if hasattr(match, 'score') else 0.0
                        if similarity < 0.5:
                            continue

                        payload = match.payload if hasattr(match, 'payload') else {}
                        linked_memory_ids = payload.get("linked_memory_ids", [])
                        if not isinstance(linked_memory_ids, list):
                            continue

                        num_linked = max(len(linked_memory_ids), 1)
                        memory_count_weight = 1.0 / (1.0 + 0.001 * ((num_linked - 1) ** 2))
                        boost = similarity * ENTITY_BOOST_WEIGHT * memory_count_weight

                        for memory_id in linked_memory_ids:
                            if memory_id:
                                memory_key = str(memory_id)
                                memory_boosts[memory_key] = max(memory_boosts.get(memory_key, 0.0), boost)

        except Exception as e:
            logger.warning(f"Entity boost computation failed: {e}")

        return memory_boosts

    # ---- writes ----------------------------------------------------------------------------------
    def update(
        self,
        memory_id,
        text: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        expiration_date: Any = _UNSET,
        data: Optional[str] = None,
    ):
        """
        Update a memory by ID.

        Args:
            memory_id (str): ID of the memory to update.
            text (str, optional): New content to update the memory with.
            metadata (dict, optional): Metadata to update with the memory. Defaults to None.
                Scope identifiers and ``actor_id`` are ignored here - they are immutable after creation.
            expiration_date (Any, optional): Date in YYYY-MM-DD format, or None to clear it.
            data (str, optional): Deprecated alias for ``text``.

        Returns:
            dict: Success message indicating the memory was updated.
        """
        if data is not None:
            logger.warning("The `data` argument to update() is deprecated. Use `text` instead.")
            if text is None:
                text = data

        if text is None and metadata is None and expiration_date is _UNSET:
            raise ValueError("At least one of text, metadata, or expiration_date must be provided.")

        update_metadata = deepcopy(metadata) if metadata is not None else None
        if expiration_date is not _UNSET:
            update_metadata = update_metadata or {}
            update_metadata["expiration_date"] = _normalize_expiration_date(expiration_date)

        existing_embeddings = {}
        if text is not None:
            existing_embeddings[text] = self.embedding_model.embed(text, "update")

        self._update_memory(memory_id, text, existing_embeddings, update_metadata)
        return {"message": "Memory updated successfully!"}

    def delete(self, memory_id):
        """
        Delete a memory by ID.

        Args:
            memory_id (str): ID of the memory to delete.
        """
        existing_memory = self.vector_store.get(vector_id=memory_id)
        if existing_memory is None:
            raise ValueError(f"Memory with id {memory_id} not found")

        self._delete_memory(memory_id, existing_memory)
        return {"message": "Memory deleted successfully!"}

    def delete_all(
        self,
        user_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        run_id: Optional[str] = None,
        project_id: Optional[str] = None,
        team_id: Optional[str] = None,
    ):
        """
        Delete every memory in a scope.

        Args:
            user_id / agent_id / run_id / project_id / team_id (str, optional): The scope to clear.
        """
        filters: Dict[str, Any] = {}
        for key, value in (("user_id", user_id), ("agent_id", agent_id), ("run_id", run_id),
                           ("project_id", project_id), ("team_id", team_id)):
            value = _validate_and_trim_entity_id(value, key)
            if value:
                filters[key] = value

        if not filters:
            raise ValueError(
                "At least one filter is required to delete all memories. If you want to delete all memories, use the `reset()` method."
            )

        # Keep listing after each batch is deleted. Most vector stores cap
        # list() at 100 results by default, which silently truncates deletes.
        deleted_count = 0
        seen_batches = set()
        while True:
            memories = _vector_store_list_rows(self.vector_store.list(filters=filters, top_k=DELETE_ALL_BATCH_SIZE))
            if not memories:
                break
            batch_ids = tuple(sorted(str(memory.id) for memory in memories))
            if batch_ids in seen_batches:
                logger.warning("Stopping delete_all after a repeated memory batch")
                break
            seen_batches.add(batch_ids)
            for memory in memories:
                self._delete_memory(memory.id)
            deleted_count += len(memories)

        if self.graph is not None:
            self._graph_call("delete_all", filters)

        logger.info(f"Deleted {deleted_count} memories")
        return {"message": "Memories deleted successfully!"}

    def history(self, memory_id):
        """
        Get the history of changes for a memory by ID.

        Args:
            memory_id (str): ID of the memory to get history for.

        Returns:
            list: List of changes for the memory, oldest first.
        """
        return self.db.get_history(memory_id)

    def _create_memory(self, data, existing_embeddings, metadata=None):
        logger.debug(f"Creating memory with {data=}")
        if data in existing_embeddings:
            embeddings = existing_embeddings[data]
        else:
            embeddings = self.embedding_model.embed(data, memory_action="add")
        memory_id = str(uuid.uuid4())
        new_metadata = deepcopy(metadata) if metadata is not None else {}
        new_metadata["data"] = data
        new_metadata["hash"] = hashlib.md5(data.encode()).hexdigest()
        if "created_at" not in new_metadata:
            new_metadata["created_at"] = datetime.now(timezone.utc).isoformat()
        new_metadata["updated_at"] = new_metadata["created_at"]
        new_metadata["text_lemmatized"] = lemmatize_for_bm25(data)

        self.vector_store.insert(
            vectors=[embeddings],
            ids=[memory_id],
            payloads=[new_metadata],
        )
        self.db.add_history(
            memory_id,
            None,
            data,
            "ADD",
            created_at=new_metadata.get("created_at"),
            updated_at=new_metadata.get("updated_at"),
            actor_id=new_metadata.get("actor_id"),
            role=new_metadata.get("role"),
        )
        return memory_id

    def _create_procedural_memory(self, messages, metadata=None, prompt=None, llm=None):
        """
        Create a procedural memory: a verbatim-preserving summary of an agent's execution history.

        Args:
            messages (list): List of messages to create a procedural memory from.
            metadata (dict): Metadata to create a procedural memory from.
            prompt (str, optional): Prompt to use for the procedural memory creation. Defaults to None.
            llm (optional): A LangChain chat model to use instead of the router.
        """
        logger.info("Creating procedural memory")

        parsed_messages = [
            {"role": "system", "content": prompt or PROCEDURAL_MEMORY_SYSTEM_PROMPT},
            *messages,
            {
                "role": "user",
                "content": "Create procedural memory of the above conversation.",
            },
        ]

        try:
            if llm is not None:
                # langchain-core is only needed to adapt messages for a custom LangChain model.
                try:
                    from langchain_core.messages.utils import convert_to_messages  # type: ignore
                except ImportError as e:
                    raise ImportError(
                        "langchain-core is required to pass a custom LLM to procedural memory. "
                        "Install it with 'pip install langchain-core'."
                    ) from e
                response = llm.invoke(input=convert_to_messages(parsed_messages))
                procedural_memory = remove_code_blocks(response.content)
            else:
                procedural_memory = self.llm.generate_response(messages=parsed_messages, task="memory_procedural")
                procedural_memory = remove_code_blocks(procedural_memory)
        except Exception as e:
            logger.error(f"Error generating procedural memory summary: {e}")
            raise

        if not procedural_memory:
            raise ValueError(
                "The model returned no content for the procedural memory summary. "
                "The model may have declined the request or returned an empty response."
            )

        if metadata is None:
            raise ValueError("Metadata cannot be done for procedural memory.")

        metadata = {**metadata, "memory_type": MemoryType.PROCEDURAL.value}
        embeddings = self.embedding_model.embed(procedural_memory, memory_action="add")
        memory_id = self._create_memory(procedural_memory, {procedural_memory: embeddings}, metadata=metadata)

        return {"results": [{"id": memory_id, "memory": procedural_memory, "event": "ADD"}]}

    def _update_memory(self, memory_id, data, existing_embeddings, metadata=None):
        logger.debug(f"Updating memory with {data=}")

        try:
            existing_memory = self.vector_store.get(vector_id=memory_id)
        except Exception:
            # Backing-store failure, not a bad memory_id: re-raise the original so callers map it to 5xx.
            logger.error(f"Error getting memory with ID {memory_id} during update.")
            raise

        if existing_memory is None:
            raise ValueError(f"Memory with id {memory_id} not found. Please provide a valid 'memory_id'")

        prev_value = existing_memory.payload.get("data")
        if data is None:
            data = prev_value
        if not isinstance(data, str):
            raise ValueError(f"Memory with id {memory_id} does not have text content to update")
        text_changed = data != prev_value

        new_metadata = deepcopy(existing_memory.payload)
        if metadata is not None:
            new_metadata.update(_strip_identity_keys(metadata, existing_memory.payload))

        new_metadata["data"] = data
        new_metadata["hash"] = hashlib.md5(data.encode()).hexdigest()
        new_metadata["text_lemmatized"] = lemmatize_for_bm25(data)
        new_metadata["created_at"] = existing_memory.payload.get("created_at")
        new_metadata["updated_at"] = datetime.now(timezone.utc).isoformat()

        if data in existing_embeddings:
            embeddings = existing_embeddings[data]
        else:
            embeddings = self.embedding_model.embed(data, "update")

        self.vector_store.update(
            vector_id=memory_id,
            vector=embeddings,
            payload=new_metadata,
        )

        self.db.add_history(
            memory_id,
            prev_value,
            data,
            "UPDATE",
            created_at=new_metadata["created_at"],
            updated_at=new_metadata["updated_at"],
            actor_id=new_metadata.get("actor_id"),
            role=new_metadata.get("role"),
        )

        # Entity-store cleanup: strip this memory's id from old-text entities,
        # then re-extract entities from the new text and link them back.
        session_filters = scope_ids(new_metadata)
        if text_changed:
            self._remove_memory_from_entity_store(memory_id, session_filters)
            self._link_entities_for_memory(memory_id, data, session_filters)

        return memory_id

    def _delete_memory(self, memory_id, existing_memory=None):
        logger.debug(f"Deleting memory with {memory_id=}")
        if existing_memory is None:
            existing_memory = self.vector_store.get(vector_id=memory_id)
            if existing_memory is None:
                raise ValueError(f"Memory with id {memory_id} not found. Please provide a valid 'memory_id'")
        prev_value = existing_memory.payload.get("data", "")
        created_at = _normalize_iso_timestamp_to_utc(existing_memory.payload.get("created_at"))
        updated_at = datetime.now(timezone.utc).isoformat()
        payload = existing_memory.payload or {}
        session_filters = scope_ids(payload)
        self.vector_store.delete(vector_id=memory_id)
        self.db.add_history(
            memory_id,
            prev_value,
            None,
            "DELETE",
            created_at=created_at,
            updated_at=updated_at,
            actor_id=existing_memory.payload.get("actor_id"),
            role=existing_memory.payload.get("role"),
            is_deleted=1,
        )

        # Entity-store cleanup: strip this memory's id from any entity records that linked to it.
        self._remove_memory_from_entity_store(memory_id, session_filters)

        return memory_id

    def reset(self):
        """
        Reset the memory store:
            Deletes the vector store collection
            Resets the database
            Recreates the vector store with a new client
        """
        logger.warning("Resetting all memories")

        graph_scopes = []
        if self.graph is not None:
            try:
                rows = _vector_store_list_rows(self.vector_store.list(top_k=None))
                graph_scopes = [dict(t) for t in {tuple(sorted(scope_ids(r.payload).items())) for r in rows}]
            except Exception as e:
                logger.warning(f"Could not list memory scopes before reset: {e}")

        self.db.reset()
        self.db.close()
        self.db = SQLiteManager(self.config.history_db_path)

        if hasattr(self.vector_store, "reset"):
            self.vector_store = VectorStoreFactory.reset(self.vector_store)
        else:
            logger.warning("Vector store does not support reset. Skipping.")
            self.vector_store.delete_col()
            self.vector_store = VectorStoreFactory.create(
                self.config.vector_store.provider, self.config.vector_store.config
            )
        # Reset entity store if initialized
        if self._entity_store is not None:
            try:
                self._entity_store.reset()
            except Exception as e:
                logger.warning(f"Failed to reset entity store: {e}")
            self._entity_store = None
        if self.graph is not None:
            self._graph_call("reset", graph_scopes)

    def close(self):
        """Release resources held by this Memory instance (SQLite connections, etc.)."""
        if hasattr(self, "db") and self.db is not None:
            self.db.close()
            self.db = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
        return False

    def chat(self, query, *, filters: Optional[Dict[str, Any]] = None, top_k: int = 10, **search_kwargs):
        """Answer a question from the memories in scope (retrieve, then answer with the model)."""
        relevant = self.search(query, filters=filters, top_k=top_k, **search_kwargs)
        return self.llm.generate_response(
            messages=[
                {"role": "system", "content": MEMORY_ANSWER_PROMPT},
                {"role": "user", "content": format_query_with_memories(query, relevant)},
            ],
            task="memory_chat",
        )


def format_query_with_memories(question: str, relevant: Dict[str, Any]) -> str:
    """The answer prompt: retrieved memories, related graph facts, then the question."""
    memories_text = "\n".join(m["memory"] for m in relevant.get("results", []))
    entities = list(relevant.get("relations") or [])
    return f"- Relevant Memories/Facts: {memories_text}\n\n- Entities: {entities}\n\n- User Question: {question}"


class AsyncMemory(MemoryBase):
    """Asynchronous memory: the same engine, with every blocking step run in a worker thread."""

    def __init__(self, config: Optional[MemoryConfig] = None, *, router: Any = None):
        self._sync = Memory(config, router=router)

    @classmethod
    def from_config(cls, config_dict: Dict[str, Any], *, router: Any = None):
        try:
            config = MemoryConfig(**config_dict)
        except ValidationError as e:
            logger.error(f"Configuration validation error: {e}")
            raise
        return cls(config, router=router)

    # shared components, exposed like the synchronous engine
    config = property(lambda self: self._sync.config)
    llm = property(lambda self: self._sync.llm)
    embedding_model = property(lambda self: self._sync.embedding_model)
    vector_store = property(lambda self: self._sync.vector_store)
    reranker = property(lambda self: self._sync.reranker)
    graph = property(lambda self: self._sync.graph)
    enable_graph = property(lambda self: self._sync.enable_graph)
    entity_store = property(lambda self: self._sync.entity_store)
    collection_name = property(lambda self: self._sync.collection_name)

    @property
    def db(self):
        return self._sync.db

    async def add(self, messages, **kwargs):
        """Create new memories asynchronously. Same arguments as ``Memory.add``."""
        return await asyncio.to_thread(self._sync.add, messages, **kwargs)

    async def add_facts(self, facts, **kwargs):
        return await asyncio.to_thread(self._sync.add_facts, facts, **kwargs)

    async def get(self, memory_id):
        return await asyncio.to_thread(self._sync.get, memory_id)

    async def get_all(self, **kwargs):
        return await asyncio.to_thread(self._sync.get_all, **kwargs)

    async def search(self, query: str, **kwargs):
        return await asyncio.to_thread(self._sync.search, query, **kwargs)

    async def update(self, memory_id, *args, **kwargs):
        return await asyncio.to_thread(self._sync.update, memory_id, *args, **kwargs)

    async def delete(self, memory_id):
        return await asyncio.to_thread(self._sync.delete, memory_id)

    async def delete_all(self, **kwargs):
        return await asyncio.to_thread(self._sync.delete_all, **kwargs)

    async def history(self, memory_id):
        return await asyncio.to_thread(self._sync.history, memory_id)

    async def reset(self):
        return await asyncio.to_thread(self._sync.reset)

    async def chat(self, query, **kwargs):
        return await asyncio.to_thread(self._sync.chat, query, **kwargs)

    def close(self):
        self._sync.close()

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        self.close()
        return False
