import json, os, subprocess, sys, shutil
M="claude-haiku-4-5-20251001"
BASE=[shutil.which("claude"),"-p","--model",M,"--output-format","json","--setting-sources","local","--strict-mcp-config","--tools","","--no-session-persistence"]
def run(label, extra=(), env=None, system="You are a terse bot. Reply with one word.", prompt="Say ok."):
    args=BASE+list(extra)+(["--system-prompt",system] if system is not None else [])
    e={**os.environ,"CAIRN_INTERNAL":"1",**(env or {})}
    r=subprocess.run(args,input=prompt,capture_output=True,text=True,cwd=sys.argv[1],env=e)
    try: d=json.loads(r.stdout)
    except Exception: print(label,"FAIL",r.stderr[:300],r.stdout[:200]); return
    u=d["usage"]; print(f"{label:34s} in={u['input_tokens']:>4} out={u['output_tokens']:>4} think={u.get('output_tokens_details',{}).get('thinking_tokens')} cw={u['cache_creation_input_tokens']:>6} cr={u['cache_read_input_tokens']:>6} ttl={u.get('cache_creation')} cost={d.get('total_cost_usd')}")
run("current args, short sys")
run("current args, short sys (repeat)")
run("+ disable-slash-commands",["--disable-slash-commands"])
run("+ MAX_THINKING_TOKENS=0",env={"MAX_THINKING_TOKENS":"0"})
run("+ both",["--disable-slash-commands"],env={"MAX_THINKING_TOKENS":"0"})
run("+ both + effort low",["--disable-slash-commands","--effort","low"],env={"MAX_THINKING_TOKENS":"0"})
