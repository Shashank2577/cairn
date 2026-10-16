---
description: "Tasks for 008-architecture-docs-export (ids kept from the 002 umbrella)"
---

# Tasks: Architecture documents and optional narrative

Ticked = code and passing tests exist. **partial** = some exists, with what is missing. open = not started.

- [x] T040 [US6] `cairn docs export [--out docs/architecture]`: context, containers, components, key flows, modules, configuration table, external systems; SVG plus evidence tables; omit empty sections; record the commit, in `src/cairn/system/export.py` — done: `export()` writes one Markdown + Mermaid + SVG per level with evidence tables, empty sections omitted, commit recorded on every page; `cairn docs export` wired in cli.py
- [x] T041 [US6] Tests: export on fixtures, rerun after a change updates, no placeholder text, in `tests/system/test_export.py` — done: 8 tests passing
- [x] T042 [US7] Narrate component names, responsibilities and flow descriptions (fast tier, `[system] narrate_tokens` budget, ledgered, labelled inferred, cites elements) in `src/cairn/system/narrate.py` — done: `narrate_export()` with `Budget` cap, `system.narrate` task, labelled inferred, cites elements; no-op without a model or opt-in
- [x] T043 [US7] Tests: identical model with and without narrative; ledger entries only with opt-in — done: 5 tests passing in tests/system/test_narrate.py
- [x] T044 [P] Docs: `docs/architecture.md` section on the system model, `docs/cli.md`, `docs/multi-repo.md` (`system.yaml` fields) — done: architecture.md "The system model" section, cli.md "Docs export" table, multi-repo.md "The system model" section
