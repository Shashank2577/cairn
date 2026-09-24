# MCP contract (server name `cairn`, stdio)

| Tool | Args | Returns |
|---|---|---|
| `cairn_context` | task: str, targets?: list[str], budget?: int=1800 | Ranked, cited context pack (markdown) — call first |
| `cairn_impact` | target: str, depth?: int=2, budget?: int | Dependents, co-change, warnings, owners, sessions, memories |
| `cairn_why` | target: str, budget?: int | Rationale, origin commits, spec items, decisions |
| `cairn_search` | query: str, kinds?: list[str], limit?: int=12 | Matching entities across layers |
| `cairn_trace` | mode: path/explain/query, a: str, b?: str | Map exploration text |
| `cairn_specs` | spec?: str, drift?: bool=false | Spec board / trace / drift findings |
| `cairn_remember` | text: str, kind?: str, supersedes?: str | Stored memory id |
| `cairn_recall` | query: str, limit?: int=8 | Memories + related session learnings |

All outputs cite ids in `[kind:key]` form and end with `(budget used: N/M tokens)`.
