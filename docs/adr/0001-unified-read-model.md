# ADR-0001: One read model for every surface

**Status:** Accepted · **Date:** 2026-09-25

## Context
Each engine keeps the store that suits it: the code graph writes `graph.json`, the spec workflow keeps Markdown
in `specs/`, git holds history, session memory keeps `sessions.db`, the fact graph lives in an embedded graph
database and memory keeps a vector index. Answering "what breaks if I change this?" needs all of them at once,
in milliseconds, with or without a model.

## Decision
Engines write to their own stores; every surface (CLI, MCP tools, HTTP API, page) reads one SQLite read model,
`.cairn/brain.db`: entities, typed links with provenance, events, memories, co-change, full-text search and the
ledgers of model calls and served context. Sync mirrors each engine's output into it with canonical ids
(`file:`, `symbol:`, `task:`, `commit:`, `obs:`, `memory:`, `fact:` …).

## Consequences
- Impact, why and context are dictionary and index lookups; they work offline.
- Any surface can cross layers (a file's dependents, the spec task that owns it, the sessions that touched it
  and the memories about it) without knowing which engine produced what.
- The read model is rebuildable: delete `.cairn/brain.db` and sync.
