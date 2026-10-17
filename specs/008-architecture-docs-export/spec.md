# Feature Specification: Architecture documents and optional narrative

**Feature Branch**: `008-architecture-docs-export`

**Created**: 2026-10-08

**Status**: Draft (slice of the umbrella [002-system-model](../002-system-model/spec.md))

**Input**: Split from 002-system-model at the 2026-10-08 checkpoint so each part ships on its own. Requirement,
scenario and task ids are kept identical to the umbrella, so the umbrella's traceability table stays valid.

## Goal

Architecture documents and optional narrative. Background, clarifications, the cost model and the full requirement set are in the umbrella spec.

## User Scenarios & Testing *(mandatory)*

### User Story 6 - Architecture documents without writing them (Priority: P3)

A lead needs architecture documents for a review. One command writes them from the system model: a
context, containers and components description with diagrams, key flows, modules, configuration and
settings, and external systems. Every statement carries its evidence or is marked inferred, nothing is
padded to hit a quota, and each document records the commit it describes.

**Why this priority**: Valuable, but it reuses everything above and can follow later.

**Independent Test**: Run the export on the multi-repo sandbox. The documents describe only what exists,
the diagrams follow the standard, and re-running after a change updates them.

**Acceptance Scenarios**:

1. **Given** a computed system model, **When** the export runs, **Then** it writes documents whose
   diagrams follow the standard and whose statements cite evidence, with no model call unless narrative
   is requested.
2. **Given** a section with nothing to report (for example no message channels), **When** the export
   runs, **Then** the section is omitted or states "none found", never filled with placeholders.

---

### User Story 7 - Optional narrative (Priority: P3)

With a model available and the user's opt-in, Cairn adds names and one-line responsibilities for
components, and short descriptions of the main flows. Each narrated statement cites the computed
elements it describes and is labelled inferred. Turning the model off leaves every view working.

**Why this priority**: Improves readability. Never required.

**Independent Test**: Run with and without a model. Views and relationships are identical; only
descriptive text differs, and model text is labelled.

**Acceptance Scenarios**:

1. **Given** no model, **When** any view is drawn, **Then** every element still has a name, a type and a
   technology, derived from code and manifests.
2. **Given** a model and opt-in, **When** narrative runs, **Then** its cost is recorded in the ledger and
   capped by a configurable budget.

## Requirements *(mandatory)*

- **FR-029**: A docs export command MUST write architecture documents from the model (context,
  containers, components, key flows, modules, configuration, external systems) with diagrams in the
  standard. Every statement cites evidence or is marked inferred, sections with nothing to report are
  omitted or say so, and each document records the commit it describes.
- **FR-030**: With a model available and opted in, the system MAY narrate component names,
  responsibilities and flow descriptions. Narrated text MUST cite the elements it describes, be labelled
  inferred, be recorded in the cost ledger and stay within a configurable budget. Disabling it MUST NOT
  change any element or relationship.

## Success Criteria *(mandatory)*

- Covered by the requirements above.

## Dependencies

003 and 004; components and flows from 006 for those sections (the export can ship context and containers first).

## Independent test

`cairn docs export` writes documents whose diagrams pass the checker and whose statements cite evidence, with zero model calls; narrative only on opt-in, capped by budget and recorded in the ledger.
