"""Step 1b: why are CLI prefixes not reused?  Controlled `claude -p` calls with a realistic system prompt.

A: system S + user message P1            -> first write
B: identical call again (same S, same P1) -> should read everything
C: same S, different user message P2      -> does a stable system prompt get read when only the message differs?
D: S + "replay" R1 in the system prompt, then S + R2 where R2 = R1 + one more exchange (append-only)
E: S + R2' where R2' drops R1's first exchange and adds one (a sliding window, as observer_replay_exchanges does)
Prints the provider-reported usage including the cache TTL split.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "src"))
from cairn.engines.recall import prompts  # noqa: E402
from cairn.engines.recall.modes import load_mode  # noqa: E402

MODEL = "claude-haiku-4-5-20251001"
FIXED_CWD = os.environ.get("PROBE_CWD")  # same working folder for every call (the router uses a new temp folder each time)
S = prompts.build_init_prompt("probe", "probe-session", "Probe the cache behaviour of the observer", load_mode("code"), "")
S += "\n\n" + "Additional stable instructions that pad the system prompt past the model's minimum cacheable size. " * 120  # ~6k tokens, above Haiku 4.5's 4096-token cache minimum
EX = [f"<turn role=\"user\">\nobserved event number {i}: " + ("the build printed some lines of output. " * 60) + "\n</turn>\n"
      f"<turn role=\"assistant\">\n<observation><type>discovery</type><title>event {i}</title></observation>\n</turn>" for i in range(6)]


def call(label, system, prompt, env=None):
    args = [shutil.which("claude"), "-p", "--model", MODEL, "--output-format", "json", "--setting-sources", "local",
            "--strict-mcp-config", "--tools", "", "--no-session-persistence", "--system-prompt", system]
    cwd = FIXED_CWD or tempfile.mkdtemp()
    r = subprocess.run(args, input=prompt, capture_output=True, text=True, cwd=cwd,
                       env={**os.environ, "CAIRN_INTERNAL": "1", "MAX_THINKING_TOKENS": "0", **(env or {})})
    u = json.loads(r.stdout)["usage"]
    cc = u.get("cache_creation") or {}
    print(f"{label:46s} in={u['input_tokens']:>4} out={u['output_tokens']:>3} cache_write={u['cache_creation_input_tokens']:>6} "
          f"(5m={cc.get('ephemeral_5m_input_tokens')}, 1h={cc.get('ephemeral_1h_input_tokens')}) cache_read={u['cache_read_input_tokens']:>6}")


P1 = "Event 1: the build finished. Reply with the single word ok."
P2 = "Event 2: the tests finished. Reply with the single word ok."
print(f"system prompt: {len(S)} chars")
call("A  S + P1 (first)", S, P1)
call("B  S + P1 (identical repeat)", S, P1)
call("C  S + P2 (same system, new message)", S, P2)
R = lambda ex: "\n".join(ex)
call("D1 S+R[0:3] in system", S + "\n\n" + R(EX[0:3]), P1)
call("D2 S+R[0:4] in system (append one exchange)", S + "\n\n" + R(EX[0:4]), P1)
call("E  S+R[1:5] in system (slide the window)", S + "\n\n" + R(EX[1:5]), P1)
call("F  S + R[0:3] in the USER message instead", S, R(EX[0:3]) + "\n\n" + P1)
call("F2 S + R[0:4] in the USER message (append)", S, R(EX[0:4]) + "\n\n" + P1)
