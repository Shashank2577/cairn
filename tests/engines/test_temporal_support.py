"""Scripted stand-in for ``cairn.router.Router`` used by the temporal engine tests.

It answers each structured prompt from a tiny "world" description: which entity names exist,
which facts a message states, and which facts contradict which. No network, no model.
"""
from __future__ import annotations

import ast
import json
import re
from dataclasses import dataclass, field


def schema_title(prompt: str) -> str:
    marker = 'in the following format:\n\n'
    i = prompt.rfind(marker)
    if i < 0:
        return ''
    schema, _ = json.JSONDecoder().raw_decode(prompt[i + len(marker):])
    return schema.get('title', '')


def section(prompt: str, tag: str) -> str:
    m = re.search(rf'<{re.escape(tag)}>\s*(.*?)\s*</{re.escape(tag)}>', prompt, re.S)
    return m.group(1) if m else ''


def literal(text: str):
    text = text.split('  #')[0].strip()
    if not text:
        return []
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return ast.literal_eval(text)


@dataclass
class Fact:
    source: str
    target: str
    relation: str
    fact: str
    trigger: str                     # substring of the message that states the fact
    valid_at: str | None = None
    invalid_at: str | None = None


@dataclass
class World:
    entities: dict[str, str] = field(default_factory=dict)   # name -> type name ('Entity' default)
    facts: list[Fact] = field(default_factory=list)
    contradicts: list[tuple[str, str]] = field(default_factory=list)   # (new fact text part, old fact text part)
    aliases: dict[str, str] = field(default_factory=dict)    # extracted name -> canonical existing name
    attributes: dict[str, dict] = field(default_factory=dict)


class FakeRouter:
    provider = 'fake'

    def __init__(self, world: World | None = None, available: bool = True):
        self.world = world or World()
        self.available = available
        self.calls: list[tuple[str, str]] = []

    def complete(self, task, prompt, *, system='', cached_context='', max_tokens=1200, budget=None, tier=None):
        if not self.available:
            raise RuntimeError('no model key configured')
        title = schema_title(prompt)
        self.calls.append((task, title))
        if budget is not None:
            budget.charge(len(prompt) // 4)
        handler = getattr(self, f'_{title}', None)
        if handler is None:
            raise AssertionError(f'no canned answer for {title} ({task})')
        return json.dumps(handler(prompt))

    def titles(self) -> list[str]:
        return [t for _, t in self.calls]

    # ---- canned answers -----------------------------------------------------------------------
    def _message(self, prompt: str) -> str:
        for tag in ('CURRENT MESSAGE', 'CURRENT_MESSAGE', 'TEXT', 'JSON'):
            body = section(prompt, tag)
            if body:
                return body
        return prompt

    def _ExtractedEntities(self, prompt):
        text = self._message(prompt)
        types = {t['entity_type_name']: t['entity_type_id'] for t in literal(section(prompt, 'ENTITY TYPES'))}
        out = []
        for name, type_name in self.world.entities.items():
            if name in text:
                out.append({'name': name, 'entity_type_id': types.get(type_name, 0)})
        return {'extracted_entities': out}

    def _ExtractedEdges(self, prompt):
        text = self._message(prompt)
        names = {n['name'] for n in literal(section(prompt, 'ENTITIES'))}
        edges = []
        for f in self.world.facts:
            if f.trigger in text and f.source in names and f.target in names:
                edges.append({'source_entity_name': f.source, 'target_entity_name': f.target,
                              'relation_type': f.relation, 'fact': f.fact,
                              'valid_at': f.valid_at, 'invalid_at': f.invalid_at})
        return {'edges': edges}

    def _NodeResolutions(self, prompt):
        extracted = literal(section(prompt, 'ENTITIES'))
        existing = literal(section(prompt, 'EXISTING ENTITIES'))
        by_name = {c['name'].lower(): c['candidate_id'] for c in existing}
        out = []
        for e in extracted:
            target = self.world.aliases.get(e['name'], e['name']).lower()
            out.append({'id': e['id'], 'name': e['name'], 'duplicate_candidate_id': by_name.get(target, -1)})
        return {'entity_resolutions': out}

    def _EdgeDuplicate(self, prompt):
        existing = literal(section(prompt, 'EXISTING FACTS'))
        candidates = literal(section(prompt, 'FACT INVALIDATION CANDIDATES'))
        new = section(prompt, 'NEW FACT').strip()
        dup = [e['idx'] for e in existing if e['fact'].strip().lower() == new.lower()]
        contradicted = []
        for e in [*existing, *candidates]:
            for new_part, old_part in self.world.contradicts:
                if new_part in new and old_part in e['fact']:
                    contradicted.append(e['idx'])
        return {'duplicate_facts': dup, 'contradicted_facts': sorted(set(contradicted))}

    def _EdgeTimestamps(self, prompt):
        return {'valid_at': None, 'invalid_at': None}

    def _BatchEdgeTimestamps(self, prompt):
        return {'timestamps': []}

    def _SummarizedEntities(self, prompt):
        ents = literal(section(prompt, 'ENTITIES'))
        return {'summaries': [{'name': e['name'], 'summary': f"{e['name']} summary."} for e in ents]}

    def _Summary(self, prompt):
        return {'summary': 'A community of related engineering entities.'}

    def _SummaryDescription(self, prompt):
        return {'description': 'Payments team'}

    def _SagaSummary(self, prompt):
        return {'summary': 'The saga so far: Alice moved from Acme to Globex.'}

    def _RelevanceJudgements(self, prompt):
        query = section(prompt, 'QUERY').lower()
        out = []
        for m in re.finditer(r'<PASSAGE id="(\d+)">\s*(.*?)\s*</PASSAGE>', prompt, re.S):
            words = set(re.findall(r'\w+', query))
            hit = len(words & set(re.findall(r'\w+', m.group(2).lower())))
            out.append({'id': int(m.group(1)), 'relevant': hit > 0, 'score': min(1.0, hit / 3)})
        return {'judgements': out}

    def __getattr__(self, name):
        # Attribute extraction answers are keyed by the custom type model name.
        if name.startswith('_') and name[1:] in self.world.attributes:
            return lambda prompt: self.world.attributes[name[1:]]
        raise AttributeError(name)


class StubProject:
    """The slice of ``cairn.project.Project`` the temporal service reads."""

    def __init__(self, root, config: dict | None = None, pid: str = 'proj'):
        from pathlib import Path

        self.root = Path(root)
        self.dir = self.root / '.cairn'
        self.id = pid
        self.name = pid
        self.config = config or {}

    def cfg(self, dotted: str, default=None):
        cur = self.config
        for part in dotted.split('.'):
            if not isinstance(cur, dict) or part not in cur:
                return default
            cur = cur[part]
        return cur


def work_world() -> World:
    """Alice moves from Acme to Globex; Bob stays at Acme."""
    return World(
        entities={'Alice': 'Person', 'Acme': 'Organization', 'Globex': 'Organization', 'Bob': 'Person'},
        facts=[
            Fact('Alice', 'Acme', 'WORKS_AT', 'Alice works at Acme', 'Alice joined Acme',
                 valid_at='2024-01-01T00:00:00Z'),
            Fact('Bob', 'Acme', 'WORKS_AT', 'Bob works at Acme', 'Bob is at Acme',
                 valid_at='2024-02-01T00:00:00Z'),
            Fact('Alice', 'Globex', 'WORKS_AT', 'Alice works at Globex', 'Alice left Acme for Globex',
                 valid_at='2024-06-01T00:00:00Z'),
            Fact('Alice', 'Bob', 'KNOWS', 'Alice knows Bob', 'Alice knows Bob'),
        ],
        contradicts=[('Globex', 'Alice works at Acme')],
    )
