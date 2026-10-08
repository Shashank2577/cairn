# Implementation Plan: System Model and Diagram Standard

**Branch**: `002-system-model` | **Date**: 2026-10-08 | **Spec**: [spec.md](spec.md)
**Input**: Feature specification from `/specs/002-system-model/spec.md`

## Summary

Add a **system model** to Cairn: elements and relationships at Context, Containers, Components, Flows and
Code levels. It is computed during setup and sync from deterministic signals, stored per repository with
evidence, linked across the repositories that `system.yaml` groups, and re-checked on every sync. One
diagram standard (`tokens.json` plus rules plus a checker) and one layout engine feed both the Map UI and
`cairn docs export`. Hand-made diagrams use the same description format and are checked with
`cairn diagram check`.

The prototype ([prototype/README.md](prototype/README.md)) shows the approach works on a four-repository
sandbox with zero model calls. The build replaces its regex passes with the graph engine's tree-sitter
parse and call graph.

## Technical Context

- **Language/Version**: Python 3.11 to 3.13 (as today); the UI stays Preact plus htm (no build step).
- **Primary Dependencies**: no new runtime dependency. Signals reuse tree-sitter extraction already in the
  graph engine; YAML via the existing parser.
- **Storage**: new tables in `.cairn/brain.db` (`sm_element`, `sm_relationship`, `sm_evidence`, `sm_flow`).
  The store has `SCHEMA_VERSION = 1` and no migration path today, so this feature adds one: versioned,
  idempotent migrations run on open (task T004a). Product-level linking reads sibling stores read-only, as `engines/systems.py`
  does today. Nothing is written into a sibling repository.
- **Testing**: pytest. Fixture products, fixed for SC-001: the four-repo shop sandbox (committed under
  `prototype/sandbox`), one Java/Spring and one Go fixture, one monorepo with several deploy units, and at
  least one real open-source multi-service product. Ground truth is committed before extraction runs. A golden containers view per fixture is checked against its ground truth.
- **Target Platform**: macOS, Linux, Windows (CI matrix as today).
- **Project Type**: CLI, local server and UI, MCP server (existing).
- **Performance Goals**: setup time grows by at most 15% (SC-008) over a baseline measured before the
  build (T045a) on the fixtures and the Cairn repository; numbers are recorded here once measured. Incremental sync
  re-checks only claims whose evidence lives in changed files.
- **Constraints**:
  - Zero model calls for everything except opt-in narrative.
  - Values in configuration and deploy files are never stored; names are, plus the host part of a service
    URL when it names a sibling container.
  - The brief line costs at most 40 tokens.
- **Scale/Scope**: products of up to about 50 repositories and 300 containers. Views collapse beyond the
  node budget.

## Constitution Check

| Principle | Status |
|---|---|
| I. One product, invisible seams | ✅ The views live under the Map. Users see Context, Containers, Components, Flows and Code; engine names never appear. |
| II. Deterministic first (non-negotiable) | ⚠️ All deterministic; the model only narrates, opt-in. Provenance adds declared, ambiguous and stale to EXTRACTED/INFERRED: **requires a PATCH amendment** to principle II, ratified with this feature (task T000). |
| III. One command, zero friction | ✅ Computed at `cairn init`; `system.yaml` and its new fields are optional. |
| IV. Useful on day one, compounding | ✅ The view exists after setup and sharpens with each sync and with declarations. |
| V. Token discipline | ✅ One brief line of 40 tokens or fewer, placed before team knowledge. Consumers are added to `cairn_context` within its existing budget. `cairn_system` joins the full toolset only; the core set is unchanged. |
| VI. Right model for the job | ✅ Narrative uses the fast tier, escalating to balanced only on request, with a budget. |
| VII. Local-first | ✅ State lives in `.cairn/`; siblings are read read-only; no network. |
| VIII. Agent-neutral | ✅ Delivered through the brief, MCP and instruction files that every agent already receives. |

One amendment needed (principle II vocabulary); no other violations.

## Design decisions

1. **One model, many views** (Structurizr's idea).
   - Elements and relationships are stored once.
   - A view is a query (level, scope, budget) plus the layout.
   - The UI and the export never compute facts themselves.
2. **Per-repository facts, product-level links at read time.**
   - Each repository stores its own containers, endpoints, calls, channels, stores, configuration names and
     evidence.
   - Linking across repositories (an HTTP call to a route, a publisher to a subscriber, a shared store, a
     package dependency, plus the existing cross-repo call and type passes) runs when a view is built, over
     this store and the siblings' stores opened read-only.
   - This keeps the rule that Cairn writes only into the repository it serves.
3. **Signals ride on the graph engine.**
   - Routes need new extraction: the engine emits decorator edges but not their arguments, and Go has no
     decorator handling at all. Capturing route arguments for 8 frameworks is a parser change across
     extractors and is the critical path (T013, split per language).
   - Containers are per deploy unit, not per repository; a monorepo yields several, linked by the same rules.
   - Outbound calls, publish and subscribe, and driver use are call sites in the existing call graph.
   - Wrappers are resolved by following that graph, up to 2 hops.
   - Deploy files (Dockerfile, compose, Kubernetes, Procfile) and manifests get small dedicated parsers.
4. **Catalog as data** (detail: [frameworks.md](frameworks.md)).
   - Frameworks are rules over five pattern kinds the engine implements once: decorator, annotation, call,
     file-route, config.
   - `src/cairn/system/catalog/*.json` maps libraries, images and env-name patterns to stores, channels and
     outside services, plus each one's "what" verb.
   - Projects extend it with `.cairn/catalog.json`.
   - A match needs a use site, never an import alone (lesson from the prototype).
5. **Labels without a model.**
   - "What" comes from rules:
     - SQL verbs and tables give "Writes and reads orders".
     - The topic gives "Publishes order.created".
     - The route's resource gives "Calls the orders API".
     - The catalog verb gives "Sends email".
   - "How" is the protocol plus the route, topic or driver.
   - Narrative may later rewrite "what" for components, labelled inferred.
6. **Evidence and staleness.**
   - Each claim stores evidence rows `(repo, file, line, kind, commit_confirmed)`.
   - On sync, claims whose evidence file changed are re-derived.
   - A claim that is no longer derived becomes stale, with a reason, rather than being deleted. It
     surfaces as a drift finding of kind `stale-architecture`. It is purged after N syncs or once the user
     acknowledges it.
7. **One layout engine, in Python.**
   - It is a layered layout ranked by data-flow direction, with a barycentric row order, libraries on a
     bottom row, orthogonal routing, fanned ports and label masks.
   - It returns geometry as JSON.
   - The UI draws that geometry with tokens and adds interaction.
   - The export writes SVG from the same geometry. Identical pictures in both places is a requirement
     (US2).
8. **Tokens.**
   - `src/cairn/system/diagram/tokens.json` is canonical.
   - `docs/diagram-standard/tokens.json` is a generated copy.
   - The UI's `tokens.css` diagram roles are generated from it at build time, so there is one source.
9. **Components.**
   - Within a container's code, communities are regrouped by dominant folder or module.
   - Each is named from its folder or module path, not its busiest symbol.
   - The number of components is capped by the node budget; smaller ones collapse into "+N".
10. **Flows.**
    - Each starts at an entry point (route, handler, command).
    - It follows calls across components and onto relationships crossing containers.
    - Steps are ordered by call order within a function body when the AST gives it. Otherwise the flow is
      marked inferred.
    - At most 12 steps, after which it splits.

11. **Mermaid as a second hand-made format and an export.** YAML stays canonical. The Mermaid exporter
    and flowchart importer share the model; facts Mermaid has no syntax for travel in `%% cairn` comments.
    Prototyped in `prototype/code/mermaid.py`; comparison in [mermaid/README.md](mermaid/README.md).
12. **Dependency layers, redesigned.** First-party folders only, cycles as one block, budgets per layer,
    entry points from the system model, inside Code as an overlay.

## Phases

- **Phase A: foundations** (US2, partly US1). Package the tokens, the description format, the checker,
  the layout engine, the SVG renderer and the evidence table. Add the `cairn diagram check|render` CLI.
  Golden tests use `docs/diagram-standard/examples`.
- **Phase B: signals and model** (US1). Deploy and manifest parsers, routes, calls, channels, catalog,
  config names, the store tables, product linking and ambiguity handling. A `cairn system --view containers`
  text output. Fixture products and ground truth.
- **Phase C: staleness** (US3). Incremental re-derivation, stale marking, drift integration.
- **Phase D: Map UI** (US5, US1). Context, Containers, Components, Flows and Code views. Remove Engine
  views; move dependency layers into Code; evidence panel.
- **Phase E: agents** (US4). Brief line; consumers in `cairn_context`; full-toolset `cairn_system`.
- **Phase F: components and flows** (US5).
- **Phase G: docs export** (US6).
- **Phase H: optional narrative** (US7).

## Project Structure

### Documentation (this feature)

```text
specs/002-system-model/
├── spec.md, plan.md, tasks.md, research.md, data-model.md
├── checklists/requirements.md
└── prototype/        # evidence from the throwaway prototype
docs/diagram-standard/ # the standard, tokens.json, examples (replacements)
```

### Source Code (repository root)

```text
src/cairn/system/
├── model.py          # Element, Relationship, Evidence, Flow; store access
├── signals/          # deploy.py, manifests.py, http.py, messaging.py, stores.py, config.py
├── catalog/          # *.json data
├── link.py           # cross-repo matching (read-only siblings)
├── components.py, flows.py
├── recheck.py        # incremental staleness, drift findings
├── views.py          # view queries with budgets and collapsing
└── diagram/          # tokens.json, check.py, layout.py, svg.py, alt.py
src/cairn/ui/app/components/c4view.js   # draws layout geometry with tokens
src/cairn/ui/app/views/map.js           # tabs: Context, Containers, Components, Flows, Code
tests/system/                           # fixtures (shop, spring, go), golden views, staleness
```

**Structure Decision**: a new `cairn.system` package beside `engines/`. It is a Cairn concept spanning
engines, not an engine.

## Risks

- **Framework coverage** decides recall. Mitigations: the catalog, `system.yaml` declarations, and
  "ambiguous" and "unresolved" markers instead of guesses.
- **Layout quality on large products.** Mitigations: budgets, collapsing, one diagram per service with its
  neighbours.
- **Setup time.** Mitigation: signals ride on the existing parse, and staleness is incremental only.

## Complexity Tracking

None.
