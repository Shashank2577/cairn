# Feature Specification: Agents know where they are in the system

**Feature Branch**: `007-agent-system-context`

**Created**: 2026-10-08

**Status**: Draft (slice of the umbrella [002-system-model](../002-system-model/spec.md))

**Input**: Split from 002-system-model at the 2026-10-08 checkpoint so each part ships on its own. Requirement,
scenario and task ids are kept identical to the umbrella, so the umbrella's traceability table stays valid.

## Goal

Agents know where they are in the system. Background, clarifications, the cost model and the full requirement set are in the umbrella spec.

## User Scenarios & Testing *(mandatory)*

### User Story 4 - Agents know where they are in the system (Priority: P2)

An agent opens a session in one repository of a multi-repo product. Its session brief says which
container it is in, which containers call it and which it calls, in one or two lines. When it asks for
context on a file that serves an endpoint other repositories call, the context names those callers with
evidence.

**Why this priority**: Agents break other repositories because they cannot see them. This puts the
system picture into the context agents already receive, within the existing token budget.

**Independent Test**: In a service repository of a grouped product, start an agent session. The brief
contains the container line. Ask for context on the file that serves a route another repository calls;
the callers appear.

**Acceptance Scenarios**:

1. **Given** a repository that is part of a grouped product, **When** a session starts, **Then** the brief
   includes one line naming this container, its inbound and outbound neighbours, within the brief's
   existing token budget.
2. **Given** a file that implements an endpoint, topic handler or shared contract used by another
   container, **When** an agent requests context for it, **Then** the context lists the consuming
   containers and their evidence.

## Requirements *(mandatory)*

- **FR-028**: The session brief MUST include, when the repository is part of a model with neighbours, one
  line naming the current container and its inbound and outbound neighbours, placed before the team
  knowledge lines so that it survives the brief's existing budget. Agent context for a file MUST name consuming containers when the file implements something they
  use.

## Success Criteria *(mandatory)*

- **SC-007**: The brief line adds no more than 40 tokens and the brief stays within its existing budget.

## Dependencies

004 (model and links).

## Independent test

In a service repository of a grouped product, the session brief carries the container line within budget even when full, and `cairn_context` on a file serving a route lists its consumers with evidence.
