---
description: "Tasks for 007-agent-system-context (ids kept from the 002 umbrella)"
---

# Tasks: Agents know where they are in the system

Ticked = code and passing tests exist. **partial** = some exists, with what is missing. open = not started.

- [ ] T036 [US4] Brief line (container, inbound and outbound neighbours; at most 40 tokens), placed before "Team knowledge" in `src/cairn/core.py` `brief()`; test on a fixture whose brief is full — open
- [ ] T037 [US4] Consuming containers in `cairn_context` for files that implement routes, handlers or shared contracts, in `src/cairn/core.py` `context()` — open
- [ ] T038 [P] [US4] `cairn_system` tool in the full MCP toolset only in `src/cairn/mcp_server.py`; update `docs/mcp.md` — open
- [ ] T039 [US4] Tests: brief budget, consumer listing on the shop fixture, in `tests/system/test_agent_context.py` — open
