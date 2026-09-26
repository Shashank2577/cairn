# CLI reference

Run `cairn <command> --help` for every option. Most query commands accept `--json`. Exit codes:
`0` ok, `1` not set up or failed, `2` usage error, `3` not in a project.

Wherever a command takes a **target**, it accepts a path (`src/pay/service.py`), a symbol
(`PaymentService`), a member (`PaymentService.process`) or a read-model id
(`task:002-refunds/T004`).

## Setup and health

| Command | What it does |
|---|---|
| `cairn` | On first run in a repository, full setup (like `cairn init`). After that, status. |
| `cairn init [--agents claude,codex,cursor,gemini,vscode] [--no-hooks] [--no-ui] [--no-capture] [--no-specs]` | Idempotent setup that never prompts. Without `--agents`, it wires the agents it detects. |
| `cairn status [--json]` | Every layer, the active spec, drift findings and the page's address. |
| `cairn doctor` | Every capability, its state and the exact command that enables it. |
| `cairn sync [--deep/--no-deep] [--budget N] [--no-map-rebuild] [-q] [--json]` | Incremental refresh of every layer. `--deep` forces timeline facts on (it needs a model) and `--no-deep` skips them. `--budget` caps their tokens. |
| `cairn uninstall [--purge]` | Removes agent wiring, session skills, git hooks and the project's registration with the local server. `--purge` also deletes `.cairn/`. Spec files are kept. |

## Questions

| Command | What it does |
|---|---|
| `cairn impact <target> [--depth 2] [--budget 1800] [--explain] [--json]` | What breaks if this changes. `--explain` adds a model-written summary. |
| `cairn why <target> [--budget 1800] [--explain] [--json]` | Why the code is the way it is. |
| `cairn ask "<question>" [--budget 1800] [--no-llm] [--json]` | An evidence pack, plus a narrated answer when a model is available. |
| `cairn search <query> [--kinds symbol,file,spec,task,req,commit,memory,obs,fact] [--limit 20] [--json]` | Search every layer. |
| `cairn brief` | The compact briefing agents receive at session start. |

## Map

| Command | What it does |
|---|---|
| `cairn trace path <a> <b>` | Shortest path between two things in the map. |
| `cairn trace explain <x>` | A node and its neighbourhood. |
| `cairn trace query "<question>" [--budget 1500]` | A scoped subgraph for a plain-language question. |
| `cairn hubs [--top 12]` | The most-connected code. |
| `cairn areas [--top 20]` | Subsystems (communities) detected in the map. |
| `cairn prs [args]` | Open pull requests with their map impact. Needs the GitHub CLI (`gh`). |
| `cairn graph <command> …` | The full code and document graph engine. See below. |

`cairn graph --help` lists the engine's commands:

- **Build:** `update [path]`, `extract <path>` (code by AST; docs, papers and images with a model;
  `--code-only`, `--postgres DSN`, `--cargo`, `--global`), `cluster-only`, `label`, `watch`,
  `check-update`, `add <url>`, `clone <github-url>`.
- **Ask:** `query "<question>"`, `path "A" "B"`, `explain "X"`, `affected "X"`, `god-nodes`,
  `diagnose multigraph`, `benchmark`, `prs`.
- **Remember:** `save-result` and `reflect` (a feedback loop stored in `.cairn/graph/memory/`).
- **Views and exports:** `tree`, `export html`, `export callflow-html`, `export obsidian`,
  `export wiki`, `export svg`, `export graphml`, `export neo4j`, `export falkordb`.
- **Many repositories:** `merge-graphs`, and `global add|remove|list|path` (a cross-repository graph
  under `$CAIRN_HOME/graph/`).
- **Plumbing:** `hook`, `merge-driver`, `merge-chunks`, `merge-semantic`, `cache-check`, `provider`.

Extra inputs and languages need extras, for example `pip install 'cairn-brain[pdf,office,sql]'`.
The full list is in `pyproject.toml`.

## Specs

| Command | What it does |
|---|---|
| `cairn specs [id] [--json]` | The spec board: features, stories, task progress and each task's files. |
| `cairn spec new "<description>"` | Create the next `specs/NNN-name/` folder with a spec from the template. |
| `cairn drift [id] [--deep] [--json]` | Where the code disagrees with its specs. `--deep` asks a model to judge up to five recently changed requirements per spec. |
| `cairn spec <command> …` | The spec workflow engine. See below. |

`cairn spec` subcommands:

| Command | What it does |
|---|---|
| `init [name] [--here] [--integration <key>] [--preset <id>] [--extension <x>] [--non-interactive] [--force]` | Scaffold the workflow. `cairn init` runs this for you. |
| `integration list|install|uninstall|switch|upgrade|status|use|search|info|scaffold|catalog` | Agent integrations. 41 agents are supported; `cairn spec integration list` shows them. |
| `extension add|remove|list|enable|disable|info|search|set-priority|update|catalog` | Workflow extensions (for example `git`, `bug`, `assess`, `agent-context`). |
| `preset list|add|remove|update|search|resolve|info|set-priority|enable|disable|catalog` | Template presets (for example `lean`, `constitution-sync`). |
| `workflow run|resume|status|list|add|remove|update|enable|disable|search|info|resolve|catalog|step|overlay` | Automation workflows and their runs. |
| `bundle search|info|list|install|add|update|remove|validate|build|init|catalog` | Bundles of extensions, presets and workflows. |
| `artifact …` | Introspect the commands, templates, scripts and hooks the workflow exposes. |
| `event …` | Manage and run event-driven commands. |
| `check`, `version [--features] [--json]` | Tooling check and version information. |

In your agent, the workflow is a set of commands: `/cairn.constitution`, `/cairn.specify`,
`/cairn.clarify`, `/cairn.plan`, `/cairn.tasks`, `/cairn.analyze`, `/cairn.checklist`,
`/cairn.implement`, `/cairn.converge` and `/cairn.taskstoissues`. Agents that use skills, such as
Claude Code, get them as `/cairn-specify` and so on. See [agents](agents.md#spec-workflow-commands).

## Memory

| Command | What it does |
|---|---|
| `cairn remember "<text>" [--kind convention|decision|gotcha|preference|fact] [--supersedes <id>]` | Store a durable learning. With a model available, it's reconciled against what's already known. |
| `cairn recall <query> [--limit 10] [--json]` | What the team knows about a topic. |
| `cairn memories [--all]` | List memories. `--all` includes superseded and forgotten ones. |
| `cairn forget <id>` | Soft-delete a memory. It's also removed from semantic recall. |

`cairn memory` gives direct access to the memory engine. Scoped commands take
`--scope project|team|user|session` (default `project`) and `--scope-id`.

| Command | What it does |
|---|---|
| `memory add [text] [--messages JSON] [--file F] [--metadata JSON] [--infer/--no-infer] [--expires YYYY-MM-DD] [--instructions T]` | Add memories from text or a conversation. |
| `memory search <query> [--top-k 10] [--threshold 0.1] [--rerank] [--keyword] [--filter JSON] [--show-expired] [--explain]` | Semantic, keyword and entity search with filters. |
| `memory list [--page 1] [--page-size 100] [--show-expired]` | Every memory in a scope. |
| `memory get <id>` / `memory update <id> [text] [--metadata] [--expires] [--clear-expiry]` | Read or change one memory. Every change is kept in its history. |
| `memory delete [id] [--all] [--dry-run]` | Delete one memory, or every memory in a scope. |
| `memory history <id>` | Every ADD, UPDATE and DELETE for a memory. Takes a read-model or an engine id. |
| `memory import <path> [--infer]` / `memory entities` | Import a JSON export; list who and what memories are scoped to. |
| `memory seed` | Learn from the repository now: ADRs, clarifications, conventions and gotchas. Sync does this too. |

## Timeline

| Command | What it does |
|---|---|
| `cairn timeline [--target file:<path>|spec:<id>] [--days 30] [--limit 40]` | What happened: commits, spec progress, sessions, memories, facts and drift. |
| `cairn timeline search <q> [--limit 10] [--current] [--at ISO-DATE] [--types a,b] [--json]` | Facts with their validity windows. `--at` answers "as of that date". |
| `cairn timeline entities <q> [--type T]` / `show <uuid>` | Entities and single facts. |
| `cairn timeline episodes [--last 10]` / `communities [--build]` / `saga [name] [--summarize]` | Episodes, clusters and running summaries. |
| `cairn timeline add <text|@file> [--at ISO] [--saga NAME] [--source text|json|message]` | Add an episode by hand. Needs a model. |
| `cairn timeline ingest [--budget 150000]` | Build facts from repository activity now. This is what sync runs. |
| `cairn timeline status` / `resync` / `reembed` | Store health; rebuild the read-model mirror; recompute vectors. |
| `cairn timeline delete <uuid> [--episode]` / `clear [--all] [--yes]` | Remove facts or episodes. |

The search, browse, add and clear subcommands also take `--group` to choose a namespace other than
the project's.

## Sessions

`cairn sessions` with no arguments lists recent agent sessions. Its subcommands:

| Command | What it does |
|---|---|
| `sessions search [query] [--type] [--obs-type] [--since] [--until] [--order relevance|date_desc|date_asc] [--limit 20] [--json]` | Search observations, summaries and prompts. |
| `sessions timeline [--anchor ID] [--query Q] [--before 5] [--after 5]` | What happened around an observation, session or time. |
| `sessions get <id…>` / `sessions tool-uses <id…>` | Full observations; raw tool input and output. |
| `sessions session <id>` / `sessions sessions [--limit 20]` | One session in full; recent sessions. |
| `sessions context [--full] [--colors]` | Print the context agents receive at session start. |
| `sessions worker [--loop] [--no-model]` / `status` / `queue [--retry-failed] [--clear-failed] [--clear-all]` / `logs` | The worker that turns queued events into observations. |
| `sessions settings [key=value …]` / `modes` | Show or change `[recall]` settings; list observation modes. |
| `sessions remember "<text>" [--title T]` / `export --out F [query]` / `import <file>` / `reindex` | Manual records, export and import, semantic index. |
| `sessions integrate install|uninstall|status <agent>` | Session capture for other agents: cursor, codex, windsurf, opencode, antigravity, gemini-cli, copilot-cli, roo-code, warp, goose. |
| `sessions push [--status] [--batch N] [--json]` | Send captured sessions to the team server named by `[team] server` and `[team] project`, using the token in `[team] token_env` (default `CAIRN_TOKEN`). The hooks do this in the background. See [teams](teams.md#session-capture-from-other-machines). |
| `sessions skills list|install|uninstall [dest]` | The bundled session skills (installed into `.claude/skills/` by `cairn init`). |
| `sessions transcripts …` / `claude-md generate|clean` / `adopt …` / `corpus …` | Transcript replay, folder context files, merged-worktree adoption, knowledge corpora. |

To point at another repository, put `--root <path>` before the subcommand:
`cairn sessions --root ../api search "retry"`.

## Server and agents

| Command | What it does |
|---|---|
| `cairn ui [--no-open]` | Register this repository, start the local server if needed and open the page. |
| `cairn up` / `cairn down` | Start the local server in the background, or stop it. One server serves every registered repository. |
| `cairn serve [--host H] [--port P] [--team]` | Run the server in the foreground. `--team` requires sign-in. A host other than loopback needs team mode. |
| `cairn mcp` | The MCP server on stdio. Agents launch this. |
| `cairn agents install [--agents a,b]` | Wire agents to Cairn (idempotent). |
| `cairn agents connect --server URL --project ID [--env-var CAIRN_TOKEN]` | Point this repository's agents at a team server's MCP endpoint, and send captured sessions there (`[team]` in `.cairn/config.toml`). |
| `cairn agents list` | Detected agents. |
| `cairn models [--ledger]` | Which model does which job, and the calls and tokens per tier. |

## Team platform

These commands act as the machine's operator: whoever can read `$CAIRN_HOME/platform.db`. Role
checks don't apply to them, but every change is written to the audit log. `--team` takes a team id
or slug (except in `team init`, where it names the new team). Without it, the command uses the only
team there is, or your Personal team.

| Command | What it does |
|---|---|
| `cairn team init --email E --name N [--team T] [--set-password]` | Create the first owner, a server admin, for team mode. Prints a one-time password. |
| `cairn team list` / `create <name> [--owner E] [--slug S]` / `delete <team> --yes` | Teams. |
| `cairn team members` / `invite <email> [--role viewer|member|admin|owner] [--days N]` / `role <email> <role>` / `remove <email>` | Members and invitations. `invite` prints a one-time link. |
| `cairn token issue --name N [--user E] [--project P] [--scope read|agent|ci|all|<action>] [--expires-days N]` | Issue an API token. The secret is printed once. |
| `cairn token list [--user E] [--revoked]` / `revoke <id|prefix|token>` | List or revoke tokens. |
| `cairn project add <folder|git-url> [--branch B] [--name N] [--slug S]` | Register a local folder, or clone a git URL under `$CAIRN_HOME/repos`. The first sync starts from the page (Run the first sync) or with `cairn sync` in the project folder. |
| `cairn project list [--json]` / `remove <ref> [--keep-files]` / `refresh <ref>` | List, unregister or pull a project. `--json` shows project ids. `refresh` only pulls; start the sync from the page or with `cairn sync` in the clone. |
| `cairn project webhook <ref>` | Create or rotate the push webhook secret. Prints the payload URL and the secret once. |
| `cairn user list` / `disable <email>` / `enable <email>` / `reset-password <email>` | Server accounts. |

See [teams](teams.md) for how these fit together.
