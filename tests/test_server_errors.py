"""The server answers failures with JSON the page can show, and says when its own files are gone (spec 012)."""
from __future__ import annotations

from fastapi.testclient import TestClient

from cairn import __version__, server
from cairn.platform import Platform, ServerConfig


def _app(tmp_path):
    config = ServerConfig(mode="local")
    return server.create_app(platform=Platform(home=tmp_path / "home", config=config), config=config)


def test_health_says_where_the_server_runs_from(tmp_path):
    body = TestClient(_app(tmp_path), base_url="http://127.0.0.1").get("/api/health").json()
    assert body["ok"] is True and body["version"] == __version__
    assert body["code_root_exists"] is True and body["ui_ok"] is True
    assert body["code_root"].endswith("cairn") and body["pid"] > 0


def test_an_unexpected_failure_is_json_with_what_and_where(tmp_path):
    app = _app(tmp_path)

    @app.get("/api/boom")
    def boom():
        raise RuntimeError("the store is on fire")

    res = TestClient(app, base_url="http://127.0.0.1", raise_server_exceptions=False).get("/api/boom")
    assert res.status_code == 500 and res.headers["content-type"].startswith("application/json")
    body = res.json()
    assert body["path"] == "/api/boom" and "the store is on fire" in body["cause"] and body["error"]


def test_missing_ui_files_are_a_structured_503_with_the_fix(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "UI_DIR", tmp_path / "deleted-ui")
    res = TestClient(_app(tmp_path), base_url="http://127.0.0.1", raise_server_exceptions=False).get("/")
    assert res.status_code == 503
    body = res.json()
    assert "no longer exists" in body["error"] and "cairn up" in body["fix"]
    health = TestClient(_app(tmp_path), base_url="http://127.0.0.1").get("/api/health").json()
    assert health["ui_ok"] is False and health["ok"] is True  # still answers, but says its page is gone


def test_a_browser_gets_a_readable_page_when_the_ui_files_are_gone(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "UI_DIR", tmp_path / "deleted-ui")
    client = TestClient(_app(tmp_path), base_url="http://127.0.0.1", raise_server_exceptions=False)
    res = client.get("/", headers={"accept": "text/html,application/xhtml+xml"})
    assert res.status_code == 503 and res.headers["content-type"].startswith("text/html")
    assert "cairn up" in res.text and "needs a restart" in res.text


def test_engine_views_are_listed_only_when_there_is_a_graph_to_draw(cairn, tmp_path):
    """A listed view that 404s is a blank white frame in the UI; no graph yet must list nothing ("run a sync")."""
    from cairn.engines.graph import api as graph_api
    config = ServerConfig(mode="local")
    app = server.create_app(platform=Platform(home=tmp_path / "home", config=config), config=config,
                            register=[cairn.project.root])
    client = TestClient(app, base_url="http://127.0.0.1")
    pid = client.get("/api/session").json()["projects"][0]["id"]
    graph = graph_api.graph_json(cairn.project.root)
    assert graph.is_file(), "the synced fixture should have a graph"
    assert [v["kind"] for v in client.get(f"/api/p/{pid}/graph/views").json()] == ["graph", "callflow", "tree"]
    backup = graph.read_bytes()
    graph.unlink()
    try:
        assert client.get(f"/api/p/{pid}/graph/views").json() == []
    finally:
        graph.write_bytes(backup)


def test_a_map_too_big_for_one_page_answers_413_with_the_reason(cairn, tmp_path, monkeypatch):
    """The engine refuses to draw a huge graph as one HTML page; that was a bodiless 500 in a blank frame."""
    from cairn.engines.graph import api as graph_api
    config = ServerConfig(mode="local")
    app = server.create_app(platform=Platform(home=tmp_path / "home", config=config), config=config,
                            register=[cairn.project.root])
    client = TestClient(app, base_url="http://127.0.0.1")
    pid = client.get("/api/session").json()["projects"][0]["id"]

    def refuse(*a, **k):
        raise ValueError("Graph has 6000 nodes - too large for HTML viz (limit: 5000).")
    monkeypatch.setattr(graph_api, "graph_view_html", refuse)
    res = client.get(f"/api/p/{pid}/graph/views/graph")
    assert res.status_code == 413 and "6000 nodes" in res.json()["detail"] and "Browse" in res.json()["detail"]
