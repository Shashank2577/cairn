# Research: Cairn Core

Each decision was validated against the installed engine versions on 2026-09-24.

## R1 Code map engine output
- **Decision**: Run the engine's incremental `update <path>` (AST-only, no model) and read its
  `graph.json` (node-link format: `nodes`, `links`, `hyperedges`, `built_at_commit`).
- **Evidence**: 101-file repo → 2,235 nodes / 4,209 edges / 198 communities in seconds, offline.
  Relations include `calls, imports, imports_from, inherits, method, contains, references, uses,
  rationale_for`; edges carry `confidence` EXTRACTED/INFERRED and `source_file`/`source_location`.
- **Rationale**: Reading JSON directly lets Cairn index once (mtime-cached) and answer impact in
  memory; engine CLI is still used for `query`, `path`, `explain` to reuse its ranking.
- **Alternatives**: importing engine internals (fragile across releases).

## R2 Spec workflow integration
- **Decision**: Bootstrap with `specify init --here --integration <agent> --non-interactive
  --offline --ignore-agent-tools --force` and ship a Spec Kit **extension** (`extension.yml`,
  commands `speckit.cairn.*`, hooks) installed with `specify extension add <dir> --dev` (verified against the CLI; see analysis A2).
- **Evidence**: `--force` on a repo with existing `CLAUDE.md`/`AGENTS.md` left them untouched and
  added only `.specify/` and agent skill files. Hook events available: before/after for specify,
  clarify, plan, tasks, implement, analyze, checklist, constitution, taskstoissues.
- **Rationale**: Hooks put the brain inside the workflow the user already runs.

## R3 Temporal fact graph
- **Decision**: `add_episode(name, episode_body, source_description, reference_time, group_id)`
  with `group_id = project id`; drivers: embedded (default) or FalkorDB/Neo4j via URL.
- **Evidence**: Driver package ships neo4j, falkordb, kuzu (embedded, deprecated upstream), neptune.
  LLM clients include Anthropic with separate `model`/`small_model`; embedder is pluggable.
- **Rationale**: Batch commits into daily episodes → ~20× fewer extraction calls than per-commit.
  A local ONNX embedder adapter removes the need for an embeddings API.

## R4 Semantic memory
- **Decision**: Base tier: SQLite FTS memories (instant, no key). Deep tier: semantic memory
  engine configured with Anthropic LLM + local embedder + local vector store; Cairn mirrors every
  memory into the read model so UI/MCP stay uniform.
- **Evidence**: Config keys `llm`, `embedder`, `vector_store`, `history_db_path`; embedder
  providers include `fastembed`; vector stores include `faiss`, `qdrant` (local path).

## R5 Session capture
- **Decision**: Read the capture engine's SQLite (`$CLAUDE_MEM_DATA_DIR` or `~/.claude-mem/
  claude-mem.db`) read-only; detect columns via `PRAGMA table_info` and use `observations`
  (title, subtitle, narrative, facts, concepts, files_read, files_modified, type, created_at_epoch,
  project) and `session_summaries` (request, investigated, learned, completed, next_steps).
- **Evidence**: Worker port is per-user (`37700 + uid % 100`), so HTTP is less stable than the DB.
- **Rationale**: Read-only DB access is fast, offline, and never interferes with capture.

## R6 MCP SDK
- **Decision**: Support SDK v2 (`mcp.server.mcpserver.MCPServer`) and v1 (`FastMCP`) via import
  fallback; stdio transport; 8 tools with terse descriptions (tool schemas cost tokens each turn).

## R7 Model routing & cost
- **Decision**: Tiers → default models: fast `claude-haiku-4-5-20251001`, balanced
  `claude-sonnet-5`, deep `claude-opus-5-5`, frontier `claude-fable-5-1`. System prompts and the
  project brief are sent as cached blocks. Escalate one tier when the input exceeds the tier's
  comfort window or a first pass returns low confidence. Every call logged to `ledger`.

## R8 UI
- **Decision**: One static HTML file, vanilla JS, custom canvas force layout (no CDN, works
  offline); fonts load from Google Fonts when online with system fallbacks.
