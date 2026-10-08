# Cairn diagram standard

Version 1.0 (draft, feature `002-system-model`). One grammar and one data source for every architecture
diagram: the Map UI, `cairn docs export`, and diagrams people draw by hand.

- **One grammar.** The rules below, enforced by a checker (`cairn diagram check`, planned; prototype in the
  feature's scratch work). Every diagram in `examples/` passes it.
- **One token file.** [`tokens.json`](tokens.json) holds colour roles (light and dark), type, grid, strokes,
  dashes, budgets, element types and diagram kinds. The UI and the export both read it; hand-made diagrams
  use it too. Roles are the contract: a value may change, a role may not.
- **One data source.** Diagrams Cairn draws come from the system model, which is computed from code,
  manifests, deploy files and `system.yaml` with **no model calls**. Hand-made diagrams cite their sources
  the same way.

It merges the C4 model, Structurizr's one-model-many-views idea, C4-PlantUML's element shapes, and the
editorial rules of diagram-design and archify, with Cairn's own addition: **every box and arrow carries
evidence and is re-checked on every sync.**

## 1. One level per diagram

Each diagram shows exactly one level, named in its title.

| Kind | Title starts with | Shows | Technology on arrows |
|------|-------------------|-------|----------------------|
| `landscape` | System landscape | the products of an organisation, people, outside systems | optional |
| `context` | System context | one product as one box, its people and outside systems | **forbidden** |
| `container` | Containers | the product's runnable pieces, data stores, channels, libraries; neighbours outside | **required** |
| `component` | Components | the main parts inside one container; its neighbours outside | **required** |
| `flow` | Flow | one request or event, step by step (sequence style) | **required** |
| `code` | Code | members of one component, generated on demand | optional |
| `data-model` | Data model | entities and how they relate | forbidden |
| `state` | Lifecycle | states of one thing and the transitions between them | forbidden |

Multi-repo products: one repository's runnable pieces are containers of the product. A product with many
repositories gets a landscape or context overview and one container diagram per product (or per service,
showing its nearest neighbours), never one giant diagram.

## 2. Elements

Every element has a **name**, an explicit **type**, a **one-line responsibility**, and **evidence**.
Containers, data stores, channels, libraries and components also state their **technology**.

| Type | Shape | Notes |
|------|-------|-------|
| person | box with a person glyph | people and roles |
| system | plain box, 6 px radius | the product (or a sibling product) |
| external-system | box, dashed border, muted fill | owned by someone else |
| container | plain box; kind tag: web-app, service, worker, cli, job, function, mobile-app | a runnable, separately deployed unit |
| data-store | cylinder | databases, buckets, caches used as storage |
| channel | pipe | queues, topics, pub/sub |
| library | box with a bar on the left | shared code used at build time; never a runtime box |
| component | box with a component glyph | a part inside one container |
| entity, code | compartment box | data model and code levels |
| state | rounded box | lifecycle diagrams |

Shape, border style and the type tag (top-left, mono, uppercase) carry the type, so colour is never the
only signal. One element per concept: replicas are a `count` field shown as a badge (`×3`), not copies.
At container level the product itself is drawn as its boundary (type "software system"), not as a box.

## 3. Relationships

1. **One arrow, one direction, one meaning.** No double-headed arrows; draw two labelled arrows.
2. **Label = what + how.** "What" is a short verb phrase saying what passes or why ("Publishes
   order.created", "Reads and writes orders"); never a bare "Uses", "Calls" or "Data". "How" is the
   protocol, route or mechanism in mono brackets (`[HTTP · POST /api/orders]`). "How" is required at
   container, component and flow level and forbidden at context level. A computed, extracted relationship
   with no rule-derived "what" shows its "how" alone; a padded generic phrase is worse than none.
   Hand-made diagrams always give a "what".
3. **Line style = interaction.** Solid with a filled head: synchronous request. Dashed `5 4` with an open
   head: asynchronous message. Dotted `1.5 3.5` with an open head: build-time dependency. Dashed with a
   filled head: reply (flows only).
4. **Routing.** Orthogonal elbows with 8 px corners, no diagonals, fanned attach points at least 12 px
   apart, no line passing behind a box, labels on an opaque mask beside the line.
5. **Budgets.** At most 32 characters of "what" and 36 of "how". Shorten before you delete; never drop a
   meaningful label to fix a collision.

## 4. Provenance and evidence

Every element and relationship carries at least one evidence reference: `repo/path:line`, a manifest entry
(`pyproject.toml: shop-contracts`), a deploy entry (`docker-compose.yml: services.db`) or, for hand-made
diagrams, the document or person that declares it. `cairn diagram check` resolves `repo/path:line`
references against the working tree when the repository is present and reports any that do not resolve,
so a hand-made diagram's tie to code is verified, not just typed. Credential names (keys, tokens,
passwords) never appear: evidence and labels say "a SendGrid credential", not the variable name. Provenance is marked before the label and in the type tag:

| Mark | Provenance | Meaning |
|------|------------|---------|
| (none) | extracted | read directly from code, a manifest or a deploy file |
| ◆ | declared | stated by a person (`system.yaml` or a hand-made source) |
| ≈ | inferred | derived; verify before relying on it |
| ? | ambiguous | more than one candidate; the evidence lists them |
| ⚠ | stale | its evidence was no longer found at the last sync; drawn in the stale colour |

Interactive renderings open the evidence on hover or click. Static renderings ship a text table of every
element and relationship with its evidence next to the image (the long text alternative).

## 5. Boundaries, title, legend

- **Title** on every diagram, starting with the kind (section 1), plus a scope line: what system or
  container it describes, and for computed diagrams the commit and "0 model calls".
- **Boundaries** only where a fact exists: ownership (software system), runtime (a container's components),
  isolation (a network, a trust boundary with a security control). Never a layout band like "Service Layer".
  Dashed, labelled top-left with name and type. At most 3 per diagram. Trust boundaries use the `trust`
  colour role.
- **Legend** on every diagram, as a strip below it, listing only what the diagram uses: element shapes,
  line styles, provenance marks, overlays.

## 6. Colour, type, density

- Neutral paper and ink (shared with the Cairn UI). **One accent**, on zero to two focal elements.
  Semantic colour roles only for meaning: `stale`, `trust`, `sensitive` (personal-data overlay),
  `inferred`. Light and dark are equal themes from the same roles.
- No shadows, gradients, glow or decorative icons on diagram elements.
- Type ramp from the tokens: names 13 px semibold sans; technology and labels' "how" in mono; type tags
  8.5 px mono uppercase. Rendered text never below 7.5 px.
- Budgets: about 12 elements per computed view (9 for hand-made), 16 relationships, 3 boundaries;
  flows at most 6 participants, 12 messages, 1 fragment. Over budget: split into an overview and details,
  or collapse a group into one box with a count, and say so in the scope line.

## 7. Overlays

Overlays add one fact to a container diagram without a new level:

- **data-class** on relationships (`public`, `internal`, `personal`, `sensitive`), shown as `data: personal`
  in the `sensitive` colour. This replaces separate "PII data flow" pictures.
- **trust-boundary** on a boundary with a real security control.
- Code view overlays (not architecture): folder **dependency layers**, fixes and reverts, agent activity.

## 7a. What the checker enforces and what the renderer enforces

- **Description rules (checker, `cairn diagram check`)**: R1 title and scope, R2 one level, R3 element
  fields, R4 labels, R5 line styles, R6 one direction per arrow, R7 evidence and provenance (references
  resolved when possible), R8 budgets, R9 boundaries, R10 legend on, R11 accents, R12 references to known
  elements, R13 text alternative.
- **Layout rules (renderer self-test, every render)**: no label overlapping another label or a box, no line
  behind a box, rendered text at least 7.5 px at the default size, contrast of every colour role against the
  paper.

## 8. Accessibility

`<svg role="img">` with `<title>` and `<desc>` (one sentence about the content), the evidence table as the
long description, contrast of at least 4.5:1 for text and 3:1 for lines and shapes against the paper,
keyboard focus and visible focus on interactive elements, and nothing that depends on colour alone.
Provenance marks (◆ ≈ ? ⚠) carry a spoken label (`aria-label="declared"` and so on) and are spelled out in
the legend.

## 9. How the three surfaces share this

| Surface | Reads | Produces |
|---------|-------|----------|
| Map UI | the system model + `tokens.json` | interactive SVG views; every element opens its evidence |
| `cairn docs export` | the same model + `tokens.json` | Markdown with SVG files and the evidence tables |
| Hand-made diagrams | a YAML description (canonical) or a Mermaid flowchart with `%% cairn` comments | the same SVG via `cairn diagram render`; `cairn diagram check` reports every broken rule |
| Mermaid export | any view | a Mermaid flowchart that renders natively on GitHub, with every fact in `%% cairn` comments and the evidence table beside it |

**What Mermaid cannot guarantee.** Mermaid's own layout and theme decide the picture: no legend inside it,
labels can crowd, no line-style difference between asynchronous and build-time links on GitHub, no hover
evidence, one theme per file. Use it where native rendering on GitHub matters; use Cairn's renderer where the
standard must hold exactly. One diagram written both ways: `specs/002-system-model/mermaid/README.md`.

A diagram description is the same structure the model emits: `diagram`, `title`, `scope`, `description`,
`elements`, `boundaries`, `relationships` (or `flow.participants` and `flow.messages`). See `examples/`.

## 10. Replacements for the Taazaa documentation templates

The Taazaa documentation skill's 18 Mermaid diagrams share five defects: one layered shape forced on every
system, unlabelled arrows, levels mixed in one picture, no boundary, legend or element types, and nothing
tying a box to code. Transcribed into the description format, the two headline templates fail this
standard with 47 and 53 violations (transcriptions in [`taazaa-original/`](taazaa-original/), reproducible
with the prototype checker in `specs/002-system-model/prototype/code`). Each replacement below passes with none.

| Taazaa template diagram | Replacement | Why it changes |
|---|---|---|
| High-Level Architecture | [01 context](examples/01-context-shop.svg) + [02 containers](examples/02-containers-shop.svg) | two levels instead of one mixed picture; real boundary; every arrow says what and how |
| Communication patterns / service catalog diagram | [02 containers](examples/02-containers-shop.svg) | sync, async and build-time are line styles, not separate drawings |
| High-Level Data Flow, data flow scenarios | [05 flow](examples/05-flow-place-order.svg) | processing steps belong in a flow, not among systems |
| Sequence: primary workflow, data creation | [05 flow](examples/05-flow-place-order.svg) | numbered messages with route and evidence |
| Sequence: authentication flow (both copies) | [06 flow](examples/06-flow-token-auth-cairn.svg) | real participants, an `alt` fragment for success and refusal |
| Data Flow Map with PII Indicators | [04 personal data](examples/04-personal-data-shop.svg) | a data-class overlay on the container view |
| Security Architecture (zones) | [03 trust boundaries](examples/03-trust-shop.svg) | zones only where isolation exists; findings marked inferred |
| Module Architecture Overview, Module Dependencies | [07 components](examples/07-components-shop-api.svg) | components of one container, named for what they do |
| Logic / Computation Architecture (domain) | component diagram, as 07 | same rules |
| Calculation Flow (domain) | flow, as 05 | same rules |
| Entity Relationship Diagram, Rule Data Model | [08 data model](examples/08-data-model-shop.svg) | fields shown, associations labelled |
| Rule Lifecycle | [09 lifecycle](examples/09-lifecycle-memory-cairn.svg) | a lifecycle diagram with labelled transitions |
| Configuration Architecture | removed; a generated table of settings, defaults and owners | configuration sources are not architecture; deletion is the better diagram |

Examples 01 to 05, 07 and 08 describe a four-repository sandbox product ("Shop") used to test the system
model; 06 and 09 describe Cairn's own code. Each `.md` beside an `.svg` is its evidence table.
