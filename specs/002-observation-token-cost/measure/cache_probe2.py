"""Step 1b (second probe): does a byte-identical or append-only request get a cache read through `claude -p`?
Every run salts the system prompt with a nonce so only this run's own earlier calls could be read."""
import os
import random
import tempfile

os.environ["PROBE_CWD"] = tempfile.mkdtemp()  # one working folder for all calls
exec(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "cache_probe.py")).read().split("P1 = ")[0].replace("Path(__file__)", f"Path({os.path.abspath(__file__)!r})"))
nonce = f"\n[nonce {random.random()}]\n"
print("-- three identical calls, stable system, tiny message")
for i in range(3):
    call(f"a{i} S+nonce", S + nonce, "Reply ok.")
print("-- three identical calls, bigger system")
BIG = S + nonce + "\n".join(EX[0:3])
for i in range(3):
    call(f"b{i} S+R3+nonce", BIG, "Reply ok.")
print("-- append-only growth in the system prompt")
for k in (3, 4, 5, 6):
    call(f"c{k} S+R[0:{k}]+nonce", S + nonce + "\n".join(EX[0:k]), "Reply ok.")
print("-- sliding window")
call("d  S+R[1:6]+nonce", S + nonce + "\n".join(EX[1:6]), "Reply ok.")
