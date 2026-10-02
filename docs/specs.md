# Spec format

Cairn's spec-driven workflow keeps its artifacts in plain Markdown, so you can read and edit them
with any tool. Every feature lives in its own folder under `specs/`:

```text
specs/001-refunds/
├── spec.md      # the specification: user stories and requirements
├── tasks.md     # the implementation checklist
└── plan.md      # optional: the technical plan
```

`cairn spec init` scaffolds the workflow (templates land in `.cairn/workflow/templates/`) and
`cairn spec new "<description>"` creates the next numbered feature folder. The agent commands
(`/cairn-specify`, `/cairn-plan`, `/cairn-tasks`, …) write these files for you; this page documents
the exact format they use and that `cairn` parses on every [sync](architecture.md).

## spec.md

| Pattern | Meaning |
|---|---|
| `# Feature Specification: Refunds` | The feature title — the text after the colon |
| `**Status**: Draft` | The feature status, shown on the spec board. All tasks done while still `Draft` is a drift finding |
| `### User Story 1 - Refund an order (Priority: P1)` | A user story. The dash can also be `—`, `–` or `:`; priorities are `P0`–`P9` |
| `- **FR-001**: The system MUST refund through the gateway.` | A requirement. Ids are `FR-`, `NFR-` or `SC-` plus three digits |
| `- Q: What happens when the gateway is down?` | An open clarification question (counted on the board) |

Bullets can start with `-` or `*`, and a requirement may wrap: an indented continuation line is
joined back onto the bullet it belongs to.

## tasks.md

| Pattern | Meaning |
|---|---|
| `## Phase 3: US1` | A phase heading; the tasks below it carry that phase |
| `- [ ] T001 Expose refund in shop/api.py` | An open task; `- [x]` (or `[X]`) marks it done |
| `[P]` in the task text | The task can run in parallel with its neighbours |
| `[US1]` in the task text | The task implements that user story |
| `FR-001` mentioned in the task text | The task covers that requirement |

Task ids are `T` plus three or four digits. File paths in a task line (backticked, or any
path-looking token with a known extension) are linked to the task in the code map, and each task is
linked to its story and requirements, so `cairn impact` and `cairn why` can surface the intent
behind code.

The checkboxes are the feature's progress (`done`/`total`). `cairn sync` records it as a progress
fact on the timeline, rewriting the same fact each time, so the board, the brief, `cairn drift` and
`cairn ask` all see current progress — ticking a box in `tasks.md` and running `cairn sync` is all
it takes.

## A complete example

`specs/001-refunds/spec.md`:

```markdown
# Feature Specification: Refunds

**Status**: Draft

### User Story 1 - Refund an order (Priority: P1)

A customer returns an item and the clerk refunds the order.

- **FR-001**: The system MUST refund through the gateway.
- **FR-002**: The system MUST record refunds.

### User Story 2 - Refund history (Priority: P2)

- **FR-003**: The system MUST list past refunds.
```

`specs/001-refunds/tasks.md`:

```markdown
# Tasks: Refunds

## Phase 3: US1

- [x] T001 [US1] Add refund call in `shop/gateway.py` for FR-001
- [ ] T002 [P] [US1] Add refund ledger in `shop/ledger.py` for FR-002
- [ ] T003 [US1] Expose refund in `shop/api.py` for FR-001, FR-002
```

This feature parses as one spec with two stories, three requirements and three tasks, one of them
done — `2` tasks remain of `3`.

## Seeing it in cairn

| Command | What it does |
|---|---|
| `cairn specs [id]` | The spec board: features, stories, requirement coverage and each task's files |
| `cairn status` | Every layer, including the active spec (the first feature with unfinished tasks) |
| `cairn drift [id]` | Where the code disagrees with the specs: missing files, tasks changed after completion, requirements no task covers, stale progress facts |
| `cairn sync` | Re-parses the specs into the read model and records progress facts on the timeline |

The workflow around these files — constitution, planning, analysis, implementation — is described
in [cli.md](cli.md#specs) and [agents.md](agents.md#spec-workflow-commands).
