"""Cross-repository map: repositories as nodes, code imports between them as links."""
from __future__ import annotations

import json

import pytest

from test_graph_support import isolated_env, make_repo  # noqa: F401  (fixtures)

CORE = {
    "pyproject.toml": '[project]\nname = "acme-core"\ndependencies = ["requests>=2", "pydantic"]\n',
    "src/acme_core/__init__.py": "",
    "src/acme_core/money.py": "def to_cents(x):\n    return int(x * 100)\n",
    "src/acme_core/ledger.py": "def post(x):\n    return x\n",
    "tests/__init__.py": "",
    "tests/test_money.py": "from acme_core.money import to_cents\n\n\ndef test_it():\n    assert to_cents(1) == 100\n",
    "package.json": json.dumps({"name": "acme-root", "private": True, "workspaces": ["packages/*"]}),
    "packages/ui/package.json": json.dumps({"name": "@acme/ui", "version": "1.0.0"}),
    "packages/ui/price.ts": "export function total(xs: number[]): number { return xs.reduce((a, b) => a + b, 0); }\n",
    "go.mod": "module github.com/acme/core\n\ngo 1.22\n",
    "money/money.go": "package money\n\nfunc Cents(x int) int { return x * 100 }\n",
    "README.md": "# Acme core\n\n[![ci](https://x/badge.svg)](https://x)\n\nShared **money**, ledger and UI packages.\n",
}
SHOP = {
    "pyproject.toml": '[project]\nname = "shop"\ndependencies = ["acme-core", "requests", "pydantic"]\n',
    "shop/__init__.py": "",
    "shop/billing.py": (
        "import os\nimport acme_core\nfrom acme_core.money import to_cents\nfrom acme_core import ledger\n"
        "import requests\n\n\ndef bill(x):\n    return to_cents(x) + ledger.post(x)\n"
    ),
    "shop/refunds.py": "from acme_core.ledger import post\n\n\ndef refund(x):\n    return post(-x)\n",
    "web/cart.ts": 'import { total } from "@acme/ui/price";\nimport React from "react";\n'
                   "export function sum(xs: number[]) { return total(xs); }\n",
    "main.go": 'package main\n\nimport (\n\t"fmt"\n\t"github.com/acme/core/money"\n)\n\n'
               "func main() { fmt.Println(money.Cents(1)) }\n",
    "README.md": "# Shop\n\nThe shop storefront: carts, billing\nand checkout.\n\n## Install\n\npip install shop\n",
}
LONELY = {"lonely.py": "import json\n\n\ndef lonely():\n    return json.dumps(1)\n"}


def _project(root, name=None):
    from cairn.engines import mapper
    from cairn.engines.graph import api
    assert api.build(root)["ok"]
    return (root.name, name or root.name.title(), root, mapper.MapIndex.load(api.graph_json(root)))


@pytest.fixture()
def three(tmp_path):
    core = make_repo(tmp_path / "core", CORE)
    shop = make_repo(tmp_path / "shop", SHOP)
    lonely = make_repo(tmp_path / "lonely", LONELY)
    return [_project(core), _project(shop), _project(lonely)]


def test_repository_nodes(three):
    from cairn.engines.graph.repos import repos
    out = repos(three)
    assert out["level"] == "repos"
    nodes = {n["id"]: n for n in out["nodes"]}
    assert set(nodes) == {"core", "shop", "lonely"}
    core = nodes["core"]
    assert core["name"] == "Core" and core["nodes"] > 5 and core["files"] >= 8 and core["edges"] > 0
    assert ".py" in core["languages"] and ".go" in core["languages"]
    assert 1 <= len(core["communities"]) <= 3
    assert {"id", "label", "area", "size"} <= set(core["communities"][0])
    packages = {(p["ecosystem"], p["name"]) for p in core["packages"]}
    assert {("python", "acme-core"), ("js", "@acme/ui"), ("go", "github.com/acme/core")} <= packages
    assert ("python", "tests") not in packages  # test folders are never a product package
    assert core["description"] == "Shared money, ledger and UI packages."
    assert nodes["shop"]["description"] == "The shop storefront: carts, billing and checkout."
    assert nodes["lonely"]["description"] == "" and nodes["lonely"]["packages"] == []


def test_import_link_with_weight_and_evidence(three):
    from cairn.engines.graph.repos import repos
    out = repos(three)
    links = {(l["source"], l["target"]): l for l in out["links"]}
    assert set(links) == {("shop", "core")}  # the lonely repo has no links
    link = links[("shop", "core")]
    assert link["kind"] == "imports" and link["declared"] is True
    # python: billing.py imports acme_core, acme_core.money; refunds.py imports acme_core.ledger;
    # typescript imports @acme/ui; go imports github.com/acme/core/money
    assert link["weight"] == 5
    assert set(link["packages"]) == {"acme-core", "@acme/ui", "github.com/acme/core"}
    assert len(link["evidence"]) == 5
    names = {(e["file"], e["name"]) for e in link["evidence"]}
    assert ("shop/billing.py", "acme_core.money") in names
    assert all(isinstance(e["line"], int) and e["package"] and e["node"] for e in link["evidence"])
    more = repos(three, evidence=10)
    ev = {(l["source"], l["target"]): l for l in more["links"]}[("shop", "core")]["evidence"]
    assert ("web/cart.ts", "@acme/ui") in {(e["file"], e["name"]) for e in ev}
    assert ("main.go", "github.com/acme/core/money") in {(e["file"], e["name"]) for e in ev}
    assert ("shop/refunds.py", "acme_core.ledger") in {(e["file"], e["name"]) for e in ev}


def test_same_repo_and_stdlib_imports_never_link(three):
    from cairn.engines.graph.repos import repos
    out = repos(three)
    assert not any(l["source"] == l["target"] for l in out["links"])
    assert not any(l["source"] == "lonely" or l["target"] == "lonely" for l in out["links"])


def test_manifest_only_dependency_is_a_depends_link(tmp_path):
    from cairn.engines.graph.repos import repos
    core = make_repo(tmp_path / "core", CORE)
    app = make_repo(tmp_path / "app", {"pyproject.toml": '[project]\nname = "app"\ndependencies = ["acme-core"]\n',
                                         "app/__init__.py": "", "app/main.py": "def run():\n    return 1\n"})
    out = repos([_project(core), _project(app)])
    [link] = out["links"]
    assert (link["source"], link["target"], link["kind"]) == ("app", "core", "depends")
    assert link["packages"] == ["acme-core"] and link["evidence"] == []


def test_ambiguous_declarations_link_to_nobody(tmp_path):
    from cairn.engines.graph.repos import repos
    core = make_repo(tmp_path / "core", CORE)
    fork = make_repo(tmp_path / "fork", {"pyproject.toml": '[project]\nname = "acme-core"\n',
                                         "src/acme_core/__init__.py": "", "src/acme_core/money.py": "X = 1\n"})
    shop = make_repo(tmp_path / "shop", SHOP)
    out = repos([_project(core), _project(fork), _project(shop)])
    py_evidence = [e for l in out["links"] for e in l["evidence"] if e["file"].endswith(".py")]
    assert py_evidence == []  # acme_core is declared twice: no guess
    assert any(l["target"] == "core" for l in out["links"])  # the JS and Go imports still resolve


def test_shared_dependencies_are_opt_in_and_weak(three):
    from cairn.engines.graph.repos import repos
    assert not any(l["kind"] == "shared" for l in repos(three)["links"])
    shared = [l for l in repos(three, shared=True)["links"] if l["kind"] == "shared"]
    assert [(sorted((l["source"], l["target"])), l["packages"]) for l in shared] == \
        [(["core", "shop"], ["pydantic", "requests"])]


def test_reads_only_existing_indexes(three):
    from cairn.engines.graph.repos import repos
    stamps = {p[0]: (p[2] / ".cairn" / "graph" / "graph.json").stat().st_mtime_ns for p in three}
    repos(three, shared=True)
    assert stamps == {p[0]: (p[2] / ".cairn" / "graph" / "graph.json").stat().st_mtime_ns for p in three}


def test_empty_and_unbuilt_projects(tmp_path):
    from cairn.engines import mapper
    from cairn.engines.graph.repos import repos
    assert repos([]) == {"level": "repos", "nodes": [], "links": []}
    bare = make_repo(tmp_path / "bare", LONELY)
    out = repos([("bare", "Bare", bare, mapper.MapIndex())])
    assert out["nodes"][0]["nodes"] == 0 and out["links"] == []


def test_readme_description_variants(tmp_path):
    from cairn.engines.graph.repos import readme_description
    (tmp_path / "README.rst").write_text("Title\n=====\n\n.. image:: x.png\n\nA *short* intro, see `docs <x>`_.\n", encoding="utf-8")
    assert readme_description(tmp_path) == "A short intro, see docs <x>."
    (tmp_path / "README.md").write_text("# T\n\n```bash\nrun me\n```\n\n" + "word " * 100, encoding="utf-8")
    d = readme_description(tmp_path, limit=50)
    assert d.endswith("…") and len(d) <= 50
