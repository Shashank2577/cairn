# Contributing

```sh
git clone https://github.com/cairn-dev/cairn && cd cairn
uv venv && uv pip install -e '.[dev]'      # add ,deep for the deep tier
pytest                                     # no network, no model calls
cairn                                      # dogfood: Cairn on its own repo
```

## How we work
Non-trivial changes follow the spec workflow: `/speckit-specify` → `/speckit-clarify` →
`/speckit-plan` → `/speckit-tasks` → `/speckit-analyze` → `/speckit-implement`. Architecture
decisions get an ADR in `docs/adr/`. The [constitution](.specify/memory/constitution.md) is the
tie-breaker.

## Code map
| Path | Responsibility |
|---|---|
| `src/cairn/core.py` | Facade and context assembler |
| `src/cairn/store.py` | Read model |
| `src/cairn/engines/` | One adapter per engine (only place engine APIs are called) |
| `src/cairn/sync.py` | Parallel orchestrator |
| `src/cairn/cli.py`, `mcp_server.py`, `server.py`, `ui/index.html` | Surfaces |
| `src/cairn/agents.py`, `hooks.py`, `templates/`, `speckit_extension/` | Integrations |

## Rules of thumb
- No model call in a deterministic path (`impact`, `why`, `context`, drift checks).
- New MCP tools need a strong reason; every tool costs tokens on every agent turn.
- Every generated file must be removable by `cairn uninstall`.
- UI copy: sentence case, plain verbs, name things by what users understand.
