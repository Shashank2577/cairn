# 016 — `cairn context` on the command line, and context packs counted once

## Why
Orchestrators that start agents (the Foundry crew engine builds each agent's prompt in `foundry_crew/prompts.py`
`compose()`; `foundry-program` builds it in `dispatch.yml`) had no way to put a Cairn context pack into the prompt:
the pack existed only as the `cairn_context` MCP tool, which the agent must choose to call.
While testing, the audit trail showed every `cairn_context` MCP call recorded twice (once by the pack when it
rendered, once by the tool), so `context` pack counts and sent tokens were doubled for that surface.

## Requirements
- FR-1 `cairn context "<task>" [--targets a,b] [--budget N] [--json]` prints the same pack as the MCP tool, as plain text. No model calls.
- FR-2 `--json` returns `{task, tokens, text}`.
- FR-3 Each pack is recorded once in `queries`, on every surface.
