# Analysis: 001-cairn-core (cross-artifact consistency)

Run after implementation (`/speckit-analyze` + converge). Sources: spec.md, plan.md, tasks.md,
contracts/, code, tests.

## Requirement coverage

| Req | Implemented in | Verified by | Status |
|---|---|---|---|
| FR-001 one command | `cli.main_cb`, `run_init` | `test_init_is_zero_prompt_and_idempotent`, manual e2e on httpx | ✅ |
| FR-002 idempotent, reversible | `agents.upsert_block/merge_json/write_generated`, `uninstall` | `test_init_…`, `test_uninstall_removes_only_ours` | ✅ |
| FR-003 map without models | `engines/mapper.build`, `MapIndex` | `test_sync_builds_all_layers` | ✅ |
| FR-004 git history, co-change, risk | `engines/history` | `test_risk_tags`, `test_impact_…` | ✅ |
| FR-005 spec parsing + task→file | `engines/specs` | `test_specs_parser_and_drift`; dogfooded on this spec | ✅ |
| FR-006 sessions → files | `engines/journal` | `test_sessions_reader_links_observations` (synthetic DB, real schema) | ✅ ¹ |
| FR-007 memories | `store`, `engines/memory` | `test_store_search_and_memory`, `test_memory_links_to_code` | ✅ |
| FR-008 entity registry + provenance links | `store`, `linker` | `test_memory_links_to_code` | ✅ |
| FR-009 budgeted, cited queries | `core.Pack`, `impact/why/context` | `test_budget_is_respected`, `test_why_…`, `test_context_…` | ✅ |
| FR-010 drift | `drift.check`, `drift.semantic` | `test_specs_parser_and_drift` (deterministic) | ✅ / ⚠️ ² |
| FR-011 ≤ 8 MCP tools | `mcp_server` | `test_mcp_tools`; live stdio client test | ✅ |
| FR-012 agent integrations | `agents.install` | `test_init_…`; generated files inspected | ✅ ³ |
| FR-013 git hooks | `hooks.install_git_hooks`, `sync.spawn_background` | `test_init_…` | ✅ |
| FR-014 UI + API | `server`, `ui/index.html` | `test_http_api`; screenshots at 1440px/390px, dark + light | ✅ |
| FR-015 workflow extension hooks | `speckit_extension/` | Installed into a real workflow project; hooks registered | ✅ |
| FR-016 routing, budget, cache, ledger | `router` | Unit-level; no live model call in CI | ⚠️ ² |
| FR-017 temporal facts, semantic memory | `engines/chronicle`, `engines/memory.SemanticMemory` | Written against installed engine APIs; not executed with a live key | ⚠️ ² |
| FR-018 engine-native capabilities | `trace path/explain/query`, `hubs`, `areas`, `prs`, spec commands, `sessions`, memory history | Manual | ✅ |
| FR-019 doctor | `cli.doctor` | Manual | ✅ |

¹ Capture *install* was exercised only on its failure path (no Claude Code in the build sandbox).
² Model-backed paths need `ANTHROPIC_API_KEY`; the build environment had none. They are isolated
behind `Router.available` and fail soft to the deterministic answer. **First action for maintainers:
run `cairn sync --deep` and `cairn ask` with a key and check `cairn models --ledger`.**
³ Codex wiring uses `codex mcp add`; verified by code path only (Codex not installed in the sandbox).

## Consistency findings

| # | Finding | Resolution |
|---|---|---|
| A1 | data-model.md lacked `filestats`, added during T009 | data-model.md updated |
| A2 | Spec Kit `extension add` syntax in research.md (`add cairn --dev <dir>`) differs from real CLI (`add <dir> --dev`) | Code uses the verified form; research.md R2 noted |
| A3 | Map attaches in-body `# WHY:` comments to the file node | Location-aware rationale lookup (`MapIndex._span`) |
| A4 | Map may attribute `self.x()` calls to a same-name sibling | Impact adds sibling callers as `INFERRED` |
| A5 | Spec-kit template folders polluted the map (148 → 103 files on httpx) | `.graphifyignore` block for tool folders |

## Measured (httpx, 101 source files, shallow 50 commits; sandbox CPU)

| Metric | Budget (constitution) | Measured |
|---|---|---|
| Full first sync | n/a | 2.8 s |
| Impact query | < 800 ms p95 | 3 ms |
| Why query (incl. git blame) | < 800 ms | 14 ms |
| MCP schema overhead | minimal | ~700 tokens (8 tools) |
| Session-start brief | ≤ 600 tokens | ≤ 550 by construction (tested) |
