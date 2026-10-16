# Real-world Mermaid corpus

Hand-authored files in the shapes people actually write, used by `tests/system/test_mermaid_golden.py`.

Each file must import with warnings, never with a crash, and must lose nothing silently: whatever the importer
cannot represent has to come back as a warning. A file that only passes because nothing is asserted is worse
than no file at all, so each one declares what it is for in `EXPECTED` in the test, next to the ids, edges,
labels and warnings it must produce.

| File | What it exercises |
|---|---|
| `flowchart-basic.mmd` | the everyday chart: shapes per element type, labelled sync and async arrows |
| `flowchain-and-edges.mmd` | chained edges, `&` fan-out, thick and dotted lines, inline and pipe labels |
| `flowchart-subgraphs.mmd` | boundaries, nested subgraphs, arrows to a subgraph, an unclosed one |
| `flowchart-labels.mmd` | labels with quotes, brackets, entities, unicode, semicolons and `&` |
| `flowchart-styled.mmd` | classDef, class, style, linkStyle, click and links: all ignored with warnings |
| `sequence-auth.mmd` | participants, actors, alt/else, opt, loop, activations, a note, autonumber |
| `sequence-messy.mmd` | the mistakes real files contain: stray `end`, a lost-message arrow, an orphan comment |
| `c4-container.mmd` | C4 containers, a software-system boundary and directional relationships |
| `c4-context.mmd` | C4 context, where technology is forbidden |
| `flowchart-cairn-comments.mmd` | `%% cairn` comments in short and JSON form, including ones that match nothing |

Nothing here is generated, and nothing is copied from a published diagram: the files exist to pin the
importer's behaviour on syntax, not on anybody's architecture.