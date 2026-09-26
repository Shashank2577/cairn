# ADR-0003: Embedded stores by default, servers for teams

**Status:** Accepted · **Date:** 2026-09-25

## Context
The fact graph, the memory index and session memory each need storage. Requiring a database server would
break "one command on any repository"; a team server needs shared, scalable stores.

## Decision
Defaults are embedded and local: the fact graph in an embedded graph database at `.cairn/temporal/graph.kuzu`,
memory vectors in `.cairn/memstore/`, sessions in `.cairn/sessions.db`. Teams can point the fact graph at
Neo4j or FalkorDB (`[temporal] url`) and memory at Qdrant, pgvector or Chroma (`[memory] vector_store`) with no
other change. The embedded graph database is a single-writer file: every process opens it per operation and
releases it, so the server, `cairn sync` from git hooks and agents never lock each other out.

## Consequences
- Nothing to install or run for an individual.
- Facts and memories are always mirrored into the read model, so surfaces never depend on a store being open.
