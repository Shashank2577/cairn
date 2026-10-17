# Feature Specification: Map views: Context, Containers, Components, Flows, Code, and dependency layers

**Feature Branch**: `006-map-views`

**Created**: 2026-10-08

**Status**: Draft (slice of the umbrella [002-system-model](../002-system-model/spec.md))

**Input**: Split from 002-system-model at the 2026-10-08 checkpoint so each part ships on its own. Requirement,
scenario and task ids are kept identical to the umbrella, so the umbrella's traceability table stays valid.

## Goal

Map views: Context, Containers, Components, Flows, Code, and dependency layers. Background, clarifications, the cost model and the full requirement set are in the umbrella spec.

## User Scenarios & Testing *(mandatory)*

### User Story 5 - A Map that answers three questions (Priority: P2)

A new joiner opens the Map. Its views are named for the questions they answer: Context (who and what
surrounds the product), Containers (the runnable pieces and how they talk), Components (the main parts
inside one container), Flows (how one request or event moves, step by step) and Code (today's Explore,
with folder dependency layers as an optional overlay). Engine views and the tab called "Architecture"
are gone.

**Why this priority**: The current Map confuses code structure with architecture and carries a
mismatched, duplicated set of views.

**Independent Test**: Open the Map for a set-up repository: the five views are present, Engine views is
absent, and the folder-layer picture is reachable only as an overlay inside Code.

**Acceptance Scenarios**:

1. **Given** any set-up repository, **When** the user opens the Map, **Then** they see Context,
   Containers, Components, Flows and Code, each following the diagram standard and each showing one level.
2. **Given** a container, **When** the user drills in, **Then** Components shows its main parts, and
   drilling into a component opens Code scoped to it.
3. **Given** the CLI, **When** a user runs the existing graph exports, **Then** they still work (only the
   embedded UI tab is removed).

## Requirements *(mandatory)*

- **FR-016**: Components within a container MUST be derived from the code map's areas and communities,
  restricted to that container's code, and linked by aggregated dependencies labelled with their "how".
  A component is named from its most specific meaningful module path, skipping generic folders (src, lib,
  internal, utils, common, core); when no meaningful path exists it takes its busiest symbol's name and its
  name is marked inferred.
- **FR-017**: The system MUST derive flows from entry points (an endpoint, a message handler, a command):
  the set of steps across components and containers the entry point reaches, each with evidence. Steps
  within one function body are ordered by their position in that body; any order across functions joined by
  callbacks, middleware, injection or a channel, and any flow spanning more than one container, MUST be
  labelled inferred.
- **FR-024**: The Map MUST offer Context, Containers, Components, Flows and Code, each drawn to the
  standard, each showing exactly one level, with drill-down from Containers to Components to Code.
- **FR-025**: The Engine views tab MUST be removed from the UI. The graph exports remain available from
  the command line.
- **FR-026**: The folder-dependency picture now labelled "Architecture" MUST be redesigned as an optional
  "dependency layers" overlay inside Code, following the diagram standard: first-party code only (vendored
  and third-party folders hidden by default, with a count), one labelled block for each large import cycle
  (expandable) instead of every member on its own row, at most the node budget per layer with the rest
  collapsed into "+N", entry points detected from the system model rather than inferred from position, and
  every folder opening its evidence.
- **FR-027**: Every box and arrow in the UI MUST open its evidence and provenance.

## Success Criteria *(mandatory)*

- **SC-005**: A new joiner can name a product's containers and how two of them communicate within 2
  minutes of opening the Map, in a moderated test with at least five people.

## Dependencies

003 (renderer and tokens) and 004 (model and views).

## Independent test

The Map shows the five views drawn to the standard, Engine views is gone, every box opens its evidence, and dependency layers lives inside Code within its budgets.
