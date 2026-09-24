# CLI contract

All commands accept `--json` for machine output unless noted. Exit 0 on success, 2 on usage error,
3 when not inside a repo.

| Command | Purpose |
|---|---|
| `cairn` | Init if needed, otherwise status |
| `cairn init [--yes] [--agents a,b] [--no-hooks] [--no-ui] [--native-skills]` | One-command setup |
| `cairn status` | Layer health, counts, drift, server URL |
| `cairn sync [--quick] [--deep] [--budget N]` | Fan-out refresh of all layers |
| `cairn ask "<question>" [--budget N] [--no-llm]` | Context pack, optional narrated answer |
| `cairn impact <target> [--depth N]` | What breaks if this changes |
| `cairn why <target>` | Why this code is the way it is |
| `cairn trace path <a> <b>` / `cairn trace explain <x>` / `cairn trace query "<q>"` | Map exploration |
| `cairn hubs` / `cairn areas` | Most-connected nodes / communities |
| `cairn specs [<id>]` / `cairn spec new "<desc>"` | Spec board, trace, new feature |
| `cairn drift [<spec>] [--deep]` | Drift findings |
| `cairn remember "<text>" [--kind k] [--supersedes id]` / `cairn recall "<q>"` / `cairn forget <id>` | Memory |
| `cairn timeline [--target t] [--since 30d]` | Events |
| `cairn sessions [--query q]` | Captured agent sessions |
| `cairn prs` | Open PRs with map impact (needs `gh`) |
| `cairn ui` / `cairn up` / `cairn down` | Open UI / start / stop daemon |
| `cairn mcp` | Stdio MCP server |
| `cairn agents [install|list] [--agents a,b]` | Agent integrations |
| `cairn models [--ledger]` | Routing table, cost ledger |
| `cairn doctor` | Capability report with fix commands |
| `cairn statusline` / `cairn hook <event>` | Agent/git hook entry points (internal) |
| `cairn uninstall [--purge]` | Remove integrations (and state) |
