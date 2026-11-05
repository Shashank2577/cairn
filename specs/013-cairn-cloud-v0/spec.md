# Feature Specification: Cairn Cloud v0 (paid hosted team memory)

**Feature Branch**: `ccr-0ece36a3-n5yyrt` (spec directory `013-cairn-cloud-v0`)

**Created**: 2026-11-05

**Status**: Draft

**Input**: Owner decisions, 2026-11: keep the free local product unchanged; sell a hosted team server; no customer
source code ever reaches the hosted service; customers bring their own model key (BYO); model work and usage stay on
the developer's laptop and are synced; GitHub sign-in for developers and teams, SSO later; shared memory must work
while each developer is on their own branch.

## Summary

Cairn stays a local-first tool that is free and complete on one machine. **Cairn Cloud** is the paid part: a hosted
server that holds a team's *learned* memory (decisions, conventions, session summaries and observations) and serves
it to every developer's agent. The *code-derived* layers (map, impact, why, drift, specs, history) keep being built
on each laptop from the clone the developer already has, per branch, so the hosted service never needs the code.

Two kinds of memory, two homes:

| | Code-derived | Learned and shared |
|---|---|---|
| Examples | map, impact, why, drift, specs, git history | decisions, conventions, gotchas, session summaries and observations |
| Built from | the repository on the laptop | people and agent sessions |
| Lives | on each developer's laptop, per branch | on Cairn Cloud, per team and project |
| Leaves the laptop | never | only as derived records under the data contract below |

## Current state (what this builds on)

- Team mode already exists in the open-source server: users, roles (owner, admin, member, viewer), scoped API tokens,
  invitations by link, an audit log, a per-project MCP endpoint, and session push from laptops
  (`cairn agents connect`, `remote_outbox`, `POST /api/p/<id>/sessions/events`).
- Today a laptop pushes **raw** hook events (prompts, tool inputs and tool output, after masking) and the server derives
  observations with its own model key (`[recall] worker_model = false` on the laptop). Raw tool output can contain
  code, so this path contradicts "no customer code" and is replaced below.
- Laptops do not pull shared memory; agents can query the server's MCP endpoint, but nothing is cached locally.
- Memories are not tagged by branch; sessions record their branch (`sdk_sessions.branch`).
- Session deletion, retention and "delete my data" exist (Unreleased: `cairn sessions forget-session`, `purge`,
  `DELETE /api/p/<id>/sessions/{sid|mine}`, `[recall] retention_days`); docs generation is refused on team servers.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - A team shares what its agents learned, without sharing code (Priority: P1)

A developer's agent learns that "payments must pass an idempotency key" during a session. Within minutes, a teammate's
agent working in the same repository gets that knowledge in its context, and nothing in Cairn Cloud contains source
code or raw tool output.

**Why this priority**: it is the product. Without it there is nothing to pay for.

**Independent Test**: two laptops connected to one hosted project; developer A finishes a session; developer B starts
one; B's session-start context and `cairn_session_search` show A's observation. A dump of the hosted store contains no
tool input, tool output or file content.

**Acceptance Scenarios**:

1. **Given** a connected laptop, **When** a session ends, **Then** the laptop derives observations and a summary locally
   (with the developer's own model key or Claude plan) and pushes only those derived records and a usage record.
2. **Given** a hosted project, **When** its store is inspected, **Then** it holds no raw prompts, tool inputs, tool
   responses, diffs or file contents (see the data contract), and every pushed record passed secret masking and size
   limits on the laptop and again on ingest.
3. **Given** a second developer, **When** their agent starts a session, **Then** it receives the team's relevant
   learned memory, served from a local cache that was synced from the server (works offline after the first sync).

---

### User Story 2 - Sign in once with GitHub, no passwords (Priority: P1)

A developer runs `cairn login`, a browser opens, they approve with GitHub, and the CLI stores a token in the OS
keychain. Their team is the GitHub organisation they belong to. No repository permission is requested.

**Why this priority**: identity, seats and entitlement all depend on it, and teams expect it.

**Independent Test**: `cairn login` on a clean machine completes with GitHub device flow; `cairn whoami` shows the
user and team; revoking the GitHub authorisation stops the next push.

**Acceptance Scenarios**:

1. **Given** a developer without a Cairn Cloud account, **When** they run `cairn login`, **Then** GitHub device
   authorisation creates the account, and the requested GitHub scopes are only identity and organisation membership.
2. **Given** a signed-in developer, **When** the CLI pushes or pulls, **Then** the credential comes from the OS
   keychain or an environment variable, never from a file in the repository.
3. **Given** local, unconnected use, **When** the developer uses Cairn, **Then** no sign-in is ever asked for.

---

### User Story 3 - Shared memory follows my branch (Priority: P2)

A developer on `feature/refunds` gets the team's main-branch memory plus what was learned on their branch. When the
branch merges, its memories become shared main-branch memory; abandoned branches' memories do not leak into main.

**Independent Test**: learn something on branch X; it is visible to developers on X and not on main; merge X; it is
visible on main.

**Acceptance Scenarios**:

1. **Given** a memory learned on branch X, **When** a developer on main queries, **Then** it is not returned (unless
   marked team-wide).
2. **Given** branch X merges into main, **When** the merge is detected (git hook on the laptop or forge webhook
   metadata only, never code), **Then** X's memories are promoted to main.
3. **Given** branch X is deleted unmerged, **When** retention runs, **Then** its branch-only memories expire.

---

### User Story 4 - Admins see usage and set budgets, but pay their model provider directly (Priority: P2)

A team admin sees tokens and estimated spend per developer and project, and sets a monthly budget per developer that
the laptop enforces. Cairn Cloud never holds or pays for a model key.

**Acceptance Scenarios**:

1. **Given** model calls on laptops, **When** usage syncs, **Then** the admin sees per-developer, per-project and
   per-job-type (observer, deep, narration) tokens; dollar figures are labelled estimates.
2. **Given** a budget, **When** a developer exceeds it, **Then** the laptop pauses model work (deterministic capture
   continues) and says why.

---

### User Story 5 - Billing and seats (Priority: P2)

A team owner subscribes per seat. Seats are members with access to any project. Downgrading or ending the
subscription keeps the data readable for a grace period, then deletes it on a documented schedule.

---

### User Story 6 - Security review passes (Priority: P1 before the first paid customer)

A prospective customer's security team receives the data contract, retention and deletion behaviour, sub-processors,
and audit coverage, and can verify "no code leaves the laptop" from the open-source client.

### Edge Cases

- A developer on an old client still pushes raw events: the hosted ingest rejects raw event types (415/422 with an
  upgrade message), so raw data can never be stored by accident.
- Two laptops edit the same memory offline: last-writer-wins on text with history kept; forget wins over edit.
- A memory text quotes a code line: allowed only within the per-field size cap; secret masking applies; the data
  contract states that derived text may quote short snippets, and an org setting can forbid code-like quotes
  [NEEDS CLARIFICATION: is any quoted code acceptable in derived records?].
- A developer leaves the org: GitHub org membership sync removes the seat and their tokens; their records stay with
  the team unless the team deletes them ("delete my data" is per user).

## Requirements *(mandatory)*

### Functional Requirements

**Data contract (what may leave the laptop)**
- **FR-001**: The hosted service MUST accept only these record types: observation (type, title, facts, narrative,
  concepts, file paths, branch), session summary (request, completed, learned, next steps, files touched, branch),
  memory (text, kind, scope, branch), usage (model, tier, job type, input/output/cache tokens, timestamp), and session
  metadata (agent, start/end, branch). Raw prompts, tool inputs, tool responses, diffs and file contents MUST be
  rejected.
- **FR-002**: Every text field MUST pass secret masking on the laptop and again on ingest, and MUST respect per-field
  size caps (initially 2,000 characters for narratives, 500 for titles and facts).
- **FR-003**: The data contract MUST be published as a versioned document and enforced by schema on ingest.

**Local derivation and sync**
- **FR-004**: Connected laptops MUST derive observations and summaries locally (the default `worker_model = true`
  stays on); the raw-event push path MUST be disabled for Cairn Cloud projects (it can remain for self-hosted servers).
- **FR-005**: Derived records MUST sync through an outbox with idempotent ids, acknowledgement cursors and backoff
  (reusing the existing outbox pattern).
- **FR-006**: Laptops MUST pull the team's learned memory into a local cache, incrementally, and serve it to agents
  offline.

**Identity**
- **FR-007**: `cairn login` MUST use GitHub device authorisation (OAuth app or GitHub App) with only identity and
  organisation-membership permissions; tokens are stored in the OS keychain.
- **FR-008**: A GitHub organisation MUST map to a Cairn team; membership changes MUST propagate within one day.
- **FR-009**: SSO (OIDC, then SAML) and SCIM MUST be available on the enterprise tier through a hosted identity provider
  rather than hand-built [NEEDS CLARIFICATION: which provider].

**Branches**
- **FR-010**: Observations, summaries and memories MUST carry the branch they were learned on; queries MUST return
  main-branch and current-branch records by default.
- **FR-011**: A merge of branch X into main MUST promote X's records to main, detected from git metadata on the laptop.

**Usage and budgets**
- **FR-012**: Each model call MUST produce a usage record (team, user, project, job type, model, tokens) synced to the
  server; the server MUST NOT hold any customer model key.
- **FR-013**: Admins MUST be able to set per-developer monthly token budgets that laptops enforce.

**Tenancy and operations**
- **FR-014**: v0 MUST run one isolated server instance and encrypted volume per team (the existing team server image),
  behind a control plane that handles signup, billing, provisioning and usage aggregation.
- **FR-015**: Retention MUST default to a documented value per plan; "delete my data" and project deletion MUST remove
  records within 30 days including backups.
- **FR-016**: Hosted mode MUST disable docs generation, the `claude-code` provider, PR tools and any server-side model
  calls.

**Open-source obligations**
- **FR-017**: The hosted product MUST keep and display the NOTICE attributions for Graphify, Spec Kit, Claude-Mem,
  Graphiti and Mem0 (Apache-2.0 / MIT), and MUST NOT use their names as product branding.

### Key Entities

- **Team**: maps to a GitHub organisation; has seats, plan, retention policy, budgets.
- **Member**: a GitHub identity in a team; holds devices and tokens.
- **Project**: a repository identified by its forge URL hash (no code); has branches seen.
- **Derived record**: observation, summary or memory, with team, project, branch, author, timestamps.
- **Usage record**: one model call's metadata.
- **Device**: a laptop's registered client with its keychain token.

## Success Criteria *(mandatory)*

- **SC-001**: A store dump from Cairn Cloud contains zero raw tool inputs/outputs, prompts or file contents across a
  test corpus of 1,000 sessions (automated check in CI).
- **SC-002**: A teammate's agent receives a new observation in its next session within 5 minutes of the author's
  session ending, and offline after the first sync.
- **SC-003**: `cairn login` to first synced record takes under 3 minutes on a clean machine.
- **SC-004**: Hosted infrastructure cost per active seat stays under 10% of the seat price (no model costs carried).
- **SC-005**: Two to three pilot teams use it for four weeks and at least one converts to paid.

## Assumptions

- Customers accept BYO model access (their own key or Claude plan) on laptops.
- File paths and symbol names in derived records are acceptable to customers when stated in the data contract.
- Per-team instances are acceptable for the first 50 teams; a shared multi-tenant service comes later if needed.
- The free local product and self-hosted team server remain Apache-2.0 (open core); paid features live in a
  separate, private codebase (control plane, billing, SSO, admin and usage dashboards).

## Out of Scope (v0)

- Hosting any customer source code, server-side map/impact/drift, or server-side model calls.
- Reselling model usage.
- SAML/SCIM (enterprise tier, after v0), SOC 2 (prepare evidence, audit later).

## Open Questions

1. Price per seat and plan limits (projects, retention) — to be set from pilot conversations, not guessed.
2. Is any quoted code acceptable in derived text, or must narratives be code-free by policy?
3. Identity provider for SSO, and whether to use a GitHub App (org install, finer permissions) or an OAuth app.
4. Data residency requirements (EU) for early customers.
5. Competitive check: what Claude-Mem's TeamBrain and Mem0's platform store and charge (not verified at writing).
