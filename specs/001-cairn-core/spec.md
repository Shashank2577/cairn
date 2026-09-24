# Feature Specification: Cairn Core — the engineering brain

**Feature Branch**: `001-cairn-core`
**Created**: 2026-09-24
**Status**: Ready for planning (clarified)
**Input**: User description: "Consolidate a code knowledge graph, spec-driven workflow, temporal
knowledge graph, long-term memory and agent session capture into one product. One command to
plug into new or existing repos, a premium terminal experience across coding agents, one
beautiful single-page UI, useful from day one and more useful over time."

## User Scenarios & Testing *(mandatory)*

### User Story 1 — One command on an existing repo (Priority: P1)

Priya maintains a four-year-old payments service. She runs `cairn` in the repo. In under a
minute she has a structural map of the code, git-derived risk signals, and her coding agents
(Claude Code and Cursor) are wired to the brain. Nothing asked her a question.

**Why this priority**: Without frictionless adoption nothing else matters. This alone delivers
value: a queryable map plus history-aware impact answers.

**Independent Test**: Run `cairn` in a clean checkout of any git repo with no keys set. Verify the
map is built, `cairn impact <symbol>` returns callers + co-change + risky past commits, the MCP
server responds, and agent config files were written idempotently.

**Acceptance Scenarios**:

1. **Given** a git repo with no Cairn state, **When** the developer runs `cairn`, **Then** Cairn
   initialises `.cairn/`, builds the map, ingests git history, wires detected agents, installs
   git hooks, starts the local UI and prints a summary with next steps — with zero prompts.
2. **Given** Cairn is already initialised, **When** the developer runs `cairn` again, **Then** it
   shows status in < 300 ms and changes nothing.
3. **Given** the repo has pre-existing `CLAUDE.md`/`AGENTS.md`, **When** Cairn wires agents,
   **Then** it only adds a marked block and never rewrites user content.

---

### User Story 2 — "What breaks if I change this?" (Priority: P1)

Before touching `PaymentService.process`, Priya (or her agent) asks for impact. Cairn returns
dependents from the map, files that historically change together, past fixes/reverts touching
the area, specs/tasks that own the code, recent agent sessions that worked there, and memories
(conventions, gotchas) — ranked, cited and within a token budget.

**Why this priority**: This is the question agents most often get wrong; answering it well is the
core value proposition.

**Independent Test**: On a repo with history, `cairn impact <target>` and the MCP `cairn_impact`
tool return all available sections with provenance tags; output respects `--budget`.

**Acceptance Scenarios**:

1. **Given** a symbol with callers, **When** impact is requested, **Then** direct and transitive
   dependents are listed with depth and edge provenance.
2. **Given** a file touched by a commit whose message indicates a fix or revert, **When** impact is
   requested, **Then** that commit appears under historical warnings.
3. **Given** no model key, **When** impact is requested, **Then** the full deterministic answer is
   returned; with a key, an optional deep-tier summary is prepended.

---

### User Story 3 — "Why is this code like this?" (Priority: P2)

A new teammate asks why retries in `client.py` are capped at three. Cairn assembles rationale
comments, the commits that introduced and changed the lines, the spec requirement and task that
produced it, decisions stored in memory, and timeline facts — then (optionally) narrates.

**Independent Test**: `cairn why <file|symbol>` returns rationale, origin commits, owning spec
items and related memories, each cited.

**Acceptance Scenarios**:

1. **Given** code with `# WHY:`/`# NOTE:` comments, **When** why is requested, **Then** those
   rationale nodes are included first.
2. **Given** a task in `tasks.md` that names the file, **When** why is requested, **Then** the task,
   its user story and requirement are linked.

---

### User Story 4 — Spec-driven work with memory (Priority: P2)

A developer uses the spec workflow (constitution → specify → clarify → plan → tasks → analyze →
implement → converge). At each stage the brain contributes: before planning it supplies impact
and conventions; after tasks it links tasks to code and flags risky files; after implementation
it verifies against the spec (drift) and records decisions to memory.

**Independent Test**: In a repo initialised by Cairn, the spec workflow's hook configuration lists
Cairn hooks at `before_plan`, `after_tasks`, `after_clarify`, `after_implement`; `cairn specs`
shows each feature's requirement → task → file trace and progress.

**Acceptance Scenarios**:

1. **Given** `specs/NNN-x/tasks.md` with file paths, **When** Cairn syncs, **Then** each task is
   linked to the files it names and completion (`[x]`) is reflected as progress.
2. **Given** a completed task whose named file no longer exists, **When** drift runs, **Then** a
   drift finding is raised with evidence.
3. **Given** a functional requirement with no task, **When** drift runs, **Then** it is flagged
   as uncovered.

---

### User Story 5 — Memory that compounds (Priority: P2)

Agents and developers record durable knowledge ("payments must be idempotent", "prefer optimistic
locking"). Agent sessions are captured automatically. Later, any agent in any tool recalls it.

**Independent Test**: `cairn remember "..." --kind convention`, then `cairn recall` and the MCP
`cairn_context` tool surface it for related targets. Captured sessions appear under Sessions,
linked to the files they read and modified.

**Acceptance Scenarios**:

1. **Given** a stored memory mentioning a symbol, **When** context for that symbol is assembled,
   **Then** the memory is included with its source and date.
2. **Given** a newer memory that contradicts an older one, **When** it is stored with
   `--supersedes`, **Then** the old one is marked superseded and hidden by default.
3. **Given** session capture is installed, **When** an agent session ends, **Then** its observations
   are ingested on next sync and linked to files.

---

### User Story 6 — One page to see everything (Priority: P2)

`cairn ui` opens a single page: a map of the system, the five layers as a stacked navigation,
a dossier for any selected thing (why, impact, history, memory, sessions, specs), a timeline
strip, and a command bar that answers questions.

**Independent Test**: With the server running, the page loads offline, renders the map, and every
layer view works against the HTTP API.

---

### User Story 7 — Premium agent experience everywhere (Priority: P3)

In Claude Code, the status line shows brain health and open drift; a session-start briefing gives
the agent a compact project brief; `/cairn:*` slash commands and model-tiered sub-agents exist.
Codex, Cursor, Gemini CLI and VS Code get the MCP server and instruction files.

**Independent Test**: `cairn agents install` writes the expected files for each detected agent;
`cairn statusline` prints one line; `cairn hook session-start` prints valid hook JSON under 600 tokens.

---

### User Story 8 — Deep tier when a key is present (Priority: P3)

With a model key, Cairn builds a temporal fact graph from commits, specs, sessions and decisions
("X was true from March until the May refactor"), upgrades memory to semantic dedupe/update, and
narrates why/impact/drift answers — routing each job to the cheapest capable model tier and
recording cost.

**Independent Test**: With a key, `cairn sync --deep` ingests episodes with a budget; `cairn models
--ledger` shows per-tier calls and tokens; without a key the same command is a no-op with a hint.

### Edge Cases

- Brand-new empty repo: map is empty; Cairn guides the user to start with constitution/specify.
- Not a git repo: Cairn offers `git init` guidance and still builds the map for the folder.
- Monorepos with >50k files: map build is incremental; UI shows communities, not raw nodes.
- Detached HEAD / shallow clones: history ingest uses what exists and reports depth.
- Session capture or spec tooling unavailable (no Node/uv): those layers show as "not connected"
  with one command to enable; nothing else degrades.
- Concurrent syncs (hook + manual): a lock file serialises them; the second exits immediately.
- Model provider errors or budget exhaustion: deterministic output is returned, error is noted.

## Clarifications

### Session 2026-09-24

- Q: Should underlying engines be visible to users? → A: No. One vocabulary (Map, Specs, Timeline,
  Memory, Sessions). Engines are dependencies, credited in THIRD_PARTY_NOTICES.md only (licence
  notices are legally required and are kept there).
- Q: Must the product work without any API key? → A: Yes. Deterministic tier is complete; keys
  unlock the deep tier (temporal facts, semantic memory, narration, semantic drift).
- Q: Graph store for the temporal layer without Docker? → A: Embedded store by default; a graph
  server URL (FalkorDB/Neo4j) switches to it. Embedded mode is flagged as "local" in doctor.
- Q: Embeddings without an embeddings API? → A: Local ONNX embeddings when installed; otherwise
  the deep tier requires an OpenAI-compatible embeddings endpoint. Memory falls back to full-text.
- Q: Which model does which job? → A: fast=Haiku (classify, summarise), balanced=Sonnet (extract,
  link tie-breaks), deep=Opus (why/impact/drift synthesis, ask), frontier=Fable (opt-in reviews).
- Q: What happens to the engines' own agent integrations? → A: Session capture installs its hooks
  (required for capture). The code map's own agent skill is NOT installed by default — agents use
  Cairn's MCP instead, to keep one surface. `--native-skills` opts in.
- Q: Default UI port? → A: 4747 (configurable), bound to 127.0.0.1 only.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: `cairn` with no arguments MUST initialise an uninitialised repo and show status otherwise.
- **FR-002**: Init MUST be idempotent, non-destructive to user files, and reversible via `cairn uninstall`.
- **FR-003**: The system MUST build and incrementally update a code/doc map without model calls.
- **FR-004**: The system MUST ingest git history incrementally, derive co-change pairs and risk commits.
- **FR-005**: The system MUST parse spec artifacts (constitution, spec, plan, tasks) into
  features, user stories, requirements and tasks, linking tasks to files they name.
- **FR-006**: The system MUST ingest captured agent sessions and link them to files read/modified.
- **FR-007**: The system MUST store memories with kind, scope, provenance, supersession and search.
- **FR-008**: The system MUST resolve entities across layers into one registry with typed, provenance-tagged links.
- **FR-009**: The system MUST answer impact, why, trace (path/explain), search, recall and context
  queries deterministically within a caller-supplied token budget, with citations.
- **FR-010**: The system MUST detect drift: uncovered requirements, done tasks with missing files,
  files changed after their task completed, and (deep tier) semantic requirement violations.
- **FR-011**: The system MUST expose all query capabilities over one MCP server with ≤ 8 tools.
- **FR-012**: The system MUST install agent integrations for Claude Code, Codex, Cursor, Gemini CLI,
  VS Code and a generic AGENTS.md, only for agents detected (or explicitly requested).
- **FR-013**: The system MUST install git hooks that trigger a quick background sync after commit,
  merge and checkout.
- **FR-014**: The system MUST serve a single-page UI and HTTP API from a local daemon.
- **FR-015**: The system MUST integrate with the spec workflow as an extension providing hooks at
  `before_plan`, `after_tasks`, `after_clarify` and `after_implement`.
- **FR-016**: With a model key, the system MUST route model work by task tier, enforce a per-sync
  token budget, use prompt caching for stable prefixes, and record a cost ledger.
- **FR-017**: With a model key, the system MUST maintain a temporal fact graph from episodes and
  support semantic memory updates.
- **FR-018**: The system MUST expose engine-native capabilities (graph query/path/explain/hubs/
  communities, PR impact, spec workflow commands, session search, memory history) through
  Cairn commands without exposing engine names.
- **FR-019**: `cairn doctor` MUST report every capability's state and the exact command to enable it.

### Key Entities

- **Entity**: anything addressable — file, symbol, rationale, spec, story, requirement, task,
  commit, session, observation, memory, fact. Canonical id `kind:key`.
- **Link**: typed, directed relation between entities with provenance, confidence and source.
- **Event**: a timestamped occurrence on the timeline (commit, spec change, session, decision).
- **Memory**: durable knowledge with kind (convention, decision, gotcha, preference, fact), scope
  (project/user), provenance and optional supersession.
- **Context Pack**: ranked, budgeted, cited bundle returned to agents.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: From `pipx/uv install` to first useful impact answer in < 2 minutes on a 1k-file repo.
- **SC-002**: Zero prompts and zero config edits needed for the deterministic tier.
- **SC-003**: Agent-facing context packs stay within budget (default 1,800 tokens) 100% of the time.
- **SC-004**: Re-running init produces no file changes (idempotence), verified in CI.
- **SC-005**: Deep-tier sync cost for a typical day of commits stays under the configured budget
  (default 150k tokens), with ≥ 70% of calls on the fast or balanced tier.

## Assumptions

- Python ≥ 3.11 is available; `uv` or `pipx` is the install path. Node ≥ 18 enables session capture.
- Model access defaults to Anthropic; OpenAI-compatible endpoints are supported for all tiers.
- The temporal graph uses an embedded store by default and a graph server when configured.
