"""Spec 011: `cairn search` finds text, not only names; citations carry the enclosing function's lines;
hubs, areas and graph queries ignore framework and library symbols."""
from __future__ import annotations

import json

from conftest import commit

from cairn.engines.mapper import MapIndex

CONFIG = '''\
"""Settings."""
PORT = 8080
password = "hunter2-do-not-leak"


def connect(host):
    # the cookie name is read here
    name = "session_cookie"
    return host + ":" + str(PORT) + name


def other():
    return 1
'''


def _sync(cairn, repo, files):
    from cairn import sync
    commit(repo, files, "add config")
    sync.run(cairn)


def test_search_finds_text_that_is_not_a_symbol_or_file_name(cairn, repo):
    _sync(cairn, repo, {"shop/config.py": CONFIG, ".env": "SECRET_TOKEN=abc123\n"})
    hits = cairn.search("session_cookie")
    text = [h for h in hits if h["kind"] == "text"]
    assert text and text[0]["path"] == "shop/config.py" and text[0]["line"] == 8, hits
    assert [h["path"] for h in cairn.search("8080") if h["kind"] == "text"] == ["shop/config.py"]
    assert not [h for h in cairn.search("abc123") if h["kind"] == "text"]      # .env is never searched


def test_text_hits_cite_the_enclosing_function_and_never_print_secrets(cairn, repo):
    _sync(cairn, repo, {"shop/config.py": CONFIG})
    hit = next(h for h in cairn.search("session_cookie") if h["kind"] == "text")
    assert hit["lines"] == "6-9", hit                                    # def connect(...): through its return
    pw = next(h for h in cairn.search("password") if h["kind"] == "text")
    assert "hunter2" not in json.dumps(pw), pw


def test_a_kind_filter_keeps_text_out(cairn, repo):
    _sync(cairn, repo, {"shop/config.py": CONFIG})
    assert all(h["kind"] != "text" for h in cairn.search("session_cookie", kinds=["symbol", "file"]))
    only = cairn.search("session_cookie", kinds=["text"])
    assert only and all(h["kind"] == "text" for h in only)


def test_enclosing_range_for_several_languages():
    from cairn.spans import enclosing
    py = "class A:\n    def m(self):\n        x = 1\n        return x\n\n    def n(self):\n        pass\n"
    assert enclosing(py, "a.py", 3) == (2, 4)           # the method, not the whole class
    js = "const a = 1;\napp.get('/x', (req, res) => {\n  request(url, cb);\n  res.send(1);\n});\nfoo();\n"
    assert enclosing(js, "s.js", 3) == (2, 5)           # the route handler, not the file
    java = "class C {\n  int f() {\n    return 1;\n  }\n  int g() { return 2; }\n}\n"
    assert enclosing(java, "C.java", 3) == (2, 4)
    assert enclosing("x = 1\ny = 2\n", "a.py", 1) is None   # module level: no enclosing function
    assert enclosing("anything", "notes.txt", 1) is None   # no grammar: no claim


def _index(nodes, links=()):
    idx = MapIndex()
    for n in nodes:
        idx.nodes[n["id"]] = n
        idx.by_file[n.get("source_file", "")].append(n["id"])
    for s, t in links:
        from cairn.engines.mapper import Edge
        idx.out[s].append(Edge(t, "references", "EXTRACTED", None))
        idx.inc[t].append(Edge(s, "references", "EXTRACTED", None))
    return idx


def test_hubs_and_areas_skip_library_symbols():
    code = {"file_type": "code", "community": 1, "community_name": "svc"}
    nodes = [{"id": "ext", "label": "Psr\\Log\\LoggerInterface", "source_file": "", **code},
             {"id": "ext2", "label": "org.springframework.GetMapping", "source_file": "", "file_type": "concept",
              "external": True, "community": 1, "community_name": "svc"},
             {"id": "a", "label": "pay()", "source_file": "p.py", "source_location": "L1", **code},
             {"id": "b", "label": "refund()", "source_file": "p.py", "source_location": "L9", **code}]
    idx = _index(nodes, [("a", "ext"), ("b", "ext"), ("a", "ext2"), ("b", "ext2"), ("a", "b")])
    assert [h["label"] for h in idx.hubs(5)] == ["pay()", "refund()"]
    area = idx.areas()[0]
    assert area["size"] == 2 and all("Psr" not in t and "spring" not in t for t in area["top"])


def test_graph_query_neither_seeds_on_nor_walks_through_library_symbols():
    import networkx as nx

    from cairn.engines.graph import serve
    g = nx.Graph()
    g.add_node("lib", label="org.slf4j.Logger", norm_label="org.slf4j.logger", file_type="code", source_file="")
    g.add_node("a", label="LoggerConfig", norm_label="loggerconfig", file_type="code", source_file="a.java")
    g.add_node("b", label="PaymentService", norm_label="paymentservice", file_type="code", source_file="b.java")
    g.add_edge("lib", "a")
    g.add_edge("lib", "b")
    assert "lib" not in [n for _, n in serve._score_nodes(g, ["logger"])]
    assert serve._score_nodes(g, ["logger"])                       # the real match is still found
    for walk in (serve._bfs, serve._dfs):
        nodes, _ = walk(g, ["a"], 3)
        assert nodes == {"a"}, (walk.__name__, nodes)              # b is only "related" through the library
