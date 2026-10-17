# Diagram standard research digest (for Cairn)

Source tags: [C4] c4model.com pages (abstractions, diagrams/*, notation, checklist, faq); [SZ] Structurizr DSL language reference; [PU] C4-PlantUML source and README; [AR] archify (SKILL.md, authoring-defaults, authoring-contract, DESIGN.md); [DD] diagram-design (SKILL.md, style-guide, type-architecture/sequence/deployment/dependency, layout-budget); [A42] arc42; [UML] uml-diagrams.org; [W3C] WCAG/WAI. "(unverified)" means from memory or not found on a fetched page.

## (a) Element taxonomy and recommended shapes

| Cairn element | Merged definition | Required fields | Shape (redundant with colour) |
|---|---|---|---|
| Person / actor | Human or agent using the system [C4] | name, type, description | Rounded box with a head-and-shoulders glyph (C4 uses a person shape [PU]; keep it as a glyph for SVG simplicity) |
| Software System | Highest abstraction; delivers value; owned by one team, usually one repo [C4] | name, type, description | Plain rectangle, rx 6 [DD] |
| External System / Person | Outside ownership, no visible internals [C4][PU] | same + "external" | Same shape, dashed 4,3 border, muted fill [DD optional treatment; PU grey fill] |
| Container | Separately running application or data store: runtime boundary, not a JAR/DLL [C4] | name, type, **technology**, description | Rectangle; data store = cylinder (ContainerDb); queue = pipe/horizontal bar (ContainerQueue) [PU] |
| Component | Grouping of related code behind an interface, same process, not deployable alone [C4] | name, type, **technology**, description | Rectangle with a small component tab (PU uses stereotype text) |
| Code element | Class, function, table [C4] | name, kind | UML-like compartment box; C4 says do NOT hand-maintain, generate on demand [C4] |
| Deployment node | Where container instances run; nestable [C4][SZ] | name, technology, instance count | Dashed zone rectangle, nested [DD][C4] |
| Infrastructure node | Load balancer, DNS, firewall [C4][SZ] | name, technology | Small rectangle with type tag |
| Boundary | System / container / enterprise grouping [PU] | label, type | Dashed rectangle, label top-left on a paper mask [PU][DD] |

Rules:
- Every element carries name, explicit type, short description; containers and components also technology [C4].
- Type tag: rectangular chip (rx 2), top-left [DD].
- One node per concept; replicas are a badge (`x3`), not duplicate nodes [DD deployment].
- Queues, topics, shared databases are containers [C4 FAQ].
- archify's kinds (frontend, backend, database, cloud, security, messagebus, external) [AR] are a tag layer; do not replace C4 type.
- Multi-repo: a repo is the natural Software System boundary (C4: one team, usually one repository) [C4]. Cairn-specific mapping; the C4 text does not mention Cairn.

## (b) Relationship and label rules

1. One arrow = one unidirectional relationship [C4]. Draw two arrows (or a labelled return) rather than a double-headed arrow. DD also bans bidirectional arrows when direction is obvious [DD type-architecture].
2. Label = intent + direction-consistent verb phrase, never bare "Uses" [C4]. Example: "Reads route table from".
3. Containers and components: add technology/protocol in the label, e.g. `[HTTPS/JSON]` [C4][PU `Rel(from,to,label,techn)`].
4. System Context relationships omit technology and protocols [C4].
6. Style encodes semantics, not routing: solid = sync/direct, dashed = async/optional/return, `link` colour = external/API call [DD]. C4 asks that line styles and arrowheads be in the legend [C4 checklist].
7. Orthogonal elbows only, radius 8 (6 min), no diagonals [DD §6]. Straight line only when ends share x or y.
8. Label geometry [DD §6]: label on an opaque mask, 6 to 10 px clear of the stroke, beside vertical segments, never on them. Fan attach points at `L*k/(N+1)`, at least 12 px apart (8 px on tiny boxes). No path behind a non-endpoint box. Offset parallel routes at least 12 px. Bridge/hop the less important line at a single crossing.
9. Label width [AR]: clear gap `6.5px x ASCII units + 21px`; CJK counts 2 units.
10. Never delete a meaningful label to fix a collision; move, re-route, then shorten [AR].

## (c) Levels and the one-level-per-view rule

- Never mix abstraction levels in a single diagram [C4 FAQ].
- Scope per level [C4]:

| View | Scope | Primary | Supporting | Audience | Recommended |
|---|---|---|---|---|---|
| System Landscape | enterprise/org | people + systems | none | everyone | for larger orgs |
| System Context | one system | the system | people, external systems | everyone | yes |
| Container | one system | containers | adjacent people/systems | technical | yes |
| Component | one container | components | sibling containers, people, systems | architects, devs | optional; automate |
| Code | one component | code elements | none | devs | no, generate on demand |

- Cairn multi-repo mapping (proposal): Landscape = repos as Software Systems plus people/external systems; Container = deployables inside one repo (from manifests, Dockerfiles, compose/k8s files); cross-repo calls shown as edges between systems at Landscape and as edges to a named external container at Container level. For many repos, C4 FAQ advises one diagram per service showing nearest inbound and outbound dependencies, not one giant diagram [C4 FAQ].
- Structurizr: one model (`softwareSystem` > `container` > `component`), many views via `include`/`exclude`, `autoLayout [tb|bt|lr|rl]`, tag-based styles [SZ]. Cairn should mirror this.
- arc42 mapping [A42]: §3 Context and Scope = Context; §5 Building Block View (level 1 = whitebox of system with blackboxes, level 2 zooms into selected blocks) = Containers/Components; §6 Runtime View = Flows; §7 Deployment View = Deployment; §5 level 1 should reuse the same external neighbours as §3.

## (d) Boundaries, legends, titles

- Title on every diagram stating type and scope, e.g. "System Context diagram for Cairn" [C4]. DD adds a 60-char title/description for the accessible name [DD].
- Legend on every diagram covering shapes, colours, border styles, line styles, arrowheads, icons, size meanings [C4 checklist]. Acronyms explained [C4].
- Legend: horizontal bottom strip, never inside the diagram area [DD]; PU auto-draws it (`SHOW_LEGEND()`) [PU].
- Legend lists only kinds actually used (and nothing extra) [DD][AR "auto" mode].
- Boundaries: dashed rectangle, label top-left on a mask, fill about 2% ink wash, stroke about 10 to 20% ink [DD]. Boundaries must express real isolation, ownership, runtime or persistence facts, not layout convenience [AR][DD deployment]. PU default boundary style is dashed [PU].
- Max 3 zones per diagram [DD].
- Colour consistency within and across diagrams [C4].

## (e) Visual grammar to adopt (numbers, bans)

- Deletion first; every node a distinct idea; every arrow informative; density 4/10 [DD].
- Budgets [DD layout-budget]: 9 nodes, 12 arrows, 2 accent elements, 2 callouts per diagram. Sequence 5 lifelines / 12 messages / 1 fragment. Dependency 9 nodes / 14 edges / 4 ranks / 1 cycle. Deployment 3 zones / 6 nodes / 8 paths / 9 artifacts. Over budget: split overview + detail, or collapse a leaf cluster into "+6 leaves" and say so in a caption [DD]. AR says no node count is a ceiling [AR]; see conflicts table.
- Accent: one accent colour on 1 to 2 focal elements only [DD]; saturated colour must map to meaning, never decoration [AR].
- Grid: geometry divisible by 4; gaps 20 to 48; padding 8/12/16; radius 4/6/8 [DD].
- Strokes 0.8/1/1.2; dashed `4,3` optional/return, `5,4` async [DD].
- Type ramp [DD]: node name 12 px sans 600; sublabel 9 px mono; eyebrow/type tag 7 to 8 px mono tracked uppercase; arrow label 8 px mono. AR floor: projected text 6 px is a failure; aim for at least 7.5 px at 1440 px [AR]. CJK floor 12 px [DD].
- Mono only for technical strings (ports, URLs, field types); names in sans [DD].
- Node treatments [DD]: focal = accent-tint fill + accent stroke; internal = white + ink; store = ink@5% + muted; external = ink@3% + ink@30%; optional = dashed; security = accent@5% + dashed 4,4.
- Z-order: background, zones, arrows, label masks, nodes [DD].
- Placement: stores directly above/below owner; fan-out side needs `32 + 14(k-1)` px [AR]; one flow direction per diagram [DD].
- Dark/light are parity themes [AR]; invert ink/paper at same opacities, brighten accent on dark [DD]. Flat, borders not shadows [DD].
- Bans [DD §4]: glow-on-dark "technical" look; identical boxes; in-diagram legend; unmasked labels; vertical text; shadows; radius above 10; accent everywhere; Mermaid layout. [AR]: decorative glass, gradient text, inferring node kind from label text.

## (f) Dynamic / flow diagram rules

- C4 Dynamic is a UML communication diagram: same elements as the static view, interactions numbered; collaboration vs sequence style is a free choice; scope = one feature/story/use case; use sparingly [C4].
- PU Dynamic: `Rel(..., $index)`, `Rel_Back`/`Rel_Neighbor` layout variants, `SHOW_INDEX` [PU]; Structurizr `dynamic` view with ordering [SZ].
- A flow reuses only elements from the static model at that level [C4][SZ].
- UML essentials [UML]: lifeline (head box + vertical line), execution specification (thin rectangle on lifeline), message kinds: sync call, async signal, reply, create, destroy (X); combined fragments with operator `ref`; arrowhead specifics (filled/open) not confirmed on the fetched page (unverified there; DD states them).
- DD sequence grammar [DD type-sequence]: actors in a top row; dashed lifelines; time top-down; activation bar w=8; return = dashed + filled head; async = dashed + open head; sync = solid + filled; self-message = U loop; at most 1 headline-success message in accent; fragments `opt` (1 region), `alt` (max 2 regions, dashed divider), `loop`, operator tab top-left, nesting max 1; frame inset at least 12 px from outer lifelines; at least 24 px between message rows; do not invent `par`, `critical`, `break`, `ref`.
- Each message gets an index badge [C4]; label rules as in (b).
- arc42 §6: important use cases, critical interfaces, start-up/error scenarios; partial scenarios allowed; sequence diagrams recommended [A42].

## (g) Accessibility

- Colour never sole carrier: C4 asks authors to consider B/W printing and colour blindness [C4]; WCAG 1.4.1 [W3C]; AR requires non-colour state cues and dark/light parity [AR]. Redundancy plan: shape (cylinder/pipe/person), border style (solid/dashed), type tag text, line style (solid/dashed), arrowhead (filled/open).
- Contrast: graphical objects at least 3:1 against adjacent colours (WCAG 1.4.11) [W3C, verified via search]; text at least 4.5:1 (WCAG AA, unverified on a fetched page); DD requires `ink` AA on `paper` and `muted` AA at 11 px+ [DD].
- SVG text alternative contract [DD]: `<svg role="img" aria-labelledby>`, `<title>` first child (60 chars or fewer), `<desc>` one sentence about content not geometry, ids prefixed per diagram (`<slug>-title`), decorative SVG `aria-hidden`.
- Complex diagram needs a two-part alternative: short description plus long description (adjacent link, `<figure>/<figcaption>`, or `aria-describedby` for plain text) [W3C WAI]. Cairn should emit a generated element/relationship list in Markdown next to every diagram as the long description (Cairn proposal).
- Keyboard-reachable nodes, visible focus, 44 px targets, reduced-motion honoured, static frame carries full meaning [AR][DD].

## (h) Anti-patterns

1. Mixing abstraction levels in one view [C4].
2. Bare "Uses" labels; unlabeled or bidirectional arrows [C4][DD].
3. Missing title, legend, type or technology [C4 checklist].
4. One giant diagram for all microservices/repos [C4 FAQ].
5. Treating JARs, packages or folders as containers/components [C4].
6. Code-level diagrams hand-maintained for persistent docs [C4].
7. Deployment details (replication, load balancers) in container view [C4].
8. Zones as layout decoration; one node per replica; unversioned artifacts; unlabeled network paths [DD deployment].
9. Hairball graph with no rank order; several highlighted cycles [DD dependency].
10. Colour as the only distinction; accent on many nodes [DD][C4].
11. Layout imported from Mermaid or auto-router with diagonals [DD].
12. Floating legends; unmasked labels; labels touching strokes [DD].
13. Inferring facts from label text or inventing nodes to fill a layout [AR][DD].

## (i) Conflicts between sources and suggested resolution

| Topic | Source A | Source B | Resolution for Cairn |
|---|---|---|---|
| Node budget | DD: max 9 nodes / 12 arrows, split beyond | AR: no ceiling; C4: split by area, no number | Soft cap 12 nodes per view for UI/Markdown; hard split or "+N more" aggregate node with count above that; Cairn auto-clusters by repo/directory. Hand-made diagrams keep DD's 9. |
| Edge label style | C4: specific sentence, no "Uses" | DD: 14 chars max, ALL CAPS, 8 px mono | Two tiers: short verb phrase (about 24 chars, sentence case) on the line; technology in brackets on a second line; full text in the tooltip/legend table. Reject all-caps for sentences (readability); keep caps for type tags only. |
| Technology on relationships | C4: omit at Context, required at Container | PU: technology always optional parameter | Enforce per level via lint: forbidden at Context, required at Container/Component. |
| Colour semantics | C4/PU: blue family by level, grey = external | DD: monochrome + one accent; AR: 7 saturated semantic hues | Level encoded by shape/tag, not hue. Neutral base + one accent focal; semantic hues limited to a fixed small set defined in tokens (store, external, security); never rely on hue alone. |
| Shadows | DD: none | AR: floating controls only | None on diagram elements; viewer chrome only. |
| Fonts | DD: serif + sans + mono, never JetBrains Mono | AR: single JetBrains Mono | Token file defines `font-title`, `font-name`, `font-tech`; default sans + mono; role split from DD, family free. |
| Bidirectional arrows | C4: unidirectional only | DD: avoid when direction obvious; PU: has `BiRel` | Unidirectional only; two arrows if both directions matter. |
| Dynamic style | C4: numbered collaboration or sequence, free choice | DD: strict UML-like lifelines, no `par` | Flows default to sequence style (lifelines, numbered), restricted DD fragments; collaboration style only for hand-made. |
| Code level | C4: do not hand-maintain | Cairn has code maps | On-demand only, one component, relevant members only. |

## Not verified
- C4 boundary/person-shape rules (notation page is "notation independent" [C4]).
- PU default hexes read from source (person #08427B, system #1168BD, container #438DD5, component #85BBF0, external #999999); recommend not adopting.
- UML arrowhead conventions (taken from DD); WCAG 4.5:1 text threshold (not fetched).
- archify repository-authoring/brand-mark references and Structurizr dynamic syntax not read.
