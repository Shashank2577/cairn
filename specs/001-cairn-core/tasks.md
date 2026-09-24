# Tasks: Cairn Core

**Input**: `specs/001-cairn-core/` (spec, plan, research, data-model, contracts)
**Format**: `[ID] [P?] [Story] Description` — `[P]` = parallelisable (different files, no deps).
**Execution tiers** (sub-agent routing used to build this feature): 🟢 fast/Haiku (boilerplate,
templates, docs polish) · 🔵 balanced/Sonnet (implementation) · 🟣 deep/Opus (architecture,
cross-cutting logic, review) · ⚪ frontier/Fable (whole-system review, opt-in).

## Phase 1: Setup

- [x] T001 🟢 Create package layout `src/cairn/`, `tests/`, `docs/` and `pyproject.toml`
- [x] T002 [P] 🟢 Add `LICENSE`, `THIRD_PARTY_NOTICES.md`, `.gitignore`, `CHANGELOG.md`
- [x] T003 [P] 🟢 Add `install.sh` one-line installer

## Phase 2: Foundational (blocks all stories)

- [x] T004 🟣 Project discovery and config in `src/cairn/project.py`
- [x] T005 🟣 Read model (entities, links, events, memories, cochange, kv, ledger, FTS) in `src/cairn/store.py`
- [x] T006 🟣 Model router with tiers, caching, budget, ledger in `src/cairn/router.py`
- [x] T007 [P] 🔵 Test harness creating throwaway git repos in `tests/conftest.py`

## Phase 3: US1 — One command on an existing repo (P1) 🎯 MVP

- [x] T008 [P] [US1] 🔵 Map engine adapter (build, index, neighbours, affected, hubs, areas) in `src/cairn/engines/mapper.py`
- [x] T009 [P] [US1] 🔵 Git history ingest, co-change, risk commits in `src/cairn/engines/history.py`
- [x] T010 [US1] 🟣 Fan-out sync orchestrator with lock and progress in `src/cairn/sync.py`
- [x] T011 [P] [US1] 🔵 Git hooks + background quick sync in `src/cairn/hooks.py`
- [x] T012 [P] [US1] 🔵 Agent integrations for Claude Code, Codex, Cursor, Gemini, VS Code, AGENTS.md in `src/cairn/agents.py`
- [x] T013 [US1] 🔵 Init pipeline, status, doctor, uninstall and Rich rendering in `src/cairn/cli.py`
- [x] T014 [US1] 🔵 Smoke test: init on generated repo is zero-prompt and idempotent in `tests/test_cli.py`

## Phase 4: US2 — Impact (P1)

- [x] T015 [US2] 🟣 Context assembler with ranking, budget packing, citations in `src/cairn/context.py`
- [x] T016 [US2] 🔵 `impact()` combining map, co-change, risk, owners, sessions, memories in `src/cairn/context.py`
- [x] T017 [P] [US2] 🔵 Tests for impact sections and budget in `tests/test_context.py`

## Phase 5: US3 — Why (P2)

- [x] T018 [US3] 🔵 Line-origin commits in `src/cairn/engines/history.py`
- [x] T019 [US3] 🔵 `why()` with rationale, origins, spec items, decisions in `src/cairn/context.py`

## Phase 6: US4 — Spec-driven work with memory (P2)

- [x] T020 [P] [US4] 🔵 Spec artifact parser (features, stories, FRs, tasks, progress, file refs) in `src/cairn/engines/specs.py`
- [x] T021 [P] [US4] 🟢 Workflow extension manifest and commands in `src/cairn/speckit_extension/`
- [x] T022 [US4] 🔵 Bootstrap + extension install in `src/cairn/engines/specs.py`
- [x] T023 [US4] 🟣 Deterministic drift checks in `src/cairn/drift.py`
- [x] T024 [P] [US4] 🔵 Tests for parser and drift in `tests/test_specs.py`

## Phase 7: US5 — Memory that compounds (P2)

- [x] T025 [P] [US5] 🔵 Memory store (FTS base tier, supersession) in `src/cairn/engines/memory.py`
- [x] T026 [P] [US5] 🔵 Session journal reader (schema-tolerant) in `src/cairn/engines/journal.py`
- [x] T027 [US5] 🟣 Cross-layer linker in `src/cairn/linker.py`
- [x] T028 [P] [US5] 🔵 Tests for memory, journal, linker in `tests/test_memory.py`

## Phase 8: US6 — One page (P2)

- [x] T029 [US6] 🔵 HTTP API in `src/cairn/server.py` and daemon lifecycle in `src/cairn/daemon.py`
- [x] T030 [US6] 🟣 Single-page UI in `src/cairn/ui/index.html`
- [x] T031 [P] [US6] 🔵 API tests in `tests/test_server.py`

## Phase 9: US7 — Premium agent experience (P3)

- [x] T032 [US7] 🔵 MCP server (8 tools) in `src/cairn/mcp_server.py`
- [x] T033 [P] [US7] 🟢 Slash commands, sub-agents (tiered), instruction blocks in `src/cairn/templates/`
- [x] T034 [P] [US7] 🔵 Session-start briefing and status line in `src/cairn/hooks.py`
- [x] T035 [P] [US7] 🔵 MCP tool tests in `tests/test_mcp.py`

## Phase 10: US8 — Deep tier (P3)

- [x] T036 [US8] 🟣 Temporal fact graph adapter with local embedder + reranker in `src/cairn/engines/chronicle.py`
- [x] T037 [US8] 🔵 Semantic memory adapter in `src/cairn/engines/memory.py`
- [x] T038 [US8] 🟣 Deep sync (daily episodes, budget) and narration in `src/cairn/sync.py`, `src/cairn/context.py`
- [x] T039 [US8] 🟣 Semantic drift judge (top-K candidates only) in `src/cairn/drift.py`

## Phase 11: Polish

- [x] T040 [P] 🟢 README, docs/architecture, cli, mcp, configuration, models-and-cost, agents
- [x] T041 [P] 🟣 ADRs 0001–0005 in `docs/adr/`
- [x] T042 🟣 Analyze cross-artifact consistency → `specs/001-cairn-core/analysis.md`
- [x] T043 ⚪ Whole-system review and converge pass (append remaining work below)

## Dependencies

Setup → Foundational → US1 → (US2 ∥ US4 ∥ US5) → US3 → US6 ∥ US7 → US8 → Polish.
Within a phase, `[P]` tasks run as parallel sub-agents; the orchestrator (deep tier) reviews each
merge. Model tiers above are also the defaults Cairn's own sub-agents use in user projects.

## Converge (remaining work, appended by T043)

- [ ] T044 [P] 🔵 Windows-native hook scripts (PowerShell) in `src/cairn/hooks.py`
- [ ] T045 [P] 🔵 Streaming narrated answers over SSE in `src/cairn/server.py`
- [ ] T046 🟣 Multi-repo workspace view (global map) in `src/cairn/server.py` and UI
- [ ] T047 [P] 🔵 Publish extension to the workflow community catalog
