# Cairn extension for the spec workflow

Adds project memory to the spec-driven workflow:

| Stage | Hook | What happens |
|---|---|---|
| before plan | `speckit.cairn.context` | Impact, past incidents and conventions go into `plan.md` |
| after tasks | `speckit.cairn.trace` | Tasks are linked to code; risky files and unsafe `[P]` tasks flagged |
| after clarify | `speckit.cairn.remember` | Clarified decisions become team memory (optional) |
| after implement | `speckit.cairn.verify` | Drift check against the spec; learnings recorded |

Installed automatically by `cairn init`. Manual install: `specify extension add cairn --dev <path>`.
