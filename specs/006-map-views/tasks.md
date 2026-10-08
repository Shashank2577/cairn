---
description: "Tasks for 006-map-views (ids kept from the 002 umbrella)"
---

# Tasks: Map views: Context, Containers, Components, Flows, Code, and dependency layers

Ticked = code and passing tests exist. **partial** = some exists, with what is missing. open = not started.

- [ ] T030 [US5] `c4view.js` drawing server geometry with tokens, hover and click evidence panel, keyboard focus in `src/cairn/ui/app/components/c4view.js` — open
- [ ] T031 [US5] Map tabs: Context, Containers, Components, Flows, Code; remove Engine views in `src/cairn/ui/app/views/map.js` — open
- [ ] T031a [US5] Redesign dependency layers (FR-026): hide vendored and third-party folders with a count, one expandable block per large cycle, per-layer budgets with "+N", entry points from the system model, evidence per folder; server side in `src/cairn/engines/mapper.py` `file_graph()` follow-up, view in `src/cairn/ui/app/components/archmap.js` — **partial**: vendored folders hidden, cycles collapsed, entry points detected, sparse labelled edges; the expanded cycle ignores the per-layer budget (+N) and the view is not yet inside Code
- [x] T031b [US5] Before/after screenshots of dependency layers on Cairn's own repository for review before merging — done: specs/002-system-model/layers/*.png
- [ ] T032 [US5] Keep the graph exports on the command line; remove only the `/graph/views` UI routes in `src/cairn/server.py` — open
- [ ] T033 [P] [US5] Components per container (communities grouped by dominant module, named from paths, budgeted) in `src/cairn/system/components.py` — open
- [ ] T034 [P] [US5] Flows from entry points (ordered by call order when the AST gives it, else inferred; split at 12 steps) in `src/cairn/system/flows.py` — open
- [ ] T035 [US5] UI tests (Playwright smoke) for the five views, evidence panel and drill-down in `tests/ui/test_map_views.py` — open
- [ ] T047 Moderated new-joiner test (at least 5 people name the containers and one interaction within 2 minutes, SC-005); results in `specs/002-system-model/usability.md` — open
