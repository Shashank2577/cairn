# Analysis: consistency of spec, plan and tasks (013-cairn-review)

Date 2026-10-17. Read-only check across `spec.md`, `plan.md`, `tasks.md`.

| Check | Result |
|---|---|
| Every functional requirement has at least one task | Pass after two fixes: FR-011 (terminal and agent triage) added to T023 and T037; FR-016 (no person-level stats) added to T031 |
| Every user story has an independent test and tasks | Pass (US1 T010–T017, US2 T018–T022, US3 T040–T046, US4 T023–T031, US5 T047–T050, US6 T037–T039) |
| Success criteria have a verifying task | SC-001/003/008 T017; SC-002 T021; SC-004 T046; SC-005 T036; SC-006 T031/T025; SC-007 T051; SC-009 T050; SC-010 T055 |
| Constitution conflicts | None. VII note: forge posting is the one outward write (opt-in token, default on once a token exists) |
| Spec vs plan mismatch | One: the spec's Assumptions say "an open source engine is used inside"; the plan chooses a native Python pipeline designed after it. Spec wording should be updated at converge to "a review pipeline, designed after an open source tool". Flagged for the owner (plan R4) |
| Ordering risk | Phase 6 (model) and T006 depend on the masking fix, which is a separate piece of work and must merge first |
| Highest-risk edits | `router.py` (T008), `store.py` (T004), `server.py` (T024, T028), `cli.py` (T015, T023, T040), `mcp_server.py` (T037), `hooks.py` (T045): keep each change minimal, run their existing suites |
| Open item | T044 (per-user model key on a server) is new sensitive storage; schedule a security review before shipping |
