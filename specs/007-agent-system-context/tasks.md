---
description: "Tasks for 007-agent-system-context (ids kept from the 002 umbrella)"
---

# Tasks: Agents know where they are in the system

Ticked = code and passing tests exist. **partial** = some exists, with what is missing. open = not started.

- [x] T036 [US4] Brief line (container, inbound and outbound neighbours; at most 40 tokens), placed before "Team knowledge" in `src/cairn/core.py` `brief()`; test on a fixture whose brief is full — done: `Cairn.system_line()` (src/cairn/core.py:395) with BRIEF_SYSTEM_TOKENS=40 cap, placed before team knowledge in brief()
- [x] T037 [US4] Consuming containers in `cairn_context` for files that implement routes, handlers or shared contracts, in `src/cairn/core.py` `context()` — done: `Cairn._system_consumers()` (src/cairn/core.py:432), surfaced as "System consumers" section in the context pack (src/cairn/core.py:617)
- [x] T038 [P] [US4] `cairn_system` tool in the full MCP toolset only in `src/cairn/mcp_server.py`; update `docs/mcp.md` — done: `cairn_system` in mcp_server.py:196, in SYSTEM_TOOLS (opt-in via `[mcp] tools = "all"`), not in CORE; docs/mcp.md updated
- [x] T039 [US4] Tests: brief budget, consumer listing on the shop fixture, in `tests/system/test_agent_context.py` — done: 16 tests passing, covering SC-007 budget, neighbour capping, consumer listing, MCP tool gating, and zero model calls
