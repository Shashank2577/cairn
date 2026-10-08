# Feature Specification: System Model and Diagram Standard

**Feature Branch**: `002-system-model`

**Created**: 2026-10-08

**Status**: Draft

**Input**: User description: "System model layer and diagram standard. Cairn computes a C4-style system model (Context, Containers, Components, Flows, Code) for single- and multi-repo products from what it already extracts (code map, manifests, deploy files, system.yaml, cross-repo calls, specs, timeline), deterministically at init with zero model calls, with an optional model step only for narrative; every element and relationship carries evidence (file/line or manifest entry) and is re-checked for staleness on sync; agents get the relevant level in their brief and cairn_context; one diagram standard (machine-readable tokens) is shared by the Map UI, a docs export and hand-made diagrams; the Map UI replaces Engine views and the misnamed Architecture tab with Context, Containers, Components, Flows and Code."

## Background

Cairn's Map answers "where is the code and what is in it" well. It does not answer "what are the parts of
this system and how do they talk", for one repository or for a product spread across several. Today:

- The "Architecture" tab shows folder import layers, not architecture.
- "Engine views" embeds three standalone pages in a different visual language that duplicate other views.
- Cairn has no notion of a deployable unit, an HTTP route, a message topic, a data store or an outside
  service. Cross-repository links come only from package imports and a few language-specific call passes.
- Teams that document architecture by hand (for example with the Taazaa documentation templates) get
  diagrams with five recurring defects: one fixed layered shape forced on every system, unlabelled arrows,
  several levels mixed in one picture, no boundary, legend or element types, and nothing tying a box to code.

This feature adds a **system model**: a stored set of elements and relationships at five zoom levels, each
backed by evidence, computed from the repository with no model calls, kept current on every sync, and
drawn with one diagram standard everywhere Cairn or its users draw architecture.

## Clarifications

### Session 2026-10-08 (answered by the user)

- Q: Where does the standard live, and is the Taazaa skill repository edited? → A: Cairn creates and owns
  its own standard (`docs/diagram-standard/`). The Taazaa repository is not touched; it was a reference only.
- Q: May `system.yaml` grow beyond `system` and `repos`? → A: Yes: optional `actors`, per-repo
  `role`/`kind`/`description` and declared `relationships`. Old files stay valid; declared facts are
  labelled declared.
- Q: Do the engine's graph exports (graph, call flow, tree) survive? → A: Yes, from the command line only,
  on condition of no extra AI cost or time. Confirmed: they are generated from the stored map with no model
  calls (no model code is imported by `callflow_html.py`, `tree_html.py` or the HTML exporter) and run only
  when asked; removing the UI tab removes their only automatic use.
- Q: Is the folder-layer picture kept? → A: Yes, inside Code as "dependency layers", but **redesigned**, not
  moved: the current one is poor. See FR-026.
- Q: What format do hand-made diagrams use? → A: Both. YAML is the canonical hand-made format; Mermaid is a
  first-class export from the model; a limited Mermaid flowchart import converts into the model, with the
  checker flagging what is missing and a `%% cairn` comment convention to supply it. See FR-031 to FR-033
  and `mermaid/README.md` for one diagram written both ways.
- Q: Which frameworks? → A: Cairn's parsers, through a data-driven catalog: a framework is a rule plus a
  fixture, not code. The first build covers Python (FastAPI, Flask, Django), JavaScript/TypeScript
  (Express, Fastify, Next.js), Java (Spring) and Go (net/http, Gin). What extraction depends on, and what the
  catalog can grow into, is in `frameworks.md` (FR-034).

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

---

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

---

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

---

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

---

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

---

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

---

### Edge Cases

- A monorepo with several deploy units (per-service build files, a compose file with many services): each
  deploy unit is a container; links inside the repository use the same rules as links across repositories.
- A repository with no deploy files and no manifest entry points: it is one container, typed by its
  primary language and labelled "entry points not found", not dropped.
- A library repository (for example shared contracts): it appears as a library, not a running container,
  and dependency arrows to it are drawn as build-time.
- The same outside service is reached from several repositories: it appears once at Containers level, with
  one arrow per caller.
- A URL built from configuration (base URL in an environment variable plus a path): matching uses the path
  and the configuration name. When the target cannot be decided, the relationship is drawn to "unresolved
  HTTP target", marked inferred, with the evidence, not guessed.
- Two repositories serve the same route path: the relationship is marked ambiguous and lists both
  candidates.
- Very large products (dozens of repositories, hundreds of containers): views stay readable by collapsing
  to the level above and by a node budget per view (see the standard).
- A repository named by the product file is missing on disk: shown as "not found", never fatal.
- Secrets in configuration or deploy files: only names are stored, never values.
- Single-repository projects: Context and Containers still work; the product is the repository.

## Requirements *(mandatory)*

### Functional Requirements

**System model**

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

**Deterministic extraction (new signals, all without a model)**

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

**Multi-repo**

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

**Components and flows**

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

**Staleness**

- **FR-018**: On every sync, the system MUST re-check each element's and relationship's evidence and mark
  as stale anything whose evidence no longer holds, with the reason. Stale claims MUST appear among drift
  findings.
- **FR-019**: Re-checking MUST be incremental: only claims whose evidence lives in changed files are
  re-examined.

**Diagram standard**

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

**Map UI**

- **FR-024**: The Map MUST offer Context, Containers, Components, Flows and Code, each drawn to the
  standard, each showing exactly one level, with drill-down from Containers to Components to Code.
- **FR-024a**: Computed views MUST be laid out readably without hand placement: ranked in the direction
  data flows (a consumer after the channel it reads), rows ordered to reduce crossings, people and outside
  systems outside the product boundary, libraries apart from runtime elements, and no label overlapping
  another label or box.
- **FR-025**: The Engine views tab MUST be removed from the UI. The graph exports remain available from
  the command line.
- **FR-026**: The folder-dependency picture now labelled "Architecture" MUST be redesigned as an optional
  "dependency layers" overlay inside Code, following the diagram standard: first-party code only (vendored
  and third-party folders hidden by default, with a count), one labelled block for each large import cycle
  (expandable) instead of every member on its own row, at most the node budget per layer with the rest
  collapsed into "+N", entry points detected from the system model rather than inferred from position, and
  every folder opening its evidence.
- **FR-027**: Every box and arrow in the UI MUST open its evidence and provenance.

**Agent context and export**

- **FR-028**: The session brief MUST include, when the repository is part of a model with neighbours, one
  line naming the current container and its inbound and outbound neighbours, placed before the team
  knowledge lines so that it survives the brief's existing budget. Agent context for a file MUST name consuming containers when the file implements something they
  use.
- **FR-029**: A docs export command MUST write architecture documents from the model (context,
  containers, components, key flows, modules, configuration, external systems) with diagrams in the
  standard. Every statement cites evidence or is marked inferred, sections with nothing to report are
  omitted or say so, and each document records the commit it describes.

- **FR-029b**: Configuration and environment names marked sensitive by the catalog (credentials, keys,
  tokens) MUST be shown and exported only as their kind ("a SendGrid credential"), never by name. A
  sibling link resolved through a URL MUST store the sibling's identifier, not the URL or host string.
- **FR-029c**: Every computed element and relationship MUST record the commit it was built at, so two
  commits' views can be compared later.

**Mermaid**

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

**Framework catalog**

- **FR-034**: Route, client, messaging and store extraction MUST be driven by catalog rules (language,
  detection dependency, and patterns of the kinds decorator, annotation, call, file-route, config); adding
  a framework MUST need only a rule, a fixture and its ground truth.

**Optional narrative**

- **FR-029a**: Where a signal cannot decide a container's kind (for example web front end versus service),
  the kind MUST be marked inferred, and `system.yaml` MAY declare it.
- **FR-030**: With a model available and opted in, the system MAY narrate component names,
  responsibilities and flow descriptions. Narrated text MUST cite the elements it describes, be labelled
  inferred, be recorded in the cost ledger and stay within a configurable budget. Disabling it MUST NOT
  change any element or relationship.

### Cost model *(mandatory for this feature)*

| Work | When it runs | Model calls |
|------|--------------|-------------|
| Container, endpoint, call, message, data store, outside-service and configuration extraction | setup, sync (changed files only) | **0** |
| Cross-repository linking, ambiguity marking | setup, sync | **0** |
| Components from areas, flows from entry points | setup, sync | **0** |
| Evidence re-check and staleness | every sync, changed files only | **0** |
| Map views, standard check, docs export (without narrative) | on request | **0** |
| Brief line and agent context | each session start or request | **0** |
| Narrative: component names, responsibilities, flow descriptions | opt-in only, budget-capped, ledgered | **> 0, bounded** |

### Key Entities *(include if feature involves data)*

- **Element**: a person, software system, external system, container, data store, message channel,
  library, component or code element; has a level, name, type, technology, responsibility, repository,
  provenance and evidence.
- **Relationship**: a directed, labelled link between two elements at one level; has what, how,
  interaction style, provenance, the matching rule that produced it, evidence and a stale flag.
- **Evidence reference**: points to a repository plus a file and line, or a manifest or deploy entry;
  records the commit at which it was last confirmed.
- **Flow**: an ordered list of steps from an entry point, each step being a relationship with evidence.
- **Catalog entry**: maps a library, image or configuration name to a data store or outside service type
  and display name.
- **Diagram standard and token file**: the rules and the shared visual tokens all renderers read.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: On the fixture set fixed in the plan (at least one real open-source multi-service product
  plus the built fixtures), with ground truth written and committed before extraction is first run on it,
  the Containers view finds at least 90% of the true containers and relationships (recall), at least 95% of
  extracted (unlabelled) claims are true (precision), and claims labelled inferred or ambiguous make up no
  more than 10% of the view.
- **SC-002**: 100% of elements and relationships in every view open at least one evidence reference.
- **SC-003**: Computing the model during setup and sync uses zero model calls, verified by the ledger.
- **SC-004**: After a change that removes a relationship's evidence, the relationship is marked stale by
  the next sync in 100% of tested cases.
- **SC-005**: A new joiner can name a product's containers and how two of them communicate within 2
  minutes of opening the Map, in a moderated test with at least five people.
- **SC-006**: Every diagram Cairn renders and every replacement diagram passes the standard check with no
  violations.
- **SC-007**: The brief line adds no more than 40 tokens and the brief stays within its existing budget.
- **SC-008**: Setup time grows by no more than 15% over a baseline measured before the build on the
  fixtures and on Cairn's own repository (task T045a).

## Evidence from the prototype

A read-only prototype on a four-repository sandbox reproduced 8 of 8 elements and 8 of 8 relationships of
the written ground truth with no false relationships, every claim with evidence, and zero model calls; one
element kind was wrong (web front end typed as service), and a staleness re-check correctly flagged a
removed relationship but also produced one false new relationship from an import without a use. See
`prototype/README.md`. FR-010, FR-024a and FR-029a come from these findings.

## Assumptions

- The product grouping file stays named `system.yaml` and remains optional; without it, the product is
  the single repository.
- Framework and client coverage starts with the languages and frameworks most common in Cairn users'
  repositories (Python, TypeScript/JavaScript, Java, Go) and grows through the catalog and extractors.
- Runtime-only wiring that leaves no trace in code, manifests or deploy files (for example routing set up
  only in a cloud console) is out of scope unless declared in `system.yaml`.
- Deployment diagrams (where things run) are out of scope for this feature; the standard reserves the
  element types for them.
- Comparing the views of two commits is out of scope; FR-029c records what it will need.
- The constitution's provenance vocabulary (EXTRACTED, INFERRED) gains declared, ambiguous and stale; this
  needs a PATCH amendment ratified with this feature (see plan).
- The engine's existing graph exports stay available from the command line; only the embedded UI tab goes.
- Users drawing by hand use the published token file and standard; Cairn does not ship a drawing editor.
- The narrative step uses the existing model router, budgets and ledger.
