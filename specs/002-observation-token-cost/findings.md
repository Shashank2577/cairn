# Findings: where the observation tokens go and what removes them

Everything here was produced by the scripts in `measure/` from snapshots of two real repositories (`cairn`, `spreix-notetaker`).
**Labels**: *measured* = a provider's own usage report, summed; *estimated* = arithmetic on measured values (stated each time).
Prices are Anthropic's published Haiku 4.5 list prices ($1 / $5 per MTok in / out; cache read 0.1×; write 1.25× for a 5-minute entry,
**2× for a 1-hour entry**). Subscription users see plan usage rather than dollars; token counts and ratios carry over.
Defaults were **not** changed in this work. The raw result files and the blind review sheets are in the shared notes folder
(they contain raw tool events from private repositories, so they are not committed).

## 0. Corrections to the brief

| Brief said | Measured |
| --- | --- |
| `recall_observe` is 80% or more of model tokens | **84%** on spreix-notetaker, but **42%** on cairn (`temporal.resolve`, 986 calls, is the largest other spender there). |
| about 13,000 cache-write tokens and 2,300 output tokens per call, $0.028 | Confirmed: 13,321 / 2,321 (spreix), 14,533 / 2,515 (cairn). The $0.028 assumes a 5-minute cache write; the CLI writes **1-hour** entries (§1.3), so the same tokens cost **$0.038–0.042 per call** (estimated). |
| the worker already batches only when far behind | The default `observe_batch` is already 5 (`settings.py`), and the 14,886 captured spreix events became 3,463 calls: **4.3 events per call**. |

## 1. Why caching does not pay (measure step 1)

### 1.1 The ledger (measured, `measure/ledger_report.py`)

| | spreix-notetaker | cairn |
| --- | --- | --- |
| `recall_observe` calls (Haiku, ok) | 3,463 | 546 |
| share of all ledger model tokens | 84% | 42% |
| per call: input / output / cache-write / cache-read | 12 / 2,321 / 13,321 / 118 | 10 / 2,515 / 14,533 / 142 |
| cache-read ÷ (read + write) | **0.88%** | **0.97%** |
| calls with any cache read | 33 of 3,463 (1.0%) | 9 of 546 (1.6%) |
| gap to the previous observe call ≤ 5 min | 97% | 95% |
| estimated cost per call, 5-min write / 1-hour write | $0.0283 / $0.0383 | $0.0308 / $0.0417 |
| next largest tasks | `recall_compress` 312 calls, 6.4M tok; `recall_summarize` 256 calls, 2.9M tok | `temporal.resolve`, `recall_compress` |

The time to live is not the problem: 95–97% of calls follow another within five minutes and still read nothing.

### 1.2 The real prompt (measured totals, split estimated from character share; `replay.py --dry`, `nocache` runs)

| per call (average) | spreix-notetaker | cairn |
| --- | --- | --- |
| system prompt (brief + request + briefing) | ~2,600 tok | ~4,300 tok |
| replayed observer conversation (≤ 8 exchanges / 24,000 chars) | ~5,000 tok | ~4,000 tok |
| the tool events of the batch | ~3,100 tok | ~8,700 tok |
| the CLI's own fixed overhead (measured, `cli_probe.py`) | 763 tok | 763 tok |
| **input total (measured)** | **11,546** | **17,783** |

The replay is a *sliding window*: from the ninth call on, each call drops its oldest exchange and adds one. The system prompt is rewritten for
every new user prompt. In the CLI path the replay is glued into `--system-prompt`, so the whole request is one block that differs every call.

### 1.3 Why prefixes are not reused (measured, `measure/cache_probe.py`, `cache_probe2.py`, `cache_knobs.py`, Haiku 4.5 through `claude -p`)

| Experiment | Result |
| --- | --- |
| Whole request below ~4,096 tokens (Haiku 4.5's cache minimum) | no cache write at all; input billed 1× |
| Same 4.4k-token request sent three times in a row (identical bytes) | **write 4,4xx each time, read 0** |
| Same 6k-token *system prompt*, three times | first writes 6.0k; 2nd and 3rd **read 5,326** and write 673 |
| System prompt grown by one exchange each call (append-only) | read **0** every time |
| System prompt with the window slid by one exchange | read **0** |
| Cache TTL on every write | **1 hour** (`ephemeral_1h_input_tokens`), i.e. 2× the input price |
| `DISABLE_PROMPT_CACHING=1` | no write; the same tokens bill as plain input (1×) |
| `FORCE_PROMPT_CACHING_5M=1` | write at the 5-minute rate (1.25×) |

So a read needs a byte-identical prefix of at least the model's minimum, and the observer's request is never that. Every
call pays **2×** for tokens it will never read: caching makes the input side cost twice what turning it off would.
*Tracked limit*: these probes ran one model (Haiku 4.5) and one CLI version (2.1.300); `run_matrix.sh` is the canary if either changes.

### 1.4 Thinking is on, and it is half the output (measured)

`claude -p` enables extended thinking by default. On replay, thinking tokens were **58%** (spreix) and **37%** (cairn) of output tokens
(`nocache` runs ×3). `MAX_THINKING_TOKENS=0` removes them; `=1024` cuts them by about half.

### 1.5 Condensing calls are a hidden second spender (measured)

`recall_compress` (a model call that condenses an oversized tool field) made **6 of 19** model calls and **46% of input tokens** in one spreix window,
and 312 calls / 6.4M tokens in the production ledger. Its output is then wrapped in `<condensed>` for the observer, which would have cut the
field head/tail anyway.

## 2. Provider paths on the same events (measure step 2)

Same captured events, same observer code, `measure/replay.py`. The first two rows are 50 events (cairn main window); the last rows are the first 20 events of each repository.

| Path | Status | Input tok | Output tok | Cache write / read | Notes |
| --- | --- | --- | --- | --- | --- |
| Claude Code CLI, as shipped (`baseline`) | **measured** | 180,638 | 18,091 | 180,538 / 0 | 10 calls; 1-hour writes; thinking not captured in this early run |
| Claude Code CLI, caching off (`nocache` ×3 mean) | **measured** | 177,826 | 30,988 (11,500 thinking) | 0 / 0 | same input tokens, billed at 1× instead of 2× |
| Claude Code CLI, caching off, thinking off (`think0` ×3) | **measured** | 178,092 | 10,489 | 0 / 0 | |
| Direct Anthropic SDK (`provider = "anthropic"`) | **estimated** (no `ANTHROPIC_API_KEY` on the machine) | ≈ 177,600 (CLI input − 763/call) | ≈ 11,000 (no thinking is sent) | write 1.25× on system+replay every call, read 0 | $0.0051/event as the Router sends it; $0.0047 with `cache_control` removed (`sdk_estimate.py`). The replay block changes each call, so the marker only adds the 25% write premium. |
| OpenAI-compatible, local `gemma4:e4b` via Ollama (20 events) | **measured** | 56,711 | 6,167 | n/a | cairn; marginal cost $0 (own hardware); 4 calls; minutes per call. spreix: 75,436 in / 7,420 out, 6 calls |
| same 20 events, Haiku through the CLI, caching off | **measured** | 62,955 | 10,653 | 0 / 0 | cairn; spreix: 124,854 in / 12,079 out (includes condensing calls) |

Quality on those 20 events (rubric §4): cairn gemma HV recall 100% / outcome 95% / grounding 100% against Haiku 100/100/97;
spreix gemma 46/50/93 against Haiku 38/75/97. Twenty events cannot separate the two; they show a local model is usable, not that it is equal.

## 3. Levers one at a time (measure step 3)

Each row changes one thing from `nocache` (caching off) unless stated. Windows of 50 consecutive captured events: *main* (used to build the
variants) and *held-out* (a later window of the same session, never used to choose). Cost is estimated; tokens are measured.
Generated by `measure/report_tables.py` from the result files.

### 3.1 Summary of effect per lever (single runs on the main window; noise caveat in §4)

| Lever | cairn est. $/event | spreix est. $/event | Quality gate | Verdict |
| --- | --- | --- | --- | --- |
| baseline (CLI as shipped) | 0.0090 | 0.0151 | ref | — |
| **caching off** | 0.0067 (−26%) | 0.0091 (−40%) | identical input; passes | **take: free** |
| thinking off (`MAX_THINKING_TOKENS=0`), ×3 | 0.0046 (−49%) | 0.0083 (−45%) | passes (HV 92 vs 97; outcome 86 vs 84; grounding 92 vs 95, range 86–95) | take, watch grounding |
| thinking capped at 1,024 | 0.0049 | 0.0077 | passes spreix; cairn HV 90% is just under the gate | not better than off |
| no condensing call | (no oversize payload in this window) | in `lean` (below): 19 → 13 calls | passes | **take** |
| closing instructions once per call + shorter brief (`terse`) | 0.0049 (−27% vs ref) | 0.0079 (−13%) | passes | take |
| replay window 2 exchanges / 6,000 chars | 0.0063 (−7%) | 0.0086 (−5%) | passes | take (small) |
| prompt-field cap 12,000 chars/call | 0.0055 | 0.0142 (thinking noise) | passes | small; not recommended alone |
| batch size 10 | 0.0034 (−49%) | 0.0073 (−20%) | cairn 84% HV, spreix passes | big saving, **fewer and broader observations**; needs a decision |
| skip list for read-only shell | see §3.2 | see §3.2 | n/a | ≤ 5% of input: optional |
| `worker_model = false` (deterministic) | $0 | $0 | **fails** | keep as the no-model fallback, not the default |
| cheaper fast-tier model | Haiku 4.5 is already the cheapest Claude model | | | point `models.fast` at a local model if wanted (§2) |

### 3.2 Skip list (measured counterfactual, `measure/tool_share.py`, all captured events)

| class | spreix: % events / % observer input | cairn: % events / % observer input | share of batches the model already drops |
| --- | --- | --- | --- |
| Bash, not read-only | 73.5% / 60.9% | 71.6% / 51.7% | 6% / 35% |
| Read | 7.1% / 13.0% | 10.9% / 19.0% | 44% / 21% |
| Write | 4.9% / 11.5% | 5.2% / 17.2% | 1% / 18% |
| Edit | 4.4% / 8.2% | 2.7% / 6.2% | 2% / 31% |
| Bash, read-only listing (`ls`, `git status`, `cat`…) | 7.9% / 4.7% | 3.8% / 3.2% | 20% / 23% |

A skip list of listing-only shell commands removes **3–5%** of the observer's input; skipping `Read` too would remove 13–19% but loses what the
agent *learned* from a file, which is the discovery type the context injection leans on. A skip list is the smallest lever here.

### 3.3 Combined candidates (replicated, both windows)

`lean` = caching off + thinking off + no condensing call + terse brief + closing instructions once (batch 5, replay 8/24,000).
`lean2` = `lean` + `observe_batch=10` + replay 2 exchanges / 6,000 chars.

**cairn — main window** (50 events; n = runs averaged; "baseline" and the thinking column of `baseline` come from an early run that did not capture thinking)

| config | n | model calls | input-side tok | of which cache-written | output tok | thinking tok | observations | tok/event | tok/observation | est. $/event | HV recall | outcome recall | grounding |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| baseline | 1 | 10 | 180,638 | 180,538 | 18,091 | 0 | 6 | 3,975 | 33,122 | $0.0090 | 94% | 83% | 97% |
| nocache | 3 | 10 | 177,826 | 0 | 30,988 | 11,500 | 26 | 4,176 | 7,930 | $0.0067 (-26%) | 97% | 84% | 95% |
| think0 | 3 | 10 | 178,092 | 0 | 10,489 | 0 | 7 | 3,772 | 26,940 | $0.0046 (-49%) | 92% | 86% | 92% |
| lean | 2 | 10 | 168,604 | 0 | 17,425 | 0 | 24 | 3,721 | 7,916 | $0.0051 (-43%) | 97% | 83% | 96% |
| lean2 | 2 | 5 | 81,648 | 0 | 8,638 | 0 | 8 | 1,806 | 10,622 | $0.0025 (-72%) | 94% | 85% | 100% |

**cairn — held-out window**

| config | n | model calls | input-side tok | of which cache-written | output tok | thinking tok | observations | tok/event | tok/observation | est. $/event | HV recall | outcome recall | grounding |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| baseline | 1 | 13 | 203,036 | 202,906 | 42,367 | 17,615 | 31 | 4,908 | 7,916 | $0.0124 | 100% | 92% | 98% |
| nocache | 2 | 13 | 199,774 | 0 | 34,536 | 14,525 | 30 | 4,686 | 7,682 | $0.0074 (-40%) | 98% | 100% | 96% |
| lean2 | 2 | 6 | 84,048 | 0 | 12,370 | 0 | 20 | 1,928 | 4,945 | $0.0029 (-76%) | 100% | 96% | 98% |

**spreix-notetaker — main window**

| config | n | model calls | input-side tok | of which cache-written | output tok | thinking tok | observations | tok/event | tok/observation | est. $/event | HV recall | outcome recall | grounding |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| baseline | 1 | 19 | 284,018 | 283,828 | 37,477 | 0 | 26 | 6,430 | 12,365 | $0.0151 | 69% | 82% | 99% |
| nocache | 3 | 19 | 280,328 | 0 | 34,576 | 20,190 | 23 | 6,298 | 13,893 | $0.0091 (-40%) | 77% | 73% | 93% |
| think0 | 3 | 18 | 273,419 | 0 | 28,518 | 0 | 22 | 6,039 | 13,936 | $0.0083 (-45%) | 71% | 88% | 94% |
| lean | 2 | 13 | 142,526 | 0 | 12,132 | 0 | 20 | 3,093 | 7,733 | $0.0041 (-73%) | 81% | 77% | 92% |
| lean2 | 2 | 9 | 71,763 | 0 | 8,836 | 0 | 16 | 1,612 | 4,885 | $0.0023 (-85%) | 91% | 86% | 94% |

**spreix-notetaker — held-out window**

| config | n | model calls | input-side tok | of which cache-written | output tok | thinking tok | observations | tok/event | tok/observation | est. $/event | HV recall | outcome recall | grounding |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| baseline | 1 | 17 | 222,940 | 222,770 | 33,039 | 16,747 | 24 | 5,120 | 10,666 | $0.0122 | 83% | 50% | 93% |
| nocache | 2 | 17 | 221,801 | 0 | 32,302 | 16,674 | 23 | 5,082 | 11,048 | $0.0077 (-37%) | 93% | 64% | 93% |
| lean2 | 2 | 8 | 78,177 | 0 | 7,955 | 0 | 14 | 1,723 | 6,152 | $0.0024 (-81%) | 91% | 57% | 96% |

**Before → after, on the two repositories (measured tokens; estimated dollars)**

| | tokens per tool event | est. $ per tool event | tokens per observation |
| --- | --- | --- | --- |
| cairn, main window | 3,975 → 1,806 (−55%) | 0.0090 → 0.0025 (−72%) | 33,122 → 10,622 (see note) |
| cairn, held-out | 4,908 → 1,928 (−61%) | 0.0124 → 0.0029 (−76%) | 7,916 → 4,945 (−38%) |
| spreix, main | 6,430 → 1,612 (−75%) | 0.0151 → 0.0023 (−85%) | 12,365 → 4,885 (−60%) |
| spreix, held-out | 5,120 → 1,723 (−66%) | 0.0122 → 0.0024 (−81%) | 10,666 → 6,152 (−42%) |

Note: the cairn main-window `baseline` run happened to produce only 6 observations (the replicate runs of the same configuration produced 23–28), so its
tokens-per-observation is an outlier; the like-for-like comparison is `nocache` (7,930) → `lean2` (10,622), i.e. **fewer, broader observations**
(8 vs 26) at about half the tokens per event. Whether that granularity matters is question 2 in `open-questions.md`.

## 4. Quality gate (measure step 4)

**Rubric** (`measure/grade.py`; every score is computed from the raw events and the stored observation text, no model verdict):

- **R1 high-value recall** — events that changed state or reported an outcome (Edit/Write; failed, test, build, commit, push, PR, deploy, install commands) whose
  anchors (file basename, command signature, distinctive outcome token) appear in the observations.
- **R1o outcome recall** — events whose result has a checkable outcome (a count such as "12 passed", a commit hash, an error class) whose outcome appears.
- **R3 grounding** — specific tokens in the observations (paths, 3+-digit numbers, backticked or CamelCase identifiers) that occur in the raw events: low means invention.
- **R4 form**, **R5 duplicates**, **R6 synthesized facts** — share of facts that state a finding rather than echo `ran`/`read`/`modified`.

**Resolution and noise.** Fifty events give about 3 points of resolution on R1 (one event is 1–3 points), and replicates of the *same* configuration moved by up to
20 points on spreix (`nocache` HV recall 69%, 77%, 89%) and by 9 points on cairn grounding across `think0` replicates (86–95%). The gate therefore compares a candidate's mean with the reference family's *minimum* and allows 5 points.
That is a coarse gate; it catches large losses and says nothing about small ones.

| Check | Result |
| --- | --- |
| lean2 vs nocache, cairn main (n=2 vs 3) | HV 94 vs 97, outcome 85 vs 84, grounding 100 vs 95 — pass |
| lean2 vs nocache, cairn held-out | HV 100 vs 98, outcome 96 vs 100, grounding 98 vs 96 — pass |
| lean2 vs nocache, spreix main | HV 91 vs 77, outcome 86 vs 73, grounding 94 vs 93 — pass |
| lean2 vs nocache, spreix held-out | HV 91 vs 93, **outcome 57 vs 64** (baseline 50), grounding 96 vs 93 — **fails the 5-point outcome rule by 2 points**; every arm is low here because the window is long `ssh` sessions |
| thinking off alone, cairn | grounding 92 [86–95] vs 95: the weakest signal in the study; watch it |
| deterministic records (`worker_model=false`) | HV recall 68–97% (it lists every file and command), **outcome recall equal or lower, synthesized facts 0%**, fails the gate on cairn (68 vs 97) |
| 20-event local model | usable; too small to compare |

**Human check.** `review_sheet.py` produced a blind sheet of 16 items per repository (8 from `nocache`, 8 from `lean2`, shuffled, labels hidden; raw events beside each).
It is in the shared notes folder and has **not been graded by a person**. I read the cairn sheet once and could not tell the arms apart by style or specificity; that is a
model's impression, not a gate. Please grade at least 10 items per repository (three 0–2 questions each) before the defaults change.

## 5. Recommended default (for review)

Two tiers, because the batch/replay change trades granularity for tokens:

1. **Safe** (`lean`): caching off for the CLI path; thinking off for fast-tier bulk tasks; no condensing call; closing instructions once per call; terse brief.
   Measured: −43% (cairn) and −73% (spreix) estimated cost per event against the shipped default; quality inside the noise band on both windows.
2. **Economy** (`lean2` = Safe + `observe_batch 10` + replay 2/6,000): −72% to −85% estimated cost per event, −55% to −75% tokens per event;
   observations are fewer and broader. Quality passes on three of four windows and misses the outcome rule by 2 points on one.

Recommend **Safe as the default and Economy as a documented profile** (`observe_batch`, `observer_replay_*`), unless review says broader observations are fine, in which case make Economy the default.
Do not make `worker_model=false` the default: it records activity but not findings. Nothing in either tier needs a key, the Claude Code CLI, or any other tool.

## 6. What I could not measure

- The Anthropic SDK path (no key). It is an estimate with its inputs stated (§2).
- Other Claude CLI versions, other fast-tier models, Sonnet/Opus minimum-cache thresholds.
- Long-run effects: whether broader observations change what later sessions retrieve (needs the `cairn_recall` evaluation, a follow-up).
- Only one session per repository was sampled (two windows of it). The skip-list and ledger numbers cover all captured events; the replay numbers do not.

## 7. Reproduce

```bash
python measure/ledger_report.py <brain.db snapshot>...                 # §1.1
python measure/cache_probe.py; python measure/cache_probe2.py; python measure/cache_knobs.py   # §1.3 (spends a few cents)
python measure/tool_share.py <sessions.db snapshot>...                 # §3.2
PY=<cairn venv python> measure/run_matrix.sh  <snapshot dir> <out>     # single-lever runs
PY=<cairn venv python> measure/run_matrix2.sh <snapshot dir> <out>     # replicates and candidates
PY=<cairn venv python> measure/run_heldout.sh <snapshot dir> <out>     # held-out window
python measure/families.py <out> --repo <name>                          # replicate-aware gate
python measure/report_tables.py <matrix> <heldout> <gemma>              # the tables above
```
Snapshots: `sqlite3 .backup` copies of `.cairn/sessions.db` and `.cairn/brain.db`; the harness never writes to the source repository.
