# Open Questions — 009 Savings Ledger

Each has a recommended default so work can proceed if you do not answer.

1. **Headline wording while unvalidated.** Show only the low bound ("at least ~X tokens saved, estimated") until
   Level 3 passes, or show the range from day one with an "unvalidated" badge? *Default: low bound only.*
2. **Money.** Should Cairn ever show dollars? Options: tokens only; dollars from a price you configure
   (spec US7); or a built-in price table per model (goes stale, needs maintenance). *Default: configured price only.*
3. **Schema overhead accounting.** MCP tool schemas are re-sent every turn but mostly cache-read. Charge them per
   observed turn at full token count (conservative, my default), per session once, or cache-weighted?
4. **Read cap default.** 2,000 lines / 40,000 tokens per file matches Claude Code's Read tool. Other agents
   (Codex, Cursor, Gemini) differ. One cap, or a per-agent cap keyed on the integration?
5. **Legacy rows.** 30 rows in this repo's store use the old uncapped baseline. Keep as a footnote (my default),
   or delete them so no old inflated figure ever shows?
6. **Validation spend.** Approve the pilot (24 headless runs on a mid-tier model)? And which repositories form
   the suite: this repo, robot-shop, plus which third product?
7. **Marketing use.** May the landing page quote a savings figure only when it carries a validation report id
   (spec "Out of Scope")? The current landing page cites "~1.24M tokens across 25 answers" which used the old
   uncapped baseline and would need to be restated or removed.
8. **Per-person visibility in team mode.** Savings per teammate/token name: show to the team, to the owner only,
   or aggregate only? (Could read as surveillance of agent use.)
9. **Prototype wrapper.** The brief mentions a prototype logging wrapper from an earlier experiment's scratchpad. I
   could not locate it from this session; point me to it if it holds ideas worth keeping (spec does not depend on it).
10. **Scope of "every call".** Include the git-hook syncs and `cairn ask` model narration (Cairn's own model use)
    in the ledger as cost lines, or keep the ledger to agent-facing calls and leave model use in `cairn models --ledger`? *Default: separate line, existing table.*
