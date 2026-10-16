---
description: "Tasks for the system model and diagram standard"
---

# Tasks: System Model and Diagram Standard

**Input**: Design documents from `/specs/002-system-model/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md

**Tests**: Required. The constitution asks for well-tested code, and each story has fixture-based golden tests.

## Status (checkpoint 2026-10-08)

Ticked = code and passing tests exist. **partial** = some of it exists, with what is missing. open = not started.
Counts at this checkpoint: 28 ticked, 9 partial, 20 open (57 tasks).

Split into slices 003 to 008 (see the spec's umbrella index); each slice's tasks.md carries the same ids and ticks.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (US1 to US7)

## Phase 1: Setup

- [x] T000 Constitution PATCH amendment: provenance vocabulary adds declared, ambiguous, stale (principle II) in `.cairn/workflow/memory/constitution.md` — done: constitution 1.1.1 (doc change, no test needed)

- [x] T001 Create the `src/cairn/system/` package skeleton (`model.py`, `signals/`, `catalog/`, `diagram/`, `link.py`, `views.py`, `recheck.py`) and `tests/system/` — done
- [x] T002 [P] Fixture products in `tests/system/fixtures/`: shop (from `specs/002-system-model/prototype/sandbox`), spring (Java), gonet (Go), a monorepo with several deploy units, and one real open-source multi-service product; each `GROUND_TRUTH.yaml` committed before extraction is first run on it — done: shop, orders (Spring+Go), example-voting-app (real, pinned, fetched not vendored), and microservices-demo (Google Online Boutique, real, pinned, second unseen product with blind ground truth committed before extraction)
- [x] T003 [P] Move canonical tokens to `src/cairn/system/diagram/tokens.json`; generate `docs/diagram-standard/tokens.json` from it in `scripts/sync_tokens.py` — done: test_foundation::test_generated_token_copies_are_in_sync

## Phase 2: Foundational (blocks every story)

- [x] T004 Schema: add `sm_element`, `sm_relationship`, `sm_evidence`, `sm_flow`, `sm_flow_step` with a schema-version bump in `src/cairn/store.py` (data-model.md) — done
- [x] T004a Versioned, idempotent schema migrations on store open (today `SCHEMA_VERSION = 1`, no ALTER path) in `src/cairn/store.py` — done
- [x] T005 [P] Element, Relationship, Evidence and Flow types plus store access in `src/cairn/system/model.py` — done
- [x] T006 [P] Diagram description schema and loader in `src/cairn/system/diagram/schema.py` (YAML and JSON; same structure the model emits) — done
- [x] T007 Standard checker (description rules R1 to R13, evidence references resolved against the working tree when present) in `src/cairn/system/diagram/check.py`; golden tests: every `docs/diagram-standard/examples/*.yaml` passes, and `docs/diagram-standard/taazaa-original/*.yaml` fail with the expected rule ids — done
- [x] T008 Layered layout (data-flow ranking, barycentric rows, library row, orthogonal routes, fanned ports, label masks, collision check) returning geometry JSON in `src/cairn/system/diagram/layout.py` — done
- [x] T008a Renderer self-test for layout rules (no overlapping labels or boxes, text at least 7.5 px at the default size) run on every render — done
- [x] T009 SVG renderer from geometry plus tokens (light and dark, title, scope, legend, `<title>`/`<desc>`) in `src/cairn/system/diagram/svg.py`, plus the evidence-table text alternative in `alt.py` — done
- [x] T010 `cairn diagram check <file>` and `cairn diagram render <file>` in `src/cairn/cli.py` — done

**Checkpoint**: hand-made diagrams can be checked and rendered (US2 core).

## Phase 3: User Story 1, the containers view of a multi-repo product (P1) 🎯 MVP

**Goal**: the computed Containers view with evidence, zero model calls.

**Independent Test**: `cairn system --view containers` on each fixture matches its ground truth (SC-001), every claim has evidence, the ledger shows 0 calls.

- [x] T011 [P] [US1] Deploy signals: Dockerfile (EXPOSE, CMD), compose (services, image, build, ports, depends_on, env names), Kubernetes workloads and Services, Procfile in `src/cairn/system/signals/deploy.py` — done
- [x] T012 [P] [US1] Entry points: `[project.scripts]`, `package.json` scripts and bin, Go `package main`, `__main__`, Spring Boot application class in `src/cairn/system/signals/entry.py` — done
- [x] T013 [US1] **Critical path.** Route extraction as a parser change (the engine emits decorator edges without arguments; Go has none): T013a Python (FastAPI, Flask, Django URLs), T013b JS/TS (Express, Fastify, Next.js), T013c Java (Spring), T013d Go (net/http, Gin), in `src/cairn/engines/graph/extractors/`; routes as nodes with method, path and location — done: DEVIATION documented in ADR-0009; routes extracted in src/cairn/system/signals/routes.py by re-parsing with the bundled tree-sitter grammars; all four languages (T013a-d) covered; no engine change, same parser, independently testable
- [x] T014 [P] [US1] Outbound HTTP call sites (fetch, axios, requests, httpx, RestTemplate and WebClient, Go http) with literal or template paths and the base-URL config name in `src/cairn/system/signals/http.py` — done
- [x] T015 [P] [US1] Messaging call sites (Redis pub/sub, Kafka, RabbitMQ, SQS, NATS), resolving wrapper functions up to 2 call-graph hops, in `src/cairn/system/signals/messaging.py` — done: pub/sub and list queues
- [x] T016 [P] [US1] Store uses (driver connect and query sites, SQL verbs and tables) in `src/cairn/system/signals/stores.py` — done
- [x] T017 [P] [US1] Configuration names read by code and set by deploy files; a URL resolves to a sibling's identifier (no host or URL string stored); names the catalog marks sensitive are kept only as their kind, in `src/cairn/system/signals/config.py` — done: privacy tests with fake secrets
- [x] T018 [P] [US1] Framework, client and store catalog as rules over the five pattern kinds (`frameworks.md`), loader with a per-project extension (`.cairn/catalog.json`); a match requires a use site, in `src/cairn/system/catalog/` — done: YAML rules, not JSON (acceptable: read with the safe YAML loader)
- [x] T019 [US1] Assemble containers per deploy unit (several per repository when present), stores, channels, libraries and outside services with evidence, and persist them on sync in `src/cairn/system/build.py`; hook into `src/cairn/sync.py` after the map step — done
- [x] T020 [US1] Optional `system.yaml` fields (actors, per-repo role/kind/description, declared relationships) in `src/cairn/engines/systems.py`; old files stay valid — done
- [x] T021 [US1] Cross-repo linking (route match, topic match, shared store, sibling package, existing cross-repo call and type passes), with ambiguity and "unresolved HTTP target", reading siblings read-only, in `src/cairn/system/link.py` — done: `link_cross_repo()` reads the merged graph's cross-repo edges (calls and same_type_as) and restates them as container relationships; 11 tests passing in test_link_cross_repo.py
- [x] T022 [US1] View queries with budgets and collapsing in `src/cairn/system/views.py`; `cairn system --view context|containers` text and `--svg` output in `src/cairn/cli.py` — done: views and `cairn system --view/--scope/--json/--out/--svg` all working
- [x] T023 [US1] Golden tests: fixture views versus ground truth (at least 90% recall, no unlabelled false positives), evidence on 100% of claims, 0 ledger calls, in `tests/system/test_containers.py` — done: shop, orders, voting-app all at 1.00 (see checkpoint note on fixture blindness)

## Phase 4: User Story 2, one diagram standard everywhere (P1)

- [x] T024 [P] [US2] Serve tokens and geometry to the UI (`/api/projects/{pid}/system/view`, `/api/diagram/tokens`) in `src/cairn/server.py` — done: both endpoints added, read-only
- [x] T025 [US2] Generate the UI's diagram colour roles from `tokens.json` into `src/cairn/ui/styles/tokens.css` in `scripts/sync_tokens.py`; a test that a token change reaches both the UI CSS and an export — done: generated by scripts/sync_tokens.py; suite fails when stale
- [x] T026 [P] [US2] Publish `docs/diagram-standard/` (rules, tokens, examples, Taazaa mapping) from the canonical sources; add the checker to CI over `docs/**.yaml` diagrams — done: `scripts/sync_diagram_docs.py` regenerates rules.md, mermaid.md, taazaa.md from canonical sources with `--check` mode; checker runs over all docs examples in the suite

## Phase 5: User Story 3, diagrams that stay true (P2)

- [x] T027 [US3] Incremental re-derivation for claims whose evidence files changed; stale marking with reason and since in `src/cairn/system/recheck.py` — done: `recheck()` with per-file fingerprints (size:mtime:sha1), per-claim tracking, stale marking with reason and since, reused claims never re-read
- [x] T028 [US3] `stale-architecture` drift findings in `src/cairn/drift.py` — done: `_system_stale()` surfaces stale elements (medium) and stale relationships (high) with evidence refs and "stale since" date
- [x] T029 [US3] Tests: remove evidence, then stale; restore, then cleared; import-without-use creates nothing, in `tests/system/test_recheck.py` — done: 10 tests passing

## Phase 6: User Story 5, a Map that answers three questions (P2)

- [x] T030 [US5] `c4view.js` drawing server geometry with tokens, hover and click evidence panel, keyboard focus in `src/cairn/ui/app/components/c4view.js` — done AFTER a rewrite: the first version re-implemented layout in JS and was unusable (boxes in type-rows, flows drawn as boxes, overlapping labels). The component now embeds `/system/svg`, the diagram standard's own renderer output, and adds only hover/click/keyboard/evidence. 13 tests in tests/system/test_system_diagram.py
- [x] T031 [US5] Map tabs: Context, Containers, Components, Flows, Code; remove Engine views in `src/cairn/ui/app/views/map.js` — done: System tab with five levels, each verified to draw its own diagram; EngineViews removed
- [x] T031a [US5] Redesign dependency layers (FR-026): hide vendored and third-party folders with a count, one expandable block per large cycle, per-layer budgets with "+N", entry points from the system model, evidence per folder; server side in `src/cairn/engines/mapper.py` `file_graph()` follow-up, view in `src/cairn/ui/app/components/archmap.js` — done
- [x] T031b [US5] Before/after screenshots of dependency layers on Cairn's own repository for review before merging — done: specs/002-system-model/layers/*.png
- [x] T032 [US5] Keep the graph exports on the command line; remove only the `/graph/views` UI routes in `src/cairn/server.py` — done: `/graph/views/{kind}` removed, list endpoint kept
- [x] T033 [P] [US5] Components per container (communities grouped by dominant module, named from paths, budgeted) in `src/cairn/system/components.py` — done: `view()`, `groups()`, `arrows()` with Leiden communities restricted to one container's files, FR-016 naming (module path, symbol fallback marked inferred), aggregated dependency arrows with evidence; 15 tests passing in tests/system/test_components.py
- [x] T034 [P] [US5] Flows from entry points (ordered by call order when the AST gives it, else inferred; split at 12 steps) in `src/cairn/system/flows.py` — done: `view()`, `flows()`, `entry_points()`, `steps_for()` with sequence diagram descriptions; 14 tests passing in tests/system/test_flows.py
- [x] T035 [US5] UI tests (Playwright smoke) for the five views, evidence panel and drill-down — done in `tests/system/test_system_diagram.py`: 13 tests, 9 of them browser tests that skip without CAIRN_UI_TESTS=1. They replaced an earlier version whose assertions (`count() >= 0`) passed on an empty page

## Phase 7: User Story 4, agents know where they are (P2)

- [x] T036 [US4] Brief line (container, inbound and outbound neighbours; at most 40 tokens), placed before "Team knowledge" in `src/cairn/core.py` `brief()`; test on a fixture whose brief is full — done: `Cairn.system_line()` (src/cairn/core.py:395) with BRIEF_SYSTEM_TOKENS=40 cap
- [x] T037 [US4] Consuming containers in `cairn_context` for files that implement routes, handlers or shared contracts, in `src/cairn/core.py` `context()` — done: `Cairn._system_consumers()` (src/cairn/core.py:432), "System consumers" section in the pack
- [x] T038 [P] [US4] `cairn_system` tool in the full MCP toolset only in `src/cairn/mcp_server.py`; update `docs/mcp.md` — done: `cairn_system` in mcp_server.py:196, SYSTEM_TOOLS (opt-in `[mcp] tools = "all"`), not in CORE
- [x] T039 [US4] Tests: brief budget, consumer listing on the shop fixture, in `tests/system/test_agent_context.py` — done: 16 tests passing

## Phase 8: User Story 6, architecture documents (P3)

- [x] T040 [US6] `cairn docs export [--out docs/architecture]`: context, containers, components, key flows, modules, configuration table, external systems; SVG plus evidence tables; omit empty sections; record the commit, in `src/cairn/system/export.py` — done: `export()` writes one Markdown + Mermaid + SVG per level with evidence tables, empty sections omitted, commit recorded; `cairn docs export` wired in cli.py
- [x] T041 [US6] Tests: export on fixtures, rerun after a change updates, no placeholder text, in `tests/system/test_export.py` — done: 8 tests passing

## Phase 9: User Story 7, optional narrative (P3)

- [x] T042 [US7] Narrate component names, responsibilities and flow descriptions (fast tier, `[system] narrate_tokens` budget, ledgered, labelled inferred, cites elements) in `src/cairn/system/narrate.py` — done: `narrate_export()` with `Budget` cap, `system.narrate` task, labelled inferred, cites elements; no-op without a model or opt-in
- [x] T043 [US7] Tests: identical model with and without narrative; ledger entries only with opt-in — done: 5 tests passing in tests/system/test_narrate.py

## Phase 9b: Mermaid (FR-031 to FR-033)

- [x] T048 [P] [US2] Mermaid export of any view (flowchart; sequence diagram for flows), token-driven theme line, `%% cairn` facts, evidence table beside it in exports, in `src/cairn/system/diagram/mermaid_out.py` (size S–M, 2–3 days) — done
- [x] T049 [US2] Mermaid flowchart import (nodes, shapes, labelled, chained and `&` edges, subgraphs, short and JSON `%% cairn` comments, warnings for ignored syntax) feeding `cairn diagram check|render`, in `src/cairn/system/diagram/mermaid_in.py` (size M, 4–6 days) — done
- [x] T050 [P] [US2] Golden tests: round-trip of every example and fixture view; a corpus of real Mermaid files imports with warnings, not errors; exports render with Mermaid in CI, in `tests/system/test_mermaid.py` — done: round trips and malformed-Mermaid tests in test_mermaid_golden.py; `.github/workflows/diagrams.yml` renders every export with the real Mermaid CLI on PR
- [x] T051 [US2] Optional: Mermaid `sequenceDiagram` import for flows (M, 3–4 days) and Mermaid C4 syntax import (S–M, 2–3 days); decide after T049 — done: sequenceDiagram and Mermaid C4 import both done

## Phase 10: Polish

- [x] T044 [P] Docs: `docs/architecture.md` section on the system model, `docs/cli.md`, `docs/multi-repo.md` (`system.yaml` fields) — done: architecture.md "The system model" section, cli.md "Docs export" table, multi-repo.md "The system model" section
- [x] T045a Measure and record the setup-time baseline on the fixtures and Cairn's repository in plan.md, before any extractor work — done: recorded in plan.md
- [x] T045 Performance: setup-time budget test (≤15% over baseline on the Cairn repository) in `tests/system/test_perf.py` — done: build_repo budget and scaling tests, layout-time budget (2 s), memory budget (200 MB) in tests/system/test_extract_perf.py
- [x] T046 [P] Windows path and line-ending cases for evidence references — done: tests/test_windows.py (56 tests) run on windows-latest in CI matrix; core.autocrlf=false keeps evidence paths byte-exact
- [ ] T047 Moderated new-joiner test (at least 5 people name the containers and one interaction within 2 minutes, SC-005); results in `specs/002-system-model/usability.md` — **needs humans**: requires 5 participants; cannot be automated. Flagged for the team.

## Dependencies & Execution Order

- Phase 1, then Phase 2 (blocks all), then US1 (MVP).
- US2's UI tasks need US1's view geometry.
- US3 needs US1.
- US5's views need US1 and US2.
- US4 needs US1.
- US6 needs US1, US2 and US5's components and flows.
- US7 is last.

## Parallel Opportunities

- T011 to T018: different signal modules.
- T024 to T026: tokens and serving.
- T033 with T034.
- T038 alongside T036 and T037.

## Implementation Strategy

1. **MVP**: Phases 1 to 3. The computed containers view on the CLI with SVG output, checked against
   fixtures.
2. Then US2 and US3, so the standard and staleness are trustworthy before the UI.
3. Then the UI (US5), agents (US4), export (US6) and narrative (US7).
