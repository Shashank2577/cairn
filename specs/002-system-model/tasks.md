---
description: "Tasks for the system model and diagram standard"
---

# Tasks: System Model and Diagram Standard

**Input**: Design documents from `/specs/002-system-model/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md

**Tests**: Required. The constitution asks for well-tested code, and each story has fixture-based golden tests.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (US1 to US7)

## Phase 1: Setup

- [ ] T000 Constitution PATCH amendment: provenance vocabulary adds declared, ambiguous, stale (principle II) in `.cairn/workflow/memory/constitution.md`

- [ ] T001 Create the `src/cairn/system/` package skeleton (`model.py`, `signals/`, `catalog/`, `diagram/`, `link.py`, `views.py`, `recheck.py`) and `tests/system/`
- [ ] T002 [P] Fixture products in `tests/system/fixtures/`: shop (from `specs/002-system-model/prototype/sandbox`), spring (Java), gonet (Go), a monorepo with several deploy units, and one real open-source multi-service product; each `GROUND_TRUTH.yaml` committed before extraction is first run on it
- [ ] T003 [P] Move canonical tokens to `src/cairn/system/diagram/tokens.json`; generate `docs/diagram-standard/tokens.json` from it in `scripts/sync_tokens.py`

## Phase 2: Foundational (blocks every story)

- [ ] T004 Schema: add `sm_element`, `sm_relationship`, `sm_evidence`, `sm_flow`, `sm_flow_step` with a schema-version bump in `src/cairn/store.py` (data-model.md)
- [ ] T004a Versioned, idempotent schema migrations on store open (today `SCHEMA_VERSION = 1`, no ALTER path) in `src/cairn/store.py`
- [ ] T005 [P] Element, Relationship, Evidence and Flow types plus store access in `src/cairn/system/model.py`
- [ ] T006 [P] Diagram description schema and loader in `src/cairn/system/diagram/schema.py` (YAML and JSON; same structure the model emits)
- [ ] T007 Standard checker (description rules R1 to R13, evidence references resolved against the working tree when present) in `src/cairn/system/diagram/check.py`; golden tests: every `docs/diagram-standard/examples/*.yaml` passes, and `docs/diagram-standard/taazaa-original/*.yaml` fail with the expected rule ids
- [ ] T008 Layered layout (data-flow ranking, barycentric rows, library row, orthogonal routes, fanned ports, label masks, collision check) returning geometry JSON in `src/cairn/system/diagram/layout.py`
- [ ] T008a Renderer self-test for layout rules (no overlapping labels or boxes, text at least 7.5 px at the default size) run on every render
- [ ] T009 SVG renderer from geometry plus tokens (light and dark, title, scope, legend, `<title>`/`<desc>`) in `src/cairn/system/diagram/svg.py`, plus the evidence-table text alternative in `alt.py`
- [ ] T010 `cairn diagram check <file>` and `cairn diagram render <file>` in `src/cairn/cli.py`

**Checkpoint**: hand-made diagrams can be checked and rendered (US2 core).

## Phase 3: User Story 1, the containers view of a multi-repo product (P1) 🎯 MVP

**Goal**: the computed Containers view with evidence, zero model calls.

**Independent Test**: `cairn system --view containers` on each fixture matches its ground truth (SC-001), every claim has evidence, the ledger shows 0 calls.

- [ ] T011 [P] [US1] Deploy signals: Dockerfile (EXPOSE, CMD), compose (services, image, build, ports, depends_on, env names), Kubernetes workloads and Services, Procfile in `src/cairn/system/signals/deploy.py`
- [ ] T012 [P] [US1] Entry points: `[project.scripts]`, `package.json` scripts and bin, Go `package main`, `__main__`, Spring Boot application class in `src/cairn/system/signals/entry.py`
- [ ] T013 [US1] **Critical path.** Route extraction as a parser change (the engine emits decorator edges without arguments; Go has none): T013a Python (FastAPI, Flask, Django URLs), T013b JS/TS (Express, Fastify, Next.js), T013c Java (Spring), T013d Go (net/http, Gin), in `src/cairn/engines/graph/extractors/`; routes as nodes with method, path and location
- [ ] T014 [P] [US1] Outbound HTTP call sites (fetch, axios, requests, httpx, RestTemplate and WebClient, Go http) with literal or template paths and the base-URL config name in `src/cairn/system/signals/http.py`
- [ ] T015 [P] [US1] Messaging call sites (Redis pub/sub, Kafka, RabbitMQ, SQS, NATS), resolving wrapper functions up to 2 call-graph hops, in `src/cairn/system/signals/messaging.py`
- [ ] T016 [P] [US1] Store uses (driver connect and query sites, SQL verbs and tables) in `src/cairn/system/signals/stores.py`
- [ ] T017 [P] [US1] Configuration names read by code and set by deploy files; a URL resolves to a sibling's identifier (no host or URL string stored); names the catalog marks sensitive are kept only as their kind, in `src/cairn/system/signals/config.py`
- [ ] T018 [P] [US1] Catalog data and loader with a per-project extension (`.cairn/catalog.json`); a match requires a use site, in `src/cairn/system/catalog/`
- [ ] T019 [US1] Assemble containers per deploy unit (several per repository when present), stores, channels, libraries and outside services with evidence, and persist them on sync in `src/cairn/system/build.py`; hook into `src/cairn/sync.py` after the map step
- [ ] T020 [US1] Optional `system.yaml` fields (actors, per-repo role/kind/description, declared relationships) in `src/cairn/engines/systems.py`; old files stay valid
- [ ] T021 [US1] Cross-repo linking (route match, topic match, shared store, sibling package, existing cross-repo call and type passes), with ambiguity and "unresolved HTTP target", reading siblings read-only, in `src/cairn/system/link.py`
- [ ] T022 [US1] View queries with budgets and collapsing in `src/cairn/system/views.py`; `cairn system --view context|containers` text and `--svg` output in `src/cairn/cli.py`
- [ ] T023 [US1] Golden tests: fixture views versus ground truth (at least 90% recall, no unlabelled false positives), evidence on 100% of claims, 0 ledger calls, in `tests/system/test_containers.py`

## Phase 4: User Story 2, one diagram standard everywhere (P1)

- [ ] T024 [P] [US2] Serve tokens and geometry to the UI (`/api/projects/{pid}/system/view`, `/api/diagram/tokens`) in `src/cairn/server.py`
- [ ] T025 [US2] Generate the UI's diagram colour roles from `tokens.json` into `src/cairn/ui/styles/tokens.css` in `scripts/sync_tokens.py`; a test that a token change reaches both the UI CSS and an export
- [ ] T026 [P] [US2] Publish `docs/diagram-standard/` (rules, tokens, examples, Taazaa mapping) from the canonical sources; add the checker to CI over `docs/**.yaml` diagrams

## Phase 5: User Story 3, diagrams that stay true (P2)

- [ ] T027 [US3] Incremental re-derivation for claims whose evidence files changed; stale marking with reason and since in `src/cairn/system/recheck.py`
- [ ] T028 [US3] `stale-architecture` drift findings in `src/cairn/drift.py`
- [ ] T029 [US3] Tests: remove evidence, then stale; restore, then cleared; import-without-use creates nothing, in `tests/system/test_recheck.py`

## Phase 6: User Story 5, a Map that answers three questions (P2)

- [ ] T030 [US5] `c4view.js` drawing server geometry with tokens, hover and click evidence panel, keyboard focus in `src/cairn/ui/app/components/c4view.js`
- [ ] T031 [US5] Map tabs: Context, Containers, Components, Flows, Code; remove Engine views; move the folder layers into Code as "dependency layers" in `src/cairn/ui/app/views/map.js`
- [ ] T032 [US5] Keep the graph exports on the command line; remove only the `/graph/views` UI routes in `src/cairn/server.py`
- [ ] T033 [P] [US5] Components per container (communities grouped by dominant module, named from paths, budgeted) in `src/cairn/system/components.py`
- [ ] T034 [P] [US5] Flows from entry points (ordered by call order when the AST gives it, else inferred; split at 12 steps) in `src/cairn/system/flows.py`
- [ ] T035 [US5] UI tests (Playwright smoke) for the five views, evidence panel and drill-down in `tests/ui/test_map_views.py`

## Phase 7: User Story 4, agents know where they are (P2)

- [ ] T036 [US4] Brief line (container, inbound and outbound neighbours; at most 40 tokens), placed before "Team knowledge" in `src/cairn/core.py` `brief()`; test on a fixture whose brief is full
- [ ] T037 [US4] Consuming containers in `cairn_context` for files that implement routes, handlers or shared contracts, in `src/cairn/core.py` `context()`
- [ ] T038 [P] [US4] `cairn_system` tool in the full MCP toolset only in `src/cairn/mcp_server.py`; update `docs/mcp.md`
- [ ] T039 [US4] Tests: brief budget, consumer listing on the shop fixture, in `tests/system/test_agent_context.py`

## Phase 8: User Story 6, architecture documents (P3)

- [ ] T040 [US6] `cairn docs export [--out docs/architecture]`: context, containers, components, key flows, modules, configuration table, external systems; SVG plus evidence tables; omit empty sections; record the commit, in `src/cairn/system/export.py`
- [ ] T041 [US6] Tests: export on fixtures, rerun after a change updates, no placeholder text, in `tests/system/test_export.py`

## Phase 9: User Story 7, optional narrative (P3)

- [ ] T042 [US7] Narrate component names, responsibilities and flow descriptions (fast tier, `[system] narrate_tokens` budget, ledgered, labelled inferred, cites elements) in `src/cairn/system/narrate.py`
- [ ] T043 [US7] Tests: identical model with and without narrative; ledger entries only with opt-in

## Phase 10: Polish

- [ ] T044 [P] Docs: `docs/architecture.md` section on the system model, `docs/cli.md`, `docs/multi-repo.md` (`system.yaml` fields)
- [ ] T045a Measure and record the setup-time baseline on the fixtures and Cairn's repository in plan.md, before any extractor work
- [ ] T045 Performance: setup-time budget test (≤15% over baseline on the Cairn repository) in `tests/system/test_perf.py`
- [ ] T046 [P] Windows path and line-ending cases for evidence references
- [ ] T047 Moderated new-joiner test (at least 5 people name the containers and one interaction within 2 minutes, SC-005); results in `specs/002-system-model/usability.md`

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
