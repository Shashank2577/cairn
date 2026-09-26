"""No upstream project name may appear in the temporal engine, its bridge or its tests."""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

# Built from pieces so this file does not contain the words it forbids.
_WORDS = [
    'graph' + 'ify', 'spec' + '-kit', 'spec' + 'kit', 'spec' + ' kit', 'spec' + 'ify_cli', 'spec' + 'ify-cli',
    'spec' + 'ify cli', r'\.' + 'spec' + 'ify', 'claude' + '-mem', 'claude' + '_mem', r'\b' + 'c' + 'mem' + r'\b',
    'the' + 'dot' + 'mack', 'graph' + 'iti', r'\b' + 'z' + 'ep' + r'\b', 'mem' + '0',
]
FORBIDDEN = re.compile('|'.join(_WORDS), re.IGNORECASE)


def _files():
    yield from (ROOT / 'src' / 'cairn' / 'engines' / 'temporal').rglob('*')
    yield ROOT / 'src' / 'cairn' / 'engines' / 'chronicle.py'
    yield from (ROOT / 'tests' / 'engines').glob('test_temporal*.py')


def test_no_upstream_names_in_code_comments_or_paths():
    hits = []
    for path in _files():
        if not path.is_file() or path.suffix in ('.pyc',) or '__pycache__' in path.parts:
            continue
        rel = path.relative_to(ROOT).as_posix()
        if FORBIDDEN.search(rel):
            hits.append(rel)
        for n, line in enumerate(path.read_text(errors='ignore').splitlines(), 1):
            if FORBIDDEN.search(line):
                hits.append(f'{rel}:{n}: {line.strip()[:120]}')
    assert hits == []


def test_env_vars_are_cairn_prefixed():
    pattern = re.compile(r"os\.(?:getenv|environ\.get)\(\s*'([A-Z0-9_]+)'")
    names = set()
    for path in (ROOT / 'src' / 'cairn' / 'engines' / 'temporal').rglob('*.py'):
        names |= set(pattern.findall(path.read_text()))
    assert names and all(n.startswith('CAIRN_') for n in names), names


def test_no_telemetry_or_hosted_gateways():
    code = '\n'.join(p.read_text() for p in (ROOT / 'src' / 'cairn' / 'engines' / 'temporal').rglob('*.py'))
    for banned in ('posthog', 'capture_event', 'from_api(', 'api.openai.com', 'OTLPSpanExporter'):
        assert banned not in code, banned
    # local tracing hooks (opentelemetry spans the caller configures) are fine; usage pings are not
    assert not re.search(r'(?<!open)telemetry', code, re.IGNORECASE)
