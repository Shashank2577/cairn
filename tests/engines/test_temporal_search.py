"""Temporal graph search: hybrid fact search with validity windows, time-travel queries, entity
search, every search recipe, graph-distance reranking, and the local / model rerankers."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from test_temporal_support import FakeRouter, StubProject, work_world  # noqa: E402

from cairn.engines.temporal import TemporalService, TemporalSettings, search_recipes  # noqa: E402
from cairn.engines.temporal.cross_encoder import (  # noqa: E402
    LexicalRerankerClient,
    LocalRerankerClient,
    RouterRerankerClient,
)
from cairn.engines.temporal.cross_encoder.local import bm25_scores, rrf_fuse  # noqa: E402
from cairn.engines.temporal.embedder.local import HashingEmbedder  # noqa: E402
from cairn.engines.temporal.llm_client import RouterLLMClient  # noqa: E402


def run(coro):
    return asyncio.run(coro)


@pytest.fixture(scope='module')
def graph(tmp_path_factory):
    """A small history: Alice moves Acme -> Globex (Jun 2024); Bob stays at Acme."""
    root = tmp_path_factory.mktemp('search')
    project = StubProject(root)
    router = FakeRouter(work_world())
    svc = TemporalService(project, router,
                          settings=TemporalSettings(path=project.dir / 'temporal', group_id='proj'))
    run(svc.add_episode('d1', 'Alice joined Acme. Alice knows Bob.', reference_time='2024-01-01T00:00:00Z'))
    run(svc.add_episode('d2', 'Bob is at Acme.', reference_time='2024-02-01T00:00:00Z'))
    run(svc.add_episode('d3', 'Alice left Acme for Globex.', reference_time='2024-06-01T00:00:00Z'))
    run(svc.build_communities())
    return svc


def test_hybrid_fact_search_returns_validity_windows(graph):
    facts = run(graph.search_facts('where does Alice work', max_facts=10))
    by_text = {f['fact']: f for f in facts}
    assert {'Alice works at Acme', 'Alice works at Globex'} <= set(by_text)
    acme = by_text['Alice works at Acme']
    assert acme['valid_at'] == '2024-01-01T00:00:00+00:00'
    assert acme['invalid_at'] == '2024-06-01T00:00:00+00:00' and not acme['current']
    globex = by_text['Alice works at Globex']
    assert globex['valid_at'] == '2024-06-01T00:00:00+00:00' and globex['invalid_at'] is None
    for f in facts:  # JSON-ready, no vectors
        assert 'fact_embedding' not in f and isinstance(f['episodes'], list)


def test_current_only_and_time_travel(graph):
    now = {f['fact'] for f in run(graph.search_facts('Alice work', current_only=True))}
    assert 'Alice works at Globex' in now and 'Alice works at Acme' not in now
    march = {f['fact'] for f in run(graph.facts_at('2024-03-01T00:00:00Z', 'Alice work'))}
    assert 'Alice works at Acme' in march and 'Alice works at Globex' not in march
    july = {f['fact'] for f in run(graph.facts_at('2024-07-01', 'Alice work'))}
    assert 'Alice works at Globex' in july and 'Alice works at Acme' not in july


def test_date_range_filters(graph):
    early = run(graph.search_facts('works at', valid_at_before='2024-03-01T00:00:00Z'))
    assert {f['fact'] for f in early} == {'Alice works at Acme', 'Bob works at Acme'}
    ended = run(graph.search_facts('works at', invalid_at_after='2024-05-01', invalid_at_before='2024-07-01'))
    assert [f['fact'] for f in ended] == ['Alice works at Acme']
    with pytest.raises(ValueError):
        run(graph.search_facts('x', max_facts=0))


def test_entity_search_and_type_filter(graph):
    names = [n['name'] for n in run(graph.search_nodes('Alice'))]
    assert names[0] == 'Alice'
    people = run(graph.search_nodes('Acme', entity_types=['Entity']))
    assert 'Acme' in [n['name'] for n in people]


def test_center_node_reranks_by_graph_distance(graph):
    alice = run(graph.search_nodes('Alice'))[0]
    facts = run(graph.search_facts('works at', center_node_uuid=alice['uuid'], max_facts=10))
    assert facts and all(f['group_id'] == 'proj' for f in facts)
    nodes = run(graph.search_nodes('Bob', center_node_uuid=alice['uuid']))
    assert 'Bob' in [n['name'] for n in nodes]


def test_every_search_recipe_runs(graph):
    recipes = search_recipes()
    assert len(recipes) == 16
    alice = run(graph.search_nodes('Alice'))[0]
    for name in recipes:
        center = alice['uuid'] if 'NODE_DISTANCE' in name else None
        res = run(graph.search('Alice Acme', recipe=name, limit=5, center_node_uuid=center))
        assert set(res) == {'facts', 'fact_scores', 'nodes', 'node_scores', 'episodes', 'episode_scores',
                            'communities', 'community_scores'}
        if name.startswith(('EDGE', 'COMBINED')):
            assert res['facts'], name
        if name.startswith(('NODE', 'COMBINED')):
            assert res['nodes'], name
    combined = run(graph.search('Alice Acme', recipe='COMBINED_HYBRID_SEARCH_RRF'))
    assert combined['episodes']                       # episode full-text search
    with pytest.raises(ValueError):
        run(graph.search('x', recipe='NOPE'))


def test_advanced_search_with_custom_config_and_filters(graph):
    config = {'edge_config': {'search_methods': ['bm25'], 'reranker': 'reciprocal_rank_fusion'}, 'limit': 3}
    res = run(graph.search('Globex', recipe=config,
                           search_filter={'invalid_at': [[{'comparison_operator': 'IS NULL'}]]}))
    assert [f['fact'] for f in res['facts']] == ['Alice works at Globex']
    alice = run(graph.search_nodes('Alice'))[0]
    bfs = run(graph.search('Alice', recipe='EDGE_HYBRID_SEARCH_RRF', bfs_origin_node_uuids=[alice['uuid']]))
    assert bfs['facts']


def test_get_memory_composes_a_query_from_messages(graph):
    facts = run(graph.get_memory([{'role_type': 'user', 'role': 'carol', 'content': 'Where does Alice work now?'}],
                                 max_facts=5))
    assert any('Alice works at' in f['fact'] for f in facts)


def test_prompt_ready_context_block(graph):
    ctx = run(graph.context('Alice work', limit=5))
    assert ctx['facts'] and ctx['entities']
    assert '<FACTS>' in ctx['context'] and 'Alice works at Globex' in ctx['context']
    assert '"invalid_at": "Present"' in ctx['context']


def test_listing_and_provenance(graph):
    facts = run(graph.list_facts(limit=100))
    assert len(facts) == 4
    entities = run(graph.list_entities(limit=100))
    assert {e['name'] for e in entities} == {'Alice', 'Acme', 'Bob', 'Globex'}
    first = run(graph.list_facts(limit=2))
    rest = run(graph.list_facts(limit=10, uuid_cursor=first[-1]['uuid']))
    assert len(first) + len(rest) == 4
    eps = run(graph.get_episodes(last_n=2))
    assert [e['name'] for e in eps] == ['d3', 'd2']
    early = run(graph.get_episodes(last_n=5, reference_time='2024-03-01'))
    assert [e['name'] for e in early] == ['d2', 'd1']


# ---- rerankers ------------------------------------------------------------------------------------
def test_bm25_and_rank_fusion_primitives():
    scores = bm25_scores('refund gateway', ['refund through the gateway', 'record refunds', 'unrelated text'])
    assert scores[0] > scores[1] > scores[2] == 0
    assert rrf_fuse([[0, 1], [1, 0]], 2)[0] == pytest.approx(rrf_fuse([[0, 1], [1, 0]], 2)[1])


def test_local_rerankers_order_passages(monkeypatch):
    monkeypatch.setenv('CAIRN_RERANKER', 'fusion')
    passages = ['Bob likes pizza', 'Alice works at Acme', 'Acme is a company']
    ranked = run(LocalRerankerClient(HashingEmbedder()).rank('where does Alice work', passages))
    assert ranked[0][0] == 'Alice works at Acme' and len(ranked) == 3
    lexical = run(LexicalRerankerClient().rank('Acme company', passages))
    assert lexical[0][0] == 'Acme is a company'
    assert run(LocalRerankerClient(HashingEmbedder()).rank('x', [])) == []


def test_model_reranker_uses_the_router_and_falls_back():
    router = FakeRouter(work_world())
    reranker = RouterRerankerClient(RouterLLMClient(router))
    ranked = run(reranker.rank('Alice Acme', ['Bob likes pizza', 'Alice works at Acme']))
    assert ranked[0][0] == 'Alice works at Acme' and ranked[0][1] > ranked[1][1]
    assert ('temporal.rerank', 'RelevanceJudgements') in router.calls
    offline = RouterRerankerClient(RouterLLMClient(FakeRouter(available=False)), LexicalRerankerClient())
    assert run(offline.rank('Acme', ['Bob', 'Acme corp']))[0][0] == 'Acme corp'


def test_model_reranker_setting(tmp_path):
    project = StubProject(tmp_path, {'temporal': {'reranker': 'model'}})
    router = FakeRouter(work_world())
    svc = TemporalService(project, router)
    run(svc.add_episode('d1', 'Alice joined Acme. Bob is at Acme.', reference_time='2024-01-01'))
    res = run(svc.search('Alice Acme', recipe='EDGE_HYBRID_SEARCH_CROSS_ENCODER'))
    assert res['facts'][0]['fact'] == 'Alice works at Acme'
    assert 'RelevanceJudgements' in router.titles()
