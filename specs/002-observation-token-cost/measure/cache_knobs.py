"""Step 1c: Claude Code environment switches that change how `claude -p` caches. Same request each time (a 6k-token
system prompt with a unique nonce, so nothing can be read from an earlier run)."""
import os
import random
import tempfile

os.environ["PROBE_CWD"] = tempfile.mkdtemp()
exec(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "cache_probe.py")).read().split("P1 = ")[0].replace("Path(__file__)", f"Path({os.path.abspath(__file__)!r})"))
for label, env in (("default", {}), ("DISABLE_PROMPT_CACHING=1", {"DISABLE_PROMPT_CACHING": "1"}),
                   ("DISABLE_PROMPT_CACHING_HAIKU=1", {"DISABLE_PROMPT_CACHING_HAIKU": "1"}),
                   ("FORCE_PROMPT_CACHING_5M=1", {"FORCE_PROMPT_CACHING_5M": "1"}),
                   ("ENABLE_PROMPT_CACHING_1H=0", {"ENABLE_PROMPT_CACHING_1H": "0"})):
    nonce = f"\n[nonce {random.random()}]\n"
    call(label, S + nonce + "\n".join(EX[0:3]), "Reply ok.", env)
