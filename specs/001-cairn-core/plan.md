# Implementation Plan: Cairn Core

**Branch**: `001-cairn-core` | **Date**: 2026-09-25 | **Spec**: [spec.md](./spec.md)

## Summary

Cairn is one Python package with five built-in engines: the code and document graph, the spec
workflow, session memory, the temporal fact graph and the memory engine. Engines write to their own
stores. A sync orchestrator mirrors them into one SQLite read model (`.cairn/brain.db`), and every
surface reads from it: the CLI, the MCP server, the HTTP API and the page. Answers are ranked, cited,
token-budgeted packs built without a model (FR-012, FR-013). One router carries every model call for
every engine (FR-017). One server per machine serves every project, and in team mode adds the
platform: users, teams, roles, tokens, git projects, webhooks and audit (FR-018, FR-019).

## Technical Context

**Language/Version**: Python 3.11+
**Primary Dependencies**: typer and rich (CLI); fastapi and uvicorn (server); mcp (agent protocol,
stdio and streamable HTTP); tree-sitter with 25 grammars and networkx (map); kuzu (embedded fact
graph); fastembed, faiss-cpu and numpy (local embeddings and vectors); anthropic and httpx (model
providers); pyyaml, json5, pathspec and packaging (workflow). Optional extras add server backends
(Neo4j, FalkorDB, Qdrant, pgvector, Chroma), more languages and document inputs.
**Storage**: SQLite with FTS5 for the read model (`.cairn/brain.db`), sessions (`.cairn/sessions.db`)
and the platform (`$CAIRN_HOME/platform.db`); node-link JSON for the map (`.cairn/graph/`); an embedded
Kuzu graph for facts (`.cairn/temporal/graph.kuzu`); an on-disk vector index for memory
(`.cairn/memstore/`) and sessions (`.cairn/recall/`).
**Testing**: pytest on throwaway git repositories, with no network and no model calls (hashing
embedder, model CLIs blocked, isolated `$CAIRN_HOME`); ruff.
**Target Platform**: macOS and Linux; Windows best effort (git hooks are POSIX shell scripts).
**Project Type**: CLI, local or team web service, MCP server; single package.
**Performance Goals**: status under 300 ms; context packs under 800 ms p95 on 5,000 files without a
model; incremental syncs; page first paint under 1 s.
**Constraints**: no key, no Docker and no database server for any core feature; loopback-only without
sign-in; packs within budget; hooks never block or break an agent.
**Scale/Scope**: repositories up to tens of thousands of files; many projects per server.

## Constitution Check

| Principle | How the plan satisfies it | Status |
|---|---|---|
| I One product, invisible seams | Engines live in `src/cairn/engines/*` under Cairn's names; users see five layers; notices only in `NOTICE` and `licenses/` (FR-022) | PASS |
| II Deterministic first | Impact, why, context, drift, map, history, spec parsing and capture need no model; models only enrich (FR-013) | PASS |
| III One command | `cairn` runs the whole setup with no prompts; optional capabilities are detected (FR-001, FR-002) | PASS |
| IV Day one, compounding | Map, history, specs and seeded memory on day one; sessions, memories and facts accrue (FR-009, FR-010, FR-011) | PASS |
| V Token discipline | Budgeted packs, a compact MCP core set, cursors and digests, daily episodes, prompt caching (FR-014, FR-020) | PASS |
| VI Right model for the job | `router.TASK_TIER`, bounded escalation, budgets, ledger (FR-017) | PASS |
| VII Local-first and private | State in `.cairn/` and `$CAIRN_HOME`; loopback without sign-in; `<private>` and credentials never stored (FR-008) | PASS |
| VIII Agent-neutral | One MCP server, generated instructions, 41 workflow integrations, capture for other agents (FR-005, FR-015) | PASS |

## Project Structure

### Documentation (this feature)

```text
specs/001-cairn-core/
├── spec.md
├── plan.md
└── tasks.md
```

### Source Code (repository root)

```text
src/cairn/
├── cli.py              # the terminal: setup, status, questions, layers, servers, platform commands
├── project.py          # repository discovery, paths, DEFAULT_CONFIG
├── store.py            # read model: entities, links, events, memories, cochange, filestats, ledger, queries, FTS
├── router.py           # model layer: providers, tiers, escalation, budgets, ledger
├── core.py             # Cairn facade: impact, why, context, ask, brief, overview, savings
├── sync.py             # parallel fan-out sync with lock and progress
├── linker.py           # map mirror and memory/fact links
├── drift.py            # deterministic and semantic drift
├── graphviews.py       # map data for the page
├── agents.py           # agent wiring, agents connect, uninstall
├── hooks.py            # git hooks, session-start briefing, status line
├── capture.py          # hook entry point for session capture
├── mcp_server.py       # MCP tools: core and all; stdio and per-project HTTP
├── server.py           # FastAPI app: hub, project routes, MCP endpoint, page
├── daemon.py           # background local server
├── engines/
│   ├── graph/          # map engine (extractors, build, cluster, views, wiki, exports, PRs)
│   ├── workflow/       # spec workflow engine (commands, templates, integrations, extensions, presets, workflows, bundles)
│   ├── recall/         # session memory engine (hooks, worker, observer, search, context, integrations, skills)
│   ├── temporal/       # temporal fact graph engine (drivers, search, extraction, service, CLI)
│   ├── memstore/       # memory engine (reconciliation, vector stores, rerankers, API)
│   ├── mapper.py specs.py history.py chronicle.py journal.py memory.py   # adapters into the read model
│   └── vectors.py      # shared local embeddings and vector index
├── platform/           # config, rbac, service, security, gitops, web, commands, db, models
├── templates/          # agent instructions, Claude Code commands and sub-agents
└── ui/                 # the page: Preact app, styles, vendored libraries
tests/
├── conftest.py test_core.py test_surfaces.py
└── engines/            # one suite per engine plus platform and naming tests
docs/                   # architecture, cli, mcp, agents, configuration, models-and-cost, teams, adr/
```

**Structure Decision**: One package and one install. The engines stay separate packages under
`engines/` so each can be tested and maintained on its own, but everything a user sees goes through the
shared surfaces. The page is static files served by the server, with no build step and no CDN, so it
works offline.

## Complexity Tracking

| Violation | Why Needed | Simpler Alternative Rejected Because |
|---|---|---|
| A read model beside the engine stores | Fast, uniform, offline queries across five engines (ADR-0001) | Querying five stores per request is slow and couples every surface to every engine's schema |
| Five full engines in one package | Users get complete features under one vocabulary (ADR-0005) | Separate installs mean five tools, five sets of names and five model setups |
| An embedded graph database | The fact graph needs no server (ADR-0003) | Requiring a graph server breaks "one command"; server backends remain available for teams |
| A platform layer in a local tool | The same server serves a solo developer and a team (ADR-0007) | A separate team product would fork the codebase and the data model |
