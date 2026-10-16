# Research: daemon health and deep clarity

## R1 Why the UI died while everything reported healthy
Decision: treat liveness and staleness as separate verdicts. Rationale: observed 2026-10-16, the server started Oct 10 from a worktree under `~/orca/workspaces/cairn/local/.venv` that was later deleted; `GET /api/health` still answered `{"ok": true}` (`daemon._health`), so `daemon.start()` reused it, while `GET /` returned 500. Alternatives: make health fail when files are missing (rejected: health must stay a cheap liveness probe other tools rely on); poll the filesystem in the server (rejected: a process whose code is gone cannot be trusted to report itself).

## R2 Why deep shows off
Decision: one `deep_state()` with a reason, and a wider search for the Claude Code CLI. Rationale: `Router.api_key` for provider `claude-code` returns a value only if `shutil.which("claude")` succeeds, so a server started with a minimal PATH (a hook, launchd, an IDE) reports deep off even though `claude` exists in `/opt/homebrew/bin`. `[deep] enabled = "auto"` is documented as "on when a model key is present", which also misleads users whose backend is the CLI login. Alternatives: require users to set PATH (rejected: violates "zero friction"); always on (rejected: hides a real missing prerequisite).

## R3 What "budget" means today
Three unrelated limits share the word: `[context] budget = 1800` (tokens in one agent-facing context pack), `[deep] budget_tokens = 150000` (hard ceiling per deep sync), `[recall] wall_seconds = 300` (stop memory seeding after this long). Decision: three plain names, one list, same text in doctor, API and UI.

## R4 Test gap
`tests/ui/smoke.mjs` already visits every page in headless Chrome but only runs by hand (CONTRIBUTING.md), so pytest was green while the UI was dead. Decision: run it from pytest against a temp server, skipping when Chrome or Node 22 is missing, plus one negative case.

## R5 Embedded-browser blank graph
The Engine views frames rendered blank in an embedded browser pane that blocks the graph script (`ERR_BLOCKED_BY_CLIENT`); in ordinary Chrome the interactive graph renders (569 nodes, 2,591 edges). Decision: not a Cairn defect; the smoke check uses ordinary Chrome and asserts the frame's script loaded.
