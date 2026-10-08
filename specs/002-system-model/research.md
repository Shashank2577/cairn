# Research: System Model and Diagram Standard

Full source digest (C4, Structurizr DSL, C4-PlantUML, archify, diagram-design, arc42, UML, WCAG):
[research-sources.md](research-sources.md). Cairn extraction inventory: summarised below.

## Decisions

| Topic | Decision | Rationale | Rejected |
|---|---|---|---|
| Level model | C4 levels plus flow (sequence), data-model and state as supplementary kinds | Industry standard; arc42 maps onto it (§3 context, §5 building blocks, §6 runtime) | Taazaa's fixed layered picture |
| Model vs views | One stored model, many views (Structurizr) | UI, export and hand-made diagrams agree by construction | Per-surface generation |
| Relationship labels | Sentence-case "what" verb phrase plus mono "how", technology forbidden at context, required at container and below | C4 rule; readable; diagram-design's all-caps labels hurt readability | Bare "Uses", all-caps 14-char labels |
| Colour | Neutral ink and paper (shared with Cairn UI), one accent on at most 2 elements, semantic roles only for stale, trust, sensitive, inferred | diagram-design's accent discipline; C4 asks for consistency and print-safety | C4-PlantUML blue-by-level; archify's 7 saturated hues |
| Shape redundancy | Type carried by shape, border and type tag, never colour alone | WCAG 1.4.1; C4 checklist | Colour-coded types |
| Budgets | 12 elements per computed view (9 hand-made), 16 relationships, 3 boundaries; flows 6 by 12 by 1 | diagram-design budgets, relaxed for computed views with collapsing | No limit (archify) |
| Boundaries | Only for ownership, runtime or isolation facts | archify and diagram-design: zones are facts, not layout | Layer bands |
| Layout | Layered by data-flow direction, barycentric row order, orthogonal routing, label masks | Prototype showed naive ranking misplaces people and crosses lines | Mermaid or force layout |
| Hand-made format | YAML description in the model's own structure, rendered and checked by Cairn | Same checker for every diagram | Mermaid (cannot honour layout and label rules) |
| Provenance | extracted, declared, inferred, ambiguous, stale, with marks | Constitution II needs EXTRACTED/INFERRED; declared and stale needed for system.yaml and re-checks | Two-value provenance |

## What Cairn extracts today (inventory, read from code)

- **Exists**:
  - code graph (files, symbols, calls, imports) with EXTRACTED/INFERRED edges and `source_location`.
  - communities and areas.
  - folder layers (the current "Architecture" tab).
  - repo-to-repo links from package declarations and imports (`engines/graph/repos.py`).
  - C++/C#/Java/Swift cross-repo member calls and .NET shared types (INFERRED).
  - `system.yaml` with only `system` and `repos`.
  - spec drift with stable finding ids.
- **Missing** (this feature adds them):
  - container identity.
  - Dockerfile, compose, Kubernetes and Procfile semantics.
  - entry points (scripts, bins, mains).
  - framework detection.
  - HTTP routes and outbound calls.
  - messaging.
  - data stores from drivers and configuration.
  - outside services.
  - generic configuration-key inventory.
  - any persisted system model or its re-check.
  - container lines in the brief and agent context.

## Prototype findings

See [prototype/README.md](prototype/README.md): 8/8 elements, 8/8 relationships, 0 false relationships,
0 model calls on the sandbox; kind error for a web front end; import-without-use false positive in the
re-check; layout fixed by data-flow ranking.
