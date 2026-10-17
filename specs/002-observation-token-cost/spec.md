# Feature Specification: Cheaper session observations — fewer model tokens, same memory

**Feature Branch**: `002-observation-token-cost`
**Created**: 2026-10-17
**Status**: Measured; **defaults NOT changed** — waiting for review (see `findings.md`, `open-questions.md`)
**Input**: User description: "Session observation (`recall_observe`) is 80% or more of all model tokens on a real repo.
Cut the model tokens spent on session observations without lowering their usefulness. Measure first, then change."

## Why this exists

Cairn writes a short memory of what an agent did (an *observation*) by sending the agent's tool events to a model.
The ledger from two real repositories says this is where the model tokens go:

| Repository | `recall_observe` calls | share of all ledger model tokens | per call: output / cache-write / cache-read |
| --- | --- | --- | --- |
| spreix-notetaker | 3,463 | 84% | 2,321 / 13,321 / 118 |
| cairn | 546 | 42% (the rest is mostly `temporal.resolve`) | 2,515 / 14,533 / 142 |

*(measured, `measure/ledger_report.py` on snapshots of each repository's `brain.db`)*

Caching is not paying off (under 1% of cache tokens are reads), and `findings.md` shows why: the observer's request
changes on every call, the Claude Code CLI writes 1-hour cache entries at twice the input price, and thinking is on by
default. This spec turns those measurements into a change that costs fewer tokens and loses nothing a reader would miss.

## User Scenarios & Testing *(mandatory)*

### User Story 1 — The same memory for far fewer tokens (Priority: P1)

Dev runs Cairn on a busy repository with their Claude Code login. Today every five tool uses cost a fresh `claude -p`
process that writes ~13,000 cache tokens it never reads and thinks before it answers. After this change the same
events produce observations a reader finds as useful, for a fraction of the tokens.

**Why this priority**: it is the whole point; observation is the largest model spend.

**Independent Test**: replay the same 50 captured events of two repositories with the old and new defaults
(`measure/replay.py`); the new default uses at least 40% fewer ledger tokens per event (priced, input + output) and passes
the quality gate in `measure/grade.py` within the measured run-to-run noise.

**Acceptance Scenarios**:

1. **Given** the Claude Code CLI provider, **When** the observer makes a call, **Then** the request is not cache-written
   unless a stable prefix of at least the model's minimum cacheable size is being sent.
2. **Given** a batch of tool events with an oversized payload, **When** it is observed, **Then** no extra model call is made
   to condense it; it is cut head and tail with an explicit elision marker.
3. **Given** the old and the new default run on the same 50 events, **When** they are graded against the raw events,
   **Then** high-value-event recall and grounding stay within the noise band measured from repeat runs.

### User Story 2 — Nothing needs a key, nothing assumes a local tool (Priority: P1)

A new user with no API key and no Claude Code CLI installs Cairn. Observations are still written (deterministic
records), and nothing in the cheaper path reads another tool's database or config.

**Independent Test**: with `CAIRN_NO_CLI_MODELS=1` and no keys, the worker writes derived records and makes zero model
calls; the test suite passes without `claude-mem` present.

### User Story 3 — Savings are provable from the ledger (Priority: P2)

Dev wants to see that the change helped. `cairn models --ledger` shows tokens per observation (and per tool event) with
thinking tokens and the cache split, and every figure on the page is labelled *measured* (from a provider's usage report)
or *estimated* (arithmetic on measured values).

**Independent Test**: after a replay, the ledger row for each call carries the provider's own counts; the page labels
dollar figures estimated.

### User Story 4 — Each provider path behaves sensibly (Priority: P2)

A user on the Anthropic API key path, the OpenAI-compatible path (hosted or local) or the Claude Code CLI path gets the
cheapest correct request for that path: no cache markers on a block that changes each call; thinking only where the
provider enables it by default.

**Independent Test**: unit tests on the request each provider builds (cache markers present only on stable blocks).

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001** The study harness (`specs/002-observation-token-cost/measure/`) MUST replay captured events through the production
  observer path and record, per model call, input, output, thinking, cache-read and cache-write tokens as the provider
  reported them.
- **FR-002** Every savings figure Cairn shows or documents MUST come from ledger rows and be labelled *measured* or
  *estimated*; an unmeasured provider path MUST be shown as estimated, with its inputs.
- **FR-003** The Claude Code CLI path MUST NOT write prompt-cache entries for requests whose prefix cannot be reused
  (every observer, summary, condensing and extraction call today), and MUST NOT bill the user's plan at the 1-hour write rate.
- **FR-004** The observer MUST NOT spend a model call to condense an oversized field by default; the existing head/tail cut with an
  elision marker applies, and the setting to condense stays available.
- **FR-005** Extended thinking MUST be off for observation calls on providers where it is on by default, unless the quality gate
  shows a loss (decided from `findings.md`).
- **FR-006** The observer prompt MUST state its closing instructions once per call, not once per event, and the brief MUST be shortened
  without dropping a rule the quality gate depends on.
- **FR-007** The default batch size and replay window MUST be set from the measured trade-off, not by guess (see plan).
- **FR-008** `worker_model=false` (deterministic records) MUST stay the behaviour when no model is available, and MUST NOT become the
  default when one is: measured, it records activity but not findings.
- **FR-009** Nothing in this feature may read or depend on `claude-mem` or any other tool that may not be installed.
- **FR-010** Every code change MUST ship with tests; every default change MUST cite the measurement that justifies it.

### Key Entities

- **Replay run**: one execution of `replay.py` — a label, the 50 events, the settings, per-call usage, the observations.
- **Quality gate**: the rubric in `measure/grade.py` (R1–R6) plus a human spot-check sheet; passes when a variant stays inside
  the noise band measured from repeat runs of the reference.
- **Lever**: one independent change (cache off, thinking off, no condensing, shorter brief, batch size, replay window, skip list,
  model, deterministic default) with its own measured effect.

## Success Criteria *(mandatory)*

- **SC-001** On at least two repositories, tokens (priced as above) per tool event fall by ≥ 40% against the old default, with the
  numbers taken from replay runs, and the quality gate passes.
- **SC-002** The cache-write share of observer calls falls below 5% of input-side tokens; cache-read share is reported, not hoped for.
- **SC-003** With no key and no CLI, zero model calls are made and observations are still stored.
- **SC-004** Re-running `measure/run_matrix*.sh` on a fresh snapshot reproduces the table in `findings.md` within the noise band.

## Out of Scope

- Changing the observation schema or what the context injection shows.
- Switching providers for the user or requiring an API key.
- Summaries (`recall_summarize`, 3–5% of observe tokens) and temporal extraction, except where they share the Router change.
- Batching across sessions or deferring observation to idle time (a possible follow-up).

## Assumptions

- Price figures are Anthropic's published Haiku 4.5 list prices; users on a subscription see plan usage, not dollars, but token
  counts and ratios carry over.
- A snapshot of `tool_uses` is a faithful record of the events the observer saw (the table is written by the same hook).
