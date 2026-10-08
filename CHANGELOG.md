# Changelog

## 0.2.2 — 2026-10-08

### Added

- **One install for every agent:** `cairn global install` wires Claude Code, Codex, Cursor, Gemini
  CLI, OpenCode, Copilot CLI and Antigravity at user level, once: Cairn's MCP tools, memory at session
  start and session capture. In a git repository without Cairn the agent gets a one-line offer to run
  `cairn init --no-deep` and nothing is written until you say yes. `cairn global remove` undoes it;
  `cairn global status` shows each agent.
- **Session-start panel:** Claude Code shows the person what Cairn remembers when a session opens:
  where the last working session left off (asked, done, next), what is new since then, what the team
  knows (each memory with its source and age), files to handle with care (fix and revert history) and
  open work. Built from the local store with no model calls.
- **Ambient context:** on each Claude Code prompt that plausibly touches code, a context nugget of at
  most 500 tokens (targets and memories). On by default; `[context] ambient = false` turns it off.
- **Review context:** `cairn review-context --files a,b` builds a reviewer's pack: the ticket from the
  branch and commit trailers, local impact, and hits in sibling repositories. No model calls.
- **System layer:** `system.yaml` groups repositories into one product; `cairn system` lists them and
  sibling stores are read read-only.
- **Recap:** `cairn recap` shows what Cairn has done for a repository, with receipts: memories learned,
  commits and sessions watched, drift, and what each answer cost against reading the files.
- **Ask:** answers close gaps from the documents they cite, every question is logged, related earlier
  questions are surfaced, and each answered question becomes recallable knowledge.
- **Team server hosting:** `deploy/` adds a Dockerfile (team mode only) and docker-compose;
  `docs/multi-repo.md` describes the multi-repository model.

### Changed

- MCP tool descriptions say when to call each tool and what it costs.
- Memory seeding stops after `memory.wall_seconds` (default 300); remaining seeds continue next sync.

### Fixed

- **Hooks:** agent hooks never fail on a busy store; a session-start hook no longer waits behind a
  running sync and errors with "database is locked".
- **MCP server:** creates nothing in a repository where Cairn is not set up; it answers with the setup
  offer instead, and `cairn init` mid-session works without restarting the agent.
- **Worktrees:** a linked worktree reads and records with its main checkout's memory, and capture never
  climbs above the repository root or treats the home folder as a project.
- **Injected context** names tools `cairn mcp` actually serves; the setup offer also appears when an
  agent starts in a sub-folder; "Handle with care" lists source files, not tests.
- **Recall worker:** re-enabled, so captured sessions become observations and summaries again;
  `.cairn/config.local.toml` overrides recall settings like every other setting.
- **Doctor** reports capture honestly (wired, nothing yet, or N observations) and flags a queue the
  worker never drains.
- **Windows:** file locks, paths, bash and claude.cmd resolution, UTF-8 input and output, and
  worker-lock detection; CI is green on Windows, macOS and Linux for Python 3.11 to 3.13.
- **Version:** `cairn --version` reports the installed version instead of a hardcoded one.

## 0.2.1 — 2026-10-02

### Added

- **Standup:** `cairn standup` prints what happened over the last 24 hours (`--days` to go further
  back), read entirely from the local store: commits with their requirement and work-item trailers,
  the requirements they touch, memories, timeline facts, agent sessions and drift. No model calls.
- **Status tool:** the MCP core set gains `cairn_status` — project health in one call: layer
  counts, active spec progress, drift findings, last sync and model availability.
- **Process dialect:** repositories that run their own spec process instead of the bundled workflow
  (foundry-program style) are read as spec boards: the requirement table in
  `requirements/index.md`, machine-checkable criteria in `requirements/coverage.yaml`, and work
  items from `story/FDY-*` / `bug/FDY-*` branches and `Work-Item:` commit trailers.
- **Deep-tier budget:** the timeline engine stops after `deep.wall_seconds` (default 300) and the
  remaining days fill in on later syncs; a model call that overruns the token budget inside one
  episode now ends the run cleanly instead of failing the whole sync.

### Changed

- **Sync progress:** every sync step reports a line when it starts, so a long sync shows where it
  is instead of going quiet.

### Fixed

- **Install:** the quick start and the installer install from GitHub with `--python 3.12` (PyPI
  publishing is pending, and kuzu has no 3.14 wheels on every platform), and the installer reports
  download and PATH failures instead of continuing silently.
- **Python support:** `requires-python` is capped at `<3.14` until kuzu ships 3.14 wheels for every
  platform. CI now tests 3.11, 3.12 and 3.13.
- **Windows sync lock:** the cross-process sync lock works on Windows (an `msvcrt` byte-range lock
  stands in for `flock`), so concurrent syncs cannot interleave there.
- **History rewrites:** after an amend or rebase moves the cursor, the history engine drops the
  previous generation and rebuilds, so rows for commits that no longer exist are not left behind.
- History rewrite no longer corrupts its stats.
- `cairn init --no-deep` and deep-tier setup are hardened.
- The Claude Code status line is guarded against missing or broken state.
- `cairn timeline` and `cairn impact` resolve targets the same way.
- `cairn spec init` has a smoother first-run UX and a documented format.
- YAML files are included in the map.

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
