"""The engine's model adapter over cairn.router.Router, local embeddings, the ontology helpers and
the tool table used to mount the temporal graph on MCP / HTTP."""
from __future__ import annotations

import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest
from pydantic import BaseModel, ValidationError

sys.path.insert(0, str(Path(__file__).parent))
from test_temporal_support import FakeRouter, StubProject, work_world  # noqa: E402

from cairn.engines.temporal import TemporalService, TemporalSettings, ontology  # noqa: E402
from cairn.engines.temporal.api import TOOL_INDEX, TOOLS, call_tool, call_tool_sync  # noqa: E402
from cairn.engines.temporal.embedder.local import HashingEmbedder, LocalEmbedder  # noqa: E402
from cairn.engines.temporal.llm_client import RouterLLMClient, task_for_prompt  # noqa: E402
from cairn.engines.temporal.llm_client.config import ModelSize  # noqa: E402
from cairn.engines.temporal.llm_client.router_client import parse_json_object  # noqa: E402
from cairn.engines.temporal.prompts.extract_nodes import ExtractedEntities  # noqa: E402
from cairn.engines.temporal.prompts.models import Message  # noqa: E402
from cairn.router import Budget, BudgetExceeded  # noqa: E402
from cairn.router import TASK_TIER


def run(coro):
    return asyncio.run(coro)


class ScriptRouter:
    """Returns the queued answers in order and records every call."""

    provider = 'script'

    def __init__(self, *answers, available=True):
        self.answers = list(answers)
        self.available = available
        self.calls: list[dict] = []

    def complete(self, task, prompt, *, system='', max_tokens=1200, budget=None, tier=None, cached_context=''):
        self.calls.append({'task': task, 'prompt': prompt, 'system': system, 'max_tokens': max_tokens,
                           'budget': budget, 'tier': tier})
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer


def msgs():
    return [Message(role='system', content='You extract entities.'), Message(role='user', content='Alice joined Acme')]


# ---- JSON parsing & validation ------------------------------------------------------------------
@pytest.mark.parametrize('text', [
    '{"extracted_entities": []}',
    '```json\n{"extracted_entities": []}\n```',
    'Sure! Here it is:\n{"extracted_entities": []}\nHope that helps.',
])
def test_parse_json_object_tolerates_fences_and_prose(text):
    assert parse_json_object(text) == {'extracted_entities': []}


def test_structured_output_is_validated_and_defaults_filled():
    router = ScriptRouter(json.dumps({'extracted_entities': [{'name': 'Alice', 'entity_type_id': 0}]}))
    client = RouterLLMClient(router)
    out = run(client.generate_response(msgs(), response_model=ExtractedEntities,
                                       prompt_name='extract_nodes.extract_text'))
    assert out == {'extracted_entities': [{'name': 'Alice', 'entity_type_id': 0, 'episode_indices': [0]}]}
    call = router.calls[0]
    assert call['task'] == 'temporal.extract' and (call['tier'] or TASK_TIER[call['task']]) == 'balanced'
    assert call['system'].startswith('You extract entities.')
    assert '"title": "ExtractedEntities"' in call['prompt']   # the schema travels with the prompt


def test_invalid_output_is_retried_with_the_error():
    router = ScriptRouter('not json at all', json.dumps({'extracted_entities': [{'name': 'Alice'}]}),
                          json.dumps({'extracted_entities': []}))
    client = RouterLLMClient(router)
    out = run(client.generate_response(msgs(), response_model=ExtractedEntities))
    assert out == {'extracted_entities': []}
    assert len(router.calls) == 3
    assert 'not a valid ExtractedEntities' in router.calls[2]['prompt']


def test_gives_up_after_max_attempts():
    router = ScriptRouter('nope', 'still nope', 'never')
    with pytest.raises(json.JSONDecodeError):
        run(RouterLLMClient(router).generate_response(msgs(), response_model=ExtractedEntities))
    router = ScriptRouter(*[json.dumps({'wrong': 1})] * 3)
    with pytest.raises(ValidationError):
        run(RouterLLMClient(router).generate_response(msgs(), response_model=ExtractedEntities))


def test_transient_errors_back_off_and_retry(monkeypatch):
    class RateLimitError(Exception):
        pass

    async def no_sleep(_):
        return None

    monkeypatch.setattr('cairn.engines.temporal.llm_client.router_client.asyncio.sleep', no_sleep)
    router = ScriptRouter(RateLimitError('slow down'), json.dumps({'extracted_entities': []}))
    assert run(RouterLLMClient(router).generate_response(msgs(), response_model=ExtractedEntities)) == \
        {'extracted_entities': []}
    router = ScriptRouter(RuntimeError('bad request'))
    with pytest.raises(RuntimeError):
        run(RouterLLMClient(router).generate_response(msgs(), response_model=ExtractedEntities))
    assert len(router.calls) == 1   # permanent errors are not retried


def test_attribute_extraction_keeps_only_returned_fields():
    class Person(BaseModel):
        """A person."""
        title: str | None = None
        team: str | None = None

    router = ScriptRouter(json.dumps({'title': 'CTO', 'junk': 'x'}))
    out = run(RouterLLMClient(router).generate_response(msgs(), response_model=Person, attribute_extraction=True,
                                                        prompt_name='extract_nodes.extract_attributes'))
    assert out == {'title': 'CTO'}                     # 'team' omitted stays omitted (overlay merge)
    assert router.calls[0]['task'] == 'temporal.attributes' \
        and (router.calls[0]['tier'] or TASK_TIER['temporal.attributes']) == 'fast'
    assert 'ATTRIBUTE EXTRACTION' in router.calls[0]['system']


def test_task_mapping_budget_and_availability():
    assert task_for_prompt('dedupe_edges.resolve_edge') == 'temporal.resolve'
    assert task_for_prompt('summarize_sagas.summarize_saga') == 'temporal.saga'
    assert task_for_prompt('something.new', ModelSize.small) == 'temporal.summarize'
    budget = Budget(10_000)
    router = ScriptRouter(json.dumps({'extracted_entities': []}))
    run(RouterLLMClient(router, budget=budget).generate_response(msgs(), response_model=ExtractedEntities))
    assert router.calls[0]['budget'] is budget
    offline = RouterLLMClient(ScriptRouter(available=False))
    assert not offline.available
    with pytest.raises(RuntimeError):
        run(offline.generate_response(msgs(), response_model=ExtractedEntities))


def test_budget_exceeded_stops_immediately():
    router = ScriptRouter(BudgetExceeded('needs more'), json.dumps({'extracted_entities': []}))
    with pytest.raises(BudgetExceeded):
        run(RouterLLMClient(router).generate_response(msgs(), response_model=ExtractedEntities))
    assert len(router.calls) == 1


def test_response_cache(tmp_path):
    router = ScriptRouter(json.dumps({'extracted_entities': []}))
    client = RouterLLMClient(router, cache=True, cache_dir=str(tmp_path / 'cache'))
    first = run(client.generate_response(msgs(), response_model=ExtractedEntities))
    second = run(client.generate_response(msgs(), response_model=ExtractedEntities))
    assert first == second and len(router.calls) == 1


def test_works_with_the_real_router_class(tmp_path, monkeypatch):
    """The adapter speaks cairn.router.Router's exact interface (tier, budget, system)."""
    from cairn.router import Router

    calls = []

    def fake_anthropic(self, model, system, cached, prompt, max_tokens):
        calls.append((model, system, prompt))
        return json.dumps({'extracted_entities': []}), {'input': 10, 'output': 5, 'cache_read': 0, 'cache_write': 0}

    monkeypatch.setenv('ANTHROPIC_API_KEY', 'test-key')
    monkeypatch.setattr(Router, '_anthropic', fake_anthropic)
    router = Router(StubProject(tmp_path))
    assert router.available
    out = run(RouterLLMClient(router).generate_response(msgs(), response_model=ExtractedEntities,
                                                        prompt_name='extract_edges.extract_timestamps'))
    assert out == {'extracted_entities': []}
    assert calls[0][0] == router.model('fast')


# ---- embeddings ---------------------------------------------------------------------------------
def test_local_embeddings_are_384d_unit_and_deterministic():
    emb = LocalEmbedder()
    a = run(emb.create(['Alice works at Acme']))
    batch = run(emb.create_batch(['Alice works at Acme', 'Bob']))
    assert len(a) == 384 and abs(sum(x * x for x in a) - 1) < 1e-4
    assert batch[0] == pytest.approx(a)
    assert emb.embedder_id
    h = HashingEmbedder()
    assert run(h.create('Alice')) == run(h.create_batch(['Alice']))[0]


# ---- ontology -------------------------------------------------------------------------------------
def test_ontology_builders():
    types = ontology.build_entity_types(['Person', {'name': 'Service', 'description': 'A deployed service'}])
    assert types['Person'] is ontology.Person and types['Service'].__doc__ == 'A deployed service'
    assert ontology.build_entity_types([]) is None
    assert set(ontology.ENGINEERING_ENTITY_TYPES) >= {'Contributor', 'Component', 'Feature', 'Decision'}
    assert all(not m.model_fields for m in ontology.ENGINEERING_ENTITY_TYPES.values())
    assert ontology.build_edge_types(['WorksFor', 'Blocks'])['Blocks'].__doc__ == 'Blocks'
    assert ontology.build_edge_type_map([{'source': 'Person', 'edge_types': ['WorksFor']}]) == \
        {('Person', 'Entity'): ['WorksFor']}
    assert ontology.parse_reference_time('2024-01-01T00:00:00Z') == datetime(2024, 1, 1, tzinfo=timezone.utc)
    assert ontology.parse_reference_time('2024-01-01').tzinfo is not None
    assert ontology.coerce_group_ids('a') == ['a'] and ontology.coerce_group_ids('') is None
    f = ontology.build_fact_search_filters(edge_types=['X'], valid_at_after='2024-01-01', current_only=True)
    assert f.edge_types == ['X'] and f.valid_at and f.invalid_at and f.expired_at
    assert ontology.build_fact_search_filters() is None


# ---- tool table -----------------------------------------------------------------------------------
def test_tool_table_dispatch(tmp_path):
    names = {t.name for t in TOOLS}
    assert {'cairn_timeline_add_episode', 'cairn_timeline_search_facts', 'cairn_timeline_search_entities',
            'cairn_timeline_get_episodes', 'cairn_timeline_delete_episode', 'cairn_timeline_clear',
            'cairn_timeline_build_communities', 'cairn_timeline_status'} <= names
    for spec in TOOLS:
        assert hasattr(TemporalService, spec.method), spec.method
        assert spec.http and spec.http[1].startswith('/api/timeline')
    project = StubProject(tmp_path)
    svc = TemporalService(project, FakeRouter(work_world()),
                          settings=TemporalSettings(path=project.dir / 'temporal', group_id='proj'))
    added = call_tool_sync(svc, 'cairn_timeline_add_episode',
                           {'name': 'd1', 'episode_body': 'Alice joined Acme.', 'reference_time': '2024-01-01'})
    assert added['facts'][0]['fact'] == 'Alice works at Acme'
    found = call_tool_sync(svc, 'cairn_timeline_search_facts', {'query': 'Alice', 'max_facts': 3})
    assert found[0]['fact'] == 'Alice works at Acme'
    fact = call_tool_sync(svc, 'cairn_timeline_get_fact', {'uuid': found[0]['uuid']})
    assert fact['uuid'] == found[0]['uuid']
    assert call_tool_sync(svc, 'cairn_timeline_status')['counts']['facts'] == 1

    async def inside_a_loop():
        # servers call from their own event loop
        return await call_tool(svc, 'cairn_timeline_clear', {})

    assert run(inside_a_loop())['success']
    assert TOOL_INDEX['cairn_timeline_add_episode'].needs_model
    with pytest.raises(KeyError):
        call_tool_sync(svc, 'nope')
