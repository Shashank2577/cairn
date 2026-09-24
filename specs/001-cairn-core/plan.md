# Implementation Plan: Cairn Core

**Branch**: `001-cairn-core` | **Date**: 2026-09-24 | **Spec**: [spec.md](./spec.md)

## Summary

Cairn is a Python CLI + local daemon that composes five engines behind one registry. Engines
write; one SQLite **read model** (`.cairn/brain.db`) serves every surface — CLI, MCP, HTTP/UI —
so answers are fast, uniform and available offline. A **context assembler** turns any question
into a ranked, cited, token-budgeted pack. A **model router** sends model work to the cheapest
capable tier. Integration is delivered as generated agent config + a spec-workflow extension.

## Technical Context

**Language/Version**: Python 3.11+ (engines are Python; one runtime keeps install to one command)
**Primary Dependencies**: typer + rich (CLI), fastapi + uvicorn (daemon), mcp (agent protocol),
code-map engine (AST graph), anthropic SDK; optional extras `deep` (temporal graph, semantic
memory, local embeddings)
**Storage**: SQLite with FTS5 (read model, memories, ledger); map JSON (engine-owned);
embedded graph store or FalkorDB/Neo4j (deep tier)
**Testing**: pytest (unit + CLI smoke on generated git repos), no network in tests
**Target Platform**: macOS, Linux, WSL; Windows best-effort
**Project Type**: CLI + local web service (single project)
**Performance Goals**: status < 300 ms; context pack < 800 ms p95 (5k files); map update
incremental; UI first paint < 1 s
**Constraints**: zero keys/zero Docker for core; loopback-only server; context ≤ budget
**Scale/Scope**: repos to ~50k files / 200k map nodes; UI aggregates to communities

## Constitution Check

| Principle | How the plan satisfies it | Status |
|---|---|---|
| I Invisible seams | Engines behind `cairn.engines.*` adapters; user copy uses five layer names | PASS |
| II Deterministic first | Impact/why/trace/drift implemented on read model; LLM only in `narrate()` | PASS |
| III One command | `cairn` → init pipeline; all optional parts detected | PASS |
| IV Day one → compounding | Map + git + specs day one; hooks + sessions + memory accrue | PASS |
| V Token discipline | Assembler budgets, cursors, hashes, batching, prompt caching | PASS |
| VI Model routing | `router.py` tiers + escalation + ledger | PASS |
| VII Local-first | `.cairn/` state, 127.0.0.1 bind, ledger shows egress | PASS |
| VIII Agent-neutral | One MCP server + per-agent generated files | PASS |

## Project Structure

### Documentation (this feature)

```text
specs/001-cairn-core/
├── spec.md  plan.md  research.md  data-model.md  quickstart.md  tasks.md  analysis.md
├── contracts/ (cli.md, mcp.md, http.md)
└── checklists/requirements.md
```

### Source Code (repository root)

```text
src/cairn/
├── cli.py            # Typer app, Rich rendering (the terminal experience)
├── project.py        # repo discovery, paths, config
├── store.py          # SQLite read model: entities, links, events, memories, kv, ledger, FTS
├── router.py         # model tiers, providers, prompt caching, budget, ledger
├── engines/
│   ├── mapper.py     # code/doc map build + graph index + query/path/explain/affected/hubs
│   ├── specs.py      # spec workflow bootstrap, extension install, artifact parser
│   ├── history.py    # git ingest, co-change, risk commits, line origins
│   ├── journal.py    # captured agent sessions (read-only, schema-tolerant)
│   ├── memory.py     # memories: local FTS store + semantic engine adapter
│   └── chronicle.py  # temporal fact graph adapter (embedded/server), local embedder
├── linker.py         # cross-layer entity resolution
├── context.py        # context assembler, impact, why, ask
├── drift.py          # deterministic + semantic drift
├── sync.py           # fan-out orchestrator with progress events and locking
├── agents.py         # agent integrations (Claude Code, Codex, Cursor, Gemini, VS Code, AGENTS.md)
├── hooks.py          # git hooks, session-start briefing, status line
├── mcp_server.py     # 8 MCP tools
├── server.py         # HTTP API + UI
├── daemon.py         # background server lifecycle
├── ui/index.html     # single-page app (no build step, no CDN required)
├── speckit_extension/# spec-workflow extension: manifest, commands
└── templates/        # agent command/sub-agent/instruction templates
tests/
docs/ (architecture, cli, mcp, configuration, models-and-cost, agents, adr/)
```

**Structure Decision**: single Python package; UI is one static file served by the daemon so
installation stays one command and works offline.

## Complexity Tracking

| Violation | Why Needed | Simpler Alternative Rejected Because |
|---|---|---|
| Separate read model beside engine stores | Uniform, fast, offline queries across five engines | Querying five stores per request is slow and couples UI to engine schemas |
| Optional embedded graph store (deprecated upstream) | Zero-Docker deep tier | Requiring a graph server violates Principle III; migration path documented (ADR-0003) |
