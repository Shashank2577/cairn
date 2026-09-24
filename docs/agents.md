# Agent integrations

`cairn init` detects installed agents and writes only what each supports. Markdown files get a
marked block (`<!-- cairn:begin -->`); JSON is merged key by key; generated files carry a header.
`cairn uninstall` removes exactly those.

| Agent | What Cairn adds |
|---|---|
| Claude Code | `.mcp.json` server · `SessionStart` briefing hook · status line · `/cairn:why`, `/cairn:impact`, `/cairn:ask`, `/cairn:drift`, `/cairn:remember`, `/cairn:brief` · sub-agents · `CLAUDE.md` block |
| Codex | `codex mcp add cairn` · `AGENTS.md` block |
| Cursor | `.cursor/mcp.json` · always-on rule `.cursor/rules/cairn.mdc` |
| Gemini CLI | `.gemini/settings.json` server · `GEMINI.md` block |
| VS Code / Copilot | `.vscode/mcp.json` |
| Any MCP client | `AGENTS.md` block; run `cairn mcp` |

## Sub-agents (Claude Code), tiered by model

| Sub-agent | Model | Role |
|---|---|---|
| `cairn-scout` | Haiku | Gathers impact and context before edits; never edits |
| `cairn-implementer` | Sonnet | Implements one spec task with memory in context; parallel-safe for `[P]` tasks |
| `cairn-architect` | Opus | Reviews plans and cross-cutting changes against reality |
| `cairn-reviewer` | Opus | Reviews a diff against specs, history and conventions |

For a spec's `[P]` tasks, fan out one `cairn-implementer` per task after `cairn-scout` confirms
the tasks don't share files. Then run `cairn-reviewer`.

## Session capture

Installed once per machine by `cairn init` when Node.js 18+ is present. It records what agents
read, change and learn; Cairn reads that store read-only and links it to code.
