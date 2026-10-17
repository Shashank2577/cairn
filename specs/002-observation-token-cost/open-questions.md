# Open questions — 002 observation token cost

Each has a recommended default so the work can proceed if you do not answer. Nothing has been changed in `src/`.

1. **Which tier is the default?** *Safe* (cache off, thinking off, no condensing call, terse brief, closing instructions once;
   −43% cairn / −73% spreix estimated cost per event) or *Economy* (Safe + batch 10 + replay 2/6,000;
   −72% / −85%, but 3× fewer, broader observations on cairn: 8 vs 26 per 50 events).
   *Default: Safe as default, Economy as a documented profile.*
2. **Does observation granularity matter to you?** The context injection shows the latest 50 observations. Fewer, richer observations may
   serve it as well or better, but nothing here measures retrieval. *Default: treat it as unmeasured and keep Safe.*
3. **Thinking off.** It halves output and passes the gate, but cairn grounding on its `think0` replicates ranged 86–95% against 94–96% with thinking.
   Accept thinking off for all fast-tier bulk tasks (`recall_observe`, `recall_summarize`, `recall_compress`, `temporal.resolve`, `classify`, `label`)?
   *Default: yes for observe/summarize/compress, leave the others for a follow-up measurement.*
4. **CLI cache switch.** `DISABLE_PROMPT_CACHING` is a Claude Code environment variable, not a documented API. If a later CLI renames it the saving
   disappears silently (the ledger would show cache writes again). Accept that, with a test on the env we set and `run_matrix.sh` as the canary?
   *Default: yes.*
5. **A person grades the blind sheet.** 10 items per repository in the shared notes folder (`002-review/`). Defaults should not change before this.
   *Default: block the change on it.*
6. **Anthropic API key.** The SDK path is estimated because this machine has no key. Provide one (or a scoped budget) to measure it, or accept the estimate?
   *Default: accept the estimate; the Router change for that path is only "stop marking a changing block cacheable".*
7. **Spend.** The runs behind `findings.md` made 558 model calls through the CLI (8.1M input-side and 1.0M output tokens, about $15 API-equivalent, estimated), plus
   some discarded early runs, the cache probes and local Ollama runs. Re-running the matrix for the *after* numbers (T026) costs about a third of that. OK?
8. **Skip list.** Add a deterministic skip for listing-only shell commands (3–5% of input), or leave `skip_tools` alone? *Default: leave it; revisit when a retrieval metric exists.*
9. **Translated modes** (`code--ja` etc.) inherit the terse brief only for keys they do not override. Should the shorter brief be applied to each translation in the same PR?
   *Default: separate follow-up.*
10. **Ledger columns.** Record thinking tokens and cache TTL per call in the ledger (needs a column in `ledger`, overlaps spec 009)? *Default: yes, additive columns with defaults.*
