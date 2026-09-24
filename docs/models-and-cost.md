# Models and cost

Deterministic features never call a model. Model work happens in four places: narration
(`--explain`, `ask`), deep sync (temporal facts), semantic memory, and semantic drift.

## Routing
`router.py` maps each job to a tier (see the README table). A job escalates one tier only when its
input exceeds the tier's comfort window; it never escalates to frontier automatically.

## Keeping tokens down
- **Batching:** one episode per day of activity instead of per commit (~20× fewer extraction calls).
- **Incremental:** cursors for commits, sessions and episodes; digests for specs; mtime for the map.
- **Prompt caching:** the project brief is sent as a cached system block; repeated `ask` calls reuse it.
- **Budgets:** `deep.budget_tokens` caps a sync; drift checks judge at most 5 requirements, chosen by
  most-recent code change; packs have hard token budgets.
- **Local embeddings:** vectors are computed on-device, so there's no embeddings bill.

## Ledger
`cairn models --ledger` shows calls, input, output and cache-read tokens per tier and model.
