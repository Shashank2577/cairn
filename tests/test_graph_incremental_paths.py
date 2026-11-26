"""The incremental map build looks each distinct source file up once, not once per node, and gets the same answers."""
from __future__ import annotations

from pathlib import Path

import pytest

from cairn.engines.graph import extract as extract_mod
from cairn.engines.graph import watch
from cairn.engines.graph.build import _norm_source_file


@pytest.fixture()
def paths(tmp_path) -> watch._StoredSourcePaths:
    root = tmp_path / "repo"
    (root / "src").mkdir(parents=True)
    return watch._StoredSourcePaths({"nodes": [], "links": []}, out=tmp_path / "out", project_root=root,
                                    watch_root=root, normalize_source=_norm_source_file)


def reference_identity(sp: watch._StoredSourcePaths, source_file):
    """What identity() answered before it was cached."""
    normalized = sp._normalize_source(source_file)
    if normalized and not Path(normalized).is_absolute() and sp.legacy_watch_relative:
        return sp.absolute_identity(normalized, sp.watch_root)
    return sp.absolute_identity(normalized, sp.existing_source_root)


def reference_rebase(sp: watch._StoredSourcePaths, source_file):
    """What rebase_preserved() did to an item before it was cached: the new source_file, or None for 'unchanged'."""
    identity = reference_identity(sp, source_file)
    if not identity:
        return None
    identity_path = Path(identity)
    if not watch._is_relative_to(identity_path, sp.watch_root):
        return sp.normalize(source_file) or None
    try:
        return identity_path.relative_to(sp.project_root).as_posix()
    except ValueError:
        return identity


def sources(sp: watch._StoredSourcePaths) -> list:
    root = sp.project_root
    return ["src/a.py", "./src/a.py", f"{root}/src/a.py", f"{root}/src/../src/b.py", "src//c.py", "elsewhere/x.py",
            "/somewhere/else/y.py", "gdoc://doc/1", "", None, "src/a.py"]


def test_cached_answers_equal_the_uncached_ones(paths):
    for source_file in sources(paths):
        assert paths.identity(source_file) == reference_identity(paths, source_file)
        assert paths.identity(source_file) == reference_identity(paths, source_file)   # the cached second ask
        inside = paths.in_watch_root(source_file)
        assert inside == paths.in_watch_root(source_file)
        expected = reference_rebase(paths, source_file)
        for _ in range(2):                                                              # first ask, then cached
            item = {"source_file": source_file}
            paths.rebase_preserved(item)
            assert item["source_file"] == (source_file if expected is None else expected), source_file


def test_a_file_shared_by_many_nodes_is_resolved_once(paths, monkeypatch):
    calls = []
    real = paths.absolute_identity
    monkeypatch.setattr(paths, "absolute_identity", lambda *a, **k: calls.append(a) or real(*a, **k))
    items = [{"source_file": "src/a.py"} for _ in range(500)]
    for item in items:
        paths.rebase_preserved(item)
        paths.in_watch_root(item["source_file"])
    assert len(calls) == 1
    assert {i["source_file"] for i in items} == {"src/a.py"}


def test_import_alias_scan_sees_each_context_file_once(tmp_path, monkeypatch):
    root = tmp_path / "proj"
    (root / "pkg").mkdir(parents=True)
    for name in ("a", "b"):
        (root / "pkg" / f"{name}.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    (root / "pkg" / "__init__.py").write_text("", encoding="utf-8")
    seen: list[int] = []
    real = extract_mod._python_absolute_import_alias_files

    def spy(paths, scan_root):
        seen.append(len(list(paths)))
        return real(paths, scan_root)

    monkeypatch.setattr(extract_mod, "_python_absolute_import_alias_files", spy)
    context = [{"id": f"n{i}", "source_file": "pkg/b.py"} for i in range(400)]
    extract_mod.extract([root / "pkg" / "a.py"], root=root, cache_root=root, parallel=False,
                        resolution_context_nodes=context)
    assert seen == [2]   # the changed file plus the one context file, not plus 400 repeats
