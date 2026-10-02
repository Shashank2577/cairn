"""The timeline bridge: repository activity -> daily episodes -> temporal facts -> read model."""
from __future__ import annotations

import asyncio
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from test_temporal_support import Fact, FakeRouter, World  # noqa: E402

from cairn.engines.chronicle import Chronicle, facts_matching  # noqa: E402
from cairn.router import Budget  # noqa: E402

DAY = 86400


def day_iso(days_ago: int) -> str:
    d = (datetime.now(timezone.utc) - timedelta(days=days_ago)).date()
    return f'{d.isoformat()}T00:00:00Z'


def repo_world() -> World:
    return World(
        entities={'Ada': 'Contributor', 'shop/payments.py': 'Component', 'shop/api.py': 'Component',
                  'retry guard': 'Decision', 'idempotency keys': 'Decision'},
        facts=[
            Fact('Ada', 'shop/payments.py', 'ADDED', 'Ada added payments in shop/payments.py', 'Add payments'),
            Fact('Ada', 'shop/api.py', 'ADDED', 'Ada added the checkout API in shop/api.py', 'Add checkout API'),
            Fact('shop/payments.py', 'retry guard', 'USES', 'shop/payments.py uses a retry guard',
                 'retry uses a guard', valid_at=day_iso(3)),
            Fact('shop/payments.py', 'idempotency keys', 'USES', 'shop/payments.py relies on idempotency keys',
                 'rely on idempotency keys', valid_at=day_iso(2)),
        ],
        contradicts=[('idempotency keys', 'retry guard')],
    )


def seed_history(brain) -> None:
    """Two older days of team decisions (the repo's commits are all from today)."""
    now = time.time()
    brain.add_events([
        {'id': 'memory:m1', 'ts': now - 3 * DAY, 'kind': 'memory', 'title': 'retry guard',
         'body': 'decision: add a retry guard; payments retry uses a guard flag in shop/payments.py', 'actor': 'ada',
         'refs': ['memory:m1'], 'meta': {'kind': 'decision'}, 'source': 'memory'},
        {'id': 'memory:m2', 'ts': now - 2 * DAY, 'kind': 'memory', 'title': 'idempotency',
         'body': 'decision: drop the retry guard; shop/payments.py and callers rely on idempotency keys',
         'actor': 'ada', 'refs': ['memory:m2'], 'meta': {'kind': 'decision'}, 'source': 'memory'},
    ])


def test_no_model_is_a_clean_skip(cairn):
    chron = Chronicle(cairn.project, cairn.brain, FakeRouter(available=False))
    out = asyncio.run(chron.ingest(Budget(1_000_000)))
    assert out['episodes'] == 0 and out['skipped'] and out['note'] == 'needs a model'
    assert not (cairn.project.dir / 'temporal').exists()


def test_builds_daily_episodes_from_the_read_model(cairn):
    seed_history(cairn.brain)
    episodes = Chronicle(cairn.project, cairn.brain, FakeRouter(repo_world())).pending_episodes()
    assert len(episodes) == 3
    assert [e['day'] for e in episodes] == sorted(e['day'] for e in episodes)
    today = episodes[-1]
    assert 'commit' in today['body'] and 'Add payments' in today['body'] and 'by Ada' in today['body']
    assert any(r.startswith('commit:') for r in today['refs'])
    assert all(e['tokens'] > 0 for e in episodes)


def test_ingest_end_to_end_mirrors_facts_with_validity(cairn):
    seed_history(cairn.brain)
    router = FakeRouter(repo_world())
    chron = Chronicle(cairn.project, cairn.brain, router)
    progress: list[str] = []
    out = asyncio.run(chron.ingest(Budget(1_000_000), on_progress=progress.append))
    assert out['episodes'] == 3 and out['facts'] >= 4 and out['invalidated'] >= 1
    assert len(progress) == 3
    assert (cairn.project.dir / 'temporal' / 'graph.kuzu').exists()

    facts = {f['name']: f for f in cairn.brain.entities('fact')}
    assert {'Ada added payments in shop/payments.py', 'Ada added the checkout API in shop/api.py',
            'shop/payments.py uses a retry guard', 'shop/payments.py relies on idempotency keys'} <= set(facts)
    guard = facts['shop/payments.py uses a retry guard']
    assert guard['source'] == 'timeline' and guard['id'].startswith('fact:')
    assert guard['meta']['invalid_at'] is not None and not guard['meta']['current']
    keys = facts['shop/payments.py relies on idempotency keys']
    assert keys['meta']['invalid_at'] is None and keys['meta']['current']
    assert abs(guard['meta']['invalid_at'] - keys['meta']['valid_at']) < 1

    kinds = {e['kind'] for e in cairn.brain.events(kinds=['fact', 'fact_end'], limit=50)}
    assert kinds == {'fact', 'fact_end'}
    ended = cairn.brain.events(kinds=['fact_end'], limit=10)
    assert ended[0]['title'].startswith('No longer true: shop/payments.py uses a retry guard')

    # deterministic lookups used by the context packs
    hits = facts_matching(cairn.brain, ['retry', 'guard'])
    assert hits and hits[0]['fact'] == 'shop/payments.py uses a retry guard' and hits[0]['invalid_at']

    # the engine task names were used for every call, and the saga chains the days
    assert {t for t, _ in router.calls} <= {'temporal.extract', 'temporal.dedupe', 'temporal.resolve',
                                            'temporal.attributes', 'temporal.timestamps',
                                            'temporal.summarize'}
    svc = chron.service()
    sagas = asyncio.run(svc.list_sagas(group_id=cairn.project.id))
    assert [s['name'] for s in sagas] == [f'{cairn.project.id}-activity']
    assert asyncio.run(chron.search('retry guard'))[0]['fact'] == 'shop/payments.py uses a retry guard'

    # idempotent: nothing new to ingest on the next sync
    again = asyncio.run(Chronicle(cairn.project, cairn.brain, router).ingest(Budget(1_000_000)))
    assert again['episodes'] == 0


def test_budget_stops_before_spending(cairn):
    seed_history(cairn.brain)
    router = FakeRouter(repo_world())
    out = asyncio.run(Chronicle(cairn.project, cairn.brain, router).ingest(Budget(10)))
    assert out['episodes'] == 0 and 'budget reached' in out['note']
    assert router.calls == []
    assert cairn.brain.get_kv('chronicle.cursor', '0') == '0'


def test_tiny_budget_stops_mid_episode_and_charged_usage_stays_bounded(cairn):
    """Per-call enforcement inside an episode (the dogfood 468k-token burn): every model call the
    engine makes checks the shared Budget before running and charges what it moved afterwards, so
    one day's fan-out of resolve/dedupe calls can no longer spend without limit."""
    seed_history(cairn.brain)
    pending = Chronicle(cairn.project, cairn.brain, FakeRouter(repo_world())).pending_episodes()

    class MeteredRouter(FakeRouter):
        """Records the payload charge the client derives from each completed call, so the
        overshoot bound below is measured from the run, not guessed."""

        def __init__(self, world):
            super().__init__(world)
            self.charges: list[int] = []

        def complete(self, task, prompt, *, system='', **kw):
            text = super().complete(task, prompt, system=system, **kw)
            # exactly what the client charges: prompt // 4 + completion // 4
            self.charges.append(len(system + prompt) // 4 + len(text or '') // 4)
            return text

    router = MeteredRouter(repo_world())
    budget = Budget(28_000)  # admits the first day's calls, stops partway through a later one
    out = asyncio.run(Chronicle(cairn.project, cairn.brain, router).ingest(budget))
    assert 'budget reached' in out['note']
    done = out['episodes']
    assert 0 < done < len(pending)
    # the cursor advanced only over completed episodes, so the next sync resumes cleanly
    assert float(cairn.brain.get_kv('chronicle.cursor', '0')) == pending[done - 1]['ts']
    # every completed call was charged, exactly once
    assert sum(router.charges) == budget.used
    # Overshoot bound: a call is admitted while remaining >= its estimate (measured prompt +
    # the 8192-token output ceiling) and charged afterwards; with concurrency 4 at most four
    # calls can be in flight past the last admission, so the total charge can exceed the
    # budget by no more than 4 x one completed call's charge.
    assert budget.used <= budget.total + 4 * max(router.charges)


def test_resync_mirror_rebuilds_from_the_graph(cairn):
    chron = Chronicle(cairn.project, cairn.brain, FakeRouter(repo_world()))
    asyncio.run(chron.ingest(Budget(1_000_000)))
    n = len(cairn.brain.entities('fact'))
    cairn.brain.drop_source('timeline', kinds=('fact',))
    assert cairn.brain.entities('fact') == []
    assert chron.resync_mirror() == n
    assert len(cairn.brain.entities('fact')) == n


def test_sync_runs_the_timeline_step(cairn):
    from cairn import sync

    seed_history(cairn.brain)
    cairn.router = FakeRouter(repo_world())
    results = sync.run(cairn, deep=True, rebuild_map=False)
    timeline = results['timeline']
    assert 'error' not in timeline, timeline
    assert timeline['episodes'] == 3 and timeline['facts'] >= 4
    assert cairn.brain.entities('fact')


def test_timeline_cli(cairn, monkeypatch):
    import json

    from typer.testing import CliRunner

    from cairn.engines.temporal import cli

    router = FakeRouter(repo_world())
    monkeypatch.setattr(cli, '_context', lambda: (cairn.project, cairn.brain, router))
    runner = CliRunner()

    def ok(*args):
        res = runner.invoke(cli.timeline_app, list(args))
        assert res.exit_code == 0, res.output
        return res.output

    out = ok('add', 'decision: add a retry guard; payments retry uses a guard flag in shop/payments.py',
             '--at', day_iso(3))
    assert '+ shop/payments.py uses a retry guard' in out
    out = ok('add', 'decision: drop the retry guard; shop/payments.py and callers rely on idempotency keys',
             '--at', day_iso(2))
    assert '- shop/payments.py uses a retry guard' in out
    assert '× shop/payments.py uses a retry guard' in ok('search', 'retry guard')
    assert 'retry guard' not in ok('search', 'retry guard', '--current')
    facts = json.loads(ok('search', 'idempotency', '--json'))
    assert ok('show', facts[0]['uuid']).startswith('shop/payments.py relies on idempotency keys')
    assert 'shop/payments.py' in ok('entities', 'payments')
    assert 'episodes' in ok('status')
    assert len(json.loads(ok('episodes', '--json'))) == 2
    assert 'Graph data cleared' in ok('clear', '--yes')
    monkeypatch.setattr(cli, '_context', lambda: (cairn.project, cairn.brain, FakeRouter(available=False)))
    res = runner.invoke(cli.timeline_app, ['add', 'anything'])
    assert res.exit_code == 2 and 'needs a model' in res.output


def test_wall_clock_stops_between_episodes_and_stays_resumable(cairn):
    """The dogfood round-2 reframe: the deep tier grinds per-day, so the cap must stop between
    episodes (cursor advanced, remaining days noted) instead of hanging past its welcome."""
    seed_history(cairn.brain)
    router = FakeRouter(repo_world())
    out = asyncio.run(Chronicle(cairn.project, cairn.brain, router).ingest(Budget(1_000_000), wall_seconds=0))
    assert out["episodes"] == 1
    assert "later syncs" in out["note"]
    assert router.calls  # the one episode genuinely ran
    assert cairn.brain.get_kv("chronicle.cursor", "0") != "0"
    # resume: a second ingest with no cap processes the remaining days
    out2 = asyncio.run(Chronicle(cairn.project, cairn.brain, router).ingest(Budget(1_000_000)))
    assert out2["episodes"] >= 1
    assert "later syncs" not in out2.get("note", "")
