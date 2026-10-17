# Tasks: Cheaper session observations

**Input**: `specs/002-observation-token-cost/` (spec.md, plan.md, findings.md)
**Rule**: Phase 1–3 are measurement and are done. Phase 4 onward changes defaults and **starts only after the review of
`open-questions.md`**. Every code task names its test.

## Phase 1: Measure the cause (done)

- [x] T001 Ledger report: per-call cache write vs read, gaps, price split — `specs/002-observation-token-cost/measure/ledger_report.py`
- [x] T002 Why prefixes are not reused: controlled CLI probes (identical repeat, append-only, sliding window, TTL, knobs) — `specs/002-observation-token-cost/measure/cache_probe.py`, `specs/002-observation-token-cost/measure/cache_probe2.py`, `specs/002-observation-token-cost/measure/cache_knobs.py`, `specs/002-observation-token-cost/measure/cli_probe.py`
- [x] T003 Real prompt size per call (system / replay / event text) from a dry run through the production observer — `specs/002-observation-token-cost/measure/replay.py`

## Phase 2: Compare provider paths on the same events (done)

- [x] T004 Replay harness through the production observer path, per-call usage incl. thinking and cache TTL — `specs/002-observation-token-cost/measure/replay.py`
- [x] T005 Claude Code CLI vs OpenAI-compatible (local) measured on the same events; Anthropic SDK path estimated (no key available) — `specs/002-observation-token-cost/measure/run_matrix.sh`, `specs/002-observation-token-cost/measure/sdk_estimate.py`

## Phase 3: Levers one at a time, quality gate (done)

- [x] T006 Lever matrix with replicates and a held-out window — `specs/002-observation-token-cost/measure/run_matrix.sh`, `specs/002-observation-token-cost/measure/run_matrix2.sh`, `specs/002-observation-token-cost/measure/run_heldout.sh`
- [x] T007 Skip-list counterfactual from `tool_uses` — `specs/002-observation-token-cost/measure/tool_share.py`
- [x] T008 Deterministic rubric R1–R6 and noise-band comparison — `specs/002-observation-token-cost/measure/grade.py`, `specs/002-observation-token-cost/measure/families.py`, `specs/002-observation-token-cost/measure/summarize.py`
- [x] T009 Blind human-review sheet generator (the generated sheets hold raw events of private repos, so they live in the shared notes folder, not here) — `specs/002-observation-token-cost/measure/review_sheet.py`
- [ ] T009b A person grades ≥ 10 items per repository from those sheets and the result is added to `specs/002-observation-token-cost/findings.md` §4
- [x] T010 Write up with measured/estimated labels — `specs/002-observation-token-cost/findings.md`

**Checkpoint — STOP FOR REVIEW.** Answer `open-questions.md`; then continue.

## Phase 4: Router (after review) — each task is one commit

- [ ] T011 Test first: the claude-code child environment carries `DISABLE_PROMPT_CACHING=1` unless the user set it or `models.cli_cache` is true; both `_claude_code` and `_claude_code_stream` — `tests/test_router_cost.py`
- [ ] T012 Implement T011 — `src/cairn/router.py`
- [ ] T013 Test first: `MAX_THINKING_TOKENS=0` is set only for tasks in `NO_THINKING_TASKS`; deep-tier tasks keep the default — `tests/test_router_cost.py`
- [ ] T014 Implement T013 — `src/cairn/router.py`
- [ ] T015 Test first: Anthropic request has no `cache_control` when the observer sends its replay in the prompt — `tests/test_router_cost.py`
- [ ] T016 Ledger rows keep the provider's thinking and cache-TTL counts; `cairn models --ledger` labels tokens measured and dollars estimated — `src/cairn/router.py`, `src/cairn/store.py`, `tests/test_router_cost.py`

## Phase 5: Observer (after review)

- [ ] T017 Test first: with default settings an oversized field makes no `recall_compress` call and is cut head/tail with an elision marker; with `observer_condense = true` it condenses — `tests/engines/test_recall_worker.py`
- [ ] T018 Implement `observer_condense` — `src/cairn/engines/recall/observer.py`, `src/cairn/engines/recall/settings.py`
- [ ] T019 Test first: a five-event batch contains the closing instructions once — `tests/engines/test_recall_worker.py`
- [ ] T020 Implement closing instructions once per call — `src/cairn/engines/recall/prompts.py`, `src/cairn/engines/recall/observer.py`
- [ ] T021 Test first: the brief still contains every rule the parser and context renderer depend on (types, skip tag, XML shape) — `tests/engines/test_recall_worker.py`
- [ ] T022 Shorter brief from `levers.terse` — `src/cairn/engines/recall/modes/code.json`
- [ ] T023 Test first: replay is passed as prompt text, not `cached_context`; defaults 2 exchanges / 6000 chars — `tests/engines/test_recall_worker.py`
- [ ] T024 Implement T023 and the `observe_batch` default — `src/cairn/engines/recall/observer.py`, `src/cairn/engines/recall/settings.py`
- [ ] T025 (optional) Read-only shell skip — `src/cairn/engines/recall/ingest.py`, `tests/engines/test_recall_capture.py`

## Phase 6: Prove it (after the code)

- [ ] T026 Re-run `run_matrix2.sh` and `run_heldout.sh` on fresh snapshots of two repositories; put the after-numbers beside the before-numbers in `specs/002-observation-token-cost/findings.md`
- [ ] T027 Full test suite green; `CHANGELOG.md` entry with the measured figures and their labels — `CHANGELOG.md`
- [ ] T028 `cairn_remember` the durable learnings (CLI cache behaviour, thinking default, harness pitfalls)

## Dependencies

T011→T012, T013→T014, T015→(T023,T024), T016 independent. T017→T018, T019→T020, T021→T022, T023→T024.
Phase 6 needs Phases 4 and 5. T012 and T018 are the two largest savings and can ship alone.
