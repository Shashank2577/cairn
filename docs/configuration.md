# Configuration

Cairn reads two files:

- `.cairn/config.toml` holds **project** settings. `cairn init` creates it, and it's meant to be
  committed so the whole team shares it. Every key is optional, and a missing key uses the default
  shown here. A file that doesn't parse is ignored, so it never blocks a command.
- `$CAIRN_HOME/server.toml` holds **server** settings, in a `[server]` table. `$CAIRN_HOME`
  defaults to `~/.cairn`.

Some settings can also be changed from the page (Project settings) or with
`PATCH /api/p/<id>/settings`: `models.*`, `sessions.capture`, `deep.enabled`,
`deep.budget_tokens` and `context.budget`. The `[recall]` settings are changed with
`cairn sessions settings key=value` or `POST /api/p/<id>/sessions/settings`.

## Project settings (`.cairn/config.toml`)

### `[context]`

| Key | Default | Meaning |
|---|---|---|
| `budget` | `1800` | Default token budget for impact, why, context and ask packs |

### `[models]`

| Key | Default | Meaning |
|---|---|---|
| `provider` | `"auto"` | `auto`, `anthropic`, `openai` or `claude-code`. `auto` picks an Anthropic key, then an OpenAI-compatible key or endpoint, then the signed-in Claude Code CLI. |
| `fast` | `"claude-haiku-4-5-20251001"` | Model for the fast tier |
| `balanced` | `"claude-sonnet-5"` | Model for the balanced tier |
| `deep` | `"claude-opus-5-5"` | Model for the deep tier |
| `frontier` | `"claude-fable-5-1"` | Model for the frontier tier (opt-in reviews only) |
| `base_url` | unset | Endpoint for `openai` (any OpenAI-compatible server, including local ones such as `http://localhost:11434/v1`), or a custom base URL for `anthropic` |

When you use `openai` or a local endpoint, set every tier to a model that endpoint serves. See
[models and cost](models-and-cost.md).

### `[deep]`

| Key | Default | Meaning |
|---|---|---|
| `enabled` | `"auto"` | `auto` builds timeline facts on every sync when a model is available. `false` never builds them. |
| `budget_tokens` | `150000` | Token ceiling per sync for timeline facts. Semantic drift checks use a third of it. |
| `graph_url` | `""` | Older name for `[temporal] url`, still read when `url` is empty |

### `[memory]`

| Key | Default | Meaning |
|---|---|---|
| `add_mode` | `"reconcile"` | `reconcile`: with a model, a new memory can update, merge with or retire overlapping ones. `additive`: always add. |
| `seed` | `true` | Learn from the repository on every sync: accepted ADRs, answered clarifications, rules in contributing and style docs, reverted and explained fixes |
| `rerank` | `false` | Rerank recall results with a model |
| `graph` | `false` | Also write memories into the timeline fact graph |
| `instructions` | `""` | Extra guidance for what counts as a memory |
| `vector_store` | local index in `.cairn/memstore/` | A shared store for a team, for example `{ provider = "qdrant", config = { url = "http://team-server:6333" } }`. `qdrant`, `pgvector` and `chroma` come with the `memory-server` extra. |

Other `[memory]` keys the code reads: `engine` (`"auto"`; `false` turns the memory engine off so
only the read model is used), `seed_commits` (`2000`, how many commits seeding reads),
`seed_model_limit` (`25`, how many seeds per run a model reconciles) and `semantic_threshold`
(minimum similarity for semantic recall; `0` uses the embedder's own floor).

### `[temporal]`

| Key | Default | Meaning |
|---|---|---|
| `backend` | `""` | Empty: worked out from `url` (`bolt://` or `neo4j://` means neo4j, `falkor://` or `redis://` means falkordb, `neptune-db://` means neptune, and no URL means the embedded `kuzu` store in `.cairn/temporal/`). Or set `kuzu`, `neo4j`, `falkordb` or `neptune` explicitly. |
| `url` | `""` | `bolt://host:7687`, `falkor://host:6379` or `neptune-db://…`. Empty means the embedded store. |
| `group_id` | `""` | Namespace for facts. Defaults to the project id. |
| `reranker` | `"local"` | `local`, `model` or `bge` |
| `update_communities` | `false` | Refresh fact communities on every ingest. This costs model calls. |
| `entity_types` | `[]` | Extra entity types to extract, for example `["Component", "Decision", "Library"]` |
| `extraction_instructions` | `""` | Extra guidance for fact extraction |

Other `[temporal]` keys the code reads: `user` (or `CAIRN_TEMPORAL_USER`), `database`,
`search_host` (Neptune's full-text endpoint), `concurrency` (`4`), `llm_cache` (`false`),
`store_raw_episode_content` (`true`), `custom_types` (a table of name → description), `edge_types`,
`edge_type_map` and `local_extractor` (`false`).
The password always comes from `CAIRN_TEMPORAL_PASSWORD`, never from the file.

### `[history]`

| Key | Default | Meaning |
|---|---|---|
| `max_commits` | `3000` | How many commits the first sync reads. Later syncs read only new commits. |

### `[recall]`

Session memory. Every key can also be set with `CAIRN_RECALL_<KEY>` (for example
`CAIRN_RECALL_CAPTURE=false`), which wins over the file. The template in a new `config.toml` lists
the first four.

| Key | Default | Meaning |
|---|---|---|
| `worker_spawn` | `true` | `true`: after each agent turn, a background worker (started by the `Stop` and `SessionEnd` hooks, or hosted by the local server) writes observations and summaries with your model provider, which spends its quota. `false`: hooks only queue events; process them with `cairn sessions worker` (`--no-model` for derived records). Without a model, every sync also drains the queue into derived records. |
| `worker_model` | `true` | `false`: the worker started by the hooks derives records without a model. `cairn agents connect` sets this, so only the team server spends model calls on pushed sessions. |
| `mode` | `"code"` | Observation vocabulary. `cairn sessions modes` lists the modes. |
| `observe_batch` | `5` | Tool events per model call |
| `observe_catch_up` | `20` | Up to this many per call while a session is far behind, so a backlog drains in about a hundred calls (`0` turns it off) |
| `observer_prompt_chars` / `observer_replay_chars` | `48000` / `24000` | Most characters of tool events, and of replayed history, in one call |
| `context_observations` | `50` | Observations in the session-start context |
| `capture` | `true` | Record agent sessions at all |
| `skip_tools` | `"ListMcpResourcesTool,SlashCommand,Skill,TodoWrite,AskUserQuestion"` | Tools that are never recorded |
| `excluded_projects` | `""` | Comma-separated globs of project paths that are never recorded |
| `runtime` | `"local"` | `local` or `server` (a team server advertises the server-only tools) |
| `tier_routing` | `true` | Send batches of read-only tool events to the fast tier |
| `max_retries` | `3` | Model failures before a queued event falls back to a derived record |
| `vectors` | `true` | Keep a local vector index for semantic session search |
| `semantic_inject` / `semantic_inject_limit` | `false` / `5` | Add relevant past observations on every prompt |
| `folder_context` / `folder_use_local_md` | `false` / `false` | Keep a `<cairn-context>` timeline in `CLAUDE.md` (or `CLAUDE.local.md`) of folders that were touched |
| `transcripts_enabled` | `true` | Allow transcript watching and replay |

More keys tune the observer (`observer_replay_exchanges`, `observer_max_conversation_chars`,
`max_concurrent_sessions`), the session-start context (`context_full_count`, `context_full_field`,
`context_session_count`, `context_show_*`, `context_observation_types`,
`context_observation_concepts`, `welcome_hint`), folder files (`folder_md_exclude`,
`folder_md_skeleton_denylist`) and transcripts (`transcripts_config_path`,
`codex_transcript_ingestion`). `cairn sessions settings` prints every effective value.

### `[map]`

| Key | Default | Meaning |
|---|---|---|
| `ignore` | `[]` | Extra gitignore-style patterns the map skips, anchored at the repository root. `.cairn/graphignore` wins on conflict. |
| `viz_node_limit` | `5000` | Above this many nodes, the generated `graph.html` shows communities instead of nodes. `0` turns it off. `CAIRN_GRAPH_VIZ_NODE_LIMIT` also sets it. |

### `[sessions]`

| Key | Default | Meaning |
|---|---|---|
| `capture` | `true` | `false` turns session capture off (same as `[recall] capture = false`) |

### `[mcp]`

| Key | Default | Meaning |
|---|---|---|
| `tools` | `"core"` | `core`: the compact tool set. `all`: every map, timeline-fact and session operation. See [MCP tools](mcp.md). |

### `[team]`

| Key | Default | Meaning |
|---|---|---|
| `id` | `"team"` | Scope id for team-scoped memories (the `cairn_remember` tool with `scope="team"`, or `cairn memory add --scope team`) |
| `server` | unset | A team server's URL. With `project`, captured session events are also sent there (see [teams](teams.md#session-capture-from-other-machines)). `cairn agents connect` writes it. |
| `project` | unset | The project id on that team server |
| `token_env` | `"CAIRN_TOKEN"` | The environment variable holding the API token for `cairn sessions push`. `cairn agents connect --env-var` writes it. |

## Server settings (`$CAIRN_HOME/server.toml`)

```toml
[server]
mode = "team"
public_url = "https://cairn.example.com"
trust_proxy = true
```

Precedence, lowest to highest: built-in defaults, `server.toml`, command-line flags
(`cairn serve --host/--port/--team`), then the environment variables `CAIRN_SERVER_MODE`,
`CAIRN_SERVER_HOST`, `CAIRN_SERVER_PORT` and `CAIRN_PUBLIC_URL`.

| Key | Default | Meaning |
|---|---|---|
| `mode` | `"local"` | `local`: one developer, no sign-in, loopback only. `team`: a session or bearer token is required. |
| `host` | `"127.0.0.1"` | Bind address. Anything but loopback requires `mode = "team"`. |
| `port` | `4747` | Port. `cairn up` and `cairn ui` move to the next free port when it's taken. |
| `public_url` | `""` | The address people use, such as `https://cairn.example.com`. It's used for invite and webhook links, and it pins the allowed Host header and browser origin. |
| `allowed_hosts` | `[]` | Extra Host header values. `*.example.com` and `.example.com` patterns work. |
| `allowed_origins` | `[]` | Extra browser origins allowed to make state-changing requests |
| `cookie_secure` | `"auto"` | `auto` sets Secure on the session cookie unless it's plain HTTP on loopback. `true` or `false` forces it. |
| `trust_proxy` | `false` | Honour `X-Forwarded-For` and `X-Forwarded-Proto` from a reverse proxy |
| `session_days` | `14` | How long a sign-in lasts |
| `token_days` | `90` | Default API token lifetime. `0` means tokens never expire. |
| `invite_days` | `7` | How long an invite link stays valid |
| `login_attempts` / `login_window` | `10` / `900` | Failed sign-ins per client and email within the window (seconds) before the server answers 429 |
| `team_creation` | `"admins"` | Who can create teams: `admins` or `anyone` |
| `allow_local_git` | `false` | Allow `file://` URLs and server paths as git sources over HTTP |
| `allow_insecure_git` | `false` | Allow `http://` and `git://` remotes |
| `repos_dir` | `$CAIRN_HOME/repos` | Where cloned projects live |

## Environment variables

| Variable | Used for |
|---|---|
| `ANTHROPIC_API_KEY` | The `anthropic` provider, and the first choice for `auto` |
| `CAIRN_API_KEY` | A key that overrides the provider's own key variable. `auto` also treats it as an Anthropic key. |
| `OPENAI_API_KEY` | The `openai` provider |
| `CAIRN_NO_CLI_MODELS` | Set it to stop `auto` from using the Claude Code CLI |
| `CAIRN_HOME` | User-level directory (default `~/.cairn`) |
| `CAIRN_ROOT` | Project root to use outside a git repository |
| `CAIRN_SERVER_MODE`, `CAIRN_SERVER_HOST`, `CAIRN_SERVER_PORT`, `CAIRN_PUBLIC_URL` | Server settings. These override `server.toml`. |
| `CAIRN_SECRET_KEY` | The server key (32 characters or more) instead of `$CAIRN_HOME/secret.key` |
| `CAIRN_TOKEN` | The API token that `cairn agents connect` configs and `cairn sessions push` use. Pick another name with `--env-var`; it's stored as `[team] token_env`. |
| `CAIRN_TEMPORAL_USER`, `CAIRN_TEMPORAL_PASSWORD` | Credentials for a fact-graph server |
| `NEO4J_PASSWORD`, `FALKORDB_PASSWORD` | Passwords for `cairn graph export neo4j|falkordb --push` |
| `CAIRN_RECALL_<KEY>` | Any `[recall]` setting, for example `CAIRN_RECALL_WORKER_SPAWN=0` |
| `CAIRN_RECALL_NO_SPAWN` | Set it to stop hooks from starting a worker or a push |
| `CAIRN_EMBEDDER` | `auto` (default), `fastembed` or `hash` |
| `CAIRN_MODEL_CACHE` | Where the local embedding model is cached (default `$CAIRN_HOME/models`) |
| `CAIRN_GRAPH_*` | Tuning for the map engine, for example `CAIRN_GRAPH_FORCE=1` to accept a smaller rebuild |
| `NO_COLOR` | Plain status line |

## Files

In the repository, under `.cairn/`:

| Path | What it is | Commit it? |
|---|---|---|
| `config.toml` | Project settings | Yes |
| `.gitignore` | Keeps everything else local | Yes |
| `workflow/` | The spec workflow: constitution, templates, scripts, integrations, extensions, presets, workflows | Yes |
| `brain.db` | The read model | No (rebuildable) |
| `graph/` | The map: `graph.json`, `GRAPH_REPORT.md`, `graph.html`, `manifest.json`, `fingerprint.json`, cache, `wiki/` | No |
| `graphignore` | Paths the map skips, in gitignore syntax. Cairn adds agent tool folders (`.claude/`, `.cursor/`, `.gemini/`) and generated or vendored code (`*.min.js`, `*.min.css`, `*.bundle.js`, `*.map`, `vendor/`, `node_modules/`, `dist/`); add your own lines. | Local |
| `sessions.db` | Captured agent sessions | No |
| `recall/` | Session vectors, worker lock and log | No |
| `memstore/` | Memory vectors, `history.db`, `seeds.db` | No |
| `temporal/graph.kuzu` | The embedded fact graph | No |
| `sync.lock`, `sync.log` | Sync lock and the background sync log | No |

Spec features live in `specs/NNN-name/` at the repository root.

In `$CAIRN_HOME` (default `~/.cairn`):

| Path | What it is |
|---|---|
| `platform.db` | Users, teams, projects, tokens, sessions and the audit log |
| `secret.key` | The server key (created with mode 0600 on first use) |
| `server.toml` | Server settings |
| `server.json`, `server.log` | The running local server's pid, port and log |
| `repos/` | Clones of git projects |
| `models/` | The local embedding model |
| `graph/` | The optional cross-repository map (`cairn graph global …`) |
