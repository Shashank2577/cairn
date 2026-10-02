# Cairn

**Institutional memory for coding agents.** Run one command in a repository. You and your agents get
a map of the code, the intent behind it, what happened to it, what the team learned and what earlier
agent sessions did. All of it comes through one CLI, one MCP server and one page.

```sh
uv tool install --python 3.12 cairn-brain
cd your-repo
cairn
```

## Why

Coding agents are capable, but they forget everything between sessions. They re-read the same files,
miss the caller three hops away, re-apply the change that was reverted last spring and never hear
about the convention the team agreed on in review. Cairn keeps that context for them, and every
commit, spec and session adds to it.

## Install

```sh
uv tool install --python 3.12 cairn-brain
# or: pipx install --python 3.12 cairn-brain
```

The `--python 3.12` pin is required: `kuzu` ships no macOS/Windows wheels for 3.14 yet. You need Python 3.11, 3.12 or
3.13 and git. You don't need Docker, a database server or an API key.
The first sync downloads a small local embedding model (about 70 MB) once. Without network access,
Cairn uses a built-in hashing embedder instead, so search still works, only less semantically.

## Start

```sh
cd your-repo
cairn
```

The first run sets the repository up without asking any questions:

- It adds the spec workflow (`.cairn/workflow/` and the `/cairn.*` agent commands).
- It wires every agent it detects to the `cairn` MCP server and adds a Cairn block to `AGENTS.md`.
  Claude Code also gets session capture hooks, a session-start briefing, a status line, `/cairn:*`
  commands, four sub-agents and the session skills.
- It installs git hooks (post-commit, post-merge, post-checkout, post-rewrite) that re-sync in the
  background, so a commit never waits on Cairn.
- It runs the first sync, starts the local server and prints the page's address.

After that, `cairn` shows status. Setup is idempotent, and `cairn uninstall` removes what it added.

## What you get on day one

None of this needs a model:

- `cairn impact <file|symbol>`: dependents, tests likely affected, files that usually change
  together, past fixes and reverts, the spec task that owns the code, team conventions, recent agent
  work and a risk level.
- `cairn why <file|symbol>`: rationale comments, the commits behind the lines, the requirement and
  task that produced the code, and recorded decisions.
- `cairn drift`: done tasks whose files are missing, requirements that no task covers, and code that
  changed after its task was closed.
- The map: `cairn trace path|explain|query`, `cairn hubs`, `cairn areas`, and `cairn graph` for views,
  the report, the wiki and exports.
- Memory seeded from the repository: accepted ADRs, answered clarifications in specs, rules in
  contributing and style docs, and reverted or explained fixes in git history.
- Session capture: prompts, files read and changed, and commands, recorded into `.cairn/sessions.db`.
  It's on for Claude Code by default. For other agents, run `cairn sessions integrate install <agent>`.
  Without a model, the events are turned into deterministic observations.
- Measured savings: every context pack an agent receives is logged with its size and the size of the
  files behind it. The Overview page shows the totals.
- `cairn ui`: one page with the map, specs, timeline, memory and sessions.

## Model features

Cairn finds a model on its own (`[models] provider = "auto"`). It uses the first of these that works:

1. `ANTHROPIC_API_KEY` (or `CAIRN_API_KEY`)
2. `OPENAI_API_KEY`, or a `[models] base_url` for any OpenAI-compatible endpoint, including local ones
3. The signed-in Claude Code CLI (`claude` on your PATH), which runs on your own plan with no key

With a model, Cairn adds:

- observations and session summaries written from raw agent activity
- timeline facts with validity windows ("true from March until the May refactor")
- memory reconciliation: a new memory updates, merges with or retires the ones it overlaps
- narrated answers (`cairn impact --explain`, `cairn why --explain`, `cairn ask`)
- semantic drift checks (`cairn drift --deep`)
- document, paper and image extraction into the map (`cairn graph extract`)

Each job goes to the cheapest model tier that does it well. Every call is recorded in a ledger
(`cairn models --ledger`). Model work runs on its own when a model is available, and it spends that
provider's quota (or your Claude Code plan):

- After each agent turn, observations and summaries are written. To only queue the events, set
  `[recall] worker_spawn = false`. You can process them later with `cairn sessions worker`.
- Every sync builds timeline facts, capped at 150,000 tokens per sync by default. Change the cap
  with `[deep] budget_tokens`, or turn this off with `[deep] enabled = false`.

See [models and cost](docs/models-and-cost.md).

## The five layers

| Layer | Answers | Built from |
|---|---|---|
| **Map** | What is the code? | Tree-sitter ASTs (25 grammars built in, more as extras), docs, schemas, rationale comments |
| **Specs** | What did we intend? | The spec-driven workflow: constitution, spec, plan, tasks, traced to files |
| **Timeline** | What happened? | Git history, co-change, fixes and reverts; with a model, facts with validity windows |
| **Memory** | What did we learn? | Conventions, decisions, gotchas and preferences from people, agents and the repository |
| **Sessions** | What did agents do? | Captured agent sessions: observations, summaries and the files they touched |

## Teams

The same server can serve a whole team. It adds sign-in, roles, API tokens, git projects that
re-sync on push, and an audit log:

```sh
cairn team init --email you@example.com --name "Your Name"     # first owner; prints a one-time password
cairn serve --team                                            # behind your HTTPS reverse proxy
cairn project add https://github.com/acme/api.git             # cloned on the server; first sync from the page
cairn team invite alice@example.com --role member             # prints a one-time invite link
```

Developers point their agents at the server with
`cairn agents connect --server https://cairn.example.com --project <project id>` and export
`CAIRN_TOKEN`. See [teams](docs/teams.md).

## Commands

| Command | What it does |
|---|---|
| `cairn` | Set up (first run) or show status |
| `cairn impact <target>` / `cairn why <target>` | The two questions to ask before an edit |
| `cairn ask "<question>"` | Evidence pack, plus a narrated answer when a model is available |
| `cairn search <query>` | Search every layer |
| `cairn remember "<text>" --kind convention` / `cairn recall <query>` | Team memory |
| `cairn specs [id]` / `cairn drift [id]` | Intent traced to code, and where the code drifted from it |
| `cairn spec …` | The spec workflow: init, integrations, extensions, presets, workflows, bundles |
| `cairn graph …` / `cairn trace …` / `cairn hubs` / `cairn areas` | Explore the map |
| `cairn timeline` / `cairn timeline search <q>` | What happened, and what was true when |
| `cairn sessions` / `cairn sessions search <q>` | What agents did |
| `cairn memory …` | The memory engine: add, search, history, import, seed |
| `cairn ui` / `cairn up` / `cairn down` / `cairn serve` | The page and the server |
| `cairn doctor` / `cairn models --ledger` | Health, models and cost |
| `cairn team` / `cairn token` / `cairn project` / `cairn user` | The team platform |

Full reference: [docs/cli.md](docs/cli.md).

## Documentation

- [Onboarding](docs/onboarding.md): the in-depth walkthrough — what a developer does, what an agent
  does automatically, real screenshots of every page, and how to read the savings numbers
- [Architecture](docs/architecture.md): engines, stores, the read model, sync, the server, MCP and the platform
- [CLI reference](docs/cli.md)
- [MCP tools](docs/mcp.md)
- [Agents](docs/agents.md): what `cairn init` wires for each agent, session capture, commands and sub-agents
- [Configuration](docs/configuration.md): every setting, environment variable and file location
- [Models and cost](docs/models-and-cost.md): providers, tiers, budgets, the ledger and how savings are measured
- [Teams](docs/teams.md): team mode, roles, invites, tokens, git projects, webhooks, audit and deployment
- [Decisions (ADRs)](docs/adr/)
- [Contributing](CONTRIBUTING.md)

Cairn builds itself spec-first. Its constitution is in
[`.cairn/workflow/memory/constitution.md`](.cairn/workflow/memory/constitution.md), and its spec,
plan and tasks are in [`specs/001-cairn-core/`](specs/001-cairn-core/spec.md).

## Licence

Apache-2.0 (see [LICENSE](LICENSE)). Cairn includes software from other open-source projects. Their
notices are in [NOTICE](NOTICE) and their licence texts are in [licenses/](licenses/).
