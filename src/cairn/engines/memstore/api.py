"""HTTP handlers for the memory engine, framework-free.

Each function takes a ``Memory`` plus request data and returns JSON-ready data, raising ``ApiError``
(with an HTTP status) for client errors. Cairn's server mounts them; authentication and roles come
from the platform layer (``admin`` flags below say which calls need an admin).

  POST   /memories                 add_memories(memory, body)
  GET    /memories                 list_memories(memory, **query)          (no scope: admin only)
  GET    /memories/{id}            get_memory(memory, id)
  PUT    /memories/{id}            update_memory(memory, id, body)
  DELETE /memories/{id}            delete_memory(memory, id)
  DELETE /memories                 delete_all_memories(memory, **scope)    (admin)
  GET    /memories/{id}/history    memory_history(memory, id)
  POST   /search                   search_memories(memory, body)
  POST   /reset                    reset_memories(memory)                  (admin)
  GET    /entities                 list_entities(memory)
  DELETE /entities/{type}/{id}     delete_entity(memory, type, id)         (admin)
  GET    /configure                get_configuration(memory)
  POST   /generate-instructions    generate_instructions(memory, body)
  POST   /v1/chat/completions      chat_completions(proxy, body)
"""
from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field, ValidationError

from cairn.engines.memstore.exceptions import LLMError
from cairn.engines.memstore.exceptions import ValidationError as MemoryValidationError
from cairn.engines.memstore.main import redact_config
from cairn.engines.memstore.scopes import SCOPE_KEYS

logger = logging.getLogger(__name__)

ALL_MEMORIES_LIMIT = 1000
SCAN_LIMIT = 10_000
EntityType = Literal["project", "team", "user", "agent", "run"]
TYPE_TO_FIELD: Dict[str, str] = {"project": "project_id", "team": "team_id", "user": "user_id",
                                 "agent": "agent_id", "run": "run_id"}
_RESERVED_PAYLOAD_KEYS = {"data", "hash", "created_at", "updated_at", "expiration_date", "text_lemmatized",
                          *SCOPE_KEYS}


class ApiError(Exception):
    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status = status
        self.detail = detail


class Message(BaseModel):
    role: str = Field(..., description="Role of the message (user or assistant).")
    content: str = Field(..., description="Message content.")
    name: Optional[str] = Field(None, description="Speaker name (stored as actor_id).")


class MemoryCreate(BaseModel):
    messages: List[Message] = Field(..., description="List of messages to store.")
    user_id: Optional[str] = None
    agent_id: Optional[str] = None
    run_id: Optional[str] = None
    project_id: Optional[str] = None
    team_id: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None
    timestamp: Optional[Any] = Field(None, description="When it was observed (unix seconds or ISO-8601).")
    expiration_date: Optional[str] = Field(None, description="Expiration date in YYYY-MM-DD format.")
    infer: Optional[bool] = Field(None, description="Whether to extract facts from messages. Defaults to True.")
    memory_type: Optional[str] = Field(None, description="'procedural_memory' for an agent run summary.")
    prompt: Optional[str] = Field(None, description="Custom prompt to use for fact extraction.")


class MemoryUpdate(BaseModel):
    text: Optional[str] = Field(None, description="New content to update the memory with.")
    metadata: Optional[Dict[str, Any]] = Field(None, description="Metadata to update.")
    expiration_date: Optional[str] = Field(None, description="Expiration date in YYYY-MM-DD format, or null to clear.")


class SearchRequest(BaseModel):
    query: str = Field(..., description="Search query.")
    filters: Optional[Dict[str, Any]] = None
    top_k: Optional[int] = Field(None, description="Maximum number of results to return.")
    threshold: Optional[float] = Field(None, description="Minimum similarity score for results.")
    rerank: Optional[bool] = Field(None, description="Rerank results with the configured reranker.")
    explain: Optional[bool] = Field(None, description="Include score details for each search result.")
    show_expired: Optional[bool] = Field(None, description="Include expired memories.")
    # scope ids may also be given at the top level
    user_id: Optional[str] = None
    agent_id: Optional[str] = None
    run_id: Optional[str] = None
    project_id: Optional[str] = None
    team_id: Optional[str] = None


class GenerateInstructionsRequest(BaseModel):
    use_case: str = Field(..., description="What the memory will be used for.")


def _parse(model, body: Any):
    try:
        return model.model_validate(body or {})
    except ValidationError as exc:
        raise ApiError(422, str(exc)) from exc


def _client_error(exc: Exception) -> ApiError:
    """'not found' is a 404, other validation problems a 400."""
    detail = str(exc)
    status = 404 if isinstance(exc, ValueError) and "not found" in detail.lower() else 400
    return ApiError(status, detail)


def _serialize_row(row: Any) -> Dict[str, Any]:
    payload = getattr(row, "payload", None) or {}
    out = {"id": getattr(row, "id", None), "memory": payload.get("data"), "hash": payload.get("hash"),
           "expiration_date": payload.get("expiration_date"),
           "metadata": {k: v for k, v in payload.items() if k not in _RESERVED_PAYLOAD_KEYS},
           "created_at": payload.get("created_at"), "updated_at": payload.get("updated_at")}
    out.update({k: payload.get(k) for k in SCOPE_KEYS})
    return out


def _rows(listed: Any) -> List[Any]:
    if listed and isinstance(listed, (list, tuple)) and isinstance(listed[0], list):
        return listed[0]
    return list(listed or [])


# ---- memories ------------------------------------------------------------------------------------
def add_memories(memory, body: Any) -> Dict[str, Any]:
    req = _parse(MemoryCreate, body)
    if not any(getattr(req, k) for k in SCOPE_KEYS):
        raise ApiError(400, "At least one identifier (" + ", ".join(SCOPE_KEYS) + ") is required.")
    params = {k: v for k, v in req.model_dump().items() if v is not None and k != "messages"}
    try:
        return memory.add(messages=[m.model_dump(exclude_none=True) for m in req.messages], **params)
    except (ValueError, MemoryValidationError) as exc:
        raise _client_error(exc) from exc
    except LLMError as exc:
        raise ApiError(503, f"needs a model: {exc}") from exc


def list_memories(memory, *, top_k: Optional[int] = None, show_expired: bool = False, admin: bool = False,
                  **scope: Optional[str]) -> Dict[str, Any]:
    filters = {k: v for k, v in scope.items() if k in SCOPE_KEYS and v}
    if top_k is not None and not (0 <= top_k <= ALL_MEMORIES_LIMIT):
        raise ApiError(422, f"top_k must be between 0 and {ALL_MEMORIES_LIMIT}")
    if not filters:
        if not admin:
            raise ApiError(403, "Admin role required to list all memories.")
        limit = top_k if top_k is not None else ALL_MEMORIES_LIMIT
        return {"results": [_serialize_row(r) for r in _rows(memory.vector_store.list(top_k=limit))]}
    params: Dict[str, Any] = {"filters": filters, "show_expired": show_expired}
    if top_k is not None:
        params["top_k"] = top_k
    return memory.get_all(**params)


def get_memory(memory, memory_id: str) -> Dict[str, Any]:
    item = memory.get(memory_id)
    if item is None:
        raise ApiError(404, f"Memory with id {memory_id} not found")
    return item


def search_memories(memory, body: Any) -> Dict[str, Any]:
    req = _parse(SearchRequest, body)
    filters = dict(req.filters or {})
    for key in SCOPE_KEYS:
        if getattr(req, key):
            filters[key] = getattr(req, key)
    params = {k: getattr(req, k) for k in ("top_k", "threshold", "rerank", "explain", "show_expired")
              if getattr(req, k) is not None}
    try:
        return memory.search(query=req.query, filters=filters, **params)
    except ValueError as exc:
        raise ApiError(400, str(exc)) from exc


def update_memory(memory, memory_id: str, body: Any) -> Dict[str, Any]:
    req = _parse(MemoryUpdate, body)
    fields_set = req.model_fields_set
    params: Dict[str, Any] = {}
    if "text" in fields_set:
        params["text"] = req.text
    if "metadata" in fields_set:
        params["metadata"] = req.metadata
    if "expiration_date" in fields_set:
        params["expiration_date"] = req.expiration_date
    try:
        return memory.update(memory_id, **params)
    except (ValueError, MemoryValidationError) as exc:
        raise _client_error(exc) from exc


def memory_history(memory, memory_id: str) -> List[Dict[str, Any]]:
    return memory.history(memory_id)


def delete_memory(memory, memory_id: str) -> Dict[str, str]:
    try:
        memory.delete(memory_id)
    except (ValueError, MemoryValidationError) as exc:
        raise _client_error(exc) from exc
    return {"message": "Memory deleted successfully"}


def delete_all_memories(memory, **scope: Optional[str]) -> Dict[str, str]:
    params = {k: v for k, v in scope.items() if k in SCOPE_KEYS and v}
    if not params:
        raise ApiError(400, "At least one identifier is required.")
    memory.delete_all(**params)
    return {"message": "All relevant memories deleted"}


def reset_memories(memory) -> Dict[str, str]:
    memory.reset()
    return {"message": "All memories reset"}


# ---- entities (who/what has memories) --------------------------------------------------------------
def _parse_timestamp(value: Any) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def list_entities(memory) -> List[Dict[str, Any]]:
    """Every scope id that owns memories, with counts and first/last activity."""
    buckets: Dict[tuple, Dict[str, Any]] = defaultdict(
        lambda: {"total_memories": 0, "created_at": None, "updated_at": None})
    for row in _rows(memory.vector_store.list(top_k=SCAN_LIMIT)):
        payload = getattr(row, "payload", None) or {}
        created = _parse_timestamp(payload.get("created_at"))
        updated = _parse_timestamp(payload.get("updated_at")) or created
        for entity_type, field in TYPE_TO_FIELD.items():
            value = payload.get(field)
            if not value:
                continue
            bucket = buckets[(entity_type, str(value))]
            bucket["total_memories"] += 1
            if created and (bucket["created_at"] is None or created < bucket["created_at"]):
                bucket["created_at"] = created
            if updated and (bucket["updated_at"] is None or updated > bucket["updated_at"]):
                bucket["updated_at"] = updated
    return [
        {"id": entity_id, "type": entity_type, "total_memories": data["total_memories"],
         "created_at": data["created_at"].isoformat() if data["created_at"] else None,
         "updated_at": data["updated_at"].isoformat() if data["updated_at"] else None}
        for (entity_type, entity_id), data in sorted(buckets.items(), key=lambda item: (item[0][0], item[0][1]))
    ]


def delete_entity(memory, entity_type: str, entity_id: str) -> Dict[str, str]:
    if entity_type not in TYPE_TO_FIELD:
        raise ApiError(422, f"entity type must be one of {sorted(TYPE_TO_FIELD)}")
    memory.delete_all(**{TYPE_TO_FIELD[entity_type]: entity_id})
    return {"message": "Entity deleted"}


# ---- configuration -------------------------------------------------------------------------------
def get_configuration(memory) -> Dict[str, Any]:
    """The engine configuration with secrets redacted."""
    cfg = memory.config.model_dump(exclude={"vector_store", "llm", "embedder", "reranker", "graph_store"})
    for section in ("vector_store", "llm", "embedder", "reranker", "graph_store"):
        value = getattr(memory.config, section)
        if value is None:
            cfg[section] = None
            continue
        inner = getattr(value, "config", None)
        cfg[section] = {**{k: v for k, v in value.__dict__.items() if k != "config"},
                        "config": inner.model_dump() if hasattr(inner, "model_dump") else inner}
    return redact_config(cfg)


def generate_instructions(memory, body: Any) -> Dict[str, str]:
    """Draft extraction instructions (and a test message) for a described use case."""
    req = _parse(GenerateInstructionsRequest, body)
    prompt = (
        "You are configuring a memory system. Given the use case below, produce two things:\n"
        "1. INSTRUCTIONS: A short paragraph of custom instructions telling the memory extraction system "
        "what kinds of facts, preferences, and context to prioritize. Be specific to the use case.\n"
        "2. TEST_MESSAGE: A single realistic sentence a user in this use case would say, suitable for "
        "testing that the memory system works.\n\n"
        "Respond in exactly this format (no markdown, no extra text):\n"
        "INSTRUCTIONS: <your instructions>\n"
        f"TEST_MESSAGE: <your test message>\n\nUse case: {req.use_case}"
    )
    try:
        response = memory.llm.generate_response([{"role": "user", "content": prompt}], task="memory_instructions")
    except LLMError as exc:
        raise ApiError(503, f"needs a model: {exc}") from exc
    instructions, test_message = response, "We always run the linter before committing."
    if "INSTRUCTIONS:" in response and "TEST_MESSAGE:" in response:
        parts = response.split("TEST_MESSAGE:")
        instructions = parts[0].replace("INSTRUCTIONS:", "").strip()
        test_message = parts[1].strip()
    return {"custom_instructions": instructions, "test_message": test_message}


# ---- OpenAI-compatible chat ------------------------------------------------------------------------
def chat_completions(proxy, body: Dict[str, Any]) -> Dict[str, Any]:
    """POST /v1/chat/completions: an OpenAI chat completion with memory (scope ids in the body)."""
    body = dict(body or {})
    try:
        return proxy.chat.completions.create(**body)
    except ValueError as exc:
        raise ApiError(400, str(exc)) from exc
    except (LLMError, RuntimeError) as exc:
        raise ApiError(503, f"needs a model: {exc}") from exc
