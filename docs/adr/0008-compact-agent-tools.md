# ADR-0008: A compact default tool set for agents

**Status:** Accepted · **Date:** 2026-09-25

## Context
Every MCP tool's schema is sent to the model on every agent turn. The engines expose close to fifty
operations; all of them cost about 8,600 tokens per turn before the agent does anything.

## Decision
Agents get a core set by default (context, impact, why, search, trace, specs, remember, recall, facts and the
session search tools: search, timeline, full observations), about 1,600 tokens per turn. `[mcp] tools = "all"` exposes every graph, timeline and
session operation for agents that need them. The HTTP API always exposes everything.

## Consequences
- Cairn saves agents more tokens than its tools cost them.
- Power operations remain one setting away.
