---
description: "Tasks for 008-architecture-docs-export (ids kept from the 002 umbrella)"
---

# Tasks: Architecture documents and optional narrative

Ticked = code and passing tests exist. **partial** = some exists, with what is missing. open = not started.

- [ ] T040 [US6] `cairn docs export [--out docs/architecture]`: context, containers, components, key flows, modules, configuration table, external systems; SVG plus evidence tables; omit empty sections; record the commit, in `src/cairn/system/export.py` — open
- [ ] T041 [US6] Tests: export on fixtures, rerun after a change updates, no placeholder text, in `tests/system/test_export.py` — open
- [ ] T042 [US7] Narrate component names, responsibilities and flow descriptions (fast tier, `[system] narrate_tokens` budget, ledgered, labelled inferred, cites elements) in `src/cairn/system/narrate.py` — open
- [ ] T043 [US7] Tests: identical model with and without narrative; ledger entries only with opt-in — open
- [ ] T044 [P] Docs: `docs/architecture.md` section on the system model, `docs/cli.md`, `docs/multi-repo.md` (`system.yaml` fields) — open
