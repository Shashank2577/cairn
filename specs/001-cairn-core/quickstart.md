# Quickstart validation

1. `uv tool install cairn-brain` (or `pipx install cairn-brain`)
2. `cd any-git-repo && cairn` → summary panel, UI URL printed, no prompts.
3. `cairn impact <a function name>` → dependents, co-change, warnings.
4. `cairn remember "Payments must be idempotent" --kind convention` then `cairn recall payments`.
5. Open Claude Code in the repo → status line shows Cairn; `/cairn:why <file>` works.
6. `/speckit-specify ...` → `/speckit-plan` → Cairn `before_plan` hook injects context.
7. `export ANTHROPIC_API_KEY=... && cairn sync --deep` → `cairn models --ledger` shows tiers.
