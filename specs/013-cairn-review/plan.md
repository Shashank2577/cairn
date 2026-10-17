# Implementation Plan: Cairn Review

**Branch**: `013-cairn-review` | **Date**: 2026-10-17 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `/specs/013-cairn-review/spec.md`

## Summary

Add a review stage to Cairn: `cairn review` (and an MCP tool, an agent command, a CI mode, a team-server pull-request trigger and an
opt-in push check) that reads a change against the map, specs, history, conventions and recent agent work, stores reviews and findings in
the one read model, shows them in a new Review view, and feeds triage decisions back into memory.

Approach (see [research.md](research.md)): a **Cairn-native review pipeline in Python**, designed after the pipeline of the open source
review tool the owner pointed at (deterministic file bundling and rule matching first, a model review per bundle second, a location fix and a
reflection pass third), instead of shipping or calling its Go binary. The deterministic stage is Cairn's own checks and needs no model.
Model calls go through `router.py` under new task names and a new budget. A rule file in a compatible shape lets users carry rules between
Cairn and that tool, but nothing user-facing names it.

## Technical Context

**Language/Version**: Python 3.11–3.13 (the package's range); UI in the existing Preact/htm modules.

**Primary Dependencies**: none new. Uses `git` through `shellcmd.py` (no shell), `httpx` (already a dependency) for forge posting, the router, the memory engine.

**Storage**: new tables in `.cairn/brain.db` (the read model): `reviews`, `review_findings`, `review_rules`. Rules also exported to `.cairn/review/rules.json`.

**Testing**: pytest; deterministic paths with fixture repositories (seeded fixes, conventions, missing co-changes); router mocked; UI covered by the existing browser smoke check plus API tests.

**Target Platform**: macOS, Linux, Windows; Python wheels only (no per-platform binary).

**Project Type**: CLI + local/team server + web UI + MCP tool.

**Performance Goals**: SC-001: deterministic review of a 500-line change < 5 s on a 5,000-file repo; SC-004: pull-request review visible < 2 min (deterministic part < 30 s).

**Constraints**: works with zero keys (II); no engine name in any output (I); credential masking and `<private>` removal before any model call (FR-014); new tools must name real MCP tools; text read/written as UTF-8 explicitly (Windows).

**Scale/Scope**: pull requests up to a few hundred files; skips generated/vendored/lock files; per-review token cap.

## Existing system constraints (from Cairn)

- `src/cairn/router.py` is **HIGH** risk: 143 dependents in 45 files, 4 past fixes/reverts; it changes with `src/cairn/engines/__init__.py` and `src/cairn/store.py`. Add task names to the tier table only; do not change routing or the ledger schema. Tests around `Budget`/`BudgetExceeded` must stay green (`tests/engines/test_temporal_router_client.py`, `test_temporal_chronicle.py`).
- `src/cairn/server.py` is **HIGH** risk: 80 dependents, 4 past fixes. Add routes through a new module included by `project_router`, not by growing the file. Keep `tests/test_surfaces.py` and `tests/test_budgets.py` green.
- `src/cairn/budgets.py` is MEDIUM: [commit:6ea4c1cbac] made budgets plain-language; `test_every_budget_is_explained_in_plain_words` requires a meaning and a "when exceeded" text for each new row.
- Windows history: [commit:f3ea2a9ec1] (explicit UTF-8) and [commit:4ccf1b82cf] (coarse clocks cut budget loops): read/write text as UTF-8 and do not rely on fine clock resolution in review budgets.
- [memory:9e1e65e301] agents get a compact core tool set (12 tools, ~1,600 tokens per turn). Adding `cairn_review` is a 13th tool (~130 tokens); decided in research D7 and recorded in an ADR.
- [memory:01f6ecdcf1] engines are invisible to users; attribution only in `NOTICE` and `licenses/`.
- [memory:38319b18e9] one read model for every surface: review data lives in `brain.db`; the CLI, MCP tool, API and page read the same rows.
- [memory:7a55756715] team mode: users, roles, project overrides, scoped tokens, push webhooks, audit log. Review uses these roles; webhook handling is extended, not replaced.
- [gotcha, memory:3492e31e28] headless browser tests need a real HOME; any UI test for the Review view uses the existing smoke fixture.
- Dependency spec: the secret-masking fix (patterns in `engines/recall/tags.py`) is a hard prerequisite for FR-014 and SC-008 because today `redact()` misses most credential shapes.

## Constitution Check

| Principle | Status | Note |
|---|---|---|
| I Invisible seams | Pass | No engine name in output; a test greps CLI, API and UI strings (SC-007). Credit lives in `NOTICE` and `licenses/`. |
| II Deterministic first | Pass | Stage 1 is deterministic and complete without keys; model findings are labelled and optional. |
| III Zero friction | Pass | No binary to install; hooks and CI are opt-in, idempotent, reversible by `cairn uninstall`. |
| IV Compounding | Pass | Triage decisions become memories and suppressions. |
| V Token discipline | Pass | Bundling, per-review cap, packs within `context.budget`, stable prefix cached. |
| VI Right model | Pass | `review.bundle` balanced, `review.reflect` fast; frontier `review` untouched and opt-in. |
| VII Local-first | Pass with note | Code goes only to the configured provider after masking; forge posting is the one outward write and is per project. |
| VIII Agent-neutral | Pass | MCP tool plus generated commands per agent. |

Re-checked after design: unchanged. One flagged risk, R1 below.

## Risks

- **R1 – "author pays" on a server.** A server has no copy of the author's local login. Making the author pay needs a per-user model credential on the server (new, sensitive storage under the server key). Plan: Phase 3 adds an optional per-user provider key (encrypted with the server key, never shown after entry); until then, server-automatic reviews that cannot be charged to the author run deterministic-only and say why (FR-019). Local CLI and CI runs are paid by whoever runs them, which for a developer's own branch is the author.
- **R2 – false positives.** Mitigated by citations, dismiss-with-reason, suppressions, severity thresholds and a calibration test set (SC-003).
- **R3 – forge write access.** Posting needs a token with comment scope; stored per project under the server key, absent by default, and posting is skipped with a clear note when missing.
- **R4 – owner's wish to embed the original tool.** This plan chooses a Python reimplementation of the pipeline. The reasons are in research D1; the adapter seam (rules file compatibility, a pluggable stage-2 reviewer) keeps the door open.

## Project Structure

### Documentation (this feature)

```text
specs/013-cairn-review/
├── spec.md  plan.md  research.md  data-model.md  quickstart.md
├── contracts/   (cli.md, mcp-and-api.md, events.md)
└── checklists/requirements.md
```

### Source code

```text
src/cairn/review/              # new package
├── __init__.py   pipeline.py (run a review: bundle → rules → model → fix → reflect)
├── diff.py       (base/head resolution, file filtering, generated/vendored skip list)
├── checks.py     (deterministic checks: co-change, drift, history, conventions, secrets)
├── rules.py      (rules from conventions + user rules; rules.json import/export; suppressions)
├── model.py      (router calls: review.bundle, review.reflect; masking; budget)
├── store.py      (tables, queries, state changes)
├── learn.py      (accepted/dismissed → memory proposals, suppression counts)
├── forge.py      (summary comment: GitHub first, GitLab and Gitea after)
└── pack.py       (compact text for MCP and agents)
src/cairn/cli.py              # `cairn review` group (thin)
src/cairn/mcp_server.py       # cairn_review tool
src/cairn/agents.py           # /cairn:review command + CLAUDE.md block line
src/cairn/hooks.py            # opt-in pre-push check (installed by init)
src/cairn/platform/web.py     # PR events on the existing webhook route
src/cairn/server.py           # include review router (one line)
src/cairn/budgets.py          # review.budget_tokens, review.wall_seconds
src/cairn/ui/app/views/review.js  # + small hooks in impact.js, specs.js, overview.js, timeline.js
docs/review.md  docs/adr/0009-review-stage.md
tests/review/…
```

**Structure Decision**: one new package with thin edits to HIGH-risk files (router untouched except its tier table; server gets one include).

## Phases

1. **Foundations** (needs the secret-masking fix): tables, diff + file filter, deterministic checks, CLI, pack, budgets, tests. Delivers Story 1–2 without a model.
2. **Model stage and triage**: router tasks, bundling, reflection, ledger entries, accept/dismiss/make-rule, suppressions, MCP tool and agent command, Review view and cross-view hooks. Delivers Stories 4 and 6 and the model half of 1.
3. **Automatic**: CI mode and exit codes, pre-push check, webhook PR events, one-review-per-head, forge summary, per-user model key (R1). Delivers Story 3.
4. **Learning**: memory proposals after three acceptances, suppression counts, rules from conventions. Delivers Story 5.

## Complexity Tracking

| Item | Why needed | Simpler alternative rejected |
|---|---|---|
| New package `review/` | The feature has five separable concerns and its own tables | Putting it in `core.py` (40 KB already, HIGH hub) |
| Per-user model key (Phase 3) | FR-019 "author pays" on a server | Charging the server owner: contradicts the decision |
