# ADR-0009: Route extraction stays in the system layer, not the graph engine

**Status:** Accepted · **Date:** 2026-10-09

## Context

T013 asked for route extraction as a parser change in `src/cairn/engines/graph/extractors/`, so the graph
engine would emit decorator edges with arguments and Go routes natively. The work was done instead in
`src/cairn/system/signals/routes.py`, which re-parses route files with the bundled tree-sitter grammars and
extracts routes for Python (FastAPI, Flask, Django URLs), JS/TS (Express, Fastify, Next.js), Java (Spring)
and Go (net/http, Gin).

## Decision

Route extraction stays in the system layer. The graph engine is not changed.

## Reasons

- **Independently testable.** The system layer's route extraction is tested by `tests/system/test_routes.py`
  without touching the graph engine's test suite. A parser change would have coupled the two.
- **Same parser, no fork.** The system layer uses the same bundled tree-sitter grammars the graph engine
  uses; the only cost is a second parse of route files, which is small next to the graph build.
- **Go has no decorators.** The graph engine's Python and JS extractors rely on decorator edges, which Go
  does not have. Adding Go support to the engine would have needed a new edge kind and new merge rules;
  the system layer's pattern-matching approach covers Go with no engine change.
- **The engine stays focused.** The graph engine answers "what calls what" at the code level. Routes are a
  deployment concern (what serves HTTP), which belongs to the system model.

## Consequences

- Route files are parsed twice: once by the graph engine (as code), once by the system layer (as routes).
  The second parse is bounded to files the catalog marks as route-bearing.
- The system layer's route nodes are not graph nodes; they live in the system model only.
- If the graph engine ever gains native route edges, the system layer can switch to reading them without
  changing its own tests.
