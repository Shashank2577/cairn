<!-- cairn:begin -->
## Cairn — project memory

This repository is connected to Cairn (MCP server `cairn`), the project's memory of code structure,
specs, history, decisions and past agent work.

- **Before editing**, call `cairn_context` with your task (or `cairn_impact` for a specific file or
  symbol). It returns dependents, files that usually change together, past fixes and reverts, the
  owning spec task, team conventions and recent agent sessions, within a small token budget.
- **When asked why code is the way it is**, call `cairn_why` before guessing.
- **When you learn something durable** (a convention, decision, gotcha), call `cairn_remember`
  with a one-sentence statement and a kind. Don't store what the code already says.
- Treat `[EXTRACTED]` evidence as fact and `[INFERRED]` as a lead to verify.
- Spec work lives in `specs/`; Cairn hooks run automatically at plan, tasks and implement stages.
<!-- cairn:end -->
