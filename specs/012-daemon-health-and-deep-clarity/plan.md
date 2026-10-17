# Implementation Plan: Daemon health and deep-mode clarity

**Branch**: `shash/ui-health-and-deep` | **Date**: 2026-10-16 | **Spec**: [spec.md](spec.md)

## Summary

The local server is judged "running and healthy" by `GET /api/health` alone, which answers even when the code
and UI files it serves were deleted. Fix that at the root: the server records where it runs from; `daemon`,
`status`, `doctor` and the UI compare that with reality; `cairn up` replaces a stale server. Deep mode gets one
function that returns on/off, backend, reason and fix, and finds the Claude Code CLI in standard locations.
Budgets get plain names in one place. A real-browser smoke check joins the normal test run so this class of
failure cannot pass green again.

## Technical Context

**Language/Version**: Python 3.11+ (FastAPI/uvicorn server, Typer CLI); UI is Preact ES modules served as static files; smoke check is Node 22 + headless Chrome (`tests/ui/smoke.mjs`, exists).
**Storage**: `$CAIRN_HOME/server.json` (extended); no new stores.
**Testing**: pytest (`tests/`), new `tests/test_daemon_health.py`, `tests/test_deep_state.py`, `tests/ui/test_browser_smoke.py` (skips without Chrome/Node).
**Target Platform**: macOS, Linux, Windows (existing Windows handling in `daemon.py` must keep working).
**Performance Goals**: `cairn status` stays under 300 ms (constitution); stale check is a file stat plus one local HTTP call already made today.
**Constraints**: zero model calls; no new dependencies; one server serves all repos so restart is announced.
**Scale/Scope**: touches `daemon.py`, `server.py` (health + error handler), `router.py`, `cli.py` (status, doctor), UI shell and overview/settings views, docs.

## Existing system constraints (from Cairn)

- `router.py` is a HIGH-impact hub: 141 dependents in 41 files, 3 past fix/reverts, 51+ tests affected. Keep `Router.available` and `deep_enabled()` signatures; add `deep_state()` beside them and make `deep_enabled()` call it. [file:src/cairn/router.py]
- `server.py` is HIGH: 85 dependents, 34+ tests (`tests/test_surfaces.py` builds apps with `create_app()`); change error handling additively. [file:src/cairn/server.py]
- Files that usually change together: `cli.py` (56%), `hooks.py`, `mcp_server.py`, `store.py`; this plan touches `cli.py` only.
- Past incidents to not repeat: Windows locks, paths and `claude.cmd` shims (commits 973abd33b6, 1481d1cf50, f3ea2a9ec1); the doctor must not claim "off" when capture is enabled (a3a1868a1a, `tests/test_dogfood_fixes.py`); agent hooks must never fail on a busy store (memory:34ffa43cc4), so nothing here may write to the store on the status path.
- Decisions to honour: one server for many projects, loopback only in local mode (memory:7a55756715, memory:ccb86f9f34); deep tier is optional and never gates a feature (constitution II).
- Tests to keep green: `tests/test_surfaces.py`, `tests/test_dogfood_fixes.py`, `tests/test_windows.py`, `tests/test_global_install.py`.

## Constitution Check

| Principle | Status |
|---|---|
| I One vocabulary | Pass: "server", "deep", "budget" only; no engine names in new copy. |
| II Deterministic first | Pass: all checks are file, process and PATH inspection; no model call. |
| III Zero friction | Pass: `cairn up` self-heals; no new flags required. |
| V Token discipline | Pass: this feature explains budgets, does not change them. |
| VII Local-first | Pass: nothing leaves the machine. |
| Quality gates | Status stays under 300 ms; test added for each deterministic path. |

No violations; Complexity Tracking is empty.

## Project Structure

### Documentation (this feature)

```text
specs/012-daemon-health-and-deep-clarity/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── checklists/requirements.md
└── tasks.md
```

### Source Code

```text
src/cairn/daemon.py        # record, stale detection, restart
src/cairn/server.py        # /api/health fields, JSON error handler, static-root check
src/cairn/router.py        # find_claude(), deep_state()
src/cairn/cli.py           # status warning, doctor rows (Server, Deep, Budgets)
src/cairn/budgets.py       # NEW: the three budgets, plain names, values, usage
src/cairn/ui/app/...       # shell banner, overview deep reason, settings budgets
docs/                      # budgets and deep explained
tests/test_daemon_health.py, tests/test_deep_state.py, tests/ui/test_browser_smoke.py
```

**Structure Decision**: single Python package, additive changes; one new small module (`budgets.py`) because three call sites (doctor, API, UI settings) need the same text.

## Design

1. **Server record** (daemon.py). `server.json` gains `code_root`, `version`, `python`, `path`, `started`. `info()` returns the record plus `stale: None | "code-missing" | "version-differs" | "python-missing"`, computed by stat and string compare. Health stays the liveness check; staleness is a separate verdict.
2. **Health payload** (server.py). `/api/health` adds `version`, `code_root`, `code_root_exists`, `pid`, `deep` (the deep state). Static UI root existence is checked at request time; a missing root returns a structured 503 with the same fields instead of a bare 500.
3. **Replace, don't reuse** (daemon.start). If `info()` reports stale, stop it, start a new one on the same port when free, and return `replaced: <reason>` so `cairn up` / `cairn ui` can say what happened and that other repositories were interrupted.
4. **Structured errors** (server.py). One exception handler returns `{"error": message, "path": ..., "cause": ...}` with the right status; no HTML or plain text.
5. **Deep state** (router.py). `find_claude()` checks `shutil.which`, then `/opt/homebrew/bin`, `/usr/local/bin`, `~/.local/bin`, `~/.claude/local` (and the Windows npm shim names), caches the found path. `deep_state()` returns `{enabled, backend, reason, fix, claude_path}`; `resolve_provider` and the `api_key` property use `find_claude()` so the whole router agrees.
6. **Budgets** (budgets.py). Returns three records `{key, name, value, unit, used, meaning, when_exceeded}` for context pack size, deep sync token allowance (used from the model ledger), and memory-seeding wall-clock limit. Doctor, `/api/p/{id}/overview` and the settings view render the same list.
7. **UI**. Shell fetches health once and on any failed API call; one banner shows stale-server or failing-request text with the fix. Overview and settings show deep reason and budgets.
8. **Test gate**. `tests/ui/test_browser_smoke.py` starts a temp server, runs `smoke.mjs` against it, and a second case starts a server whose code root is then deleted and asserts the banner and non-zero smoke result. Skips cleanly without Chrome or Node 22.

## Risks

- Restarting the shared server interrupts other repositories: announced, and only done when stale.
- PATH differences on Windows: covered by reusing the existing `claude.cmd` handling and keeping the Windows tests.
- `router.py` blast radius: additive functions, old methods delegate, run the full suite.
