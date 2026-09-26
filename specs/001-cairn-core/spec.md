# Feature Specification: Cairn Core — one product for project memory

**Feature Branch**: `001-cairn-core`
**Created**: 2026-09-24
**Updated**: 2026-09-25
**Status**: Implemented, with converge items open
**Input**: User description: "One product that gives coding agents and developers an institutional
memory: a code and document knowledge graph, a spec-driven workflow, agent session memory, a temporal
fact graph and self-reconciling memory, built in under one vocabulary. One command on any repository,
useful with no model, better with one, and the same product for one developer or a team."

## User Scenarios & Testing *(mandatory)*

### User Story 1 — One command on any repository (Priority: P1)

Priya maintains a four-year-old payments service. She runs `cairn` in the repository. In a minute or
two she has a map of the code, git-derived risk signals, the spec workflow, her agents wired to the
project's memory and a page to look at. Nothing asked her a question, and she needed no key.

**Why this priority**: Without frictionless adoption nothing else matters.

**Independent Test**: Run `cairn` in a clean git repository with no keys and no Claude Code CLI.
The map is built, `cairn impact <symbol>` answers, agent configs are written, and a second run
changes no files.

**Acceptance Scenarios**:

1. **Given** a git repository with no Cairn state, **When** the developer runs `cairn`, **Then** it
   adds the spec workflow, wires detected agents, installs git hooks, syncs every layer, starts the
   local server and prints the page address, with zero prompts.
2. **Given** Cairn is set up, **When** the developer runs `cairn` again, **Then** it shows status and
   changes nothing.
3. **Given** existing `CLAUDE.md`, `AGENTS.md` or agent configs, **When** Cairn wires agents, **Then**
   it only adds a marked block or merges its own keys, and `cairn uninstall` removes exactly those.

---

### User Story 2 — "What breaks if I change this?" (Priority: P1)

Before touching `PaymentService.process`, Priya or her agent asks for impact. Cairn returns
dependents, tests likely affected, files that change together, past fixes and reverts, the spec task
that owns the code, conventions and recent agent work, ranked, cited and within a token budget.

**Why this priority**: This is the question agents most often get wrong.

**Independent Test**: `cairn impact <target>` and the `cairn_impact` tool return every available
section with provenance tags and never exceed the budget.

**Acceptance Scenarios**:

1. **Given** a symbol with callers, **When** impact is requested, **Then** direct and transitive
   dependents are listed with depth and provenance.
2. **Given** a file touched by a commit whose message marks a fix or revert, **When** impact is
   requested, **Then** that commit appears as a warning and raises the risk level.
3. **Given** no model, **When** impact is requested, **Then** the full deterministic answer is
   returned; with a model, `--explain` adds a short summary.

---

### User Story 3 — "Why is this code like this?" (Priority: P2)

A new teammate asks why retries in `client.py` are capped at three. Cairn assembles rationale
comments, the commits that wrote the lines, the requirement and task that produced the code,
recorded decisions and timeline facts.

**Independent Test**: `cairn why <file|symbol>` returns rationale, origin commits, owning spec items
and related memories, each cited.

**Acceptance Scenarios**:

1. **Given** code with rationale comments, **When** why is requested, **Then** they come first.
2. **Given** a task in `tasks.md` naming the file, **When** why is requested, **Then** the task, its
   story and its requirement are linked.

---

### User Story 4 — Spec-driven work with memory (Priority: P2)

A developer works spec-first in any of 41 agents: constitution, specify, clarify, plan, tasks,
analyze, implement, converge. The project's memory joins in at each stage: plans are grounded in
impact and history, tasks are traced to code, clarifications become decisions, and implementation
ends with a drift check and recorded learnings.

**Independent Test**: After `cairn init`, the `/cairn.*` commands (or `/cairn-*` skills) exist for the
agent, `cairn specs` shows each feature's requirement → task → file trace, and `cairn drift`
reports done tasks whose files are missing.

**Acceptance Scenarios**:

1. **Given** `specs/NNN-x/tasks.md` naming files, **When** Cairn syncs, **Then** each task links to
   those files and `[x]` counts as progress.
2. **Given** a done task naming a file that doesn't exist, **When** drift runs, **Then** a
   high-severity finding is raised with evidence.
3. **Given** a functional requirement no task or plan references, **When** drift runs, **Then** it is
   flagged as uncovered.
4. **Given** a repository on an older workflow layout, **When** `cairn init` runs, **Then** the
   workflow is migrated into `.cairn/workflow/` with its constitution and settings.

---

### User Story 5 — Memory that keeps itself tidy (Priority: P2)

Agents and developers record durable knowledge ("payments must be idempotent"). Cairn also learns
from what the repository already says: accepted ADRs, answered clarifications, contributing rules,
reverted and explained fixes. With a model, a new memory updates, merges with or retires the ones it
overlaps, and every change is kept in its history.

**Independent Test**: `cairn remember "…" --kind convention`, then `cairn recall` and
`cairn_context` surface it for related targets; `cairn memory history <id>` shows its changes.

**Acceptance Scenarios**:

1. **Given** a memory mentioning a symbol, **When** context for that symbol is assembled, **Then** the
   memory is included with its source.
2. **Given** a newer memory stored with `--supersedes`, **Then** the old one is hidden by default and
   kept in history.
3. **Given** an ADR is edited or deleted, **When** Cairn syncs, **Then** the memory seeded from it is
   updated or retired; hand-written memories are never retired by seeding.

---

### User Story 6 — Agents remember earlier sessions (Priority: P1)

Every Claude Code session in the repository is captured: prompts, tool use, files read and changed.
The next session starts with a compact timeline of recent work, reading a file brings up what earlier
sessions learned about it, and agents can search, expand and read past observations.

**Why this priority**: Agents forget everything between sessions; this is where much of the value is.

**Independent Test**: With capture on, a session's events land in `.cairn/sessions.db`; after the
worker or a sync, `cairn sessions search` finds observations linked to the files touched, and the
`SessionStart` hook prints a context block.

**Acceptance Scenarios**:

1. **Given** a hook receives malformed input or a locked store, **Then** it exits 0 and the agent is
   unaffected.
2. **Given** a prompt containing `<private>…</private>` or a credential-like value, **Then** neither
   is stored.
3. **Given** no model, **When** the queue is drained, **Then** deterministic observations are
   recorded; with a model, observations and summaries are written by it.

---

### User Story 7 — What was true, and when (Priority: P3)

With a model available, Cairn builds a fact graph from each day's commits, spec progress, sessions
and decisions. Facts carry validity windows; a later contradiction ends a fact instead of deleting it.

**Independent Test**: With a model, `cairn sync` (or `cairn timeline ingest`) creates episodes and
facts within the budget; `cairn timeline search <q> --at <date>` answers as of that date; without a
model, ingestion is a no-op and reads still work.

---

### User Story 8 — One page for everything (Priority: P2)

`cairn ui` opens one page with an overview (layers, activity, savings, hubs), impact and why for any
target, the map with its views and wiki, specs with drift, sessions, the timeline, memory and
settings, updated live while syncs and sessions run.

**Independent Test**: With the server running, the page loads with no network access and every view
works against the HTTP API.

---

### User Story 9 — Every agent gets the same memory (Priority: P2)

Claude Code, Codex, Cursor, Gemini CLI and VS Code get the `cairn` MCP server and instructions. The
default tool set is small enough that it saves agents more tokens than it costs. Claude Code also
gets a status line, a session-start briefing, `/cairn:*` commands and model-tiered sub-agents.

**Independent Test**: `cairn agents install` writes the expected files per detected agent; the core
tool set lists 12 tools; `[mcp] tools = "all"` lists every operation.

---

### User Story 10 — Model features with no key (Priority: P2)

A developer signed in to Claude Code gets model features without creating a key. A team with an API
key or a local OpenAI-compatible server uses that instead. Every job goes to the cheapest capable
tier, within budgets, and every call is in the ledger.

**Independent Test**: With only the `claude` CLI on PATH, `cairn doctor` shows the `claude-code`
model and `cairn sync` records ledger rows; with nothing available, every model feature reports
that it needs a model and nothing else changes.

---

### User Story 11 — One server, many projects, a whole team (Priority: P2)

A developer with several repositories runs one local server for all of them. A team runs the same
server in team mode: people sign in, belong to teams with roles, add projects from git URLs that
re-sync on push, issue tokens for agents and CI, and can see who changed what.

**Independent Test**: `cairn ui` in two repositories reuses one server; in team mode, requests without
a session or token get 401, a viewer's write gets 403, a project outside the caller's teams gets 404,
and a signed push webhook triggers a refresh and sync.

---

### User Story 12 — Savings you can see (Priority: P3)

Priya wants to know whether Cairn pays for itself. Every pack an agent receives is logged with its
size and the size of the files behind it, and the Overview page shows the totals by surface.

**Independent Test**: Calling `cairn_impact` through MCP adds one `queries` row with sent and source
tokens; the page's own lookups add none; session briefings count as cost.

### Edge Cases

- Brand-new empty repository: the map is empty; Cairn suggests starting spec-driven work with
  `/cairn-specify` (and `cairn specs` points to `/cairn-constitution` first).
- Not a git repository: history and hooks are skipped with guidance; map, specs and memory still work.
- Large repositories: map builds are incremental; the page shows areas rather than every node.
- Concurrent syncs (hook and manual): a lock serialises them; the second exits at once.
- The embedded fact graph is a single-writer file: every process opens it per operation, so the
  server, git-hook syncs and agents never lock each other out.
- No network on first run: a hashing embedder replaces the local model; search stays lexical.
- Model errors or budget exhaustion: deterministic output is returned and the error noted; unfinished
  timeline work resumes on the next sync.
- A malformed `config.toml` is ignored rather than blocking commands.

## Clarifications

### Session 2026-09-24

- Q: Should the engines be visible to users? → A: No. Users learn one vocabulary (Map, Specs,
  Timeline, Memory, Sessions); engine origins are credited only in NOTICE and licenses/.
- Q: Must Cairn work without any model? → A: Yes. Impact, why, context, drift, the map, git history,
  spec parsing and session capture are deterministic; models add observations, facts,
  reconciliation, narration and semantic drift.
- Q: Default page port and binding? → A: 4747 on 127.0.0.1 in local mode, configured in
  $CAIRN_HOME/server.toml.

### Session 2026-09-25

- Q: Are the engines separate installs? → A: No. All five engines are part of Cairn's source under
  src/cairn/engines with their full feature sets, sharing one model layer, one vector module, one
  read model, one CLI, one MCP server, one HTTP API and one page.
- Q: Which model provider does Cairn use by default? → A: provider auto picks an Anthropic key, then
  an OpenAI-compatible key or endpoint, then the signed-in Claude Code CLI, which needs no key.
- Q: Where do the stores live by default? → A: Embedded in .cairn: the fact graph in
  .cairn/temporal/graph.kuzu, memory vectors in .cairn/memstore, sessions in .cairn/sessions.db;
  teams can point the fact graph at Neo4j or FalkorDB and memory at Qdrant, pgvector or Chroma.
- Q: How many MCP tools do agents get by default? → A: A compact core set of 12 tools; setting
  mcp tools to all exposes every map, timeline-fact and session operation.
- Q: One server per repository? → A: No. One server serves every project on the machine; team mode
  adds sign-in, roles, scoped hashed tokens, git projects with push webhooks and an audit log.
- Q: How do agents on other machines use a team server? → A: Through /mcp/<project id> with a
  bearer token; cairn agents connect writes the config with the token read from an environment
  variable, and callers who cannot write get read-only tools.
- Q: Where does the spec workflow keep its files? → A: Its constitution, templates, scripts and
  integrations live in .cairn/workflow (committed with the repository); features live in specs.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: `cairn` with no arguments MUST set up an uninitialised repository and show status otherwise.
- **FR-002**: Setup MUST never prompt, MUST be idempotent, MUST NOT rewrite user content, and MUST be
  reversible with `cairn uninstall`.
- **FR-003**: The system MUST build and incrementally update a code and document map with tree-sitter,
  with no model calls, and offer path, explain, query, hubs, areas, views, a report, a wiki, exports
  and PR impact.
- **FR-004**: The system MUST ingest git history incrementally and derive co-change pairs and risk tags.
- **FR-005**: The system MUST bundle the spec-driven workflow in `.cairn/workflow/` with commands for
  41 agent integrations, extensions, presets, workflows and bundles, working offline, and MUST migrate
  older workflow layouts.
- **FR-006**: The system MUST parse spec artifacts into features, stories, requirements and tasks and
  link tasks to the files they name.
- **FR-007**: The system MUST detect drift deterministically (missing files for done tasks, uncovered
  requirements, files changed after their task was done, finished specs still marked Draft) and, with
  a model, judge recently changed requirements under a budget.
- **FR-008**: The system MUST capture agent sessions through hooks that only write to
  `.cairn/sessions.db`, never fail the agent, drop `<private>` content and mask credential-like values.
- **FR-009**: The system MUST turn captured events into observations and session summaries with a
  model, or deterministic records without one, make them searchable by text and meaning, and inject
  recent work at session start.
- **FR-010**: The system MUST store memories with kind, scope, provenance, supersession and history,
  reconcile new memories with a model, and seed memory from ADRs, clarifications, contributing rules
  and git history.
- **FR-011**: The system MUST maintain a temporal fact graph with validity windows from daily episodes
  under a token budget, embedded by default and on a graph server when configured.
- **FR-012**: The system MUST mirror every engine into one read model with canonical ids and typed,
  provenance-tagged links.
- **FR-013**: The system MUST answer impact, why and context within a caller-supplied token budget
  and search, recall and facts within a result limit, all with citations, and narrate only when asked
  and a model is available.
- **FR-014**: The system MUST expose a compact core MCP tool set by default and every operation when
  `[mcp] tools = "all"`.
- **FR-015**: The system MUST wire detected agents (Claude Code, Codex, Cursor, Gemini CLI, VS Code)
  and a generic `AGENTS.md`, adding a status line, briefing, commands and sub-agents for Claude Code.
- **FR-016**: The system MUST install git hooks that sync in the background after commit, merge,
  checkout and rewrite, with syncs serialised by a lock.
- **FR-017**: The system MUST route every model call through one layer with providers `anthropic`,
  `openai`, `claude-code` and `auto`, task tiers, bounded escalation, budgets and a ledger.
- **FR-018**: One server per machine MUST serve every registered project: the HTTP API, a live event
  stream, a per-project MCP endpoint and the page.
- **FR-019**: Team mode MUST require a session or token and provide teams, roles with per-project
  overrides, invitations, scoped hashed tokens, git projects with push webhooks and an audit log.
- **FR-020**: The system MUST log every context pack handed to a reader with sent and source tokens
  and show the savings on the page.
- **FR-021**: `cairn doctor` MUST report every capability's state and the command that enables it.
- **FR-022**: The product MUST use Cairn's names only; the engines' origins MUST be credited only in
  `NOTICE` and `licenses/`.
- **FR-023**: Agents on other machines MUST be able to send captured session events to a team server
  project.

### Key Entities

- **Entity**: anything addressable (file, symbol, rationale, spec, story, requirement, task, commit,
  session, observation, fact), with a canonical id `kind:key`.
- **Link**: a typed, directed relation between entities with provenance, confidence and source.
- **Event**: a timestamped occurrence on the timeline (commit, spec change, workflow run, session,
  memory, fact, drift finding).
- **Memory**: durable knowledge with kind (convention, decision, gotcha, preference, fact), scope
  (project, team, user, session), source, provenance, supersession and history.
- **Observation / session summary**: what an agent did and learned, with the files it read and changed.
- **Fact**: a statement with valid-from and valid-until times, extracted from an episode.
- **Context pack**: a ranked, budgeted, cited answer.
- **Project, team, membership, token, audit entry**: the platform's records in `platform.db`.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: `cairn` reaches a synced, useful state in a git repository with zero prompts, zero
  config edits and no model.
- **SC-002**: Re-running setup produces no file changes (verified by the test suite).
- **SC-003**: Context packs stay within their budget 100% of the time (verified by the test suite).
- **SC-004**: The core MCP tool set costs under 2,000 tokens of schema per agent turn (measured at
  about 1,600).
- **SC-005**: Capture hooks exit 0 on any input and write only to the session store.
- **SC-006**: The test suite runs with no network access and no model calls.
- **SC-007**: Timeline-fact work per sync stays within `[deep] budget_tokens` (150,000 by default).
- **SC-008**: Targets from the constitution: `cairn` status under 300 ms; `cairn_context` under 800 ms
  p95 on a 5,000-file repository without a model; first paint of the page under 1 s.

## Assumptions

- Python 3.11 or newer and git are available; `uv` or `pipx` installs the tool.
- The local embedding model downloads once; without network access a hashing embedder is used.
- Model access comes from an API key, an OpenAI-compatible endpoint or a signed-in Claude Code CLI;
  none is required.
- Team servers run behind an HTTPS reverse proxy.
