---

description: "Task list for Cairn Review"
---

# Tasks: Cairn Review

**Input**: `/specs/013-cairn-review/` (spec, plan, research, data-model, contracts, quickstart)

**Format**: `- [ ] T### [P?] [US#] Description with file path`. `[P]` = different files, no unfinished dependency.

**Risk key (from `cairn impact`)**: HIGH files are `router.py`, `server.py`, `store.py`, `cli.py`, `mcp_server.py`, `agents.py`, `hooks.py`. Edit them with the smallest possible change and keep their existing tests green.

**Hard prerequisite**: the secret-masking fix (patterns in `src/cairn/engines/recall/tags.py`) lands first; T006 depends on it.

## Phase 1: Setup

- [x] T001 Confirm the license of the design source and whether any prompt text or rule wording is reused; if so add attribution to `NOTICE` and `licenses/` and record the result in `specs/013-cairn-review/research.md` D1
- [x] T002 Create the package skeleton `src/cairn/review/__init__.py` and `tests/review/__init__.py`
- [x] T003 [P] Write `docs/adr/0010-review-stage.md (0009 was taken)` (native pipeline, tool count 13, author pays, forge posting default)

## Phase 2: Foundational (blocks all stories)

- [x] T004 Add tables `reviews`, `review_findings`, `review_rules` exactly as in `data-model.md` (severity in high|medium|low|note; state in open|accepted|dismissed|fixed; unique on project, head_sha, base_sha) in `src/cairn/review/store.py`, created through the existing schema path in `src/cairn/store.py` (Cairn: HIGH, 139 dependents; add only the table creation call)
- [x] T005 [P] Implement diff resolution and file filtering (base/head, `--staged`, PR number via the forge or local refs; skip generated, vendored and lock files with a reason) in `src/cairn/review/diff.py`, reading and writing UTF-8 explicitly
- [ ] T006 Mask credentials and remove `<private>` from every string before it can reach a model or a comment, reusing the improved patterns, in `src/cairn/review/model.py` (`sanitize()`); depends on the masking fix
- [x] T007 [P] Add budgets `review.budget_tokens` (60000) and `review.wall_seconds` (180) with plain-language meaning and when-exceeded text in `src/cairn/budgets.py` (Cairn: `test_every_budget_is_explained_in_plain_words`)
- [ ] T008 [P] Add task names `review.bundle` (balanced) and `review.reflect` (fast) to the tier table only, in `src/cairn/router.py` (Cairn: HIGH, 143 dependents, 4 past fixes; do not touch routing or the ledger; keep `Budget` tests green)
- [x] T009 [P] Tests for the tables, diff filtering and sanitizing in `tests/review/test_foundations.py`

**Checkpoint**: foundations pass tests; nothing user-visible yet.

## Phase 3: User Story 1 - Review my branch (P1) MVP

**Goal**: `cairn review` works with no model. **Independent test**: quickstart step 1.

- [x] T010 [P] [US1] Deterministic check: files that usually change together but are missing from the diff, in `src/cairn/review/checks.py`
- [x] T011 [P] [US1] Deterministic check: open drift on touched requirements, in `src/cairn/review/checks.py` (uses `src/cairn/drift.py`)
- [x] T012 [P] [US1] Deterministic check: tests likely affected but absent, and dependency change without lock update, in `src/cairn/review/checks.py`
- [x] T013 [US1] Deterministic check: secrets in the diff, in `src/cairn/review/checks.py`
- [x] T014 [US1] Pipeline runner (no model path): resolve diff, run checks, store a review and findings, handle "nothing to review", in `src/cairn/review/pipeline.py`
- [x] T015 [US1] `cairn review` command group per `contracts/cli.md` (run, list, show, text and json output, `--ci` exit status, model-not-run notice) in `src/cairn/cli.py` (Cairn: HIGH, 6 past fixes; thin wrapper only)
- [x] T016 [US1] Compact pack formatter within `context.budget` in `src/cairn/review/pack.py`
- [x] T017 [P] [US1] Fixture repository with seeded issues in `tests/review/fixtures/` and tests for each check and the CLI in `tests/review/test_deterministic.py` (SC-001, SC-003, SC-008)

## Phase 4: User Story 2 - Findings that know the project (P1)

**Goal**: history, spec and convention evidence in findings. **Independent test**: change a file with a recorded fix.

- [x] T018 [P] [US2] Check: past fixes and reverts on touched code, with commit citations, in `src/cairn/review/checks.py`
- [x] T019 [P] [US2] Check: conventions and gotchas linked to touched files, with memory citations, in `src/cairn/review/checks.py`
- [x] T020 [US2] Attach the owning spec task and recent agent sessions to each review as context, in `src/cairn/review/pipeline.py`
- [x] T021 [US2] Reject any finding without provenance and at least one citation (SC-002) in `src/cairn/review/store.py`
- [x] T022 [P] [US2] Tests in `tests/review/test_project_knowledge.py`

## Phase 5: User Story 4 - See and triage in the page (P2)

**Goal**: the Review view and triage actions. **Independent test**: quickstart step 5. (Done before US3 because the page is where value shows.)

- [ ] T023 [US4] Triage state changes (accept, dismiss with reason, make a rule) in `src/cairn/review/store.py` and `src/cairn/review/rules.py`, and the matching terminal subcommands `accept`, `dismiss`, `rule`, `rules`, `--forget` from `contracts/cli.md` in `src/cairn/cli.py` (FR-011, FR-016)
- [ ] T024 [US4] HTTP routes per `contracts/mcp-and-api.md` in `src/cairn/review/api.py`, included with one line in `src/cairn/server.py` (Cairn: HIGH, 80 dependents)
- [ ] T025 [US4] Review view: list and detail with severity groups, evidence, "what was sent", skipped files, Accept / Dismiss / Make a rule, in `src/cairn/ui/app/views/review.js`; route and nav entry in `src/cairn/ui/app/main.js`
- [ ] T026 [P] [US4] Open findings block in the Impact view, `src/cairn/ui/app/views/impact.js`
- [ ] T027 [P] [US4] Review state per task, `src/cairn/ui/app/views/specs.js`
- [ ] T028 [P] [US4] Latest review of the current branch on the Overview, `src/cairn/ui/app/views/overview.js` and the overview payload in `src/cairn/server.py`
- [ ] T029 [P] [US4] Review events on the Timeline, `src/cairn/ui/app/views/timeline.js`
- [ ] T030 [P] [US4] Review budgets, fail threshold and posting switch in `src/cairn/ui/app/views/settings.js`
- [ ] T031 [US4] API tests (including: per-person triage is never returned as person-level statistics, FR-016) and a browser smoke case (real HOME fixture) in `tests/review/test_api.py` and `tests/ui/test_browser_smoke.py`

## Phase 6: Model review (extends US1; P1 for value, needs T006 to land)

- [ ] T032 Bundling: group changed files by directory and related symbols under a token cap, in `src/cairn/review/pipeline.py`
- [ ] T033 `review.bundle` calls with the cached stable prefix, ledger entries, wall and token budget, partial status, in `src/cairn/review/model.py`
- [ ] T034 `review.reflect` pass: drop duplicates and low-confidence findings, verify line numbers against the diff, in `src/cairn/review/model.py`
- [ ] T035 Rules matched per bundle from conventions and user rules, `src/cairn/review/rules.py`
- [ ] T036 [P] Tests with a mocked router in `tests/review/test_model_stage.py` (budget stop, partial, masking, ledger rows)

## Phase 7: User Story 6 - Agent can run and fix (P3)

- [ ] T037 [US6] MCP tool `cairn_review` with optional triage arguments `accept`, `dismiss`, `rule` by finding id (13th core tool; description names only real tools) in `src/cairn/mcp_server.py` (Cairn: HIGH, 46 dependents, 3 past fixes)
- [ ] T038 [US6] `/cairn:review` command and a line in the generated agent instruction block in `src/cairn/agents.py` (Cairn: HIGH, 1 past fix)
- [ ] T039 [P] [US6] Tests in `tests/review/test_agent_surface.py` (pack within budget, citations present, tool count documented)

## Phase 8: User Story 3 - Every pull request automatically (P2)

- [ ] T040 [US3] CI mode: JSON output, `--fail-on` default `high`, exit status, in `src/cairn/cli.py` and `src/cairn/review/pipeline.py`
- [ ] T041 [US3] Webhook: accept pull request events (GitHub, GitLab, Gitea), same signatures, one review per (project, head, base), mark older ones superseded, run in background and broadcast progress, in `src/cairn/platform/web.py` (Cairn: LOW risk)
- [ ] T042 [US3] Forge summary comment, updated in place via a hidden marker, never including masked values; GitHub first, in `src/cairn/review/forge.py`
- [ ] T043 [US3] Per-project forge token and automatic-review switch (stored under the server key, optional) in `src/cairn/platform/service.py` and the Team → Projects view `src/cairn/ui/app/views/team.js`
- [ ] T044 [US3] Author-pays: map forge login to a Cairn user (optional account field), per-user provider key encrypted under the server key, deterministic-only fallback with the reason in the summary, in `src/cairn/platform/service.py`, `src/cairn/review/model.py`
- [ ] T045 [US3] Opt-in local pre-push check (deterministic, never blocks), installed by init and removed by uninstall, in `src/cairn/hooks.py` (Cairn: HIGH, 6 past fixes) and `src/cairn/agents.py`
- [ ] T046 [P] [US3] Tests: duplicate delivery, force-push, superseded, forge stub, CI exit status, no-token and no-user fallback, in `tests/review/test_automatic.py`

## Phase 9: User Story 5 - The review learns (P3)

- [ ] T047 [US5] Finding signature, and a proposal for a memory after three acceptances (stored only on confirmation), in `src/cairn/review/learn.py`
- [ ] T048 [US5] Suppressions with reason and match counter, listed in the page and by `cairn review suppressions`, in `src/cairn/review/rules.py`
- [ ] T049 [US5] Rules generated from conventions; export and import of `.cairn/review/rules.json`, in `src/cairn/review/rules.py`
- [ ] T050 [P] [US5] Tests in `tests/review/test_learning.py` (SC-009)

## Phase 10: Polish

- [ ] T051 [P] Test that no output names the underlying engine across CLI help, API strings and UI strings (SC-007), `tests/review/test_no_engine_name.py`
- [ ] T052 [P] `cairn doctor` reports review readiness, model availability and automatic triggers (FR-017), `src/cairn/cli.py` and doctor module
- [ ] T053 [P] Documentation `docs/review.md`; update `docs/cli.md`, `docs/mcp.md`, `docs/teams.md`, `docs/models-and-cost.md`
- [ ] T054 Run quickstart steps 1–10 and record results in `specs/013-cairn-review/quickstart.md`
- [ ] T055 Full test suite green on macOS, Linux and Windows CI

## Dependencies and order

- Setup → Foundational → US1 → US2 → US4 → Model review → US6 → US3 → US5 → Polish.
- US1+US2 (no model) is the MVP and can ship alone. US4 makes it visible. Model review waits for the masking fix.
- US3 phase has the riskiest work (T044); ship it after T041–T043 if needed.

## Parallel examples

- After T004: T005, T007, T008, T009 together.
- US1: T010, T011, T012 together; later T026–T030 together.

## Strategy

MVP = Phases 1–4 (deterministic review in the terminal). Then the page (Phase 5), then the model stage, then agent surface, then automation, then learning. Each phase ends with its tests passing.
