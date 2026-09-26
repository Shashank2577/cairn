# ADR-0004: One model layer, task-tiered, any provider

**Status:** Accepted · **Date:** 2026-09-25

## Context
Five engines need models for different jobs, from thousands of small classifications to a few judgments. Each
used to carry its own provider clients and keys. Most developers already have a signed-in coding agent.

## Decision
Every model call goes through `cairn.router`. Providers: `anthropic` (API key), `openai` (any OpenAI-compatible
endpoint, including local ones) and `claude-code` (the signed-in Claude Code CLI, the developer's own plan, no
key). `provider = "auto"` picks the first that works. Calls through the CLI run isolated: no user settings,
hooks, plugins, MCP servers or tools, from an empty folder. Jobs map to tiers (fast, balanced, deep, frontier)
in `TASK_TIER`; oversized input escalates one tier at most and never to frontier. Budgets cap each sync;
every call is written to the ledger (`cairn models --ledger`).

## Consequences
- Model features work out of the box for anyone signed in to Claude Code.
- Cost and quota are visible per task and per tier.
