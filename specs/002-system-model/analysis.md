# Analysis: spec, plan and tasks consistency (2026-10-08)

## Requirement coverage

| Requirement | Tasks |
|---|---|
| FR-001 to FR-005 model, provenance, zero calls | T004, T005, T019, T023 |
| FR-006 deploy and run units | T011, T012 |
| FR-007 inbound endpoints | T013 |
| FR-008 outbound calls | T014 |
| FR-009 messaging | T015 |
| FR-010 catalog (use site required) | T018, T029 |
| FR-011 config names | T017 |
| FR-012 system.yaml additions | T020 |
| FR-013 to FR-015 linking, ambiguity, read-only siblings | T021, T023 |
| FR-016 components | T033 |
| FR-017 flows | T034 |
| FR-018, FR-019 staleness | T027, T028, T029 |
| FR-020 to FR-023 standard, tokens, checker, replacements | T003, T006, T007, T024, T025, T026 (replacements already in docs/diagram-standard) |
| FR-024, FR-024a, FR-027 views, layout, evidence | T008, T030, T031, T035 |
| FR-025, FR-026 remove Engine views, move layers | T031, T032 |
| FR-028 agent context | T036, T037, T038, T039 |
| FR-029 docs export | T040, T041 |
| FR-029a kind inferred or declared | T012, T020 |
| FR-030 narrative | T042, T043 |

| Success criterion | Verified by |
|---|---|
| SC-001 recall at least 90% | T023 |
| SC-002 evidence 100% | T023, T007 |
| SC-003 zero model calls | T023 |
| SC-004 stale detection | T029 |
| SC-005 new joiner in 2 minutes | T047 (added during analysis) |
| SC-006 standard check | T007, T026 |
| SC-007 brief line budget | T039 |
| SC-008 setup time | T045 |

## Findings

1. **Fixed:** SC-005 had no task; T047 added.
2. **Constitution:** no conflicts (see plan, Constitution Check).
3. **Terminology:** the spec says "product file (`system.yaml`)"; the plan and tasks say `system.yaml`. Consistent.
4. **Open:** six clarifications await the user (spec, Clarifications section).
