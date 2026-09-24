# ADR-0001: One read model for every surface

**Status:** Accepted · **Date:** 2026-09-24

## Context
Five engines each keep their own store: a node-link JSON map, spec Markdown, git, a session SQLite
database, a vector store and a temporal graph. Surfaces (CLI, MCP, UI) need joined answers
("callers + incidents + owning task + conventions") in well under a second.

## Decision
Engines write into one SQLite database (`.cairn/brain.db`: entities, links, events, memories,
co-change, file stats, ledger, FTS5). Surfaces never query engine stores at request time. The map
index is the one exception: it stays in memory, mtime-cached, for graph traversals.

## Options considered
| Option | Latency | Coupling | Offline |
|---|---|---|---|
| **Read model (chosen)** | ms | Surfaces depend on one schema | Yes |
| Federated queries per request | 100s of ms to seconds | UI coupled to five schemas | Partly |
| Put everything in a graph DB | ms | Requires a server; Docker | No |

## Consequences
Answers are fast and uniform. Sync must keep the read model fresh (incremental cursors). Stale rows
are avoided by source-scoped `drop_source` before re-ingest.
