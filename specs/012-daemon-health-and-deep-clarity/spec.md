# Feature Specification: Daemon health and deep-mode clarity

**Feature Branch**: `shash/ui-health-and-deep` (spec directory `012-daemon-health-and-deep-clarity`; numbered 012 because specs 002 to 011 are already taken on the open system-model and gap-fix branches)

**Created**: 2026-10-16

**Status**: Draft

**Input**: User description: "Daemon health and deep clarity. The Cairn UI daemon on :4751 had been started on Oct 10 from a git worktree that was later deleted; every page returned 500 and tabs, dropdowns and API-backed views failed with no explanation, while status and tests stayed green. Deep mode shows 'off again' with no reason, and 'budget' appears in several places without a plain meaning."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - A dead or stale server says so, and one command fixes it (Priority: P1)

A developer opens the Cairn UI and the server behind it is running from code that no longer exists (a deleted worktree or an uninstalled version), or from a different version than the `cairn` they just installed. Today every page fails with a bare error and nothing explains it. Instead, the developer sees one clear message naming the cause and the single command that fixes it, in the UI, in `cairn status` and in `cairn doctor`.

**Why this priority**: a silently broken UI makes every other feature look broken. It is the failure that happened, and it hid behind green tests.

**Independent Test**: start the server from a temporary copy of the code, delete that copy, then open the UI and run `cairn doctor`. Both must name the cause and the fix; running the fix must restore a working UI without any other manual step.

**Acceptance Scenarios**:

1. **Given** a server started from a directory that has since been deleted, **When** the developer runs `cairn status` or `cairn doctor`, **Then** the output says the server runs from code that no longer exists and names the one command that restarts it.
2. **Given** the same server, **When** the developer opens the UI in a browser, **Then** a single banner explains the problem and the fix instead of blank panels or bare error text.
3. **Given** a server whose version differs from the installed `cairn`, **When** the developer runs `cairn up`, **Then** the stale server is replaced by one running the installed version, and the output says so.
4. **Given** a healthy server, **When** the developer runs `cairn status`, **Then** no warning appears and the output is not longer than today.

---

### User Story 2 - Deep mode always says why it is on or off (Priority: P1)

A developer sees "deep" off. Today there is no reason given. They should see, everywhere deep appears (status, doctor, the UI overview and settings), whether it is on or off, which model backend it would use, and the exact missing prerequisite when it is off. When the model backend is the signed-in Claude Code CLI, Cairn must find it in the usual install locations even when the server was started with a minimal search path.

**Why this priority**: "deep is off again" was reported repeatedly. The suspected cause is a server started without the Claude Code CLI on its search path, which today is indistinguishable from "no model configured".

**Independent Test**: start the server with a search path that excludes the Claude Code CLI but where it exists in a standard install location; deep must report on. Remove the CLI; deep must report off with the reason "Claude Code CLI not found" and the fix.

**Acceptance Scenarios**:

1. **Given** the Claude Code CLI is installed in a standard location but not on the server's search path, **When** deep availability is evaluated, **Then** deep is on and the reason names the path it used.
2. **Given** no model backend is available, **When** the developer views status, doctor or the UI, **Then** deep shows off with the specific missing prerequisite and the command or setting that supplies it.
3. **Given** deep is turned off by configuration, **When** it is shown, **Then** the reason says it was turned off in configuration and which key.

---

### User Story 3 - "Budget" means one thing per place, in plain words (Priority: P2)

A developer sees the word "budget" in several places and cannot tell what each limits. Each budget (the size of an agent-facing context pack, the model tokens one deep sync may spend, and the two wall-clock limits for deep sync and memory learning) is named in plain language wherever it appears, with its current value, how much has been used where that is knowable, and what happens when it is exceeded.

**Why this priority**: it removes recurring confusion but does not block work.

**Independent Test**: open project settings in the UI and run `cairn doctor`; each budget appears with a plain name, current value, usage where available and the consequence of exceeding it.

**Acceptance Scenarios**:

1. **Given** default configuration, **When** the developer opens doctor or project settings, **Then** each limit is listed in plain language with value and consequence.
2. **Given** a deep sync has spent part of its allowance, **When** budgets are shown, **Then** the amount spent and remaining are shown for that budget.

---

### User Story 4 - Failures are visible, not blank (Priority: P2)

When the server returns an error, the UI shows one banner with the error text and the failing request instead of leaving panels blank, and the server's error responses are structured so the UI can show a meaningful message.

**Why this priority**: it turns every future server fault into a diagnosable one.

**Independent Test**: make one API route fail; the UI shows the banner naming that route while other panels keep working.

**Acceptance Scenarios**:

1. **Given** one API route fails, **When** the developer opens the view that uses it, **Then** a banner names the failure and the rest of the UI remains usable.

---

### Edge Cases

- The daemon serves several repositories; restarting it briefly interrupts all of them, which the restart message must state.
- The server is healthy but on a different port than expected: the UI and status report the real address.
- The recorded code location exists but was replaced by a different checkout: treated as stale when the version differs.
- Windows and macOS path differences for the Claude Code CLI location.
- No network: nothing in this feature requires it.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The server MUST record, at start, where its code lives, its version, its search path and its address, and expose them to status, doctor and the UI.
- **FR-002**: `cairn status`, `cairn doctor` and the UI MUST warn, with the exact fix, when the server's code location no longer exists, its version differs from the installed `cairn`, or it listens on an unexpected address.
- **FR-003**: `cairn up` MUST replace a stale server instead of reusing it, and MUST say what it replaced and that other repositories were briefly interrupted.
- **FR-004**: Server error responses MUST be structured (machine-readable message, request and cause), never bare text.
- **FR-005**: The UI MUST show one banner when API calls fail, naming the failing request, without hiding panels that still work.
- **FR-006**: Deep availability MUST report on or off together with its backend and, when off, the exact missing prerequisite and the fix.
- **FR-007**: The Claude Code CLI MUST be located in standard install locations when it is not on the search path, and the location used MUST be shown.
- **FR-008**: Status, doctor, the UI overview and project settings MUST all show the same deep state and reason (one source of truth).
- **FR-009**: Every budget shown anywhere MUST carry a plain-language name, its current value, usage where measurable, and what happens when exceeded.
- **FR-010**: A real-browser smoke check MUST open every top-level tab and Map sub-tab and every dropdown, and fail on any failed request, console error or blank frame; it MUST run in the project's test command.
- **FR-011**: Documentation MUST explain deep mode and every limit in plain language.

### Key Entities

- **Server record**: where the running server's code is, its version, search path and address, written at start.
- **Deep state**: on or off, the backend, the reason and the fix.
- **Budget**: a named limit with current value, usage and consequence.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: With the server deliberately started from a deleted directory, 100% of status, doctor and UI checks name the cause and the single fix, and running that fix returns the UI to working in under 15 seconds.
- **SC-002**: Deep on/off is always accompanied by a reason: zero places in status, doctor and the UI show deep without one.
- **SC-003**: A developer can state what each limit caps after reading one screen, with no document lookup.
- **SC-004**: The real-browser smoke check passes on a healthy server and fails on a server with one broken route, a stale code location, or a blank Engine view.
- **SC-005**: A healthy installation shows no new warnings and `cairn status` stays under its existing response-time budget.

## Assumptions

- The model-call ledger already records tokens spent per deep sync, so usage can be shown without new accounting.
- A single local server serves every repository on the machine, as today; restarting it is brief and acceptable if announced.
- The graph views blank in an embedded browser pane that blocks the engine's script asset are a limit of that pane, not of Cairn; the smoke check runs in an ordinary Chrome.
- Specs 002 to 011 belong to open branches; this spec is numbered 012 to avoid collisions.
