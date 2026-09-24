# CLI reference

Every query command accepts `--json`. Exit codes: 0 ok, 1 not set up, 2 usage, 3 not in a project.

## Setup and health
| Command | What it does |
|---|---|
| `cairn` | First run: full setup. Afterwards: status. |
| `cairn init [--agents a,b] [--no-hooks] [--no-ui] [--no-capture] [--no-specs]` | Idempotent setup. Never prompts. |
| `cairn status` | Layers, active spec, drift, UI address. |
| `cairn doctor` | Each capability, its state, and the exact fix. |
| `cairn sync [--deep/--no-deep] [--budget N] [--no-map-rebuild] [-q]` | Incremental refresh of every layer. |
| `cairn uninstall [--purge]` | Removes agent wiring and hooks (and `.cairn/` with `--purge`). |

## Questions
| Command | What it does |
|---|---|
| `cairn impact <target> [--depth 2] [--budget 1800] [--explain]` | What breaks if this changes. |
| `cairn why <target> [--explain]` | Why it is the way it is. |
| `cairn ask "<question>" [--no-llm]` | Evidence pack plus narrated answer (with a key). |
| `cairn search <q> [--kinds symbol,file,…]` | Search every layer. |
| `cairn brief` | The briefing agents receive at session start. |

Targets can be a path (`src/pay/service.py`), a symbol (`PaymentService`), a member
(`PaymentService.process`), or an id (`task:002-refunds/T004`).

## Map
`cairn trace path A B` · `cairn trace explain X` · `cairn trace query "<question>"` · `cairn hubs` ·
`cairn areas` · `cairn prs` (open PRs with impact; needs the GitHub CLI)

## Specs
`cairn specs [id]` (board and trace) · `cairn spec new "<description>"` · `cairn drift [id] [--deep]`

In your agent: `/speckit-constitution`, `/speckit-specify`, `/speckit-clarify`, `/speckit-plan`,
`/speckit-tasks`, `/speckit-analyze`, `/speckit-checklist`, `/speckit-implement`,
`/speckit-converge`, `/speckit-taskstoissues`. Cairn's hooks run at plan, tasks, clarify and
implement.

## Memory, timeline, sessions
`cairn remember "<text>" --kind convention|decision|gotcha|preference|fact [--supersedes id]` ·
`cairn recall <q>` · `cairn memories [--all]` · `cairn forget <id>` ·
`cairn timeline [--target file:x] [--days 30]` · `cairn sessions [-q text]`

## Servers and agents
`cairn ui` · `cairn up` · `cairn down` · `cairn mcp` · `cairn agents install [--agents …]` ·
`cairn agents list` · `cairn models [--ledger]`
