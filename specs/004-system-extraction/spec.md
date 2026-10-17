# Feature Specification: System extraction, linking and the containers view

**Feature Branch**: `004-system-extraction`

**Created**: 2026-10-08

**Status**: Draft (slice of the umbrella [002-system-model](../002-system-model/spec.md))

**Input**: Split from 002-system-model at the 2026-10-08 checkpoint so each part ships on its own. Requirement,
scenario and task ids are kept identical to the umbrella, so the umbrella's traceability table stays valid.

## Goal

System extraction, linking and the containers view. Background, clarifications, the cost model and the full requirement set are in the umbrella spec.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - See how a multi-repo product fits together (Priority: P1)

A developer works on one service of a product made of several repositories. They open the Map and choose
Containers. They see every repository's runnable pieces, the data stores and message channels they use,
the outside services they call, and labelled arrows between them, each saying what passes and how (for
example "creates orders · HTTP POST /api/orders"). Clicking any box or arrow shows the file and line, the
manifest entry or the deploy file it came from.

**Why this priority**: This is the picture nobody holds in their head and no single tool shows. It is the
core value of the feature and the reason for the multi-repo work.

**Independent Test**: Group three or four repositories into one product and run setup with no model
configured. The Containers view shows every container and relationship in the product's known design,
each with evidence, and nothing that is not there.

**Acceptance Scenarios**:

1. **Given** four repositories grouped into one product and no model configured, **When** setup finishes,
   **Then** the Containers view lists each runnable unit, each data store, each message channel and each
   outside service with its name, element type, technology and a one-line responsibility, and every
   relationship carries a "what" and a "how" label.
2. **Given** a relationship on the Containers view, **When** the user opens it, **Then** they see at least
   one evidence reference (repository, file and line, or manifest/deploy entry) and whether it was read
   directly from a source (extracted) or derived (inferred).
3. **Given** a repository that the product file names but that has not been set up, **When** the view is
   drawn, **Then** that repository appears as a box marked "not mapped yet" with the command that maps it,
   and no relationships are guessed for it.

## Requirements *(mandatory)*

- **FR-001**: The system MUST compute a system model with five levels: Context, Containers, Components,
  Flows and Code. Each element has an identifier, name, element type, technology (when known), one-line
  responsibility (derived or narrated), owning repository and evidence.
- **FR-002**: Element types MUST come from a fixed vocabulary defined by the diagram standard: person,
  software system, external system, container (application, service, worker, CLI, web front end),
  data store, message channel, library, component, and code element.
- **FR-003**: Every relationship MUST have a direction, a "how" label (protocol, mechanism or route), a
  level, an interaction style (synchronous, asynchronous or build-time) and at least one evidence reference.
  It MUST have a "what" label (the purpose, as a verb phrase) whenever a deterministic rule produces one
  (a topic, a catalog verb, literal SQL verbs and tables, a declared label). Otherwise the extracted
  relationship carries "how" alone; the system MUST NOT pad it with a generic phrase. Narrative (FR-030)
  may add "what", labelled inferred.
- **FR-004**: Every element and relationship MUST record provenance: extracted (read directly from a
  source) or inferred (derived), with each evidence reference pointing to a repository, file and line, or
  a manifest or deploy entry.
- **FR-005**: The model MUST be computed during setup and sync with zero model calls. The optional
  narrative step (FR-030) is the only part that may call a model.
- **FR-006**: The system MUST identify containers per deploy unit, not per repository (a repository may
  hold several, as in a monorepo with many build files or a compose file of many services), from deploy and
  run descriptions: container build files
  (exposed ports, start command), compose-style service definitions (services, images, ports, dependencies,
  environment variable names), process files, package entry points and scripts, and language entry points.
- **FR-007**: The system MUST identify inbound HTTP endpoints (method and path) for widely used web
  frameworks in the languages Cairn already parses, recording the handler and its location.
- **FR-008**: The system MUST identify outbound HTTP calls (method, literal path or path template, and the
  configuration name that supplies the base address when present), with their location.
- **FR-009**: The system MUST identify message publishing and subscribing (channel or topic names) for
  widely used messaging clients, with their location.
- **FR-010**: The system MUST identify data stores and outside services from a curated, versioned catalog
  that maps client libraries, images and configuration names to a named store or service (for example a
  PostgreSQL driver or image to "PostgreSQL", an email SDK to that email provider). The catalog MUST be
  data, extendable per project without code changes. A catalog match MUST rest on a use site (a connection,
  a client call, a query), never on an import or a declared dependency alone.
- **FR-011**: The system MUST record environment and configuration key names (never values) used by each
  container, so connections that pass through configuration can be resolved.
- **FR-012**: The product file (`system.yaml`) MUST keep working unchanged. It MAY additionally declare,
  per repository, a role, a description and explicit relationships. Declared facts take precedence over
  derived ones and are labelled declared.
- **FR-013**: The system MUST link containers, within one repository and across repositories, by the same
  rules: matching outbound HTTP calls to
  inbound endpoints (method and path template), matching message publishers to subscribers on the same
  channel, shared data stores, package dependencies on a sibling's package (build-time), and the existing
  cross-repository call and type passes. Each link records which rule matched.
- **FR-014**: Ambiguous matches (more than one candidate) MUST be shown as ambiguous with all candidates.
  The system MUST NOT pick one silently.
- **FR-015**: Sibling repositories MUST be read read-only, as today.
- **FR-029b**: Configuration and environment names marked sensitive by the catalog (credentials, keys,
  tokens) MUST be shown and exported only as their kind ("a SendGrid credential"), never by name. A
  sibling link resolved through a URL MUST store the sibling's identifier, not the URL or host string.
- **FR-029c**: Every computed element and relationship MUST record the commit it was built at, so two
  commits' views can be compared later.
- **FR-034**: Route, client, messaging and store extraction MUST be driven by catalog rules (language,
  detection dependency, and patterns of the kinds decorator, annotation, call, file-route, config); adding
  a framework MUST need only a rule, a fixture and its ground truth.
- **FR-029a**: Where a signal cannot decide a container's kind (for example web front end versus service),
  the kind MUST be marked inferred, and `system.yaml` MAY declare it.

## Success Criteria *(mandatory)*

- **SC-001**: On the fixture set fixed in the plan (at least one real open-source multi-service product
  plus the built fixtures), with ground truth written and committed before extraction is first run on it,
  the Containers view finds at least 90% of the true containers and relationships (recall), at least 95% of
  extracted (unlabelled) claims are true (precision), and claims labelled inferred or ambiguous make up no
  more than 10% of the view.
- **SC-002**: 100% of elements and relationships in every view open at least one evidence reference.
- **SC-003**: Computing the model during setup and sync uses zero model calls, verified by the ledger.
- **SC-008**: Setup time grows by no more than 15% over a baseline measured before the build on the
  fixtures and on Cairn's own repository (task T045a).

## Dependencies

002 foundation (model, store migrations). Optional: 003 for `--svg` output.

## Independent test

On each fixture, `cairn system --view containers --json` meets SC-001 against a ground truth committed before extraction, with zero ledger entries and no network.
