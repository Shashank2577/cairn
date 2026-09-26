"""The graph engine ships under Cairn's names only: sources, assets, CLI text, output
files and environment variables."""
from __future__ import annotations

import ast
import re
from pathlib import Path

import cairn.engines.graph as pkg

from test_graph_support import built_repo, isolated_env  # noqa: F401  (fixtures)

PKG = Path(pkg.__file__).parent
MAPPER = PKG.parent / "mapper.py"
# Assembled so this file does not itself contain the names it guards against.
_WORDS = ["graph" + "ify", "spec-" + "kit", "spec" + "kit", "spec" + " kit", "specify" + "_cli", "specify" + "-cli",
          "specify" + " cli", r"\.spec" + "ify", "claude" + "-mem", "claude" + "_mem", r"\bc" + r"mem\b",
          "thedot" + "mack", "graph" + "iti", r"\bz" + r"ep\b", "mem" + "0"]
FORBIDDEN = re.compile("|".join(_WORDS), re.IGNORECASE)


def _text_files():
    for p in [*PKG.rglob("*"), MAPPER]:
        if p.is_file() and "__pycache__" not in p.parts:
            yield p


def test_no_upstream_names_in_package_sources_or_assets():
    hits = []
    for p in _text_files():
        text = p.read_text(encoding="utf-8", errors="ignore")
        for m in FORBIDDEN.finditer(text):
            hits.append(f"{p.relative_to(PKG.parent)}: {text[max(0, m.start() - 30):m.end() + 30]!r}")
    assert not hits, "\n".join(hits[:20])


def test_env_vars_are_cairn_prefixed():
    names = set()
    for p in PKG.rglob("*.py"):
        names |= set(re.findall(r"""environ(?:\.get)?\(\s*["']([A-Z][A-Z0-9_]+)["']""", p.read_text()))
        names |= set(re.findall(r"""environ\[\s*["']([A-Z][A-Z0-9_]+)["']""", p.read_text()))
    own = {n for n in names if "GRAPH" in n}
    assert own and all(n.startswith("CAIRN_") for n in own), sorted(own)


def test_no_external_upstream_package_imports():
    for p in PKG.rglob("*.py"):
        tree = ast.parse(p.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            mods = []
            if isinstance(node, ast.Import):
                mods = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                mods = [node.module]
            for m in mods:
                assert not FORBIDDEN.search(m), f"{p.name} imports {m}"


def test_cli_text_and_outputs_are_clean(built_repo, capsys):
    from cairn.engines.graph.cli import main
    main(["--help"])
    assert not FORBIDDEN.search(capsys.readouterr().out)
    out = built_repo / ".cairn" / "graph"
    for p in out.rglob("*"):
        assert not FORBIDDEN.search(p.name), p
        if p.is_file() and p.suffix in (".json", ".md", ".html"):
            assert not FORBIDDEN.search(p.read_text(encoding="utf-8", errors="ignore")), p
    assert not any(FORBIDDEN.search(p.name) for p in built_repo.iterdir())


def test_output_folder_is_cairn_graph():
    from cairn.engines.graph import paths
    assert paths.DEFAULT_OUT == ".cairn/graph"
    assert paths.cairn_home().name == "cairn-home"  # CAIRN_HOME honoured (set by the fixture)
