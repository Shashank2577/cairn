# Feature Specification: Diagram standard: checker, renderer and Mermaid

**Feature Branch**: `003-diagram-standard`

**Created**: 2026-10-08

**Status**: Draft (slice of the umbrella [002-system-model](../002-system-model/spec.md))

**Input**: Split from 002-system-model at the 2026-10-08 checkpoint so each part ships on its own. Requirement,
scenario and task ids are kept identical to the umbrella, so the umbrella's traceability table stays valid.

## Goal

Diagram standard: checker, renderer and Mermaid. Background, clarifications, the cost model and the full requirement set are in the umbrella spec.

## User Scenarios & Testing *(mandatory)*

### User Story 2 - One diagram standard everywhere (Priority: P1)

A team lead wants every architecture picture the team sees, whether in Cairn, in exported documents or
drawn by hand, to look and read the same way. Cairn publishes one diagram standard (element types,
shapes, colour roles, arrow-label rules, boundary and legend rules, one level per diagram, evidence on
every element) and one machine-readable token file. The Map UI and the docs export both read that file,
and people drawing by hand can use the same file and check their diagram against the standard.

**Why this priority**: Without one grammar, each surface drifts into its own style, which is the problem
the user reported with Engine views and with the Taazaa templates.

**Independent Test**: Change one colour role in the token file. The Map UI and a fresh docs export both
reflect it with no other change. A hand-made diagram description that leaves an arrow unlabelled is
reported as breaking the standard.

**Acceptance Scenarios**:

1. **Given** the standard's token file, **When** the Map UI and the docs export render the same view,
   **Then** element shapes, colour roles, labels, boundary and legend match.
2. **Given** a diagram description with an unlabelled relationship, an element without a type, two levels
   in one view or no legend, **When** it is checked against the standard, **Then** each defect is reported
   with the rule it breaks.
3. **Given** the old Taazaa template diagrams, **When** they are compared with their replacements, **Then**
   each replacement fixes all five recorded defects.

## Requirements *(mandatory)*

- **FR-020**: Cairn MUST publish one diagram standard covering element types and shapes, colour roles,
  relationship label rules (what and how), boundary rules, legend and title rules, one level per diagram,
  a node budget per diagram, evidence references, light and dark themes, and accessibility (shape and
  colour redundancy, contrast, text alternative).
- **FR-021**: The standard MUST be backed by one machine-readable token file that the Map UI and the docs
  export both read. Hand-made diagrams MUST be able to use the same file.
- **FR-022**: Cairn MUST provide a check that reports, for a diagram description, every rule it breaks,
  and resolves each `repo/path:line` evidence reference against the working tree when that repository is
  present, reporting references that do not resolve. Layout rules (no overlapping labels, minimum rendered
  text size) are verified by the renderer's own self-test on every render.
- **FR-023**: The standard MUST include corrected replacements for the Taazaa documentation template
  diagrams, each following every rule.
- **FR-024a**: Computed views MUST be laid out readably without hand placement: ranked in the direction
  data flows (a consumer after the channel it reads), rows ordered to reduce crossings, people and outside
  systems outside the product boundary, libraries apart from runtime elements, and no label overlapping
  another label or box.
- **FR-031**: Any view MUST be exportable as a Mermaid flowchart (sequence diagram for flows) with shapes per
  element type, what and how on every edge, colours from the token file, and every element's and
  relationship's facts kept in `%% cairn` comments, so the export re-imports without loss.
- **FR-032**: Cairn MUST import a Mermaid flowchart subset (nodes, the standard shapes, labelled edges,
  chained and `&` edges, subgraphs) into the model, read `%% cairn` comments in a short `key=value` form and
  a full JSON form, warn on syntax it ignores (classDef, style, click, linkStyle), and run the standard
  check on the result.
- **FR-033**: The standard MUST state what a Mermaid rendering cannot guarantee (layout rules, legend,
  dark-mode parity, hover evidence) and the docs export MUST place the evidence table beside every Mermaid
  diagram.

## Success Criteria *(mandatory)*

- **SC-006**: Every diagram Cairn renders and every replacement diagram passes the standard check with no
  violations.

## Dependencies

002 foundation (tokens, safe YAML, limits). Nothing else.

## Independent test

`cairn diagram check` passes every docs example and fails the Taazaa transcriptions with the expected rule ids; every example round-trips YAML -> Mermaid -> YAML without loss; hostile labels stay escaped.
