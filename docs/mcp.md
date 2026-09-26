# MCP tools

Agents reach Cairn through one MCP server named `cairn`:

- **Locally**, agents launch it over stdio (`cairn mcp`). `cairn init` writes that into each agent's
  config. The server answers about the repository the agent runs in.
- **On a team server**, the same tools are served over streamable HTTP at `/mcp/<project id>/`. See
  [HTTP endpoint](#http-endpoint).

The server sends agents one line of instructions: call `cairn_context` before editing, call
`cairn_impact` before changing a file or symbol, save durable learnings with `cairn_remember`, and
use `cairn_session_search`, then `cairn_session_timeline` and `cairn_session_observations`, to find
what earlier sessions did.

## Core and all

Every tool's schema is sent to the model on every agent turn, so the default is a compact core set
([ADR-0008](adr/0008-compact-agent-tools.md)).

| Setting | Tools | Schema size (name, description and input schema, estimated at 4 characters per token) |
|---|---|---|
| `[mcp] tools = "core"` (default) | 12 | about 1,600 tokens per turn |
| `[mcp] tools = "all"` | 61 | about 8,600 tokens per turn |

Set it in `.cairn/config.toml`:

```toml
[mcp]
tools = "all"
```

The REST API under `/api/p/<id>/` exposes every map and timeline operation
(`/graph/tools/<name>`, `/temporal/<tool>`), whatever this setting says. The MCP endpoint over
HTTP follows the setting.

## Core tools

| Tool | Use it when | Arguments |
|---|---|---|
| `cairn_context` | Starting any non-trivial task. Call it first. It returns ranked, cited context: dependents, co-change, past incidents, the owning spec, conventions and past agent work. | `task`, `targets?` (list), `budget=1800` |
| `cairn_impact` | Before changing a file or symbol (`src/pay.py`, `Client.send`). It returns the risk level, dependents, tests, co-change, warnings and owners. | `target`, `depth=2`, `budget=1500` |
| `cairn_why` | You're asked why code is the way it is. It returns rationale comments, origin commits, the spec task and requirement, decisions and sessions. | `target`, `budget=1200` |
| `cairn_search` | Finding anything across layers. | `query`, `kinds?` (subset of `symbol,file,spec,task,req,commit,memory,obs,fact`), `limit=12` |
| `cairn_trace` | Exploring structure. `explain` shows a node and its neighbours, `path` goes from `a` to `b`, and `query` answers a question. | `mode`, `a`, `b=""` |
| `cairn_specs` | The spec board (features, stories, progress and file trace), or with `drift=true`, where the code disagrees with its specs. | `spec=""`, `drift=false` |
| `cairn_remember` | You learned something durable. Write it as one precise sentence. | `text`, `kind=fact` (`convention`, `decision`, `gotcha`, `preference`, `fact`), `supersedes=""`, `scope=project` (`project`, `team`, `user`) |
| `cairn_recall` | What does the team know about X? It returns memories plus related session learnings. | `query`, `limit=8` |
| `cairn_facts` | What was true about something, and when. It returns facts with valid-from and valid-until dates. `at=YYYY-MM-DD` answers "as of that date". Facts exist once a model has built them. | `query`, `at=""`, `limit=12` |
| `cairn_session_search` | Step 1 of session search: an index of past observations, summaries and prompts, with ids. | `query?`, `limit?`, `type?`, `obs_type?`, `dateStart?`, `dateEnd?`, `orderBy?`, `offset?`, `project?`, `platformSource?` |
| `cairn_session_timeline` | Step 2: what happened around a result. | `anchor?` (observation id) or `query?`, `depth_before?`, `depth_after?`, `project?` |
| `cairn_session_observations` | Step 3: full details, only for the ids that matter. | `ids` (list) |

`cairn_context`, `cairn_impact` and `cairn_why` return Markdown packs. Each has a title, section
headings and one line per item with a `[kind:key]` citation, and ends with
`(budget used: N/M tokens)`. Truncated sections say `(+N more)`. Evidence is tagged `EXTRACTED`
(read from a source) or `INFERRED` (derived), so the agent knows what to verify. Here's an
example from this repository, shortened:

```text
## Impact: src/cairn/router.py
**Risk: MEDIUM** — 105 dependents in 32 other files
### Dependents (+68 more)
- run() src/cairn/sync.py:95 — calls Budget (depth 1, EXTRACTED) [symbol:src_cairn_sync_run]
- .__init__() src/cairn/core.py:140 — calls Router (depth 1, EXTRACTED) [symbol:src_cairn_core_cairn_init]
### Tests likely affected (+25 more)
- test_budget_stops_before_spending() tests/engines/test_temporal_chronicle.py:117 — uses Budget (depth 1, INFERRED) […]
### Intent
- T006 Model router with tiers, caching, budget, ledger in `src/cairn/router.py` (done) [task:001-cairn-core/T006]
### Memory
- [decision] Task-tiered model routing: … [memory:8772351bcc]
(budget used: 600/600 tokens)
```

Every pack handed to an agent is logged with its size and the size of the files it cites. See
[how savings are measured](models-and-cost.md#how-savings-are-measured).

## Everything else (`tools = "all"`)

| Group | Tools |
|---|---|
| Sessions | `cairn_session_tool_uses` (raw tool input and output, a last resort), `cairn_session_context` (the exact session-start context), `cairn_session_workflow` (the search → timeline → details routine) |
| Structural code reading | `cairn_smart_search`, `cairn_smart_outline`, `cairn_smart_unfold`: tree-sitter views of symbols and files, cheaper than reading whole files |
| Knowledge corpora | `cairn_build_corpus`, `cairn_list_corpora`, `cairn_prime_corpus`, `cairn_query_corpus`, `cairn_rebuild_corpus`, `cairn_reprime_corpus`: focused knowledge agents built from session observations |
| Map | `cairn_graph_query_graph`, `cairn_graph_get_node`, `cairn_graph_get_neighbors`, `cairn_graph_get_community`, `cairn_graph_god_nodes`, `cairn_graph_graph_stats`, `cairn_graph_shortest_path`, `cairn_graph_list_prs`, `cairn_graph_get_pr_impact`, `cairn_graph_triage_prs` |
| Timeline facts | 27 `cairn_timeline_*` tools. Reads: `search`, `search_facts`, `facts_at`, `search_entities`, `context`, `get_memory`, `get_episodes`, `episode_entities`, `get_fact`, `get_entity`, `list_facts`, `list_entities`, `communities`, `sagas`, `status`. Writes: `add_episode`, `enqueue_episode`, `add_episodes_bulk`, `add_messages`, `add_fact`, `add_entity`, `build_communities`, `summarize_saga`, `delete_fact`, `delete_episode`, `delete_group`, `clear` |

The map and timeline tools are generated from the engines' own tool specs, so their arguments match
the engines' schemas.

## HTTP endpoint

A server serves the tools per project at `/mcp/<project id>/` over streamable HTTP (stateless,
JSON responses).

- **Authentication:** in team mode, send `Authorization: Bearer <token>`, using a token from
  `cairn token issue` or the Tokens page. A browser session also works. In local mode the server
  listens on loopback only and needs no token.
- **Authorisation:** the caller needs `project.read` on the project. If the project isn't visible to
  the caller, the endpoint answers 404. If the caller's role or token scopes don't include
  `project.write`, the server hands out the read-only set: `cairn_remember` and the timeline write
  tools are left out.
- **Tool set:** the project's own `[mcp] tools` setting decides between core and all.

`cairn agents connect --server https://cairn.example.com --project <project id>` writes this
endpoint into `.mcp.json` (and into `.cursor/mcp.json`, `.gemini/settings.json` and
`.vscode/mcp.json` when those exist). The header reads `Bearer ${CAIRN_TOKEN}`, so the token itself
is never written to disk. Each developer exports their own. See [teams](teams.md#agents-on-a-team-server).
