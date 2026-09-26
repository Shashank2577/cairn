# Agents

`cairn init` (and `cairn agents install`) detects the agents on your machine and in the repository.
It writes only what each agent supports. Every write is idempotent and reversible:

- Markdown files get a marked block between `<!-- cairn:begin -->` and `<!-- cairn:end -->`.
- JSON configs are merged key by key. A file Cairn can't parse is never overwritten.
- Generated files carry a header comment. A file with the same name that you wrote yourself is left alone.

`cairn uninstall` removes exactly what Cairn added. That covers the marked blocks, its MCP entries
and Claude Code settings, every agent's capture hooks, the generated commands, sub-agents and Cursor
rule, and the session skills. It also removes the Codex server entry and the git hooks, including
hooks under a custom `core.hooksPath`. Entries you added yourself to the same files stay. To choose
agents yourself, pass `--agents claude,codex,cursor,gemini,opencode,copilot,vscode,windsurf,antigravity`.

## What each agent gets

| Agent | Detected by | What Cairn adds |
|---|---|---|
| Claude Code | `claude` on PATH, `~/.claude/`, `.claude/` or `CLAUDE.md` | `.mcp.json` server entry; in `.claude/settings.json`: session capture hooks, the session-start briefing, the status line and `mcp__cairn` in allowed permissions; `/cairn:*` commands in `.claude/commands/cairn/`; sub-agents in `.claude/agents/`; session skills in `.claude/skills/`; a `CLAUDE.md` block |
| Codex | `codex` on PATH or `~/.codex/` | `codex mcp add cairn` (when `codex` is on PATH; this is user-level config in `~/.codex/config.toml`), and a session-start memory hook plus capture hooks in `.codex/hooks.json` |
| Cursor (IDE and `cursor-agent`) | `cursor` or `cursor-agent` on PATH, `~/.cursor/` or `.cursor/` | `.cursor/mcp.json`, an always-on rule `.cursor/rules/cairn.mdc`, and a session-start memory hook plus capture hooks in `.cursor/hooks.json` |
| Gemini CLI | `gemini` on PATH, `~/.gemini/` or `GEMINI.md` | In `.gemini/settings.json`: the server entry, a session-start memory hook and capture hooks; a `GEMINI.md` block |
| OpenCode | `opencode` on PATH, `~/.config/opencode/`, `.opencode/` or `opencode.json` | A plugin, `.opencode/plugins/cairn.js`, that adds the memory to the system prompt and captures sessions |
| Copilot CLI | `copilot` on PATH or `~/.copilot/` | Capture hooks in `.github/hooks/cairn.json`, and the memory in `.github/instructions/cairn-context.instructions.md` (untracked) |
| VS Code / Copilot | `code` on PATH or `.vscode/` | `.vscode/mcp.json`, and the same untracked instructions file |
| Antigravity, Windsurf | `agy` / `windsurf` on PATH, or the app | The memory in an always-on rule, `.agents/rules/cairn-context.md` / `.windsurf/rules/cairn-context.md` (untracked) |
| Any MCP client | always | An `AGENTS.md` block. Point the client at `cairn mcp`. |

Everything is written inside the repository except the Codex MCP entry, which Codex only keeps in
`~/.codex/config.toml`. `cairn init` lists every file it wrote outside the repository, and reminds you to
approve Cairn's hooks in Codex.

The MCP entry runs `cairn mcp` when `cairn` is on your PATH, and `python -m cairn mcp` otherwise.
The instruction block tells agents to call `cairn_context` or `cairn_impact` before editing,
`cairn_why` before guessing at intent, and `cairn_remember` for durable learnings. It also says to
treat `[EXTRACTED]` evidence as fact and `[INFERRED]` evidence as a lead to verify.

`cairn doctor` shows which agents are wired and which are detected but not wired yet, and for each
one how memory reaches it at session start and whether its sessions are captured.

## Memory at session start

Every agent starts a session knowing the same two things: the Cairn brief (map hubs, the active spec,
constitution principles, key conventions, decisions and gotchas) and the session-memory timeline
(recent observations and summaries from every agent's sessions in this repository). How it gets
there depends on what the agent supports:

| Agent | How memory arrives |
|---|---|
| Claude Code | Two `SessionStart` hooks: the brief (`cairn hook session-start`) and the timeline (`cairn.capture context`) |
| Gemini CLI | A `SessionStart` hook in `.gemini/settings.json`, returned as `additionalContext` (also under `gemini -p`) |
| Codex | A `SessionStart` hook in `.codex/hooks.json`, returned as `additionalContext`. Codex runs a hook only once the project is trusted and you have approved the hook in `/hooks`, so approve Cairn's hooks once there. `cairn doctor` shows whether they are approved; `codex exec` skips hooks that aren't. |
| Cursor (IDE and `cursor-agent`) | A `sessionStart` hook in `.cursor/hooks.json`, returned as `additional_context` (also under `cursor-agent -p`) |
| OpenCode | The plugin adds it to the system prompt of every request, fetched once per session |
| Copilot CLI, VS Code / Copilot | `.github/instructions/cairn-context.instructions.md` (`applyTo: "**"`). Copilot CLI 1.0 ignores what its hooks print, so an instructions file is the only way in. |
| Antigravity, Windsurf | An always-on rule in `.agents/rules/` / `.windsurf/rules/` |

The hooks compute the memory when the session starts, for the repository the agent runs in. The
instruction files are this clone's own: each path is added to `.git/info/exclude`, so it is never
committed and `.gitignore` is untouched. Cairn rewrites them after each session summary, on
`cairn sync` and when a memory is stored, and only when the content changed. `AGENTS.md`,
`CLAUDE.md` and `GEMINI.md` only ever hold the static instructions block.

In those files the memory sits between `<!-- cairn:memory:begin -->` and `<!-- cairn:memory:end -->`.
They are comments rather than a tag because Copilot CLI removes unknown tags from instruction files
along with their content. The block is also left out of captured tool output, so memory is never
built out of memory.

## Claude Code extras

**Session-start briefing.** A `SessionStart` hook runs `cairn hook session-start`. It injects a
compact project brief of about 550 tokens: map size and hubs, the active spec, constitution
principles, key conventions, decisions and gotchas, open drift and recent agent work. Each briefing
is logged as cost in the `queries` table.

**Status line.** `cairn statusline` prints one line: files, memories, the active spec's progress,
drift findings and the current model. Set `NO_COLOR` for plain text.

**Commands** (`.claude/commands/cairn/`):

| Command | What it does |
|---|---|
| `/cairn:impact <target>` | Risk, dependents and tests to re-run, co-changing files, warnings, the owning task, and a checklist for making the change |
| `/cairn:why <target>` | Intent, key decisions and what not to break, with citations |
| `/cairn:ask <question>` | Answers from `cairn_context` evidence |
| `/cairn:drift [spec]` | Drift findings grouped by severity, confirmed or dismissed, and the smallest fix |
| `/cairn:remember <learning>` | Rewrites the learning as one sentence, picks a kind and stores it (superseding when needed) |
| `/cairn:brief` | The project briefing and the two most useful next actions |

**Sub-agents** (`.claude/agents/`), each on a model tier that fits its job:

| Sub-agent | Model | Role |
|---|---|---|
| `cairn-scout` | haiku | Gathers impact, rationale, conventions and past work before an edit. Never edits. |
| `cairn-implementer` | sonnet | Implements one spec task with memory in context. Safe to run in parallel for `[P]` tasks. |
| `cairn-architect` | opus | Reviews plans and cross-cutting changes against the constitution, hubs, impact and past incidents. |
| `cairn-reviewer` | opus | Reviews a diff against specs, history and conventions before merge. |

For a spec's `[P]` tasks, use `cairn-scout` first to confirm the tasks don't touch the same files.
Then run one `cairn-implementer` per task and finish with `cairn-reviewer`.

## Session capture

Session memory is built in ([ADR-0006](adr/0006-built-in-session-capture.md)). For Claude Code,
`cairn init` adds these hooks to `.claude/settings.json`. Each one runs
`"<python>" -m cairn.capture --platform claude-code <event>`:

| Claude Code event | Cairn event | What it does |
|---|---|---|
| `SessionStart` (startup, resume, clear, compact) | `context` | Injects a timeline of recent observations and session summaries (or a first-run hint) |
| `UserPromptSubmit` | `session-init` | Stores the numbered prompt |
| `PreToolUse` (Read) | `file-context` | Adds the earlier observations about a file before the agent reads it |
| `PostToolUse` (all tools) | `observation` | Queues the tool event |
| `Stop` | `summarize` | Queues a summary of the prompt that just finished, and starts the worker |
| `SessionEnd` | `session-end` | Marks the session completed |

The hooks use only the standard library, and all they do is write to `.cairn/sessions.db`. A hook
never fails the agent: bad input or a locked store is ignored. The older event names
(`python -m cairn.capture prompt|tool|stop`) still work.

A worker drains the queue. With `[recall] worker_spawn = true` (the default), the `Stop` and
`SessionEnd` hooks start it in the background and the local server hosts one per project, so
observations are written after each agent turn. With a model, that spends your provider's quota
(or your Claude Code plan). Set `worker_spawn = false` to only queue events. You can then process
them when you choose with `cairn sessions worker` (add `--no-model` for derived records only). With a model, the worker
writes **observations** (type, title, narrative, facts, concepts, files read and modified) and
**session summaries** (request, investigated, learned, completed, next steps). The default `code`
mode uses these observation types: bugfix, feature, refactor, change, discovery, decision,
security_alert, security_note and sensitive. Without a model, or after `[recall] max_retries`
failures, the worker derives records deterministically from the tool events, so capture is never
empty.

Observations are searchable by full text and by meaning. Agents reach them through the
`cairn_session_*` tools (search, then timeline, then full details) and see them at session start.
Each sync mirrors them into the read model as `obs:` and `session:` entities, linked to the files
they read and modified. That's how `cairn impact` and `cairn why` show recent agent work on the code.

**Privacy.** Anything inside `<private>…</private>` is never stored. Values that look like
credentials (`API_KEY=…`, `--password …`, `Bearer …`) are masked before they're written. Cairn's
own model calls (which run with `CAIRN_INTERNAL=1`) are never recorded. Everything stays in the
repository's `.cairn/` folder.

**Skills.** `cairn init` also copies 13 session skills into `.claude/skills/`:

| Skill | Use it to |
|---|---|
| `cairn-recall-search` | Search earlier sessions ("did we already solve this?") |
| `cairn-smart-explore` | Explore code structurally with tree-sitter views instead of reading whole files |
| `cairn-knowledge-agent` | Build and query focused knowledge bases from session observations |
| `cairn-learn-codebase` | Prime a session by reading every source file |
| `cairn-how-it-works` | Explain how session memory captures, injects and stores data |
| `cairn-timeline-report` | Write a narrative report of the project's history from the session timeline |
| `cairn-weekly-digests` | Write a week-by-week digest of the session timeline |
| `cairn-standup` | Compare worktrees, branches or PRs and produce one consolidation plan |
| `cairn-make-plan` | Write a phased implementation plan |
| `cairn-do` | Carry out a phased plan with sub-agents |
| `cairn-mode-creator` | Create and activate a custom observation mode |
| `cairn-pathfinder` | Map features into flowcharts and propose a unified architecture |
| `cairn-what-the` | Explain something technical in plain English |

`cairn sessions skills list|install|uninstall` manages them.

**Other agents.** `cairn init` adds capture for the other agents it detects, in the repository:

| Agent | Where | Events | Notes |
|---|---|---|---|
| Gemini CLI | `.gemini/settings.json` | `SessionStart`, `BeforeAgent`, `AfterTool`, `AfterAgent`, `SessionEnd` | Runs under `gemini -p` too |
| Codex | `.codex/hooks.json` | `UserPromptSubmit`, `PostToolUse`, `Stop` | Codex runs them once the project is trusted and you approve each hook in `/hooks` |
| Cursor | `.cursor/hooks.json` | `beforeSubmitPrompt`, `postToolUse`, `stop`, `sessionEnd` | `beforeSubmitPrompt` and `stop` fire only in interactive sessions. Cursor also runs Claude Code's hooks; Cairn ignores those runs so nothing is recorded twice. |
| OpenCode | `.opencode/plugins/cairn.js` | user messages, tool runs, idle (summary), deleted sessions | Loaded automatically |
| Copilot CLI | `.github/hooks/cairn.json` | `userPromptSubmitted`, `postToolUse`, `agentStop`, `sessionEnd` | Copilot runs repository hooks only in interactive sessions of a trusted folder |

`cairn sessions integrate install <agent>` installs the user-level variants (and windsurf and
antigravity hooks). For copilot-cli, roo-code, warp and goose it adds the MCP server only.
`cairn sessions integrate status <agent>` shows what's installed.

**Settings.** Capture is controlled by the `[recall]` section in `.cairn/config.toml` (see
[configuration](configuration.md#recall)). To turn it off, set `[recall] capture = false` or
`[sessions] capture = false`, or run `cairn init --no-capture`.

## Spec workflow commands

`cairn init` adds the spec workflow for the first agent it detects, using the workflow's
integration for that agent (Claude Code by default). The workflow supports 41 agents. To add
another one, run `cairn spec integration install <key>`, and use `cairn spec integration list` to
see the keys. Its templates, scripts and constitution live in `.cairn/workflow/`, and features
live in `specs/NNN-name/`.

| Command | Stage |
|---|---|
| `/cairn.constitution` | The project's principles |
| `/cairn.specify` | A feature spec: user stories, requirements, success criteria |
| `/cairn.clarify` | Up to five targeted questions, with answers recorded in the spec |
| `/cairn.plan` | Technical plan and design artifacts |
| `/cairn.tasks` | Dependency-ordered tasks, each naming its files |
| `/cairn.analyze` | Cross-artifact consistency check |
| `/cairn.checklist` | A requirements-quality checklist |
| `/cairn.implement` | Carry out the tasks |
| `/cairn.converge` | Find what's left and append it as tasks |
| `/cairn.taskstoissues` | Turn tasks into GitHub issues |

Agents that load skills get the same commands as skills, for example `/cairn-specify`. Claude
Code's integration installs skills by default.

Cairn's memory steps are built into four of these commands:

- `/cairn.plan` grounds the plan in `cairn_context`, `cairn_impact` for the main change points,
  and `cairn_recall`.
- `/cairn.tasks` re-syncs and runs `cairn impact` on every file the tasks name.
- `/cairn.clarify` picks the answers that are durable decisions and, if you agree, checks
  `cairn recall` for duplicates and stores each one with `cairn remember --kind decision`.
- `/cairn.implement` re-syncs, runs `cairn drift` until it's clean or every finding is explained,
  and records one to three learnings.

## Using a team server

`cairn agents connect --server https://cairn.example.com --project <project id>` switches this
repository's agents from the local `cairn mcp` process to a team server's `/mcp/<project id>/`
endpoint. It also writes `[team] server` and `[team] project`, so captured sessions are pushed to
the server. The configs reference `${CAIRN_TOKEN}` (or the variable you name with `--env-var`), so
you can commit them. Each developer creates a token and exports it. See [teams](teams.md).
