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
- [ ] T002 [P] Fixture products in `tests/system/fixtures/`: shop (from `specs/002-system-model/prototype/sandbox`), spring (Java), gonet (Go), a monorepo with several deploy units, and one real open-source multi-service product; each `GROUND_TRUTH.yaml` committed before extraction is first run on it — **partial**: shop, orders (Spring+Go) and example-voting-app (real, pinned, fetched not vendored); voting-app truth committed before extraction, orders truth written in the same uncommitted batch as the extractor so its 'before' cannot be proven; the voting app is no longer blind (shell-client rule added while it was visible); a second unseen real product is still needed
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
- [ ] T013 [US1] **Critical path.** Route extraction as a parser change (the engine emits decorator edges without arguments; Go has none): T013a Python (FastAPI, Flask, Django URLs), T013b JS/TS (Express, Fastify, Next.js), T013c Java (Spring), T013d Go (net/http, Gin), in `src/cairn/engines/graph/extractors/`; routes as nodes with method, path and location — **partial**: DEVIATION: routes are extracted in src/cairn/system/signals/routes.py by re-parsing with the bundled tree-sitter grammars, not by changing the graph engine; all four languages (T013a-d) covered. Acceptable: no engine change, same parser, independently testable; cost is a second parse of route files only
- [x] T014 [P] [US1] Outbound HTTP call sites (fetch, axios, requests, httpx, RestTemplate and WebClient, Go http) with literal or template paths and the base-URL config name in `src/cairn/system/signals/http.py` — done
- [x] T015 [P] [US1] Messaging call sites (Redis pub/sub, Kafka, RabbitMQ, SQS, NATS), resolving wrapper functions up to 2 call-graph hops, in `src/cairn/system/signals/messaging.py` — done: pub/sub and list queues
- [x] T016 [P] [US1] Store uses (driver connect and query sites, SQL verbs and tables) in `src/cairn/system/signals/stores.py` — done
- [x] T017 [P] [US1] Configuration names read by code and set by deploy files; a URL resolves to a sibling's identifier (no host or URL string stored); names the catalog marks sensitive are kept only as their kind, in `src/cairn/system/signals/config.py` — done: privacy tests with fake secrets
- [x] T018 [P] [US1] Framework, client and store catalog as rules over the five pattern kinds (`frameworks.md`), loader with a per-project extension (`.cairn/catalog.json`); a match requires a use site, in `src/cairn/system/catalog/` — done: YAML rules, not JSON (acceptable: read with the safe YAML loader)
- [x] T019 [US1] Assemble containers per deploy unit (several per repository when present), stores, channels, libraries and outside services with evidence, and persist them on sync in `src/cairn/system/build.py`; hook into `src/cairn/sync.py` after the map step — done
- [x] T020 [US1] Optional `system.yaml` fields (actors, per-repo role/kind/description, declared relationships) in `src/cairn/engines/systems.py`; old files stay valid — done
- [ ] T021 [US1] Cross-repo linking (route match, topic match, shared store, sibling package, existing cross-repo call and type passes), with ambiguity and "unresolved HTTP target", reading siblings read-only, in `src/cairn/system/link.py` — **partial**: links within and across repositories, ambiguity and unresolved targets done; the existing cross-repo call and shared-type passes are not integrated
- [ ] T022 [US1] View queries with budgets and collapsing in `src/cairn/system/views.py`; `cairn system --view context|containers` text and `--svg` output in `src/cairn/cli.py` — **partial**: views and `cairn system --view/--scope/--json/--out` done; no `--svg` (use `cairn diagram render` on the --out file)
- [x] T023 [US1] Golden tests: fixture views versus ground truth (at least 90% recall, no unlabelled false positives), evidence on 100% of claims, 0 ledger calls, in `tests/system/test_containers.py` — done: shop, orders, voting-app all at 1.00 (see checkpoint note on fixture blindness)

## Phase 4: User Story 2, one diagram standard everywhere (P1)

- [ ] T024 [P] [US2] Serve tokens and geometry to the UI (`/api/projects/{pid}/system/view`, `/api/diagram/tokens`) in `src/cairn/server.py` — open
- [x] T025 [US2] Generate the UI's diagram colour roles from `tokens.json` into `src/cairn/ui/styles/tokens.css` in `scripts/sync_tokens.py`; a test that a token change reaches both the UI CSS and an export — done: generated by scripts/sync_tokens.py; suite fails when stale
- [ ] T026 [P] [US2] Publish `docs/diagram-standard/` (rules, tokens, examples, Taazaa mapping) from the canonical sources; add the checker to CI over `docs/**.yaml` diagrams — **partial**: checker runs over all docs examples in the suite; docs regeneration from canonical sources only for tokens

## Phase 5: User Story 3, diagrams that stay true (P2)

- [ ] T027 [US3] Incremental re-derivation for claims whose evidence files changed; stale marking with reason and since in `src/cairn/system/recheck.py` — open
- [ ] T028 [US3] `stale-architecture` drift findings in `src/cairn/drift.py` — open
- [ ] T029 [US3] Tests: remove evidence, then stale; restore, then cleared; import-without-use creates nothing, in `tests/system/test_recheck.py` — open

## Phase 6: User Story 5, a Map that answers three questions (P2)

- [ ] T030 [US5] `c4view.js` drawing server geometry with tokens, hover and click evidence panel, keyboard focus in `src/cairn/ui/app/components/c4view.js` — open
- [ ] T031 [US5] Map tabs: Context, Containers, Components, Flows, Code; remove Engine views in `src/cairn/ui/app/views/map.js` — open
- [ ] T031a [US5] Redesign dependency layers (FR-026): hide vendored and third-party folders with a count, one expandable block per large cycle, per-layer budgets with "+N", entry points from the system model, evidence per folder; server side in `src/cairn/engines/mapper.py` `file_graph()` follow-up, view in `src/cairn/ui/app/components/archmap.js` — **partial**: vendored folders hidden, cycles collapsed, entry points detected, sparse labelled edges; the expanded cycle ignores the per-layer budget (+N) and the view is not yet inside Code
- [x] T031b [US5] Before/after screenshots of dependency layers on Cairn's own repository for review before merging — done: specs/002-system-model/layers/*.png
- [ ] T032 [US5] Keep the graph exports on the command line; remove only the `/graph/views` UI routes in `src/cairn/server.py` — open
- [ ] T033 [P] [US5] Components per container (communities grouped by dominant module, named from paths, budgeted) in `src/cairn/system/components.py` — open
- [ ] T034 [P] [US5] Flows from entry points (ordered by call order when the AST gives it, else inferred; split at 12 steps) in `src/cairn/system/flows.py` — open
- [ ] T035 [US5] UI tests (Playwright smoke) for the five views, evidence panel and drill-down in `tests/ui/test_map_views.py` — open

## Phase 7: User Story 4, agents know where they are (P2)

- [ ] T036 [US4] Brief line (container, inbound and outbound neighbours; at most 40 tokens), placed before "Team knowledge" in `src/cairn/core.py` `brief()`; test on a fixture whose brief is full — open
- [ ] T037 [US4] Consuming containers in `cairn_context` for files that implement routes, handlers or shared contracts, in `src/cairn/core.py` `context()` — open
- [ ] T038 [P] [US4] `cairn_system` tool in the full MCP toolset only in `src/cairn/mcp_server.py`; update `docs/mcp.md` — open
- [ ] T039 [US4] Tests: brief budget, consumer listing on the shop fixture, in `tests/system/test_agent_context.py` — open

## Phase 8: User Story 6, architecture documents (P3)

- [ ] T040 [US6] `cairn docs export [--out docs/architecture]`: context, containers, components, key flows, modules, configuration table, external systems; SVG plus evidence tables; omit empty sections; record the commit, in `src/cairn/system/export.py` — open
- [ ] T041 [US6] Tests: export on fixtures, rerun after a change updates, no placeholder text, in `tests/system/test_export.py` — open

## Phase 9: User Story 7, optional narrative (P3)

- [ ] T042 [US7] Narrate component names, responsibilities and flow descriptions (fast tier, `[system] narrate_tokens` budget, ledgered, labelled inferred, cites elements) in `src/cairn/system/narrate.py` — open
- [ ] T043 [US7] Tests: identical model with and without narrative; ledger entries only with opt-in — open

## Phase 9b: Mermaid (FR-031 to FR-033)

- [x] T048 [P] [US2] Mermaid export of any view (flowchart; sequence diagram for flows), token-driven theme line, `%% cairn` facts, evidence table beside it in exports, in `src/cairn/system/diagram/mermaid_out.py` (size S–M, 2–3 days) — done
- [x] T049 [US2] Mermaid flowchart import (nodes, shapes, labelled, chained and `&` edges, subgraphs, short and JSON `%% cairn` comments, warnings for ignored syntax) feeding `cairn diagram check|render`, in `src/cairn/system/diagram/mermaid_in.py` (size M, 4–6 days) — done
- [ ] T050 [P] [US2] Golden tests: round-trip of every example and fixture view; a corpus of real Mermaid files imports with warnings, not errors; exports render with Mermaid in CI, in `tests/system/test_mermaid.py` — **partial**: round trips and malformed-Mermaid tests done; exports are not rendered with Mermaid in CI
- [x] T051 [US2] Optional: Mermaid `sequenceDiagram` import for flows (M, 3–4 days) and Mermaid C4 syntax import (S–M, 2–3 days); decide after T049 — done: sequenceDiagram and Mermaid C4 import both done

## Phase 10: Polish

- [ ] T044 [P] Docs: `docs/architecture.md` section on the system model, `docs/cli.md`, `docs/multi-repo.md` (`system.yaml` fields) — open
- [x] T045a Measure and record the setup-time baseline on the fixtures and Cairn's repository in plan.md, before any extractor work — done: recorded in plan.md
- [ ] T045 Performance: setup-time budget test (≤15% over baseline on the Cairn repository) in `tests/system/test_perf.py` — **partial**: budget and scaling tests for build_repo exist; no layout-time or memory budget yet, no 5k-file 20-repo set
- [ ] T046 [P] Windows path and line-ending cases for evidence references — **partial**: posix path tests exist; not yet run on Windows CI
- [ ] T047 Moderated new-joiner test (at least 5 people name the containers and one interaction within 2 minutes, SC-005); results in `specs/002-system-model/usability.md` — open

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
