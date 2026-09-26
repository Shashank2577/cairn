# Tasks: Cairn Core

**Input**: `specs/001-cairn-core/` (spec and plan)
**Format**: `[ID] [P?] [Story] Description`. `[P]` means parallelisable (different files, no
dependency). Every task names the files that implement it, with full repository paths, so drift can
check that done tasks exist in the code.

## Phase 1: Setup

- [x] T001 Package layout, metadata, entry point and optional extras in `pyproject.toml`, `src/cairn/__init__.py`, `src/cairn/__main__.py`
- [x] T002 [P] Licence and third-party notices in `LICENSE`, `NOTICE`, `licenses/` (FR-022)
- [x] T003 [P] Offline test harness with throwaway git repositories and an isolated Cairn home in `tests/conftest.py`

## Phase 2: Foundational (blocks all stories)

- [x] T004 Repository discovery, paths and default configuration in `src/cairn/project.py` (FR-002)
- [x] T005 Read model: entities, links, events, memories, co-change, file stats, ledger, queries and full-text search in `src/cairn/store.py` (FR-012)
- [x] T006 Model router with providers, task tiers, escalation, budgets and ledger in `src/cairn/router.py` (FR-017)
- [x] T007 [P] Shared local embeddings and vector index, with a hashing fallback, in `src/cairn/engines/vectors.py`

## Phase 3: US1 — One command on any repository (P1)

- [x] T008 [P] [US1] Map engine: tree-sitter extractors, build, clustering, report in `src/cairn/engines/graph/build.py`, `src/cairn/engines/graph/extract.py`, `src/cairn/engines/graph/cluster.py`, `src/cairn/engines/graph/report.py` (FR-003)
- [x] T009 [P] [US1] Map adapter and in-memory index (dependents, hubs, areas, file graph) in `src/cairn/engines/mapper.py` (FR-003)
- [x] T010 [P] [US1] Incremental git history, co-change and risk tags in `src/cairn/engines/history.py` (FR-004)
- [x] T011 [US1] Parallel sync with lock, progress and per-step isolation in `src/cairn/sync.py`, and cross-layer links in `src/cairn/linker.py` (FR-012)
- [x] T012 [P] [US1] Git hooks that sync in the background in `src/cairn/hooks.py` (FR-016)
- [x] T013 [P] [US1] Agent wiring for Claude Code, Codex, Cursor, Gemini CLI, VS Code and `AGENTS.md`, plus uninstall, in `src/cairn/agents.py`, `src/cairn/templates/instructions.md` (FR-015)
- [x] T014 [US1] Zero-prompt init, status, doctor, sync and uninstall in `src/cairn/cli.py` (FR-001, FR-002, FR-021)
- [x] T015 [US1] Tests: init is zero-prompt and idempotent, uninstall removes only Cairn's files, in `tests/test_surfaces.py`

## Phase 4: US2 — Impact (P1)

- [x] T016 [US2] Context packs with two-pass budget packing, citations and provenance; `impact()` and `context()` in `src/cairn/core.py` (FR-013)
- [x] T017 [P] [US2] Tests for impact sections, risk and budgets in `tests/test_core.py`

## Phase 5: US3 — Why (P2)

- [x] T018 [US3] `why()` with rationale, origin commits, spec items and decisions in `src/cairn/core.py`, using line origins from `src/cairn/engines/history.py` (FR-013)

## Phase 6: US4 — Spec-driven work with memory (P2)

- [x] T019 [P] [US4] Spec workflow engine: init, commands, templates and scripts in `src/cairn/engines/workflow/command_init.py`, `src/cairn/engines/workflow/assets/commands/`, `src/cairn/engines/workflow/assets/templates/` (FR-005)
- [x] T020 [P] [US4] 41 agent integrations and their management commands in `src/cairn/engines/workflow/integrations/` (FR-005)
- [x] T021 [P] [US4] Extensions, presets, workflows and bundles in `src/cairn/engines/workflow/extensions/`, `src/cairn/engines/workflow/presets/`, `src/cairn/engines/workflow/workflows/`, `src/cairn/engines/workflow/bundles/` (FR-005)
- [x] T022 [US4] Memory steps in the plan, tasks, clarify and implement commands in `src/cairn/engines/workflow/assets/commands/plan.md`, `src/cairn/engines/workflow/assets/commands/tasks.md`, `src/cairn/engines/workflow/assets/commands/clarify.md`, `src/cairn/engines/workflow/assets/commands/implement.md` (FR-005)
- [x] T023 [US4] Offline bootstrap, legacy-layout migration, spec parser and task status in `src/cairn/engines/specs.py` (FR-005, FR-006)
- [x] T024 [US4] Deterministic and semantic drift in `src/cairn/drift.py` (FR-007)
- [x] T025 [P] [US4] Tests for init, offline use, migration, scripts and management in `tests/engines/test_workflow_init.py`, `tests/engines/test_workflow_migrate.py`, `tests/engines/test_workflow_offline.py`, `tests/engines/test_workflow_scripts.py`, `tests/engines/test_workflow_manage.py`

## Phase 7: US5 — Memory that keeps itself tidy (P2)

- [x] T026 [P] [US5] Memory engine with reconciliation, history and vector stores in `src/cairn/engines/memstore/main.py`, `src/cairn/engines/memstore/vector_stores/`, `src/cairn/engines/memstore/storage.py` (FR-010)
- [x] T027 [US5] Memory adapter (base tier, engine tier, supersession) and seeding from ADRs, clarifications, contributing rules and git history in `src/cairn/engines/memory.py` (FR-010)
- [x] T028 [P] [US5] Memory commands and HTTP API in `src/cairn/engines/memstore/commands.py`, `src/cairn/engines/memstore/api.py`, `src/cairn/cli.py` (FR-010)
- [x] T029 [P] [US5] Tests in `tests/engines/test_memstore.py`, `tests/engines/test_memstore_bridge.py`

## Phase 8: US6 — Agents remember earlier sessions (P1)

- [x] T030 [US6] Standard-library capture hooks with privacy tags and credential masking in `src/cairn/capture.py`, `src/cairn/engines/recall/hooks.py`, `src/cairn/engines/recall/tags.py` (FR-008)
- [x] T031 [US6] Worker, model observer and deterministic fallback in `src/cairn/engines/recall/worker.py`, `src/cairn/engines/recall/observer.py`, `src/cairn/engines/recall/fallback.py` (FR-009)
- [x] T032 [P] [US6] Session search, timeline and session-start context in `src/cairn/engines/recall/search.py`, `src/cairn/engines/recall/context.py`, `src/cairn/engines/recall/mcp.py` (FR-009)
- [x] T033 [US6] Mirror observations and sessions into the read model in `src/cairn/engines/journal.py` (FR-009, FR-012)
- [x] T034 [P] [US6] `cairn sessions` commands, capture for other agents and bundled skills in `src/cairn/engines/recall/cli.py`, `src/cairn/engines/recall/integrations.py`, `src/cairn/engines/recall/skills/` (FR-008)
- [x] T035 [P] [US6] Session routes and live relay on the server in `src/cairn/engines/recall/api.py`, `src/cairn/server.py` (FR-009)
- [x] T036 [P] [US6] Tests in `tests/engines/test_recall_capture.py`, `tests/engines/test_recall_worker.py`, `tests/engines/test_recall_search.py`

## Phase 9: US7 — What was true, and when (P3)

- [x] T037 [US7] Temporal fact graph engine with embedded and server drivers in `src/cairn/engines/temporal/engine.py`, `src/cairn/engines/temporal/driver/kuzu_driver.py`, `src/cairn/engines/temporal/driver/neo4j_driver.py`, `src/cairn/engines/temporal/driver/falkordb_driver.py` (FR-011)
- [x] T038 [US7] Per-project service, router-backed model client and local embedder in `src/cairn/engines/temporal/service.py`, `src/cairn/engines/temporal/llm_client/router_client.py`, `src/cairn/engines/temporal/embedder/local.py` (FR-011, FR-017)
- [x] T039 [US7] Daily episodes under a budget and the read-model mirror in `src/cairn/engines/chronicle.py` (FR-011)
- [x] T040 [P] [US7] `cairn timeline` commands and tool specs in `src/cairn/engines/temporal/cli.py`, `src/cairn/engines/temporal/api.py` (FR-011)
- [x] T041 [P] [US7] Tests in `tests/engines/test_temporal_chronicle.py`, `tests/engines/test_temporal_concurrency.py`, `tests/engines/test_temporal_engine.py`

## Phase 10: US8 — One page for everything (P2)

- [x] T042 [US8] Server with a per-project hub, project routes and live stream in `src/cairn/server.py`; background lifecycle in `src/cairn/daemon.py` (FR-018)
- [x] T043 [P] [US8] Map data for the page in `src/cairn/graphviews.py` and graph views in `src/cairn/engines/graph/tree_html.py`, `src/cairn/engines/graph/callflow_html.py`, `src/cairn/engines/graph/wiki.py` (FR-003)
- [x] T044 [US8] The page: shell, routing and views in `src/cairn/ui/index.html`, `src/cairn/ui/app/main.js`, `src/cairn/ui/app/router.js`, `src/cairn/ui/app/views/` (FR-018)
- [x] T045 [P] [US8] Tests in `tests/test_surfaces.py`, `tests/engines/test_graph_views.py`

## Phase 11: US9 — Every agent gets the same memory (P2)

- [x] T046 [US9] MCP server with the core and all tool sets, generated engine tools and read-only binding in `src/cairn/mcp_server.py` (FR-014)
- [x] T047 [P] [US9] Claude Code commands and model-tiered sub-agents in `src/cairn/templates/claude/commands/`, `src/cairn/templates/claude/agents/` (FR-015)
- [x] T048 [P] [US9] Session-start briefing and status line in `src/cairn/hooks.py` (FR-015)

## Phase 12: US10 — Model features with no key (P2)

- [x] T049 [US10] `auto` provider selection and the Claude Code CLI provider in `src/cairn/router.py` (FR-017)
- [x] T050 [P] [US10] Router-backed model clients for the engines in `src/cairn/engines/graph/llm.py`, `src/cairn/engines/memstore/llms/router.py`, `src/cairn/engines/temporal/llm_client/router_client.py` (FR-017)
- [x] T051 [P] [US10] `cairn models` table and ledger in `src/cairn/cli.py`, `src/cairn/store.py` (FR-017)

## Phase 13: US11 — One server, many projects, a whole team (P2)

- [x] T052 [US11] Server configuration, roles, actions and token scopes in `src/cairn/platform/config.py`, `src/cairn/platform/rbac.py` (FR-019)
- [x] T053 [US11] Platform service, secrets and schema in `src/cairn/platform/service.py`, `src/cairn/platform/security.py`, `src/cairn/platform/db.py`, `src/cairn/platform/models.py` (FR-019)
- [x] T054 [P] [US11] Git clones, refreshes and URL validation in `src/cairn/platform/gitops.py` (FR-019)
- [x] T055 [US11] Auth gate, CSRF, login throttling and platform routes in `src/cairn/platform/web.py` (FR-019)
- [x] T056 [P] [US11] `cairn team`, `cairn token`, `cairn project` and `cairn user` in `src/cairn/platform/commands.py` (FR-019)
- [x] T057 [US11] Per-project MCP over HTTP in `src/cairn/server.py` and `cairn agents connect` in `src/cairn/agents.py` (FR-014, FR-018)
- [x] T058 [P] [US11] Team, sign-in, users and account pages in `src/cairn/ui/app/views/team.js`, `src/cairn/ui/app/views/auth.js`, `src/cairn/ui/app/views/users.js`, `src/cairn/ui/app/views/account.js` (FR-019)
- [x] T059 [P] [US11] Tests in `tests/engines/test_platform_core.py`, `tests/engines/test_platform_rbac.py`, `tests/engines/test_platform_web.py`, `tests/engines/test_platform_git.py`, `tests/engines/test_platform_cli.py`

## Phase 14: US12 — Savings you can see (P3)

- [x] T060 [US12] Log every pack handed to a reader with sent and source tokens in `src/cairn/core.py`, `src/cairn/store.py` (FR-020)
- [x] T061 [P] [US12] Overview page with savings, activity and hubs in `src/cairn/ui/app/views/overview.js` (FR-020)

## Phase 15: Polish

- [x] T062 [P] Documentation in `README.md`, `docs/architecture.md`, `docs/cli.md`, `docs/mcp.md`, `docs/agents.md`, `docs/configuration.md`, `docs/models-and-cost.md`, `docs/teams.md`, `CONTRIBUTING.md`
- [x] T063 [P] Architecture decisions in `docs/adr/`
- [x] T064 [P] Naming tests for the engines in `tests/engines/test_graph_naming.py`, `tests/engines/test_recall_naming.py`, `tests/engines/test_temporal_naming.py` (FR-022)

## Dependencies

Setup → Foundational → US1 → (US2 ∥ US4 ∥ US5 ∥ US6) → US3 → (US7 ∥ US8 ∥ US9 ∥ US10) → US11 → US12
→ Polish. Within a phase, `[P]` tasks can run as parallel sub-agents.

## Converge (remaining work)

- [x] T065 [US6] Accept session events from agents on other machines on a team server (the project capture permission), and have `cairn agents connect` write the `[team]` settings, in `src/cairn/server.py`, `src/cairn/agents.py`, `src/cairn/engines/recall/api.py` (FR-023)
- [x] T066 [US1] Native Windows git hooks in `src/cairn/hooks.py` (FR-016)
- [x] T067 [US8] Stream narrated answers to the page in `src/cairn/server.py` (FR-013)
- [x] T068 [US8] Cross-repository map view on the page in `src/cairn/ui/app/views/map.js` (FR-003)
- [x] T069 [US1] `cairn uninstall` also removes the session skills it installed and git hooks written to a custom git hooks path, in `src/cairn/agents.py`, `src/cairn/hooks.py` (FR-002)
- [x] T070 [US1] `cairn doctor` reports the model provider, local embeddings and the timeline store, and the setup summary names the Claude Code sign-in as a way to turn on model features, in `src/cairn/cli.py` (FR-021)
- [x] T071 [US7] `[temporal] backend` is empty by default and worked out from `url`, in `src/cairn/project.py`, `src/cairn/engines/temporal/service.py` (FR-011)
- [x] T072 [US11] The project template carries no unused `[server]` table; the port lives in the server settings, in `src/cairn/project.py`, `src/cairn/platform/config.py` (FR-018)
- [x] T073 [US1] The one-line installer installs only `cairn-brain`, with optional extras, in `install.sh` (FR-002)
- [x] T074 [US6] Client side of remote capture: outbox, background push and `cairn sessions push` in `src/cairn/engines/recall/remote.py`, `src/cairn/engines/recall/cli.py` (FR-023)
- [x] T075 [US1] Fingerprinted map sync (skip when nothing changed, re-extract only changed files, one incremental pass otherwise) and generated or vendored code ignored by default, in `src/cairn/engines/mapper.py` (FR-003)
