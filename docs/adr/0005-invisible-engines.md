# ADR-0005: One product, engines built in

**Status:** Accepted · **Date:** 2026-09-25

## Context
Cairn does five jobs that each deserve a full engine: a code and document knowledge graph, a spec-driven
workflow, agent session memory, a temporal fact graph and self-reconciling memory. Installing and wiring five
separate tools, each with its own names, folders, commands and model setup, is not a product.

## Decision
All five engines are part of Cairn's source (`src/cairn/engines/{graph,workflow,recall,temporal,memstore}`),
under Cairn's names, with their complete feature sets, sharing one model layer, one embedding and vector
module, one read model, one CLI, one MCP server, one HTTP API and one page. Where two engines did the same job,
one implementation serves both (one vector index, one router, one session store). Nothing is installed
behind the user's back. Copyright notices required by the engines' licences are kept in `NOTICE` and
`licenses/`, and nowhere else.

## Consequences
- Users learn one vocabulary: Map, Specs, Timeline, Memory, Sessions.
- Cairn is responsible for maintaining every engine.
