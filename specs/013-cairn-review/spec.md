# Feature Specification: Cairn Review — a review stage for every pull request

**Feature Branch**: `013-cairn-review`

**Created**: 2026-10-17

**Status**: Draft

**Input**: User description: "cairn review: built-in code review stage for every pull request, branded as Cairn, automatic and manual triggers, memory-aware findings, UI surface"

## Clarifications

### Session 2026-10-17

- Q: Post the review to the forge pull request by default? → A: Yes, one updating summary; per-project off switch.
- Q: Should CI fail the build by default? → A: Yes, on high-severity findings; configurable.
- Q: Who pays for automatic reviews? → A: Whoever created the pull request (their provider and quota); no user or model means deterministic-only.

## User Scenarios & Testing *(mandatory)*

Cairn already helps with specify, plan, tasks and implement. Review closes the cycle: a change is read
against the code, the spec it belongs to, the team's conventions and the project's own history, and what
the reviewer decides is remembered for the next change.

### User Story 1 - Review my branch before I open the pull request (Priority: P1)

A developer runs `cairn review` on a branch (or a pull request number). They get a short list of findings,
most serious first, each saying what is wrong, where, why Cairn thinks so, and what evidence backs it.

**Why this priority**: This is the whole feature in its smallest form, and it must work with no model and
no keys (constitution II). Everything else attaches to it.

**Independent Test**: In a repository with a branch that changes a file and misses a file that always changes
with it, run `cairn review` with no model configured. It reports the missed file with a citation to the commits
that show the pattern.

**Acceptance Scenarios**:

1. **Given** a branch with changes and no model available, **When** the developer runs `cairn review`, **Then**
   a deterministic review runs and lists findings with their evidence, and says plainly that a model review was not run and how to enable it.
2. **Given** a model is available, **When** the developer runs `cairn review`, **Then** the deterministic findings
   appear first and model findings are added, each marked as model-written.
3. **Given** a branch with nothing to review (no changes against its base), **When** the developer runs `cairn review`,
   **Then** Cairn says so and exits cleanly.

---

### User Story 2 - Findings that know the project (Priority: P1)

A finding says things a generic reviewer cannot: "this repeats the regression fixed in commit X", "the spec task that owns this
requires Y", "this breaks the convention Z", "files that usually change with this one are missing from the diff", "this change
touches code with open drift".

**Why this priority**: This is why a Cairn review is worth running instead of any other. It uses what only Cairn has.

**Independent Test**: Change a file that has a recorded past fix; the review cites that fix. Change code that violates a stored convention; the review cites the convention.

**Acceptance Scenarios**:

1. **Given** a change to code with a recorded past fix or revert, **When** the review runs, **Then** a finding cites that history.
2. **Given** a stored convention or gotcha linked to a changed file, **When** the review runs, **Then** the convention is part of what the change is checked against, and a finding cites it.
3. **Given** every finding, **When** it is shown anywhere, **Then** it carries provenance: read from a source (`EXTRACTED`) or derived (`INFERRED`), and the source id.

---

### User Story 3 - Every pull request gets reviewed automatically (Priority: P2)

A team turns on automatic review once. After that each pull request is reviewed when it is opened or updated, and the result is
waiting where the team already looks.

**Why this priority**: "Every PR" is the goal, but it only has value once a single review (stories 1 and 2) is good.

**Independent Test**: Open a pull request against a project served by a team server with automatic review on; a review for that
exact commit appears without anyone running a command. Push a new commit; a new review for the new commit replaces it as current.

**Acceptance Scenarios**:

1. **Given** automatic review is on for a project, **When** a pull request is opened or updated, **Then** a review of that head commit is produced once, and repeated deliveries of the same event do not re-run it.
2. **Given** a CI job runs `cairn review --ci`, **When** findings meet the configured severity, **Then** the job exits with a non-zero status, and otherwise it exits zero. The default is to fail the job on high-severity findings; the threshold and an advisory mode are configurable per project.
3. **Given** a developer opted in locally, **When** they push a branch, **Then** a quick deterministic review runs first and never blocks the push.
4. **Given** automatic review is on (posting is on by default), **When** it finishes, **Then** one summary (not one comment per finding unless asked) appears on the pull request, and it is updated, not repeated, on later pushes.

---

### User Story 4 - See and triage reviews in the page (Priority: P2)

On the Cairn page, a **Review** view lists reviews by branch and pull request. Opening one shows findings grouped by severity,
each with its evidence, the file and lines, and three actions: **Accept** (this is a real problem), **Dismiss** (not a problem
here), and **Make a rule** (check this every time).

**Why this priority**: The page is where the team sees value (see the project's feedback that the UI must show value).

**Independent Test**: Run a review, open the Review view, dismiss one finding and accept another; reload; the choices are still there.

**Acceptance Scenarios**:

1. **Given** reviews exist, **When** the user opens Review, **Then** they see each review with branch or PR, commit, time, counts by severity, the review's cost in tokens, and whether a model was used.
2. **Given** a finding, **When** the user dismisses it with a reason, **Then** it no longer appears in later reviews of similar code, and the reason is kept.
3. **Given** a changed file, **When** the user opens its Impact view, **Then** open findings for that file are listed there. **Given** a spec task, **Then** its detail shows the review state of the changes that implement it.

---

### User Story 5 - The review learns (Priority: P3)

Findings the team accepts repeatedly become remembered gotchas or conventions; dismissed findings become suppressions. The next review and the next
session brief use them.

**Why this priority**: It completes the loop the owner asked for, but only pays off after many reviews.

**Independent Test**: Accept the same kind of finding in three reviews; Cairn proposes a memory for it and, once the user confirms, the fourth review and the session brief cite it.

**Acceptance Scenarios**:

1. **Given** the same finding accepted at least three times, **When** the next review completes, **Then** Cairn proposes a memory and stores it only after the user confirms.
2. **Given** a dismissed finding with a reason, **When** similar code is reviewed, **Then** it is suppressed and counted, and the count is visible.

---

### User Story 6 - The coding agent can run the review and fix what it finds (Priority: P3)

An agent calls Cairn's review tool on its own work before finishing, receives the findings as compact, cited text within a token budget, and can fix them.

**Why this priority**: Brings review into the loop where the code is written (constitution VIII, agent-neutral).

**Independent Test**: From an MCP client, ask for a review of the working changes; the reply is within the budget and every finding has a citation.

**Acceptance Scenarios**:

1. **Given** an agent working in a repository, **When** it asks for a review of its uncommitted changes, **Then** it gets findings within the pack budget and the review is also visible in the page.

---

### Edge Cases

- A pull request with hundreds of files, generated files, lock files or vendored code: Cairn reviews what matters, lists what it skipped and why, and respects the budget.
- A diff containing secrets or `<private>` blocks: they are masked or removed before anything leaves the machine, and a finding is raised if a secret is being committed.
- The model or network fails mid-review: the deterministic findings are kept and the review is marked partial, never lost.
- The same pull request is delivered twice, or a force-push rewrites the commit: one review per head commit; the older ones are marked superseded.
- A fork or a pull request from a repository Cairn has no map for: the review runs on the diff alone and says what context was missing.
- Windows, macOS and Linux: the same command works on all three with no extra install step.
- The user runs review on a repository where Cairn is not set up: same behaviour as other commands (it offers setup, it does not silently create files).

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: Cairn MUST provide `cairn review [<branch>|<pull request number>|--staged|--base <ref>]` that reviews a change against its base.
- **FR-002**: A deterministic review MUST work with no model and no keys: files that usually change together but are missing, open drift on touched code, risk from history, conventions and gotchas linked to touched files, and secrets in the diff.
- **FR-003**: When a model is available, Cairn MUST add model-written findings, route the call through the model router, record it in the ledger under its own task name, and stay within a stated budget.
- **FR-004**: Every finding MUST carry severity, file and lines, explanation, provenance (`EXTRACTED` or `INFERRED`) and a citation to its source, and MUST say whether a model wrote it.
- **FR-005**: Findings MUST use project knowledge: past fixes and reverts, the owning spec task and its requirements, stored conventions and gotchas, and recent agent work on the same files.
- **FR-006**: No user-facing text (command output, page, docs, generated files, tool descriptions, errors) may name the review engine used inside Cairn; attribution lives only in `NOTICE` and `licenses/` (constitution I).
- **FR-007**: Cairn MUST support automatic review: on a team server when a pull request is opened or updated; as a CI mode (`--ci`) with machine-readable output and a configurable failing severity (default: advisory); and as an opt-in local check on push that never blocks.
- **FR-008**: Cairn MUST run at most one review per project and head commit, ignore repeated deliveries, and mark earlier reviews of the same pull request as superseded.
- **FR-009**: Posting to the forge MUST be on by default for projects with automatic review, with a per-project switch to turn it off. It MUST produce one summary that is updated rather than repeated, and MUST never include masked values or `<private>` content.
- **FR-019**: Automatic reviews MUST be paid for by the pull request's author: the model call uses that person's own provider and quota, matched from the forge login to a Cairn user. If the author has no matching user or no model available, the review runs deterministic-only and says why in the summary. A review is never charged to someone else.
- **FR-010**: The page MUST have a Review view (list, detail, triage actions as in Story 4), show open findings in a file's Impact view and review state in a spec task, and add review events to the Timeline. The Overview MUST show the latest review state of the current branch.
- **FR-011**: Users MUST be able to Accept, Dismiss (with a reason) and Make a rule from any finding, from the page, the terminal and the agent tool.
- **FR-012**: Accepted and dismissed findings MUST feed back: repeated acceptance proposes a memory (stored only on confirmation); dismissals become suppressions that are counted and listed.
- **FR-013**: Cairn MUST offer a review tool to MCP clients and a `/cairn:review` command for agents that support commands, with compact output within the pack budget.
- **FR-014**: Before anything is sent to a model, the diff and context MUST pass through credential masking and `<private>` removal, and the page MUST show what was sent (sizes and files, not content) for each review.
- **FR-015**: Review MUST follow the project's size rules: skip generated, vendored and lock files by default (listed with the reason), cap per-review tokens, and say when a review is partial.
- **FR-016**: Reviews and decisions MUST be stored with the project, readable by team members according to existing project roles, and removable. Per-person decisions MUST NOT be shown as person-level statistics.
- **FR-017**: `cairn doctor` MUST report whether review is ready, whether a model is available for it, and which automatic triggers are on.
- **FR-018**: The feature MUST ship with CLI help, documentation, an MCP tool description that names only real tools, and tests for every deterministic path.

### Key Entities

- **Review**: one run on one head commit of one branch or pull request: base, head, time, mode (deterministic, with model), status (complete, partial, superseded), cost, what was skipped.
- **Finding**: severity, location, explanation, provenance, citation, author kind (rule or model), state (open, accepted, dismissed, fixed), reason.
- **Rule**: a check the team asked for ("Make a rule"), tied to paths or conventions, created from a finding or from a stored convention.
- **Suppression**: a dismissed finding pattern with its reason and a count of how often it matched.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A deterministic review of a 500-line change finishes in under 5 seconds on a 5,000-file repository, with no keys set.
- **SC-002**: 100% of findings shown anywhere carry provenance and a citation.
- **SC-003**: In a test repository with seeded past fixes, conventions and missing co-changes, the deterministic review finds at least 90% of the seeded issues and raises no more than one unrelated finding per review.
- **SC-004**: A pull request opened on a project with automatic review on has a review in the page within 2 minutes of the event (deterministic part within 30 seconds), and a repeated event produces no second review.
- **SC-005**: A model review of a typical pull request stays inside its stated token budget, and its cost appears in the ledger.
- **SC-006**: A developer can go from a finding to Accept, Dismiss or Make a rule in two clicks or one command.
- **SC-007**: No user-facing output contains the name of the engine used inside (checked by a test over the CLI, page and generated files).
- **SC-008**: On a test set of secrets in diffs, none reaches a model call or a posted comment.
- **SC-009**: After the same finding is accepted three times, the proposal for a memory appears in the next review 100% of the time.
- **SC-010**: The same commands work on macOS, Linux and Windows without an extra installation step.

## Assumptions

- A review engine from the open source ecosystem is used inside Cairn under its own license, kept out of sight, in the way Cairn's other engines are (constitution I and ADR 0005). Which one, and how it is packaged, is a plan decision, not part of this spec.
- Automatic review needs a model call made by Cairn itself, because no coding agent is present at that moment. It therefore goes through the router and the ledger, and respects budgets. Reviews run by an agent through the tool may use the agent's own model instead.
- Team-server automatic review builds on the existing webhook mechanism, extended to pull request events.
- The default for CI is blocking on high-severity findings (configurable down to advisory).
- The default for posting to the forge is on, one updating summary per pull request.
- The pull request's author pays for an automatic review (their own provider and quota); without one, the review is deterministic-only.
- Findings are personal to the person who triaged them only where the user chooses; shared rules and suppressions are project-wide.
- Review is a stage after implement in the Cairn workflow (specify, plan, tasks, analyze, implement, review, converge); it may use the owning spec when one exists and works without one.
- The existing budget list (`src/cairn/budgets.py`) gets one new entry for review; no other limit changes.
