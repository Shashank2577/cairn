# Contributing

```sh
git clone https://github.com/Shashank2577/cairn && cd cairn
uv venv && uv pip install -e '.[dev]'
pytest                      # offline: no network, no model calls
ruff check src tests
cairn                       # dogfood: Cairn on its own repository
```

## Layout

| Path | What lives there |
|---|---|
| `src/cairn/engines/graph/` | Map: code and document knowledge graph (tree-sitter extractors, clustering, views, wiki, exports, PR impact) |
| `src/cairn/engines/workflow/` | Specs: the spec-driven workflow (commands, templates, scripts, 41 agent integrations, extensions, presets, workflows, bundles) |
| `src/cairn/engines/recall/` | Sessions: capture hooks, queue, observer and worker, search, session-start context, skills |
| `src/cairn/engines/temporal/` | Timeline facts: the temporal fact graph and its drivers (embedded Kuzu, Neo4j, FalkorDB, Neptune) |
| `src/cairn/engines/memstore/` | Memory: the self-reconciling memory engine and its vector stores |
| `src/cairn/engines/*.py` | Adapters that mirror each engine into the read model (`mapper`, `specs`, `history`, `chronicle`, `journal`, `memory`) and the shared embeddings (`vectors`) |
| `src/cairn/project.py`, `store.py`, `router.py` | Project config, the read model, the model layer |
| `src/cairn/core.py`, `sync.py`, `linker.py`, `drift.py`, `graphviews.py` | Answers (impact, why, context, ask, brief), sync, cross-layer links, drift, map views |
| `src/cairn/cli.py`, `mcp_server.py`, `server.py`, `daemon.py`, `ui/` | Surfaces: the CLI, the MCP server, the HTTP API and hub, the background server, the page |
| `src/cairn/agents.py`, `hooks.py`, `capture.py`, `templates/` | Agent wiring, git and agent hooks, the capture entry point, generated agent files |
| `src/cairn/platform/` | Teams, roles, tokens, projects, webhooks, audit |
| `tests/test_core.py`, `tests/test_surfaces.py`, `tests/engines/` | Core, surfaces, and each engine's own suite (`test_<engine>_*.py`) |
| `docs/`, `docs/adr/`, `specs/` | Documentation, architecture decisions, this project's own specs |

The engines are built in and owned by Cairn ([ADR-0005](docs/adr/0005-invisible-engines.md)). An
engine may use `cairn.project`, `cairn.store`, `cairn.router` and `cairn.engines.vectors`.
Anything a user or agent sees is added in the shared surfaces: a command in `cli.py`, a tool in
`mcp_server.py`, a route in `server.py` or a view in `ui/`. Changes to those surfaces, and to
`core.py`, `store.py`, `sync.py` and `router.py`, touch every engine. Review them with that in mind.

## Running tests

- `pytest` runs everything. `tests/conftest.py` builds throwaway git repositories and isolates
  `$CAIRN_HOME`. It forces the hashing embedder (`CAIRN_EMBEDDER=hash`) and blocks model calls
  (`CAIRN_NO_CLI_MODELS=1`, empty API keys), so tests never touch the network or your plan.
- `pytest tests/engines -k recall` runs one engine's suite. Shared helpers live in
  `tests/engines/*_support.py` and `workflow_helpers.py`.
- Some optional dependencies (server backends, extra languages) are only exercised when their
  extras are installed.
- `node tests/ui/smoke.mjs --base http://127.0.0.1:4747` (or `--mock` for the sample data, no server) visits every UI page in headless Chrome at 1440 and 390 px and fails on console errors, failed requests or sideways overflow; it skips without Chrome or Node 22+.

## How we work

- Non-trivial changes follow the spec workflow: `/cairn.specify`, `/cairn.clarify`, `/cairn.plan`, `/cairn.tasks`, `/cairn.analyze`, `/cairn.implement`, then `/cairn.converge` (in Claude Code, the `/cairn-*` skills).
- Architecture decisions get an ADR in `docs/adr/`, and the constitution in `.cairn/workflow/memory/constitution.md` breaks ties.
- A task in `tasks.md` names the files that implement it with full repository paths, because drift checks that every file a done task names exists.
- A feature ships with its CLI command, MCP tool or page view where relevant, its tests and its docs.

## Rules

- Never call a model in a deterministic path: impact, why, context packs, drift checks, map builds, git history, spec parsing and session capture must work with no model at all.
- Every model call goes through `cairn.router` with a task name that is listed in `TASK_TIER`, so it is tiered, budgeted and recorded in the ledger.
- Adding an MCP tool to the core set needs a strong reason, because every tool's schema costs tokens on every agent turn; put power operations behind `[mcp] tools = "all"`.
- Every file Cairn generates in a user's repository must be idempotent to write and removable by `cairn uninstall`.
- Capture hooks stay standard-library only and never fail or slow the agent.
- UI and CLI copy uses sentence case and plain verbs, and names things by the five layers (Map, Specs, Timeline, Memory, Sessions).

## Naming rules

- Cairn has one vocabulary: code, commands, tool names, environment variables, file names, UI copy and docs use Cairn's names only, never the names of the open-source projects the engines came from.
- Environment variables start with `CAIRN_`, and on-disk state lives under `.cairn/` or `$CAIRN_HOME`.
- The naming tests enforce this for every engine (`tests/engines/test_*_naming.py`, plus the naming checks in `tests/engines/test_memstore.py` and `tests/engines/test_workflow_bridge.py`); keep them passing.

## Licences and notices

Cairn is Apache-2.0 ([LICENSE](LICENSE)). When you bring in code derived from another project, or
bundle a library:

- Add its copyright notice and required attribution to [NOTICE](NOTICE).
- Put its full licence text in [licenses/](licenses/) (`<component>-<LICENCE>.txt`). Vendored
  browser libraries (`src/cairn/ui/vendor/`, `src/cairn/engines/graph/assets/`) are listed in NOTICE
  with their version and licence.
- Nowhere else. Notices don't go in source headers, docs, commands or the UI.
