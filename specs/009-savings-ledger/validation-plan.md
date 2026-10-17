# Validation Plan — does the ledger's estimate match real agent token use?

**Spec**: [spec.md](spec.md) (US6, FR-015, SC-004, SC-005). **Rule**: no range is headlined, and no figure
reaches marketing copy, until Level 3 passes. Levels 1 and 2 are cheap and run first.

## Level 1 — Tokenizer calibration (cheap, offline)

Goal: put an error band on `bytes4-v1`.
- Sample ≥ 300 files across languages in 3 repos (Python, TypeScript, Java/PHP; include the robot-shop
  fixture used for spec 002) and ≥ 100 real Cairn outputs (impact/why/context packs).
- Count each with the model provider's token-counting endpoint (needs an API key; dev/CI only, never a Cairn
  runtime dependency) and compare to `bytes4-v1`.
- Report ratio and error by file type and for packs. Decide: keep `bytes4-v1` with a published band, or ship
  per-type factors as tokenizer `bytes-typed-v1`.
- Pass: median absolute error ≤ 15% on files and on packs, else adopt typed factors and re-run.

## Level 2 — Behavioural replay on captured sessions (free, real usage)

Goal: test the *baseline assumptions* on what agents really did.
- Input: Cairn's own captured sessions (events with tool uses) joined to ledger rows by session key.
- For each ledger call, measure: did the agent open any file in the replacement set afterwards (observed
  re-read rate, by tool)? how many whole files did the agent read in the 10 minutes before the first Cairn call
  of a session on similar tasks (empirical read behaviour vs `B_low`/`B_high`)?
- Outputs: re-read rate per tool, share of sessions where agent reads ≥ `B_low` anyway, and a table of
  "agent actually read X tokens vs baseline range [low, high]" for sessions that had no Cairn.
- Pass: the median real pre-Cairn read volume falls between `B_low` and `B_high` for ≥ 70% of
  sessions; if it falls above `B_high` the cap is too low; if below `B_low` the slice baseline is too generous
  and must be reduced. Adjust definitions, record why in the spec's clarifications.
- Cost: none (reads existing captures). Privacy: runs locally; the report contains counts, not content.

## Level 3 — Paired A/B on a real agent (the proof)

Goal: compare the ledger's predicted net saved tokens with the measured token difference.
- **Suite**: ≥ 20 tasks over ≥ 3 repositories, each a question or small change with an objective check
  (answer key or test). Reuse the 2026-10-16 robot-shop three-arm experiment as the first task family.
  Task kinds mirror ledger tools: orientation, impact-before-change, why-question, bug locate.
- **Arms**: A = agent without Cairn (MCP and hooks disabled); B = agent with Cairn. Same model, same prompt,
  clean checkout, cold prompt cache (or record cache state), temperature default.
- **Repetitions**: 3 per arm per task (run-to-run variance in agent paths is large).
- **Measure**: the agent CLI's own usage report per run (input, output, cache-read, cache-write tokens, turns,
  tool calls) from its structured output — the vendor's number, not ours. Primary metric: total input-side
  tokens (input + cache-read + cache-write). Secondary: same weighted by published relative prices.
- **Quality gate**: a run counts only if it passes the task check. Arms are compared on tasks both pass; the
  pass-rate difference is reported. A saving bought with wrong answers is not a saving.
- **Prediction**: for arm B runs, the ledger's net saved range for that run.
- **Compare**: per task, measured Δ = median(A) − median(B) with bootstrap 95% CI, against predicted range.
  Report: median absolute error, share of tasks where measured Δ is inside the range, bias (signed), by tool kind.
- **Gate (SC-004)**: ≥ 20 tasks, median abs error ≤ 25%, ≥ 80% inside range. Failing → fix definitions or
  show the low bound only; never relax the gate to pass.
- **Calibration**: if bias is stable (same sign, CI excludes zero), derive a factor per tool kind, store it
  with the report id, apply it explicitly (US6 AC3), re-run on a held-out half to confirm.
- **Confounds to log**: Cairn briefing and schema overhead (already netted), different path lengths, agent
  non-determinism, cache warm/cold, model version drift (record model id and date).

### Cost and sequencing (token budget rule)

Full run: 20 tasks × 2 arms × 3 reps = 120 headless runs; at roughly 150k–400k input tokens each that is on
the order of tens of millions of tokens — expensive. Therefore:
1. **Pilot first**: 6 tasks × 2 arms × 2 reps = 24 runs on a mid-tier model, ≤ 3 concurrent, to size variance.
2. Ask the user before the full run; do not run background spend unprompted.
3. Use the cheapest model that exercises the same tool-use behaviour; repeat a 5-task subset on the premium
   model to check the result transfers.

## Deliverables of the harness

- `cairn savings validate --suite <dir>`: runs the paired suite (or ingests recorded runs), writes
  `.cairn/savings/validation/<date>-<id>.json` and a human report.
- The report schema holds suite id, model ids, tokenizer, caps, per-task predicted vs measured, statistics,
  calibration, gate result. `cairn savings` and the UI read the newest report for the badge.
- Re-run triggers: tokenizer change, cap change, baseline-rule change, or a new default agent integration.

## What would make us stop

If Level 2 shows agents routinely read far less than `B_low`, or Level 3 shows measured savings consistently
below the low bound, the honest outcome is a smaller number or a different claim ("faster orientation",
"fewer file opens") and the spec changes, not the gate.
