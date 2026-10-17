---
description: "Tasks for 006-map-views (ids kept from the 002 umbrella)"
---

# Tasks: Map views: Context, Containers, Components, Flows, Code, and dependency layers

Ticked = code and passing tests exist. **partial** = some exists, with what is missing. open = not started.

- [x] T030 [US5] `c4view.js` drawing server geometry with tokens, hover and click evidence panel, keyboard focus in `src/cairn/ui/app/components/c4view.js` — done AFTER a rewrite: the first version re-implemented layout in JS (TYPE_COLOR, per-type grouping) and was unusable. The component now embeds `/system/svg`, the diagram standard's own renderer output, and adds only hover/click/keyboard/evidence; 13 tests in tests/system/test_system_diagram.py
- [x] T031 [US5] Map tabs: Context, Containers, Components, Flows, Code; remove Engine views in `src/cairn/ui/app/views/map.js` — done: System tab with five levels added, EngineViews replaced with redirect notice, TABS updated
- [x] T031a [US5] Redesign dependency layers (FR-026): hide vendored and third-party folders with a count, one expandable block per large cycle, per-layer budgets with "+N", entry points from the system model, evidence per folder; server side in `src/cairn/engines/mapper.py` `file_graph()` follow-up, view in `src/cairn/ui/app/components/archmap.js` — done: vendored folders hidden with count, cycles expandable, +N per-layer budget, entry points from system model, evidence panel per folder
- [x] T031b [US5] Before/after screenshots of dependency layers on Cairn's own repository for review before merging — done: specs/002-system-model/layers/*.png
- [x] T032 [US5] Keep the graph exports on the command line; remove only the `/graph/views` UI routes in `src/cairn/server.py` — done: `/graph/views/{kind}` removed, `/graph/views` list kept for API compat, EngineViews UI removed
- [x] T033 [P] [US5] Components per container (communities grouped by dominant module, named from paths, budgeted) in `src/cairn/system/components.py` — done: `view()`, `groups()`, `arrows()` with Leiden communities restricted to one container's files, FR-016 naming (module path, symbol fallback marked inferred), aggregated dependency arrows with evidence; 15 tests passing in tests/system/test_components.py
- [x] T034 [P] [US5] Flows from entry points (ordered by call order when the AST gives it, else inferred; split at 12 steps) in `src/cairn/system/flows.py` — done: `view()`, `flows()`, `entry_points()`, `steps_for()` with sequence diagram descriptions; 14 tests passing in tests/system/test_flows.py
- [x] T035 [US5] UI tests (Playwright smoke) for the five views, evidence panel and drill-down — done in `tests/system/test_system_diagram.py`: 13 tests, 9 of them browser tests that skip without CAIRN_UI_TESTS=1. They replaced an earlier version whose assertions (`count() >= 0`) passed on an empty page
- [ ] T047 Moderated new-joiner test (at least 5 people name the containers and one interaction within 2 minutes, SC-005); results in `specs/002-system-model/usability.md` — **needs humans**: requires 5 participants; cannot be automated. Flagged for the team.
