# Changelog

## 0.2.0 — 2026-09-25

Cairn is now one product. Five engines are built in, and one server serves every project for one
developer or a whole team. Model features work with a Claude Code login and need no key.

### Added

- **Five built-in engines** under `src/cairn/engines/`, each with its full feature set and all
  sharing one model layer, one vector module, one read model, one CLI, one MCP server, one HTTP API
  and one page:
  - **Map** (`cairn graph …`): a code and document knowledge graph. It has 25 tree-sitter grammars
    built in and more as extras, and it can pull in documents, papers and images with a model. It
    offers interactive, call-flow and tree views, a report and a wiki. It exports to HTML, Obsidian,
    SVG, GraphML, Neo4j and FalkorDB. It also shows the impact of pull requests and keeps a
    cross-repository graph.
  - **Specs** (`cairn spec …`): the spec-driven workflow, offline, in `.cairn/workflow/`. It has
    `/cairn.*` commands (or `/cairn-*` skills) for 41 agents, plus extensions, presets, workflows and
    bundles. Project memory is built into the plan, tasks, clarify and implement commands.
  - **Sessions** (`cairn sessions …`): agent session memory. Six Claude Code hooks write to
    `.cairn/sessions.db`. A worker turns those events into observations and session summaries, with
    a model or deterministically without one. Past work is injected at session start and before a
    file is read, and three search tools (search, timeline, full details) let agents look it up.
    Other agents get capture through `cairn sessions integrate`. It includes 13 session skills.
  - **Timeline facts** (`cairn timeline …`): a temporal fact graph with validity windows, built from
    each day's activity. It's embedded at `.cairn/temporal/graph.kuzu` by default, and Neo4j,
    FalkorDB or Neptune can be used instead.
  - **Memory** (`cairn memory …`): self-reconciling memory. With a model, a new memory can be added,
    update or retire an existing one, or be dropped as already known. Every memory keeps its history
    and has a scope (project, team, user or session). Shared Qdrant, pgvector or Chroma stores are
    supported for teams.
- **Memory seeding:** every sync learns decisions from accepted ADRs and answered spec
  clarifications, conventions from contributing and style docs, and gotchas from reverted and
  explained fixes. An edited source updates its memory and a removed source retires it.
- **Model layer:** providers `auto`, `anthropic`, `openai` (any compatible endpoint, including
  local servers) and `claude-code`. `claude-code` uses the signed-in CLI, runs isolated and needs no
  key. Every engine's jobs map to four tiers. Syncs run under a token budget, and every call is
  recorded in the ledger (`cairn models --ledger`).
- **MCP:** a compact core set of 12 tools by default (about 1,600 tokens per turn), including the
  session search tools. `[mcp] tools = "all"` exposes all 61 map, timeline-fact and session
  operations.
- **One server for many projects:** `cairn ui`, `cairn up`, `cairn down` and `cairn serve`. A
  second repository joins the running server. Server state lives in `$CAIRN_HOME`.
- **Team platform:** `cairn serve --team`, and `cairn team`, `cairn token`, `cairn project` and
  `cairn user`. It adds:
  - teams with owner, admin, member and viewer roles, and per-project overrides
  - invite links, and scoped, hashed API tokens
  - git projects cloned on the server and re-synced by push webhooks (GitHub, GitLab, Gitea,
    Bitbucket)
  - an audit log, CSRF protection and login throttling
- **Team access for agents:** agents on other machines use a team server through `/mcp/<project id>/`
  with a bearer token. They get read-only tools when their role can't write.
  `cairn agents connect --server URL --project ID` configures this. It also makes captured sessions
  push to the server (`cairn sessions push`).
- **New page:** an app with no build step and every library vendored, updated live. It has these
  views:
  - Overview: layers, activity, savings and hubs
  - Impact
  - Map: architecture, engine views, wiki and report
  - Specs: with drift and workflow state
  - Sessions, Timeline, Memory and Project settings
  - team pages, server users and your account
- **Token-savings measurement:** every context pack handed to an agent is logged with its size and
  the size of the files behind it. The Overview page compares the two.
- **Streamed explanations:** Impact, Why and a new Ask box stream a model's explanation onto the
  page as it's written (`POST /api/p/<id>/narrate`), with citations as links and a Stop button that
  ends the model call.
- **Cross-repository map:** when you can see more than one repository, the Map opens on all of them
  and shows which repository imports which, with the import lines as evidence
  (`GET /api/repos/map`).
- **Sessions from other machines are attributed:** each pushed session belongs to the member whose
  token sent it, and the Sessions page shows whose machine it came from.

### Changed

- **Map location:** the map lives in `.cairn/graph/`. Old generated copies at the repository root
  are removed on `cairn init`.
- **Incremental map sync:** an unchanged repository skips the map build, a few changed files are
  re-extracted on their own, and anything else takes one incremental pass. Generated and vendored
  code (`*.min.js`, `*.min.css`, `*.bundle.js`, `*.map`, `vendor/`, `node_modules/`, `dist/`) is
  ignored by default through `.cairn/graphignore`.
- **Workflow location:** the spec workflow lives in `.cairn/workflow/`. Older layouts are migrated
  by `cairn init`.
- **Model provider:** `[models] provider` defaults to `auto`. `cairn doctor` reports the provider,
  the local embeddings and the timeline store.
- **Project settings:** new sections `[memory]`, `[temporal]`, `[recall]`, `[map]`, `[mcp]` and
  `[team]`. The server's own settings moved to `$CAIRN_HOME/server.toml`.
- **Uninstall:** `cairn uninstall` also removes the session skills and git hooks under a custom
  `core.hooksPath`.
- **Model spend on background work:** session notes, labels and timeline housekeeping stay on the
  fast tier whatever their input size, judgement tasks move up at most one tier, and the session
  observer bounds each call (`observer_prompt_chars`, `observer_replay_chars`). A session that falls
  far behind is caught up in bigger batches (`observe_catch_up`).
- **One model bill for connected machines:** `cairn agents connect` sets
  `[recall] worker_model = false`, so only the team server spends model calls on pushed sessions.

### Removed

- The separate install of an external spec workflow CLI. The workflow is built in, and the
  installer installs only `cairn-brain`.
- The `deep` install extra. Model features need no extra install.
- `THIRD_PARTY_NOTICES.md`. Notices are in `NOTICE` and licence texts in `licenses/`.

## 0.1.0 — 2026-09-24

First public release. Map, Specs, Timeline, Memory and Sessions behind one command, one MCP
server and one page. Deterministic tier works with no keys; deep tier with a model key.
