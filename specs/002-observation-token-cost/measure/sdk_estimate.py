"""ESTIMATE of the direct Anthropic SDK path (provider "anthropic") for the same 50 events.  No ANTHROPIC_API_KEY exists on the
machine that ran this study, so this path was NOT called; the estimate is arithmetic on measured inputs:

  - per call, input tokens = the CLI's measured input of the `nocache` run minus the CLI's own fixed overhead (763 tokens,
    measured in cli_probe.py), split over system / replay / event text by character share;
  - Router._anthropic puts `system` first and the replay in a cache_control block (5-minute TTL, 1.25x write).  The replay
    block changes on every call (the window slides), so no call can read an earlier entry (see cache_probe2.py for the
    same effect through the CLI): every call pays the 1.25x write on system+replay.  Reads: 0.
  - output: the SDK path does not enable extended thinking, so its output is the `think0` run's measured output.

    python sdk_estimate.py <results dir> <repo>
"""
import json
import sys
from pathlib import Path

CLI_OVERHEAD = 763
d, repo = Path(sys.argv[1]), sys.argv[2]
nc = json.loads((d / f"nocache-{repo}.json").read_text())
t0 = json.loads((d / f"think0-{repo}.json").read_text())
calls = [c for c in nc["calls"]]
cost_cached = cost_plain = 0.0
tin = 0
for c in calls:
    tokens = max(0, c["input"] - CLI_OVERHEAD)
    chars = c["system_chars"] + c["replay_chars"] + c["prompt_chars"]
    share = lambda x: tokens * x / chars
    sys_t, replay_t, ev_t = share(c["system_chars"]), share(c["replay_chars"]), share(c["prompt_chars"])
    tin += tokens
    cost_cached += (sys_t + replay_t) * 1.25 + ev_t * 1.0
    cost_plain += tokens * 1.0
out = t0["totals"]["output"]
n = nc["events"]
print(f"{repo}: observe calls {len(calls)}, estimated input tokens {tin:,}, output (think0 run) {out:,}")
print(f"  as Router sends it (cache_control on a replay block that changes each call): ${ (cost_cached/1e6 + out/1e6*5)/n:.4f}/event")
print(f"  with the cache_control removed:                                             ${ (cost_plain/1e6 + out/1e6*5)/n:.4f}/event")
print(f"  (compress calls excluded; both ESTIMATED at Haiku 4.5 list prices)")
