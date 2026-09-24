# MCP tools

Server name `cairn`, stdio (`cairn mcp`). Eight tools with terse descriptions, about 700 tokens
of schema, because tool definitions are paid for on every agent turn.

| Tool | Use it when | Key args |
|---|---|---|
| `cairn_context` | Starting any non-trivial task (call first) | `task`, `targets?`, `budget=1800` |
| `cairn_impact` | Before changing a file or symbol | `target`, `depth=2`, `budget` |
| `cairn_why` | Asked why code is as it is | `target` |
| `cairn_search` | Finding anything across layers | `query`, `kinds?` |
| `cairn_trace` | Exploring structure | `mode=explain|path|query`, `a`, `b?` |
| `cairn_specs` | Spec board, or `drift=true` for violations | `spec?`, `drift` |
| `cairn_remember` | Learned something durable | `text`, `kind`, `supersedes?` |
| `cairn_recall` | What does the team know about X | `query` |

Outputs are markdown packs with `[kind:key]` citations, ending with `(budget used: N/M tokens)`.

Example (`cairn_context`, "change retry behaviour in Client.send", budget 600):

```text
## Context: change retry behaviour in Client.send
Targets: Client.send, …
### Dependents
- .request() httpx/_client.py:825 — calls .send() (depth 1, INFERRED) [symbol:httpx_client_client_request]
### Changes together
- httpx/_api.py — changed together in 2 commits (67%) [file:httpx/_api.py]
### Memory
- [gotcha] Client.send must never retry non-idempotent requests [memory:a1b935d1ed]
(budget used: 443/600 tokens)
```
