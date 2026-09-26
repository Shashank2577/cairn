<!--
Sync Impact Report
Version: 1.0.0 → 1.1.0 (2026-09-25)
Modified principles: I (attribution now lives in NOTICE and licenses/, per ADR-0005); VII (state may
also live on a team server the team runs, per ADR-0007; model calls go to whichever provider is
available, including the signed-in Claude Code CLI, per ADR-0004).
Templates reviewed: plan/spec/tasks — compatible, no edits required.
Previous: 0.0.0 → 1.0.0 (initial ratification, principles I–VIII).
-->
# Cairn Constitution

Cairn gives coding agents and the developers who drive them an institutional memory:
what the code is, what was intended, what happened, what was learned, and why.

## Core Principles

### I. One Product, Invisible Seams
Users see one tool with one vocabulary — **Map, Specs, Timeline, Memory, Sessions**.
Underlying engines are implementation details: they never appear in commands, UI copy,
or agent-facing tool names. Engine-specific behaviour is reached only through Cairn
concepts. Attribution lives in `NOTICE` and `licenses/`, and nowhere else.

### II. Deterministic First, Models Second (NON-NEGOTIABLE)
Every core answer (impact, why, trace, drift checks) MUST have a deterministic path built
from ASTs, git history, spec artifacts and session logs, and MUST work with zero API keys.
Model calls enrich, rank or narrate; they never gate a feature. Every claim carries
provenance: `EXTRACTED` (read from a source) or `INFERRED` (derived), plus its source id.

### III. One Command, Zero Friction
`cairn` in any git repository — new or ten years old — must reach a useful state without
questions, config files, Docker, or keys. Setup is idempotent and reversible
(`cairn uninstall`). Every optional capability is detected, never demanded.

### IV. Useful on Day One, Compounding Over Time
Day one: structural map, git-derived risk, spec traceability. Every commit, spec, session
and decision after that makes answers better. Nothing a developer or agent learns should
have to be learned twice.

### V. Token Discipline
Context is a budget, not a dump. Agent-facing outputs are ranked, deduplicated and packed
to an explicit token budget with citations instead of copied source. Stable prefixes are
prompt-cached. Work is incremental (content hashes, cursors) and batched. The cheapest
model that meets the quality bar does the work; escalation is explicit and logged.

### VI. Right Model for the Job
Model tiers are routed by task, not by habit: **fast** (Haiku) for classification and
summaries at volume, **balanced** (Sonnet) for extraction, **deep** (Opus) for judgment
and synthesis, **frontier** (Fable) only for opt-in whole-system reviews. Routing is
configurable per project and every call is attributed in the cost ledger.

### VII. Local-First and Private by Default
All state lives in the repository's `.cairn/` directory and the developer's `$CAIRN_HOME`,
or on a team server the team runs itself. Nothing leaves the machine except model calls, which
go only to a provider that is available (an API key, a configured endpoint or the developer's
signed-in Claude Code CLI), and every call is recorded in the ledger (`cairn models --ledger`).

### VIII. Agent-Neutral
Claude Code, Codex, Cursor, Gemini CLI, Copilot and any MCP client get the same brain
through one MCP server and generated instruction files. Premium touches (status line,
session-start briefings, sub-agents, slash commands) are added where an agent supports them.

## Quality Gates

- Tests MUST cover every deterministic path; `pytest` is green before merge.
- A feature ships with: CLI command, MCP tool or UI surface where relevant, and docs.
- Performance budgets: `cairn` status < 300 ms; MCP context call < 800 ms p95 on a
  5k-file repo (deterministic tier); UI first paint < 1 s from a local server.

## Development Workflow

All non-trivial work follows Cairn: constitution → specify → clarify → plan → tasks →
analyze → implement → converge. Architecture decisions are recorded as ADRs in `docs/adr/`.

## Governance

This constitution supersedes other practices. Amendments require an ADR, a version bump
(semver: MAJOR for principle removal/redefinition, MINOR for additions, PATCH for wording),
and a Sync Impact Report at the top of this file.

**Version**: 1.1.0 | **Ratified**: 2026-09-24 | **Last Amended**: 2026-09-25
