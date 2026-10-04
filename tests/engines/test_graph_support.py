"""Shared fixtures for the graph-engine tests: a small multi-language git repo and a
fake model router. No network, no model, no user state (CAIRN_HOME / HOME are tmp)."""
from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

import pytest

GIT_ENV = {**os.environ, "GIT_AUTHOR_NAME": "Ada", "GIT_AUTHOR_EMAIL": "ada@example.com",
           "GIT_COMMITTER_NAME": "Ada", "GIT_COMMITTER_EMAIL": "ada@example.com"}

FILES = {
    "shop/__init__.py": "",
    "shop/gateway.py": (
        "def charge(order_id: str, amount: int) -> str:\n"
        "    return f\"{order_id}:{amount}\"\n\n\n"
        "def refund(order_id: str) -> str:\n"
        "    return charge(order_id, -1)\n"
    ),
    "shop/payments.py": (
        '"""Payments."""\n'
        "from .gateway import charge\n\n\n"
        "class PaymentService:\n"
        "    def process(self, order_id: str, amount: int) -> str:\n"
        "        # WHY: the provider retries on timeout, so every call carries an idempotency key.\n"
        "        return charge(order_id, amount)\n"
    ),
    "shop/api.py": (
        "from .payments import PaymentService\n\n\n"
        "def checkout(order_id: str, amount: int) -> str:\n"
        "    return PaymentService().process(order_id, amount)\n"
    ),
    "web/cart.ts": (
        'import { total } from "./price";\n\n'
        "export class Cart {\n"
        "  items: number[] = [];\n"
        "  sum(): number { return total(this.items); }\n"
        "}\n"
    ),
    "web/price.ts": "export function total(xs: number[]): number { return xs.reduce((a, b) => a + b, 0); }\n",
    "svc/main.go": (
        "package main\n\nimport \"fmt\"\n\n"
        "func greet(name string) string { return fmt.Sprintf(\"hi %s\", name) }\n\n"
        "func main() { fmt.Println(greet(\"x\")) }\n"
    ),
    "docs/architecture.md": (
        "# Architecture\n\nThe `PaymentService` calls `charge` in the gateway. See [api](../shop/api.py).\n"
    ),
}


def git(root: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True,
                          env=GIT_ENV, check=True, encoding="utf-8", errors="replace").stdout


def make_repo(root: Path, files: dict[str, str] | None = None) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    git(root, "init", "-q", "-b", "main")
    write(root, files or FILES)
    git(root, "add", "-A")
    git(root, "commit", "-qm", "init")
    return root


def write(root: Path, files: dict[str, str]) -> None:
    for rel, text in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")


def load_graph(root: Path) -> dict:
    return json.loads((root / ".cairn" / "graph" / "graph.json").read_text(encoding="utf-8"))


def node_ids(root: Path) -> set[str]:
    return {n["id"] for n in load_graph(root)["nodes"]}


@pytest.fixture(autouse=True)
def isolated_env(tmp_path, monkeypatch):
    """No keys, no signed-in CLI, no user-level state."""
    for k in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "CAIRN_API_KEY", "CAIRN_GRAPH_OUT", "PYTHONHASHSEED"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("CAIRN_NO_CLI_MODELS", "1")
    monkeypatch.setenv("CAIRN_HOME", str(tmp_path / "cairn-home"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    from cairn.engines.graph import llm
    llm.set_router(None)
    yield
    llm.set_router(None)


@pytest.fixture(scope="module")
def built_repo(tmp_path_factory):
    """The fixture repo, built once per module with the in-process API."""
    from cairn.engines.graph import api
    old = {k: os.environ.get(k) for k in ("CAIRN_NO_CLI_MODELS", "CAIRN_GRAPH_OUT")}
    os.environ["CAIRN_NO_CLI_MODELS"] = "1"
    os.environ.pop("CAIRN_GRAPH_OUT", None)
    try:
        root = make_repo(tmp_path_factory.mktemp("graph") / "shop-repo")
        res = api.build(root)
        assert res["ok"], res
        yield root
    finally:
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


class FakeRouter:
    """Stands in for cairn.router.Router: records calls, answers per task."""

    _client = None

    def __init__(self, provider: str = "anthropic", available: bool = True, replies: dict | None = None):
        self.provider = provider
        self._available = available
        self.replies = replies or {}
        self.calls: list[dict] = []

    @property
    def available(self) -> bool:
        return self._available

    @property
    def api_key(self) -> str | None:
        return "test-key" if self._available else None

    def model(self, tier: str) -> str:
        return f"fake-{tier}"

    def tier_for(self, task: str, input_tokens: int = 0) -> str:
        return {"graph-label": "fast", "graph-dedup": "fast", "graph-triage": "deep"}.get(task, "balanced")

    def table(self) -> list[dict]:
        return [{"tier": t, "model": self.model(t), "tasks": []} for t in ("fast", "balanced", "deep")]

    def complete(self, task, prompt, *, system="", max_tokens=1200, **kw):
        self.calls.append({"task": task, "prompt": prompt, "system": system, "max_tokens": max_tokens,
                           "model": self.model(self.tier_for(task))})
        reply = self.replies.get(task)
        if callable(reply):
            return reply(prompt)
        if reply is not None:
            return reply
        if task == "graph-label":
            ids = re.findall(r"^(\d+):", prompt, re.M)
            return json.dumps({i: f"Named Area {i}" for i in ids})
        return ""
