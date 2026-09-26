"""Test harness: throwaway git repos with realistic history. No network, no model calls."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

ENV = {**os.environ, "GIT_AUTHOR_NAME": "Ada", "GIT_AUTHOR_EMAIL": "ada@example.com",
       "GIT_COMMITTER_NAME": "Ada", "GIT_COMMITTER_EMAIL": "ada@example.com", "ANTHROPIC_API_KEY": "",
       "CAIRN_API_KEY": "", "OPENAI_API_KEY": "",
       "CAIRN_EMBEDDER": "hash",        # offline, deterministic embeddings (no model download in tests)
       "CAIRN_NO_CLI_MODELS": "1"}      # never call the signed-in Claude Code CLI from tests


# Every test runs offline, never calls the signed-in agent CLI, and never touches the real ~/.cairn.
os.environ.setdefault("CAIRN_EMBEDDER", "hash")
os.environ.setdefault("CAIRN_NO_CLI_MODELS", "1")


@pytest.fixture(autouse=True, scope="session")
def _isolated_cairn_home(tmp_path_factory):
    home = tmp_path_factory.mktemp("cairn-home")
    old = os.environ.get("CAIRN_HOME")
    os.environ["CAIRN_HOME"] = str(home)
    yield home
    if old is None:
        os.environ.pop("CAIRN_HOME", None)
    else:
        os.environ["CAIRN_HOME"] = old


def git(root: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, env=ENV, check=True).stdout


def commit(root: Path, files: dict[str, str], msg: str) -> None:
    for rel, text in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    git(root, "add", "-A")
    git(root, "commit", "-qm", msg)


PAY = '''\
"""Payments."""
from .gateway import charge


class PaymentService:
    def process(self, order_id: str, amount: int) -> str:
        # WHY: the provider retries on timeout, so every call carries an idempotency key.
        return charge(order_id, amount, idempotency_key=order_id)
'''
GATEWAY = '''\
def charge(order_id: str, amount: int, idempotency_key: str) -> str:
    return f"{order_id}:{amount}:{idempotency_key}"
'''
API = '''\
from .payments import PaymentService


def checkout(order_id: str, amount: int) -> str:
    return PaymentService().process(order_id, amount)
'''
TEST = '''\
from shop.api import checkout


def test_checkout():
    assert checkout("o1", 5).startswith("o1")
'''
SPEC = """# Feature Specification: Refunds

**Status**: Draft

### User Story 1 - Refund an order (Priority: P1)

- **FR-001**: The system MUST refund through the gateway.
- **FR-002**: The system MUST record refunds.
"""
TASKS = """# Tasks: Refunds

## Phase 3: US1

- [x] T001 [US1] Add refund call in `shop/gateway.py` for FR-001
- [x] T002 [P] [US1] Add refund ledger in `shop/ledger.py`
- [ ] T003 [US1] Expose refund in `shop/api.py`
"""


@pytest.fixture()
def repo(tmp_path, monkeypatch) -> Path:
    root = tmp_path / "shop-repo"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    commit(root, {"shop/__init__.py": "", "shop/gateway.py": GATEWAY, "shop/payments.py": PAY}, "Add payments")
    commit(root, {"shop/api.py": API, "tests/test_api.py": TEST}, "Add checkout API")
    commit(root, {"shop/payments.py": PAY + "\n# retry guard\n"}, "Fix double charge on retry")
    commit(root, {"shop/payments.py": PAY, "shop/api.py": API + "\n"}, "Revert \"Fix double charge on retry\"")
    commit(root, {"specs/001-refunds/spec.md": SPEC, "specs/001-refunds/tasks.md": TASKS,
                  "specs/001-refunds/plan.md": "Plan for FR-001."}, "Spec refunds")
    for k, v in ENV.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.chdir(root)
    return root


@pytest.fixture()
def cairn(repo):
    from cairn import sync
    from cairn.core import Cairn
    c = Cairn.here(repo)
    c.project.ensure_dir()
    sync.run(c)
    return c
