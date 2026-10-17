"""The Docs view's endpoints: the same document set as ``cairn docs export``, previewed, zipped and written."""
from __future__ import annotations

import io
import subprocess
import zipfile

import pytest
from fastapi.testclient import TestClient

from cairn import server
from cairn.platform import Platform, ServerConfig
from cairn.system.export import commits_behind

from .util import GIT_ENV, product

REPOS = ["shop-web", "shop-api", "shop-worker", "shop-contracts"]


@pytest.fixture
def client(tmp_path, monkeypatch):
    for k, v in {"HOME": str(tmp_path / "home"), "USERPROFILE": str(tmp_path / "home"), "CAIRN_EMBEDDER": "hash",
                 "CAIRN_NO_CLI_MODELS": "1", "ANTHROPIC_API_KEY": "", "CAIRN_API_KEY": "", "OPENAI_API_KEY": ""}.items():
        monkeypatch.setenv(k, v)
    root = product(tmp_path, "shop", "shop-api", REPOS)
    config = ServerConfig(mode="local")
    app = server.create_app(platform=Platform(home=tmp_path / "ph", config=config), config=config, register=[root])
    c = TestClient(app, base_url="http://127.0.0.1")
    c.pid = c.get("/api/session").json()["projects"][0]["id"]
    c.root = root
    return c


def test_list_says_which_commit_the_documents_describe(client):
    body = client.get(f"/api/p/{client.pid}/docs").json()
    assert body["docs"] and body["commit"] and len(body["commit"]) == 12
    assert body["behind"] in (0, None) or body["behind"] > 0
    assert body["narrate_tokens"] > 0 and body["export_dir"] == "docs/architecture"
    assert any(d["slug"] == "flows" and not d["diagram"] for d in body["docs"])
    assert sum(1 for d in body["docs"] if d["level"] == "flows") <= body["max_flow_docs"] + 1


def test_one_document_carries_markdown_diagram_and_evidence(client):
    docs = client.get(f"/api/p/{client.pid}/docs").json()["docs"]
    d = next(x for x in docs if x["diagram"])
    one = client.get(f"/api/p/{client.pid}/docs/{d['slug']}").json()
    assert one["md"].startswith("# ") and "Records commit" in one["md"]
    assert one["svg"].lstrip().startswith("<svg") and one["evidence"]
    assert all(not e["file"].startswith(client.root.name + "/") for e in one["evidence"] if "/" in e["ref"])
    assert client.get(f"/api/p/{client.pid}/docs/no-such-doc").status_code == 404


def test_zip_holds_the_same_files_the_export_writes(client):
    res = client.post(f"/api/p/{client.pid}/docs/zip", json={})
    assert res.status_code == 200 and res.headers["content-type"] == "application/zip"
    names = zipfile.ZipFile(io.BytesIO(res.content)).namelist()
    assert "architecture/README.md" in names and any(n.endswith(".svg") for n in names)
    assert not (client.root / "docs").exists()          # a download never writes into the repository


def test_export_writes_into_the_repository_and_calls_no_model(client):
    res = client.post(f"/api/p/{client.pid}/docs/export", json={})
    assert res.status_code == 200 and res.json()["narrated"] is False
    assert (client.root / "docs" / "architecture" / "README.md").exists()


def test_commits_behind_counts_commits_since_the_model(tmp_path):
    root = tmp_path / "r"
    root.mkdir()
    def git(*a):
        return subprocess.run(["git", "-C", str(root), *a], check=True, capture_output=True, text=True, env=GIT_ENV).stdout.strip()
    git("init", "-q", "-b", "main")
    (root / "a").write_text("1")
    git("add", "-A"); git("commit", "-qm", "one", "--no-gpg-sign")
    first = git("rev-parse", "HEAD")[:12]
    assert commits_behind(root, first) == 0
    (root / "a").write_text("2")
    git("commit", "-qam", "two", "--no-gpg-sign")
    assert commits_behind(root, first) == 1
    assert commits_behind(root, "") is None and commits_behind(root, "deadbeef0000") is None
