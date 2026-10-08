# Feature Specification: Diagrams that stay true: staleness re-check and drift

**Feature Branch**: `005-architecture-staleness`

**Created**: 2026-10-08

**Status**: Draft (slice of the umbrella [002-system-model](../002-system-model/spec.md))

**Input**: Split from 002-system-model at the 2026-10-08 checkpoint so each part ships on its own. Requirement,
scenario and task ids are kept identical to the umbrella, so the umbrella's traceability table stays valid.

## Goal

Diagrams that stay true: staleness re-check and drift. Background, clarifications, the cost model and the full requirement set are in the umbrella spec.

## User Scenarios & Testing *(mandatory)*

### User Story 3 - Diagrams that stay true (Priority: P2)

After a refactor removes the code that published an event, the developer opens the Containers view. The
relationship whose evidence disappeared is marked stale with the reason, instead of being drawn as if it
were still true. Relationships that newly appear in the code show up after the next sync.

**Why this priority**: A diagram that silently goes stale is worse than none. Re-checking claims is what
separates Cairn from hand-written architecture documents.

**Independent Test**: Remove the only evidence for one relationship and commit. After the sync that the
commit triggers, that relationship is reported stale. Restore it, and the mark clears.

**Acceptance Scenarios**:

1. **Given** a relationship whose only evidence file or line no longer contains the cited construct,
   **When** sync runs, **Then** the relationship is marked stale, with the missing evidence named, and it
   appears in the drift findings.
2. **Given** a new outbound call to another container, **When** sync runs, **Then** the relationship
   appears with its evidence without a full rebuild.

## Requirements *(mandatory)*

- **FR-018**: On every sync, the system MUST re-check each element's and relationship's evidence and mark
  as stale anything whose evidence no longer holds, with the reason. Stale claims MUST appear among drift
  findings.
- **FR-019**: Re-checking MUST be incremental: only claims whose evidence lives in changed files are
  re-examined.

## Success Criteria *(mandatory)*

- **SC-004**: After a change that removes a relationship's evidence, the relationship is marked stale by
  the next sync in 100% of tested cases.

## Dependencies

004 (stored model and evidence).

## Independent test

Remove the only evidence of a relationship and commit: the next sync marks it stale with the reason and a drift finding; restoring clears it; an import without a use creates nothing.
