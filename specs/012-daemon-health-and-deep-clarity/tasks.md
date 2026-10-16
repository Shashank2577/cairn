# Tasks: Daemon health and deep-mode clarity

Spec: [spec.md](spec.md) | Plan: [plan.md](plan.md). Tests are written first and must fail before the change.

## Phase 1: Deep state (US2) - independent, lowest blast radius first
- [x] T001 [US2] Write `tests/test_deep_state.py`: claude found via fallback dirs when absent from PATH; off with reason and fix when absent everywhere; off by config names the key; anthropic key path; deep_enabled() agrees with deep_state().
- [x] T002 [US2] `router.py`: add `find_claude()` and `deep_state()`; make `resolve_provider`, `api_key` and `deep_enabled()` use them; keep signatures.
- [x] T003 [US2] `cli.py` doctor: add a Deep row with state, backend, reason and fix; `status` uses the same state.

## Phase 2: Server record and staleness (US1)
- [x] T004 [US1] Write `tests/test_daemon_health.py`: record fields written at start; `info()` reports `code-missing`, `version-differs`, `python-missing`; healthy reports none; `start()` replaces a stale server and reports why (fake process helpers, no real server).
- [x] T005 [US1] `daemon.py`: extend `server.json`, compute the verdict, replace stale servers in `start()`.
- [x] T006 [US1] `server.py`: `/api/health` returns version, code root, existence, pid and deep state; missing static root returns structured 503.
- [x] T007 [US1] `cli.py`: `status` and `doctor` Server row with the exact fix; `up` and `ui` print what was replaced and that other repositories were interrupted.

## Phase 3: Structured errors and banner (US4)
- [x] T008 [US4] Write server test: an exception in a route returns JSON `{error, path, cause}` with the right status.
- [x] T009 [US4] `server.py` exception handler; UI shell banner for failed API calls and stale-server verdict (`src/cairn/ui/app/components/shell.js`, `state.js`, `api.js`).

## Phase 4: Budgets (US3)
- [x] T010 [US3] Write `tests/test_budgets.py`: three budgets, plain names, values from config, usage from the ledger, consequence text present.
- [x] T011 [US3] `src/cairn/budgets.py`; doctor section; overview API field; Project settings view and overview deep reason in the UI.
- [x] T012 [US3] Docs: `docs/` page "Deep mode and budgets" in plain language.

## Phase 5: Test gate (FR-010)
- [x] T013 Write `tests/ui/test_browser_smoke.py`: start a temp server, run `smoke.mjs`; second case deletes the server's code root and asserts banner, verdict and non-zero result; skip without Chrome or Node 22.
- [x] T014 Extend `smoke.mjs` to open every dropdown and Map sub-tab and assert Engine view frames load their script.

## Phase 6: Prove it
- [x] T015 Reproduce the original failure on this machine (server from a deleted directory), show doctor and UI output, run the fix, screenshot in ordinary Chrome.
- [x] T016 `pytest -q` full suite, `ruff check`, tick tasks, record gotchas with `cairn remember`.
