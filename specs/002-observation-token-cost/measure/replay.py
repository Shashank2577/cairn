"""Replay captured tool events through Cairn's real SessionObserver and record every model call.

Nothing here writes to the repository that supplied the events: it reads a snapshot of that repository's
``.cairn/sessions.db`` and works in a throwaway root. The code path is the production one (ingest_observation
with the skip list, batching rule from worker._next_batch, SessionObserver.process, Router.complete); only the
ledger is replaced by a recorder, so each call's input / output / cache tokens come from the provider's own
usage report.

    python replay.py --snap <dir holding .cairn/sessions.db> --label baseline --out results/ [--dry]
"""
from __future__ import annotations

import argparse
import json
import os
import random
import shutil
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "src"))

from cairn.engines.recall import ingest, prompts  # noqa: E402
from cairn.engines.recall.context import generate_context  # noqa: E402
from cairn.engines.recall.modes import load_mode  # noqa: E402
from cairn.engines.recall.observer import ObserverLLM, Outcome, SessionObserver, render_exchanges  # noqa: E402
from cairn.engines.recall.settings import DEFAULTS  # noqa: E402
from cairn.engines.recall.store import Store  # noqa: E402
from cairn.engines.recall.worker import batch_size  # noqa: E402
from cairn.router import Router  # noqa: E402
import levers  # noqa: E402

CANNED = ("<observation><type>discovery</type><title>Canned dry-run observation of typical length</title>"
          "<subtitle>Stands in for a model answer so prompt sizes can be measured without spending tokens</subtitle>"
          "<facts>" + "".join(f"<fact>Fact number {i} is a sentence of typical length about one file or one command</fact>"
                              for i in range(4)) + "</facts>"
          "<narrative>" + "A narrative of the typical size the production observer writes. " * 8 + "</narrative>"
          "<concepts><concept>how-it-works</concept></concepts><files_read></files_read><files_modified></files_modified>"
          "</observation>")


_orig_result = Router._claude_code_result


def _result_with_detail(data: dict) -> dict:
    """The router keeps four counters; the CLI also reports thinking tokens and the cache TTL split. Keep them for the study."""
    out = _orig_result(data)
    u = data.get("usage") or {}
    out["thinking"] = (u.get("output_tokens_details") or {}).get("thinking_tokens", 0)
    out["cache_1h"] = (u.get("cache_creation") or {}).get("ephemeral_1h_input_tokens", 0)
    return out


Router._claude_code_result = staticmethod(_result_with_detail)

import httpx  # noqa: E402

_post = httpx.post
httpx.post = lambda *a, **k: _post(*a, **{**k, "timeout": 900})  # a local model can need minutes for a 10k-token prompt


class StubProject:
    """Just enough of Project for Router: config lookups."""

    def __init__(self, cfg: dict):
        self._cfg = cfg

    def cfg(self, key, default=None):
        return self._cfg.get(key, default)


class DryRouter:
    available = True
    provider = "dry"

    def __init__(self):
        self.on_settle = None

    def tier_for(self, task, n=0):
        return "fast"

    def model(self, tier):
        return "dry"

    def complete(self, task, prompt, *, system="", cached_context="", max_tokens=0, tier=None, **_):
        if self.on_settle:
            self.on_settle({"input": 0, "output": 0, "cache_read": 0, "cache_write": 0})
        return CANNED


class RecLLM(ObserverLLM):
    """ObserverLLM that records the size of what it sends and the usage the provider reports."""

    def __init__(self, router):
        super().__init__(router)
        self.records: list[dict] = []   # one per observer chat() call
        self.settled: list[dict] = []   # every model call the router made, whatever its task (condensing included)
        self._usage: dict = {}
        if isinstance(router, DryRouter):
            router.on_settle = lambda u: self.settled.append({"task": "dry", **u})
        if isinstance(router, Router):
            def settle(task, tier, model, usage, ok, est, budget):
                self.settled.append({"task": task, "tier": tier, "model": model, "ok": ok, **usage})
            router._settle = settle

    def chat(self, system, exchanges, prompt, *, task, tier=None, max_tokens=4000):
        if getattr(prompts, "_TRAILER", None) and task == "recall_observe":
            prompt = prompt + "\n\n" + prompts._TRAILER
        rec = {"task": task, "system_chars": len(system), "replay_chars": len(render_exchanges(exchanges)),
               "replay_exchanges": len(exchanges), "prompt_chars": len(prompt)}
        mark = len(self.settled)
        t0 = time.time()
        try:
            return super().chat(system, exchanges, prompt, task=task, tier=tier, max_tokens=max_tokens)
        finally:
            rec["secs"] = round(time.time() - t0, 2)
            last = next((c for c in reversed(self.settled[mark:])), {})
            rec.update({k: last.get(k, 0) for k in ("input", "output", "cache_read", "cache_write", "thinking", "cache_1h")})
            rec["model"] = last.get("model")
            self.records.append(rec)


def decode(text):
    """tool_uses keeps the JSON text of the hook payload; the hook hands ingest the decoded value (re-encoding the text
    would double-escape every quote and inflate and blur what the observer reads)."""
    try:
        return json.loads(text) if isinstance(text, str) else text
    except ValueError:
        return text


def pick_events(snap: Path, session: str | None, n: int, seed: int, start: int | None) -> tuple[str, list[dict], dict]:
    db = sqlite3.connect(f"file:{snap}/.cairn/sessions.db?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    if not session:  # the most recent session with plenty of observed events
        row = db.execute("SELECT content_session_id FROM tool_uses GROUP BY 1 HAVING count(*) >= 300 "
                         "ORDER BY max(created_at_epoch) DESC LIMIT 1").fetchone()
        session = row[0]
    rows = [dict(r) for r in db.execute("SELECT * FROM tool_uses WHERE content_session_id=? ORDER BY created_at_epoch, id",
                                        (session,))]
    off = start if start is not None else random.Random(seed).randrange(0, max(1, len(rows) - n))
    events = rows[off:off + n]
    prompts_by_no = {r[0]: r[1] for r in db.execute(
        "SELECT prompt_number, prompt_text FROM user_prompts WHERE content_session_id=?", (session,))}
    return session, events, {"offset": off, "session_events": len(rows), "prompts": prompts_by_no}


def run(args) -> dict:
    snap = Path(args.snap)
    session, events, meta = pick_events(snap, args.session, args.n, args.seed, args.start)
    settings = dict(DEFAULTS)
    settings.update({k: json.loads(v) if v[:1] in "0123456789[{tf" else v for k, v in (s.split("=", 1) for s in args.set)})
    for kv in args.env:
        k, v = kv.split("=", 1)
        os.environ[k] = v
    if args.dry:
        router = DryRouter()
    else:
        cfg = {"models.provider": args.provider, "models.base_url": args.base_url or None}
        for tier in ("fast", "balanced", "deep", "frontier"):
            cfg[f"models.{tier}"] = args.model
        router = Router(StubProject(cfg), None)
    llm = RecLLM(router)
    if not llm.available and not args.derive:
        raise SystemExit(f"provider {args.provider!r} is not usable here (no key / endpoint)")

    root = Path(tempfile.mkdtemp(prefix="cairn-bench-"))
    (root / ".cairn").mkdir()
    store = Store.open(root, project_name="bench")
    real = sqlite3.connect(f"file:{snap}/.cairn/sessions.db?mode=ro", uri=True).execute(
        "SELECT project FROM sdk_sessions WHERE content_session_id=?", (session,)).fetchone()
    project = args.project if args.project != "auto" else (real[0] if real else "bench")
    prior = generate_context(snap, cwd=str(snap), projects=[project], settings=settings) if args.prior else ""
    mode = load_mode(settings.get("mode"))
    flags = levers.apply(args.patch, mode)
    if "trailer_once" in flags:
        levers.install_trailer_once()
    queued = skipped = 0
    for e in events:
        no = e["prompt_number"] or 1
        if not store.get_user_prompt(session, no):
            store.save_user_prompt(session, no, meta["prompts"].get(no) or "(prompt unavailable)")
        res = ingest.ingest_observation(store, settings, content_session_id=session, tool_name=e["tool_name"],
                                        tool_input=decode(e["tool_input"]), tool_response=decode(e["tool_response"]), cwd=e["cwd"],
                                        project="bench", tool_use_id=e["tool_use_id"], prompt_number=no)
        queued += res["status"] == "queued"
        skipped += res["status"] == "skipped"
    sid = store.db.execute("SELECT id FROM sdk_sessions WHERE content_session_id=?", (session,)).fetchone()[0]
    store.ensure_memory_session_id(sid)
    sess = store.get_session_by_id(sid)
    observer = SessionObserver(root, store, llm, mode, settings, prior_context=lambda: prior)
    if args.derive:  # worker_model=false: the deterministic records the worker writes without a model
        from cairn.engines.recall.worker import Worker
        msgs = store.claim(sid, 10_000)
        Worker(events[0].get('cwd') or root, settings=settings).derive(store, sess, msgs)  # paths are relative to the real checkout
        obs = [dict(r) for r in store.db.execute("SELECT * FROM observations ORDER BY id")]
        result = {"label": args.label, "snap": str(snap), "session": session, "offset": meta["offset"], "events": len(events),
                  "queued": queued, "derive": True, "calls": [], "settled": [], "by_task": {}, "outcomes": {"derived": 1},
                  "totals": {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0}, "observations": obs, "batches": [],
                  "event_rows": [{k: e[k] for k in ("tool_use_id", "tool_name", "tool_input", "tool_response", "prompt_number",
                                                    "observation_id")} for e in events]}
        store.close()
        shutil.rmtree(root, ignore_errors=True)
        return result
    outcomes: dict[str, int] = {}
    batches = []
    while True:
        pending = store.peek_pending(sid)
        if not pending:
            break
        size = batch_size(int(settings.get("observe_batch") or 1), int(settings.get("observe_catch_up") or 0), len(pending))
        n = 0
        for p in pending:
            if p["message_type"] != "observation" or n >= size or p["prompt_number"] != pending[0]["prompt_number"]:
                break
            n += 1
        msgs = store.claim(sid, max(1, n))
        before = len(llm.records)
        obs_before = store.db.execute("SELECT COALESCE(MAX(id),0) FROM observations").fetchone()[0]
        outcome = observer.process(sess, msgs)
        new_obs = [r[0] for r in store.db.execute("SELECT id FROM observations WHERE id>? ORDER BY id", (obs_before,))]
        outcomes[outcome] = outcomes.get(outcome, 0) + 1
        batches.append({"events": [m["tool_use_id"] for m in msgs], "outcome": outcome, "calls": len(llm.records) - before,
                        "observation_ids": new_obs})
        if outcome in (Outcome.PRESERVED, Outcome.UNAVAILABLE):
            print("stopping early:", outcome, file=sys.stderr)
            break
    obs = [dict(r) for r in store.db.execute("SELECT * FROM observations ORDER BY id")]
    totals = {k: sum(r.get(k, 0) for r in llm.settled) for k in ("input", "output", "cache_read", "cache_write", "thinking", "cache_1h")}
    by_task: dict[str, dict] = {}
    for c in llm.settled:
        t = by_task.setdefault(c["task"], {"calls": 0, "input": 0, "output": 0, "cache_read": 0, "cache_write": 0, "thinking": 0, "cache_1h": 0})
        t["calls"] += 1
        for k in ("input", "output", "cache_read", "cache_write", "thinking", "cache_1h"):
            t[k] += c.get(k, 0)
    result = {"label": args.label, "snap": str(snap), "session": session, "offset": meta["offset"], "events": len(events),
              "queued": queued, "skipped_by_skip_list": skipped, "prior_chars": len(prior), "settings": {
                  k: settings[k] for k in ("skip_tools", "observe_batch", "observe_catch_up", "observer_replay_exchanges",
                                           "observer_replay_chars", "observer_prompt_chars", "tier_routing")},
              "provider": args.provider, "model": args.model, "env": args.env, "dry": args.dry,
              "calls": llm.records, "settled": llm.settled, "by_task": by_task, "batches": batches, "outcomes": outcomes, "totals": totals,
              "observations": obs, "event_rows": [{k: e[k] for k in ("tool_use_id", "tool_name", "tool_input",
                                                                    "tool_response", "prompt_number", "observation_id")}
                                                  for e in events]}
    store.close()
    shutil.rmtree(root, ignore_errors=True)
    return result


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--snap", required=True, help="directory containing .cairn/sessions.db (a snapshot)")
    ap.add_argument("--project", default="auto", help="project whose session-start context the observer is briefed with")
    ap.add_argument("--session")
    ap.add_argument("--n", type=int, default=50)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--start", type=int, help="event offset inside the session (default: seeded random)")
    ap.add_argument("--label", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--provider", default="claude-code")
    ap.add_argument("--model", default="claude-haiku-4-5-20251001")
    ap.add_argument("--base-url")
    ap.add_argument("--env", action="append", default=[], help="K=V set for the model call")
    ap.add_argument("--set", action="append", default=[], help="recall setting override key=value")
    ap.add_argument("--prior", action="store_true", help="include the session-start context as production does")
    ap.add_argument("--patch", action="append", default=[], help="experimental prompt lever from levers.py")
    ap.add_argument("--derive", action="store_true", help="no model: the deterministic records (worker_model=false)")
    ap.add_argument("--dry", action="store_true", help="no model calls: measure prompt sizes only")
    args = ap.parse_args()
    res = run(args)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    name = f"{args.label}-{Path(args.snap).name}.json"
    (out / name).write_text(json.dumps(res, indent=1, default=str))
    t = res["totals"]
    print(f"{name}: events={res['events']} queued={res['queued']} calls={len(res['calls'])} obs={len(res['observations'])} "
          f"in={t['input']} out={t['output']} cr={t['cache_read']} cw={t['cache_write']} outcomes={res['outcomes']} "
          f"tasks={ {k: v['calls'] for k, v in res['by_task'].items()} }")


if __name__ == "__main__":
    main()
