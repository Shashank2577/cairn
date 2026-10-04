"""Temporal graph engine: episodes, extraction, dedupe, invalidation, types, namespaces, sagas,
communities, bulk ingest, triplets, deletion, persistence and migrations — on the embedded store
with a scripted router (no network, no model)."""
from __future__ import annotations

import asyncio
import json
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from test_temporal_support import Fact, FakeRouter, StubProject, World, work_world  # noqa: E402

from cairn.engines.temporal import (  # noqa: E402
    EntityEdge,
    GroupIdValidationError,
    ModelUnavailableError,
    StoreBusyError,
    TemporalService,
    TemporalSettings,
)
from cairn.engines.temporal.ontology import Organization, Person, WorksFor  # noqa: E402


def service(tmp_path: Path, router: FakeRouter, **settings) -> TemporalService:
    project = StubProject(tmp_path)
    cfg = TemporalSettings(path=project.dir / 'temporal', group_id='proj', **settings)
    return TemporalService(project, router, settings=cfg)


def run(coro):
    return asyncio.run(coro)


def facts_by_text(result: dict) -> dict[str, dict]:
    return {f['fact']: f for f in result['facts']}


# ---- episodes & extraction -------------------------------------------------------------------------
def test_text_episode_extracts_entities_and_facts(tmp_path):
    router = FakeRouter(work_world())
    svc = service(tmp_path, router)
    res = run(svc.add_episode('day-1', 'Alice joined Acme. Bob is at Acme. Alice knows Bob.',
                              reference_time='2024-01-01T00:00:00Z'))
    assert {n['name'] for n in res['nodes']} == {'Alice', 'Acme', 'Bob'}
    facts = facts_by_text(res)
    assert set(facts) == {'Alice works at Acme', 'Bob works at Acme', 'Alice knows Bob'}
    assert facts['Alice works at Acme']['valid_at'] == '2024-01-01T00:00:00+00:00'
    assert facts['Alice works at Acme']['invalid_at'] is None and facts['Alice works at Acme']['current']
    assert res['episode']['name'] == 'day-1' and res['episode']['source'] == 'text'
    # every model call went through the router with an engine task name
    assert {task for task, _ in router.calls} <= {
        'temporal.extract', 'temporal.dedupe', 'temporal.resolve', 'temporal.attributes',
        'temporal.timestamps', 'temporal.summarize', 'temporal.community', 'temporal.saga'}
    assert 'ExtractedEntities' in router.titles() and 'ExtractedEdges' in router.titles()


def test_json_and_message_episodes_use_their_own_prompts(tmp_path):
    router = FakeRouter(work_world())
    svc = service(tmp_path, router)

    async def go():
        async with svc.session() as engine:
            await svc.add_episode('crm', {'record': 'Alice joined Acme'}, source='json',
                                  reference_time='2024-01-01')
            await svc.add_episode('chat', 'user(alice): Alice knows Bob; Bob is at Acme', source='message',
                                  reference_time='2024-02-01')
            return engine.llm_client.token_tracker.get_usage()

    usage = run(go())
    assert 'extract_nodes.extract_json' in usage
    assert 'extract_nodes.extract_message' in usage
    eps = run(svc.get_episodes(last_n=5))
    assert [e['source'] for e in eps] == ['message', 'json']
    assert json.loads(eps[1]['content']) == {'record': 'Alice joined Acme'}


def test_same_entity_is_deduplicated_across_episodes(tmp_path):
    svc = service(tmp_path, FakeRouter(work_world()))
    r1 = run(svc.add_episode('d1', 'Alice joined Acme.', reference_time='2024-01-01'))
    r2 = run(svc.add_episode('d2', 'Bob is at Acme.', reference_time='2024-02-01'))
    acme1 = next(n for n in r1['nodes'] if n['name'] == 'Acme')
    acme2 = next(n for n in r2['nodes'] if n['name'] == 'Acme')
    assert acme1['uuid'] == acme2['uuid']
    counts = run(svc.status())['counts']
    assert counts['entities'] == 3 and counts['episodes'] == 2


def test_model_resolves_aliases_to_existing_entities(tmp_path):
    world = World(entities={'Alice Smith': 'Entity', 'Acme': 'Entity'},
                  facts=[Fact('Alice Smith', 'Acme', 'WORKS_AT', 'Alice Smith works at Acme', 'joined Acme')],
                  aliases={'Alice': 'Alice Smith'})
    router = FakeRouter(world)
    svc = service(tmp_path, router)
    run(svc.add_episode('d1', 'Alice Smith joined Acme.', reference_time='2024-01-01'))
    world.entities = {'Alice': 'Entity'}
    res = run(svc.add_episode('d2', 'Alice was promoted.', reference_time='2024-02-01'))
    assert 'NodeResolutions' in router.titles()  # the dedupe prompt was used
    assert [n['name'] for n in res['nodes']] == ['Alice Smith']
    assert run(svc.status())['counts']['entities'] == 2


# ---- bi-temporal facts ---------------------------------------------------------------------------
def test_contradicting_fact_invalidates_the_old_one(tmp_path):
    router = FakeRouter(work_world())
    svc = service(tmp_path, router)
    run(svc.add_episode('d1', 'Alice joined Acme.', reference_time='2024-01-01T00:00:00Z'))
    res = run(svc.add_episode('d2', 'Alice left Acme for Globex.', reference_time='2024-06-01T00:00:00Z'))
    assert 'EdgeDuplicate' in router.titles()
    facts = facts_by_text(res)
    old, new = facts['Alice works at Acme'], facts['Alice works at Globex']
    assert old['invalid_at'] == '2024-06-01T00:00:00+00:00'   # stopped being true when Globex began
    assert old['expired_at'] is not None and not old['current']
    assert new['valid_at'] == '2024-06-01T00:00:00+00:00' and new['current']
    assert [f['fact'] for f in res['invalidated']] == ['Alice works at Acme']
    # the history is kept: the old fact is still in the graph, just no longer current
    stored = run(svc.get_entity_edge(old['uuid']))
    assert stored['invalid_at'] == old['invalid_at']
    counts = run(svc.status())['counts']
    assert counts['facts'] == 2 and counts['current_facts'] == 1


def test_older_information_arriving_late_is_bounded_by_newer_facts(tmp_path):
    world = work_world()
    world.contradicts = [('Acme', 'Alice works at Globex'), ('Globex', 'Alice works at Acme')]
    svc = service(tmp_path, FakeRouter(world))
    run(svc.add_episode('later', 'Alice left Acme for Globex.', reference_time='2024-06-01T00:00:00Z'))
    res = run(svc.add_episode('backfill', 'Alice joined Acme.', reference_time='2024-01-01T00:00:00Z'))
    acme = facts_by_text(res)['Alice works at Acme']
    assert acme['invalid_at'] == '2024-06-01T00:00:00+00:00' and not acme['current']
    globex = [f for f in run(svc.search_facts('Alice Globex', current_only=True)) if 'Globex' in f['fact']]
    assert globex and globex[0]['current']


def test_duplicate_fact_is_merged_not_repeated(tmp_path):
    svc = service(tmp_path, FakeRouter(work_world()))
    r1 = run(svc.add_episode('d1', 'Alice knows Bob.', reference_time='2024-01-01'))
    r2 = run(svc.add_episode('d2', 'Alice knows Bob.', reference_time='2024-01-02'))
    assert r1['facts'][0]['uuid'] == r2['facts'][0]['uuid']
    assert len(r2['facts'][0]['episodes']) == 2
    assert run(svc.status())['counts']['facts'] == 1


# ---- types & namespaces ---------------------------------------------------------------------------
def test_custom_entity_types_attributes_and_exclusions(tmp_path):
    world = work_world()
    world.attributes = {'Person': {'description': 'an engineer'}, 'Organization': {'description': 'a company'}}
    router = FakeRouter(world)
    svc = service(tmp_path, router)
    res = run(svc.add_episode('d1', 'Alice joined Acme. Bob is at Acme.', reference_time='2024-01-01',
                              entity_types={'Person': Person, 'Organization': Organization},
                              excluded_entity_types=['Organization']))
    names = {n['name']: n for n in res['nodes']}
    assert set(names) == {'Alice', 'Bob'}            # Acme (Organization) was excluded
    assert 'Person' in names['Alice']['labels']
    assert names['Alice']['attributes'] == {'description': 'an engineer'}
    assert 'Person' in router.titles()               # attribute extraction ran with the type model
    found = run(svc.search_nodes('Alice', entity_types=['Person']))
    assert [n['name'] for n in found][:1] == ['Alice']
    assert run(svc.search_nodes('Alice', entity_types=['Organization'])) == []


def test_custom_edge_types_are_constrained_by_the_type_map(tmp_path):
    world = World(entities={'Alice': 'Person', 'Acme': 'Organization'},
                  facts=[Fact('Alice', 'Acme', 'WorksFor', 'Alice works for Acme as an engineer', 'joined Acme')],
                  attributes={'WorksFor': {'role': 'engineer'}, 'Person': {'description': 'an engineer'},
                              'Organization': {'description': 'a company'}})
    router = FakeRouter(world)
    svc = service(tmp_path, router)
    res = run(svc.add_episode('d1', 'Alice joined Acme as an engineer.', reference_time='2024-01-01',
                              entity_types={'Person': Person, 'Organization': Organization},
                              edge_types={'WorksFor': WorksFor},
                              edge_type_map={('Person', 'Organization'): ['WorksFor']}))
    fact = res['facts'][0]
    assert fact['name'] == 'WorksFor' and fact['attributes'] == {'role': 'engineer'}
    typed = run(svc.search_facts('Alice Acme', edge_types=['WorksFor']))
    assert [f['uuid'] for f in typed] == [fact['uuid']]
    assert run(svc.search_facts('Alice Acme', edge_types=['KNOWS'])) == []


def test_configured_types_come_from_project_config(tmp_path):
    project = StubProject(tmp_path, {'temporal': {
        'entity_types': ['Person', {'name': 'Service', 'description': 'A deployed service'}],
        'custom_types': {'Team': 'A group of engineers'},
        'edge_types': ['WorksFor'],
        'edge_type_map': [{'source': 'Person', 'target': 'Entity', 'edge_types': ['WorksFor']}]}})
    svc = TemporalService(project, FakeRouter(World()))
    types = svc.entity_types()
    assert types['Person'] is Person and types['Service'].__doc__ == 'A deployed service'
    assert types['Team'].__doc__ == 'A group of engineers'
    assert svc.edge_types() == {'WorksFor': WorksFor}
    assert svc.edge_type_map() == {('Person', 'Entity'): ['WorksFor']}
    assert svc.settings.group_id == 'proj' and svc.settings.backend == 'kuzu'
    assert svc.store_path == tmp_path / '.cairn' / 'temporal' / 'graph.kuzu'


def test_namespaces_are_isolated(tmp_path):
    svc = service(tmp_path, FakeRouter(work_world()))
    run(svc.add_episode('a', 'Alice joined Acme.', reference_time='2024-01-01', group_id='team-a'))
    run(svc.add_episode('b', 'Bob is at Acme.', reference_time='2024-01-01', group_id='team-b'))
    assert [f['fact'] for f in run(svc.search_facts('Acme', group_ids='team-a'))] == ['Alice works at Acme']
    assert [f['fact'] for f in run(svc.search_facts('Acme', group_ids=['team-b']))] == ['Bob works at Acme']
    both = run(svc.search_facts('Acme', group_ids=['team-a', 'team-b']))
    assert {f['group_id'] for f in both} == {'team-a', 'team-b'}
    run(svc.delete_group('team-b'))
    assert run(svc.search_facts('Acme', group_ids=['team-b'])) == []
    assert len(run(svc.search_facts('Acme', group_ids=['team-a']))) == 1
    with pytest.raises(GroupIdValidationError):
        run(svc.add_episode('bad', 'x', group_id='no spaces allowed'))


# ---- sagas, communities, bulk, triplets --------------------------------------------------------
def test_sagas_chain_episodes_and_summarize(tmp_path):
    router = FakeRouter(work_world())
    svc = service(tmp_path, router)
    e1 = run(svc.add_episode('d1', 'Alice joined Acme.', reference_time='2024-01-01', saga='story'))
    e2 = run(svc.add_episode('d2', 'Alice left Acme for Globex.', reference_time='2024-06-01', saga='story'))
    sagas = run(svc.list_sagas())
    assert len(sagas) == 1
    saga = sagas[0]
    assert saga['first_episode_uuid'] == e1['episode']['uuid']
    assert saga['last_episode_uuid'] == e2['episode']['uuid']
    summary = run(svc.summarize_saga('story'))
    assert summary['summary'].startswith('The saga so far') and summary['last_summarized_at']
    assert [e['name'] for e in run(svc.get_episodes(saga='story'))] == ['d2', 'd1']
    with pytest.raises(ValueError):
        run(svc.summarize_saga('missing'))


def test_communities_are_built_and_searchable(tmp_path):
    router = FakeRouter(work_world())
    svc = service(tmp_path, router)
    run(svc.add_episode('d1', 'Alice joined Acme. Bob is at Acme. Alice knows Bob.', reference_time='2024-01-01'))
    built = run(svc.build_communities())
    assert built['community_count'] >= 1 and built['edge_count'] >= 3
    assert {'Summary', 'SummaryDescription'} & set(router.titles())
    listed = run(svc.list_communities())
    assert {c['uuid'] for c in listed} == {c['uuid'] for c in built['communities']}
    res = run(svc.search('Payments team', recipe='COMMUNITY_HYBRID_SEARCH_RRF'))
    assert res['communities']
    # rebuilding replaces (not duplicates) the group's communities
    run(svc.build_communities())
    assert len(run(svc.list_communities())) == built['community_count']


def test_update_communities_on_ingest(tmp_path):
    svc = service(tmp_path, FakeRouter(work_world()))
    run(svc.add_episode('d1', 'Alice joined Acme. Alice knows Bob.', reference_time='2024-01-01'))
    run(svc.build_communities())
    res = run(svc.add_episode('d2', 'Bob is at Acme.', reference_time='2024-02-01', update_communities=True))
    assert res['communities']


def test_bulk_ingest(tmp_path):
    svc = service(tmp_path, FakeRouter(work_world()))
    res = run(svc.add_episodes_bulk([
        {'name': 'b1', 'content': 'Alice joined Acme.', 'reference_time': '2024-01-01'},
        {'name': 'b2', 'content': 'Bob is at Acme. Alice knows Bob.', 'reference_time': '2024-02-01'},
    ], saga='bulk'))
    assert len(res['episodes']) == 2
    assert {n['name'] for n in res['nodes']} == {'Alice', 'Acme', 'Bob'}
    assert {f['fact'] for f in res['facts']} == {'Alice works at Acme', 'Bob works at Acme', 'Alice knows Bob'}
    counts = run(svc.status())['counts']
    assert counts['episodes'] == 2 and counts['entities'] == 3 and counts['sagas'] == 1


def test_triplets_messages_and_direct_entities(tmp_path):
    svc = service(tmp_path, FakeRouter(work_world()))
    run(svc.add_episode('d1', 'Alice joined Acme.', reference_time='2024-01-01'))
    res = run(svc.add_triplet('Carol', 'MANAGES', 'Carol manages Alice', 'Alice', valid_at='2024-03-01'))
    assert {n['name'] for n in res['nodes']} == {'Carol', 'Alice'}
    alice = next(n for n in res['nodes'] if n['name'] == 'Alice')
    node = run(svc.get_node(alice['uuid']))
    assert {f['fact'] for f in node['facts']} == {'Carol manages Alice', 'Alice works at Acme'}
    ent = run(svc.add_entity_node('Dave', summary='joined in March'))
    assert run(svc.get_node(ent['uuid']))['summary'] == 'joined in March'
    msgs = run(svc.add_messages([{'content': 'Alice knows Bob', 'role_type': 'user', 'role': 'carol'}]))
    assert msgs['results'][0]['facts'][0]['fact'] == 'Alice knows Bob'
    ep = run(svc.get_episodes(last_n=1))[0]
    assert ep['source'] == 'message' and ep['content'] == 'carol(user): Alice knows Bob'


def test_delete_episode_cascades_to_what_only_it_created(tmp_path):
    svc = service(tmp_path, FakeRouter(work_world()))
    r1 = run(svc.add_episode('d1', 'Alice joined Acme.', reference_time='2024-01-01'))
    r2 = run(svc.add_episode('d2', 'Bob is at Acme.', reference_time='2024-02-01'))
    prov = run(svc.get_episode_entities([r2['episode']['uuid']]))
    assert {n['name'] for n in prov['nodes']} == {'Bob', 'Acme'}
    run(svc.delete_episode(r2['episode']['uuid']))
    counts = run(svc.status())['counts']
    assert counts['episodes'] == 1 and counts['facts'] == 1
    assert counts['entities'] == 2                 # Bob removed, Acme kept (still mentioned by d1)
    run(svc.delete_entity_edge(r1['facts'][0]['uuid']))
    assert run(svc.status())['counts']['facts'] == 0
    run(svc.clear())
    assert run(svc.status())['counts']['entities'] == 0


# ---- store ---------------------------------------------------------------------------------------
def test_embedded_store_persists_across_reopen(tmp_path):
    svc = service(tmp_path, FakeRouter(work_world()))
    run(svc.add_episode('d1', 'Alice joined Acme. Alice knows Bob.', reference_time='2024-01-01'))
    before = run(svc.status())['counts']
    assert (tmp_path / '.cairn' / 'temporal' / 'graph.kuzu').exists()
    # a brand-new service (as in a new process): no model needed to read
    reopened = service(tmp_path, FakeRouter(available=False))
    assert run(reopened.status())['counts'] == before
    facts = run(reopened.search_facts('Alice'))
    assert {f['fact'] for f in facts} == {'Alice works at Acme', 'Alice knows Bob'}


def test_store_is_released_between_calls_and_busy_store_is_reported(tmp_path):
    svc = service(tmp_path, FakeRouter(work_world()), lock_wait_seconds=0.3)
    run(svc.add_entity_node('Alice'))
    path = svc.store_path
    # another process can open it now (the service released the lock)
    code = f'import kuzu; kuzu.Database({str(path)!r}).close(); print("ok")'
    assert subprocess.run([sys.executable, '-c', code], capture_output=True, text=True, encoding="utf-8", errors="replace").stdout.strip() == 'ok'
    holder = subprocess.Popen([sys.executable, '-c', textwrap.dedent(f'''
        import kuzu, sys, time
        db = kuzu.Database({str(path)!r}); print("held", flush=True); time.sleep(5)''')],
        stdout=subprocess.PIPE, text=True, encoding="utf-8", errors="replace")
    try:
        assert holder.stdout.readline().strip() == 'held'
        with pytest.raises(StoreBusyError):
            run(svc.search_facts('Alice'))
    finally:
        holder.kill()
        holder.wait()


def test_migrations_upgrade_an_older_store(tmp_path):
    import kuzu

    from cairn.engines.temporal.migrations import SCHEMA_VERSION, get_meta

    path = tmp_path / '.cairn' / 'temporal' / 'graph.kuzu'
    path.parent.mkdir(parents=True)
    db = kuzu.Database(str(path))
    conn = kuzu.Connection(db)
    # the first on-disk layout: sagas without summary columns, no meta table
    conn.execute('CREATE NODE TABLE Saga (uuid STRING PRIMARY KEY, name STRING, group_id STRING, '
                 'created_at TIMESTAMP)')
    conn.close()
    db.close()
    svc = service(tmp_path, FakeRouter(work_world()))
    run(svc.add_episode('d1', 'Alice joined Acme.', reference_time='2024-01-01', saga='story'))
    assert run(svc.summarize_saga('story'))['summary']

    async def version():
        async with svc.session() as engine:
            return await get_meta(engine.driver, 'schema_version')

    assert run(version()) == str(SCHEMA_VERSION)


def test_changing_the_embedder_reembeds_the_store(tmp_path):
    from cairn.engines.temporal.embedder.local import HashingEmbedder, LocalEmbedder, hash_vector
    from cairn.engines.temporal.service import _migrated

    project = StubProject(tmp_path)
    settings = TemporalSettings(path=project.dir / 'temporal', group_id='proj')
    first = TemporalService(project, FakeRouter(work_world()), settings=settings, embedder=HashingEmbedder())
    run(first.add_episode('d1', 'Alice joined Acme.', reference_time='2024-01-01'))

    class Reversed(LocalEmbedder):
        embedder_id = 'reversed-v1'

        def __init__(self):
            super().__init__(embed_fn=lambda texts: [hash_vector(t[::-1]) for t in texts])

    _migrated.clear()  # a new process
    second = TemporalService(project, FakeRouter(work_world()), settings=settings, embedder=Reversed())

    async def fact_vector():
        async with second.session() as engine:
            edges = await EntityEdge.get_by_group_ids(engine.driver, ['proj'])
            await edges[0].load_fact_embedding(engine.driver)
            return edges[0].fact, edges[0].fact_embedding

    fact, vector = run(fact_vector())
    expected = hash_vector(fact.replace('\n', ' ')[::-1])
    assert max(abs(a - b) for a, b in zip(vector, expected)) < 1e-5


def test_no_model_means_no_writes_but_reads_work(tmp_path):
    svc = service(tmp_path, FakeRouter(available=False))
    with pytest.raises(ModelUnavailableError):
        run(svc.add_episode('d1', 'Alice joined Acme.'))
    with pytest.raises(ModelUnavailableError):
        run(svc.build_communities())
    assert run(svc.search_facts('Alice')) == []
    assert run(svc.status())['status'] == 'ok'


def test_queue_processes_each_group_in_order(tmp_path):
    router = FakeRouter(work_world())
    svc = service(tmp_path, router)

    async def go():
        await svc.enqueue_episode('d1', 'Alice joined Acme.', reference_time='2024-01-01')
        await svc.enqueue_episode('d2', 'Alice left Acme for Globex.', reference_time='2024-06-01')
        await svc.enqueue_episode('x1', 'Bob is at Acme.', reference_time='2024-02-01', group_id='other')
        await svc.queue.join()
        done = list(svc.queue.results)
        await svc.close()
        return done

    done = run(go())
    assert [r['label'] for r in done if r['group_id'] == 'proj'] == ['d1', 'd2']
    assert all(r['status'] == 'ok' for r in done)
    counts = run(svc.status())['counts']
    assert counts['episodes'] == 2 and counts['current_facts'] == 1


def test_backend_selection_from_config(tmp_path):
    def settings(cfg):
        return TemporalSettings.from_project(StubProject(tmp_path, cfg))

    assert settings({}).backend == 'kuzu'
    assert settings({'temporal': {'url': 'bolt://graph:7687'}}).backend == 'neo4j'
    assert settings({'temporal': {'url': 'falkor://graph:6379'}}).backend == 'falkordb'
    assert settings({'deep': {'graph_url': 'redis://graph:6379'}}).backend == 'falkordb'   # legacy key
    assert settings({'temporal': {'url': 'neptune-db://cluster'}}).backend == 'neptune'
    s = settings({'temporal': {'group_id': 'team', 'reranker': 'model', 'concurrency': 2}})
    assert (s.group_id, s.reranker, s.concurrency) == ('team', 'model', 2)


def test_server_backend_driver_is_built_lazily(tmp_path, monkeypatch):
    pytest.importorskip('neo4j')
    monkeypatch.setenv('CAIRN_TEMPORAL_PASSWORD', 'secret')
    project = StubProject(tmp_path, {'temporal': {'url': 'bolt://localhost:7999', 'user': 'neo'}})
    svc = TemporalService(project, FakeRouter(World()))
    driver = svc._server_driver()   # no connection is made until the first query
    assert type(driver).__name__ == 'Neo4jDriver' and driver._database == 'neo4j'


def test_local_entity_extractor_answers_extraction_locally(tmp_path, monkeypatch):
    import types

    class FakeNER:
        @classmethod
        def from_pretrained(cls, model_id):
            return cls()

        def extract_entities(self, text, labels, threshold=0.5, include_confidence=False):
            names = [n for n in ('Alice', 'Acme', 'Bob') if n in text]
            return {'entities': {'Entity': names}}

    monkeypatch.setitem(sys.modules, 'gliner2', types.SimpleNamespace(GLiNER2=FakeNER))
    router = FakeRouter(work_world())
    svc = service(tmp_path, router, local_extractor=True)
    res = run(svc.add_episode('d1', 'Alice joined Acme.', reference_time='2024-01-01'))
    assert {n['name'] for n in res['nodes']} == {'Alice', 'Acme'}
    assert 'ExtractedEntities' not in router.titles()      # answered by the local model
    assert 'ExtractedEdges' in router.titles()             # everything else still uses the router
    assert res['facts'][0]['fact'] == 'Alice works at Acme'
