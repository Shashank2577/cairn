# Implementation Plan: Cheaper session observations

**Branch**: `002-observation-token-cost` | **Spec**: [spec.md](spec.md) | **Evidence**: [findings.md](findings.md)
**Status**: Plan only. Nothing below is implemented until the review in `open-questions.md` is answered.

## Summary

The measured causes of the spend are specific and mostly independent: the CLI path caches requests it can never reuse
and bills them at the 1-hour rate; extended thinking runs on every call; a model call is spent condensing oversized
payloads; the brief and the closing instructions are long and repeated; the replay window re-sends ~5,000 tokens each
call; and batches are small. Each gets one change, each change is a setting (so a user can go back), and each is
justified by a row in `findings.md`. Nothing here depends on a particular provider being installed.

## Technical Context

**Language/Version**: Python 3.11+ (existing). **Dependencies**: none added.
**Storage**: unchanged (`sessions.db`, `brain.db` ledger). **Testing**: pytest; the harness in `measure/` is the
before/after instrument, not a test dependency (it spends tokens).
**Constraints**: ships to everyone; default works with zero keys; no `claude-mem` or other local tool; deterministic
first (Constitution II), token discipline (V), right model for the job (VI).

## Constitution Check

| Principle | How this plan honours it |
| --- | --- |
| II Deterministic First, Models Second | The deterministic record path is untouched and stays the zero-key path; the change trims what the model is asked, it does not add model work. Skip decisions stay deterministic. |
| V Token Discipline | The whole feature; every default is backed by a measured row. |
| VI Right Model for the Job | Fast tier stays Haiku; thinking is off for fast-tier bulk tasks, on for `why`/`impact`/`ask`. |
| One vocabulary | Settings use Cairn names (`observer_condense`, `cli_cache`); no other product's names appear. |

## Changes (in the order they should land; each is its own commit and can be reverted alone)

1. **CLI cache off** — `src/cairn/router.py` `_claude_code`, `_claude_code_stream`: set `DISABLE_PROMPT_CACHING=1` in the child
   environment unless the user already set it or `models.cli_cache = true`. Applies to every task: the ledger shows no task
   reading cache through the CLI. *Measured:* cost per event −25% to −40% with no change in what the model sees.
2. **Thinking off for fast-tier bulk tasks** — same file: `MAX_THINKING_TOKENS=0` for tasks in a new `NO_THINKING_TASKS`
   set (`recall_observe`, `recall_summarize`, `recall_compress`, `temporal.resolve`, `classify`, `label`, `graph-label`, …); the
   API paths already send no thinking block, so this is a no-op there. Gated by the quality result in `findings.md`.
3. **No condensing call by default** — `src/cairn/engines/recall/observer.py` + `settings.py`: new `observer_condense`
   (default `false`); `process()` passes `condense=None` unless set. Oversized fields keep the existing head/tail cut with an
   elision marker (already in `prompts.truncate_observation_field`).
4. **Closing instructions once per call** — `src/cairn/engines/recall/prompts.py`: `build_observation_prompt` gains a
   `trailer=False` parameter; `SessionObserver.process` joins event blocks and appends `OBSERVATION_TRAILER` once.
5. **Shorter brief** — `src/cairn/engines/recall/modes/code.json`: the `terse` text from `measure/levers.py` (same rules, fewer
   words, hard limits on facts and narrative). Translated modes inherit untouched keys; they are listed in the PR for a follow-up.
6. **Observer replay as plain prompt text, smaller window** — `observer.py`: replay is no longer sent as `cached_context`
   (so the Anthropic path stops writing a 1.25× cache block that changes every call); defaults `observer_replay_exchanges 8 → 2`,
   `observer_replay_chars 24000 → 6000`.
7. **Batch size** — `settings.py`: `observe_batch 5 → 10` (catch-up stays 20).
8. **Ledger honesty** — `Router._claude_code_result` also returns `thinking` and the cache TTL split; `cairn models --ledger`
   labels dollar figures *estimated* and token figures *measured*, and shows tokens per observation.
9. **Read-only shell skip (optional, P3)** — `ingest.py`: `skip_commands` default for listing-only Bash with a short
   successful result. Measured share is 3–5% of observer input; ship only if review wants it.

10. **Models and routing** (added after review) — `src/cairn/router.py` `model()`: fast tier default `claude-haiku-5-5`; the direct API path is already
    chosen first by `resolve_provider("auto")` when a key exists, the CLI stays the no-key fallback. Escalation to `claude-sonnet-5-5` only on a failed parse by default,
    size rules opt-in via `models.escalate` (§8.2 of findings.md: size-based escalation multiplies cost 2.7–4.2× for 2–4% of events).
11. **Outcome-based skip with a timeline record** (added after review) — `src/cairn/engines/recall/ingest.py`: rule S1–S3 of §8.3 behind `skip_rule = "outcome"` (default off until a guard
    for high-value commands exists); a skipped event is stored as a one-line derived record (`fallback.turn_evidence`), never dropped.

Not changed: `worker_model` default (stays model-when-available; deterministic when not), the fast-tier model (Haiku 4.5
is already the cheapest Claude model; `models.fast` can point at a local OpenAI-compatible model, measured in `findings.md`).

## Risks and how the plan contains them

| Risk | Containment |
| --- | --- |
| Thinking off lowers observation quality | Gate in `measure/grade.py` with replicates; setting `NO_THINKING_TASKS` is a set in one place; env override. |
| Smaller replay window repeats things already recorded | R5 duplicates metric; `observer_replay_exchanges` is a setting. |
| Bigger batches blur which event said what | R1 recall per event; `observe_batch` is a setting; catch-up logic unchanged. |
| The CLI's environment switches change in a later release | Tests assert the env we set; the replay harness is the canary (`run_matrix.sh`); the ledger records what the provider reports. |
| Noise hides a real loss | Replicate runs + a held-out window + a human spot-check sheet; the gate states its resolution (≈3 points). |

## Project Structure

```text
specs/002-observation-token-cost/
├── spec.md  plan.md  tasks.md  findings.md  open-questions.md
└── measure/   ledger_report.py cache_probe.py cache_probe2.py cache_knobs.py cli_probe.py tool_share.py
               replay.py levers.py grade.py families.py summarize.py sdk_estimate.py review_sheet.py
               run_matrix.sh run_matrix2.sh run_heldout.sh
src/cairn/router.py                          (changes 1, 2, 8)
src/cairn/engines/recall/{observer,prompts,settings,ingest}.py   (changes 3, 4, 6, 7, 9)
src/cairn/engines/recall/modes/code.json     (change 5)
tests/engines/recall/…                       (new tests per change)
```
