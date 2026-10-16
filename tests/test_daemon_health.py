"""A server that runs from deleted or outdated code is reported and replaced, not trusted because /api/health
answers (spec 012). No real server is started: the process helpers are replaced."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from cairn import __version__, daemon


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("CAIRN_HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir()
    return tmp_path / "home"


def _record(tmp_path: Path, **over) -> dict:
    code = tmp_path / "code"
    code.mkdir(exist_ok=True)
    py = tmp_path / "python"
    py.write_text("", encoding="utf-8")
    rec = {"pid": 4242, "port": 4747, "started": 1.0, "code_root": str(code), "version": __version__,
           "python": str(py), "path": "/usr/bin"}
    rec.update(over)
    return rec


def test_new_record_says_where_the_server_runs_from():
    rec = daemon.new_record(pid=7, port=4747)
    assert Path(rec["code_root"]).is_dir() and (Path(rec["code_root"]) / "daemon.py").exists()
    assert rec["version"] == __version__ and rec["pid"] == 7 and rec["port"] == 4747
    assert rec["python"] and "path" in rec


def test_healthy_record_has_no_verdict(tmp_path):
    assert daemon.verdict(_record(tmp_path)) is None


def test_deleted_code_root_is_stale_with_the_fix(tmp_path):
    v = daemon.verdict(_record(tmp_path, code_root=str(tmp_path / "gone")))
    assert v["stale"] == "code-missing"
    assert "no longer exists" in v["reason"] and "cairn up" in v["fix"]


def test_different_version_is_stale(tmp_path):
    v = daemon.verdict(_record(tmp_path, version="0.0.1"))
    assert v["stale"] == "version-differs" and "0.0.1" in v["reason"] and __version__ in v["reason"]


def test_missing_python_is_stale(tmp_path):
    v = daemon.verdict(_record(tmp_path, python=str(tmp_path / "no-python")))
    assert v["stale"] == "python-missing"


def test_an_old_record_without_fields_is_judged_by_whether_the_ui_loads(tmp_path):
    old = {"pid": 1, "port": 4747, "started": 1.0}
    assert daemon.verdict(old, ui_ok=True) is None
    v = daemon.verdict(old, ui_ok=False)
    assert v["stale"] == "ui-broken" and "cairn up" in v["fix"]


def test_info_reports_the_verdict(home, tmp_path, monkeypatch):
    (home / "server.json").write_text(json.dumps(_record(tmp_path, code_root=str(tmp_path / "gone"))), encoding="utf-8")
    monkeypatch.setattr(daemon, "_health", lambda port: {"ok": True})
    monkeypatch.setattr(daemon, "_ui_ok", lambda port: True)
    info = daemon.info()
    assert info["stale"]["stale"] == "code-missing" and info["port"] == 4747


def test_info_for_a_healthy_server_has_no_stale_marker(home, tmp_path, monkeypatch):
    (home / "server.json").write_text(json.dumps(_record(tmp_path)), encoding="utf-8")
    monkeypatch.setattr(daemon, "_health", lambda port: {"ok": True})
    monkeypatch.setattr(daemon, "_ui_ok", lambda port: True)
    assert daemon.info()["stale"] is None


def test_start_replaces_a_stale_server_and_says_why(home, tmp_path, monkeypatch):
    stopped, spawned = [], []
    stale = {"pid": 4242, "port": 4747, "url": "http://127.0.0.1:4747",
             "stale": {"stale": "code-missing", "reason": "its code no longer exists", "fix": "cairn up"}}
    monkeypatch.setattr(daemon, "info", lambda: None if stopped else stale)
    monkeypatch.setattr(daemon, "stop", lambda: stopped.append(True) or True)
    monkeypatch.setattr(daemon, "_free", lambda port: True)
    monkeypatch.setattr(daemon, "_health", lambda port: {"ok": True})

    class _Proc:
        pid = 99

    monkeypatch.setattr(daemon.subprocess, "Popen", lambda *a, **k: spawned.append(a) or _Proc())
    out = daemon.start(None, wait=0.1)
    assert stopped and spawned
    assert out["replaced"]["stale"] == "code-missing" and out["pid"] == 99


def test_start_reuses_a_healthy_server(home, monkeypatch):
    healthy = {"pid": 1, "port": 4747, "url": "http://127.0.0.1:4747", "stale": None}
    monkeypatch.setattr(daemon, "info", lambda: healthy)
    monkeypatch.setattr(daemon.subprocess, "Popen", lambda *a, **k: pytest.fail("must not start a second server"))
    out = daemon.start(None)
    assert out["pid"] == 1 and "replaced" not in out


# ---- servers the record does not know about (a leftover server on another port kept answering for a week) ----
def test_a_cairn_server_the_record_does_not_know_is_a_stray(home, monkeypatch):
    (home / "server.json").write_text(json.dumps({"pid": 10, "port": 4748}), encoding="utf-8")
    monkeypatch.setattr(daemon, "_listeners", lambda lo, hi: {4747: 85413, 4748: 10, 4760: 555})
    cmds = {85413: "/old/.venv/bin/python3 .venv/bin/cairn serve --port 4747", 10: "python -m cairn serve --port 4748",
            555: "node server.js"}
    monkeypatch.setattr(daemon, "_command_line", lambda pid: cmds[pid])
    strays = daemon.stray_servers()
    assert [(s["port"], s["pid"]) for s in strays] == [(4747, 85413)]  # not the recorded server, not node
    assert "cairn serve" in strays[0]["command"]


def test_stop_strays_stops_only_cairn_servers(home, monkeypatch):
    monkeypatch.setattr(daemon, "stray_servers", lambda: [{"port": 4747, "pid": 85413, "command": "cairn serve"}])
    killed = []
    monkeypatch.setattr(daemon, "_terminate", lambda pid: killed.append(pid) or True)
    assert daemon.stop_strays() == [4747] and killed == [85413]


def test_no_lsof_means_no_strays_not_an_error(home, monkeypatch):
    def boom(*a, **k):
        raise FileNotFoundError("lsof")
    monkeypatch.setattr(daemon.subprocess, "run", boom)
    assert daemon._listeners(4747, 4760) == {} and daemon.stray_servers() == []
