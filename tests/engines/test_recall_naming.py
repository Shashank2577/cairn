"""No upstream product name may appear in the recall engine, its bridge, its data files or its tests."""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
# The names are assembled from pieces so this file itself stays free of them.
_SPEC, _MEM, _GRAPH = "spec" + "ify", "claude" + "-mem", "graph" + "ify"
FORBIDDEN = re.compile("|".join([
    _GRAPH, "spec-" + "kit", "spec" + "kit", "spec " + "kit", _SPEC + "_cli", _SPEC + "-cli", _SPEC + " cli",
    r"(?<![\w])\." + _SPEC + r"\b", _MEM, _MEM.replace("-", "_"), r"\bc" + r"mem\b", "the" + "dotmack",
    "graph" + "iti", r"\bz" + r"ep\b", "mem" + "0"]), re.IGNORECASE)
TEXT_SUFFIXES = {".py", ".json", ".md", ".js", ".mjs", ".toml", ".txt", ".yaml", ".yml", ".scm"}


def _files():
    yield from (p for p in (ROOT / "src/cairn/engines/recall").rglob("*") if p.is_file()
                and p.suffix in TEXT_SUFFIXES and "__pycache__" not in p.parts)
    yield ROOT / "src/cairn/capture.py"
    yield ROOT / "src/cairn/engines/journal.py"
    yield from (ROOT / "tests/engines").glob("test_recall*.py")


def test_no_upstream_names_in_recall():
    hits = []
    for path in _files():
        if path.resolve() == Path(__file__).resolve():
            continue  # this file spells the forbidden names out
        for n, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            if FORBIDDEN.search(line):
                hits.append(f"{path.relative_to(ROOT)}:{n}: {line.strip()[:120]}")
    assert not hits, "\n".join(hits)


def test_hook_path_stays_light():
    """The hook entry imports only the standard-library part of recall."""
    import subprocess
    import sys
    probe = ("import sys, cairn.capture; print(sorted(m for m in sys.modules if m.split('.')[0] in "
             "('numpy', 'httpx', 'anthropic', 'typer', 'rich', 'fastapi', 'tree_sitter') or m in "
             "('cairn.core', 'cairn.engines.vectors', 'cairn.engines.recall.worker', 'cairn.router')))")
    out = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True, check=True, encoding="utf-8", errors="replace").stdout.strip()
    assert out == "[]"
