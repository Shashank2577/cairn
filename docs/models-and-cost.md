# Models and cost

Cairn's core never calls a model. Impact, why, context packs, drift checks, the map, git history,
spec parsing and session capture are all deterministic ([ADR-0002](adr/0002-deterministic-first.md)).
Models add what can't be computed, and every call goes through one module, `src/cairn/router.py`
([ADR-0004](adr/0004-model-routing.md)).

## Providers

| Provider | What it needs | Notes |
|---|---|---|
| `anthropic` | `ANTHROPIC_API_KEY` (or `CAIRN_API_KEY`) | `[models] base_url` can point at a compatible proxy. The stable prefix (the project brief) is sent as a prompt-cached block. |
| `openai` | `OPENAI_API_KEY`, or `[models] base_url` for a keyless local server | Any OpenAI-compatible `/chat/completions` endpoint. Set each tier to a model the endpoint serves. |
| `claude-code` | The `claude` CLI on your PATH, signed in | Uses your own Claude Code plan, no key. Each call runs `claude -p` from an empty temporary folder with local settings only, no MCP servers, no tools and no saved session, with `CAIRN_INTERNAL=1` set so your hooks, including Cairn's own capture, never record it. |

`[models] provider = "auto"` (the default) picks the first of these that works:

1. `ANTHROPIC_API_KEY` or `CAIRN_API_KEY` is set: `anthropic`
2. `OPENAI_API_KEY` is set, or `[models] base_url` is configured: `openai`
3. `claude` is on your PATH and `CAIRN_NO_CLI_MODELS` isn't set: `claude-code`
4. Otherwise there's no model, and the model features report that they need one.

`cairn doctor` shows which provider is in use, and so does the page's Project settings view.

## What uses a model

| Engine | With a model | Without one |
|---|---|---|
| Sessions | Observations and session summaries written from raw agent activity | Records derived deterministically from tool events |
| Timeline | Facts with validity windows, built on each sync (`[deep] enabled = "auto"`) or by `cairn timeline ingest` | Git history, co-change and risk tags only |
| Memory | Reconciliation (ADD, UPDATE, DELETE or NONE), optional reranking, extraction from conversations | Exact de-duplication, with local-embedding and full-text recall |
| Map | Documents, papers and images (`cairn graph extract`), community names, PR triage | Code via tree-sitter ASTs |
| Answers | `--explain`, `cairn ask`, `cairn drift --deep` | The same evidence packs, without narration |

## Tiers

Each job maps to a tier in `TASK_TIER`, and each tier maps to a model in `[models]`.

| Tier | Default model | Jobs |
|---|---|---|
| fast | `claude-haiku-4-5-20251001` | `classify`, `summarize`, `label`, `brief`; timeline `temporal.resolve`, `temporal.attributes`, `temporal.timestamps`, `temporal.summarize`, `temporal.community`, `temporal.saga`, `temporal.rerank`; memory `memory_rerank`, `memory_instructions`; map `graph-label`, `graph-dedup`; sessions `recall_observe`, `recall_summarize`, `recall_compress` |
| balanced | `claude-sonnet-5` | `extract`, `episode`, `memory`, `link`; timeline `temporal.extract`, `temporal.dedupe`; memory `memory_chat`, `memory_procedural`; map `graph-extract`; sessions `recall_corpus` |
| deep | `claude-opus-5-5` | `why`, `impact`, `drift`, `ask`; map `graph-triage` |
| frontier | `claude-fable-5-1` | `review` (opt-in only) |

`cairn models` prints this table from the code. A judgement job whose input is larger than its
tier's comfort window (balanced 80,000 tokens, deep 180,000) moves up one tier, and only one;
automatic escalation never reaches frontier. Background work on the fast tier (session notes,
labels, timeline housekeeping) never moves up: it bounds its own prompts instead, because a
stronger model on every background call is how a plan's quota disappears. A task that isn't in the
table runs on balanced.

## Budgets

- **Per sync:** timeline facts stop at `[deep] budget_tokens` (150,000 by default). You can
  override it for one run with `cairn sync --budget N` or `cairn timeline ingest --budget N`.
  Unfinished days are picked up on the next sync.
- **Batching:** timeline facts are built from one episode per day of activity, not one per commit.
  Session events go to the model in batches of `[recall] observe_batch` (5), and each call replays
  only the last `[recall] observer_replay_exchanges` (8) exchanges, at most
  `observer_replay_chars` (24,000 characters) of them. Each call's tool events are trimmed to
  `observer_prompt_chars` (48,000 characters) in total. When a session falls far behind (a long
  autonomous turn, or capture while the worker was off), batches grow up to `observe_catch_up` (20)
  so the backlog drains in about a hundred calls; those catch-up batches skip the extra
  condensing calls.
- **Sessions:** with `[recall] worker_spawn = true` (the default), observations are written after
  every agent turn. Set it to `false` to only queue events, and run `cairn sessions worker` when you
  choose.
- **Semantic drift** (`cairn drift --deep`) judges at most five requirements per spec, the ones
  whose code changed most recently, within a third of `budget_tokens` shared across all specs.
- **Memory seeding** reconciles at most `[memory] seed_model_limit` (25) new candidates per run.
- **Context packs** have hard token budgets (`[context] budget`, 1,800 by default), and the tests
  check that they hold.
- **Incremental work:** cursors for commits and sessions, digests for specs, file modification
  times for the map, and a seed ledger for memory mean an unchanged repository costs nothing to
  re-sync.
- **Local embeddings:** vectors are computed on your machine, so there's no embeddings bill.

## Ledger

Every model call is recorded in the `ledger` table of `.cairn/brain.db`: time, task, tier, model,
input, output and cache tokens, and whether it succeeded.

```sh
cairn models --ledger
```

This shows calls and tokens per tier and model. The page's Project settings view shows the same
data, and so does `GET /api/p/<id>/models`. With `claude-code`, the tokens are counted against your
plan's usage rather than billed.

## How savings are measured

Cairn records what its answers cost agents and what they replace.

- Each time a context pack reaches a reader, Cairn writes one row to the `queries` table. The kind
  is `impact`, `why` or `context` (`cairn ask` counts as `context`), or `brief` for the session
  briefing. The row holds the surface
  (`mcp` for agent tool calls, `cli` for the terminal, `hook` for the session briefing), the kind,
  the target, the **tokens sent** and the **source tokens**.
- Tokens sent is the rendered pack's size, estimated at 4 characters per token, which is the same
  rule packs use to fit their budget.
- Source tokens is the size of the repository files the evidence was gathered from: the target's
  files, its dependents' files, the files that change with it, and the spec documents behind its
  intent citations. Each file is counted once, at its size in bytes divided by 4. It stands for
  what an agent would otherwise have read to find the same answer.
- A pack is logged once, the first time it's delivered. The page's own views aren't logged, because
  nobody is handed those packs.
- Session-start briefings are logged too, but they have no file baseline, so they count as cost,
  not savings.

The Overview page compares the tokens agents were sent with the size of the code those answers cite,
and breaks the totals down by surface and kind. The same data is at `GET /api/p/<id>/savings`.
