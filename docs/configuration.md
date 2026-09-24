# Configuration

`.cairn/config.toml` (created on first run, safe to commit). Every key is optional.

```toml
[server]
port = 4747                 # first free port from here; bound to 127.0.0.1

[context]
budget = 1800               # default token budget for agent-facing packs

[models]
provider = "anthropic"      # or "openai" for any OpenAI-compatible endpoint (incl. local servers)
fast = "claude-haiku-4-5-20251001"
balanced = "claude-sonnet-5"
deep = "claude-opus-5-5"
frontier = "claude-fable-5-1"
# base_url = "http://localhost:11434/v1"

[deep]
enabled = "auto"            # auto: on when a key is present; false: never
budget_tokens = 150000      # ceiling per deep sync; unfinished work resumes next sync
graph_url = ""              # falkor://host:6379 or bolt://host:7687; empty = embedded store

[history]
max_commits = 3000          # first-run history depth

[sessions]
project = ""                # capture project name if it differs from the folder name
```

Environment: `ANTHROPIC_API_KEY` (or `CAIRN_API_KEY`), `OPENAI_API_KEY` for `provider="openai"`,
`NEO4J_USER`/`NEO4J_PASSWORD` for a Neo4j timeline store, `CLAUDE_MEM_DATA_DIR` to relocate
session capture data, `NO_COLOR` for the status line.

State in `.cairn/`: `brain.db` (read model), `memory/` and `timeline.kuzu` (deep tier),
`server.json`, `*.log`. The map lives in `graphify-out/` (share it with the team, or ignore it).
