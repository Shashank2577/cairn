from __future__ import annotations

import pytest

from tests.conftest import ENV
from tests.review.fixtures import seeded


@pytest.fixture()
def seeded_cairn(tmp_path, monkeypatch):
    """The seeded repository, synced, with one stored convention, checked out on branch `feature`."""
    from cairn import sync
    from cairn.core import Cairn
    root = tmp_path / "shop-repo"
    root.mkdir()
    for k, v in ENV.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "home"))
    monkeypatch.chdir(root)
    seeded.build(root)
    c = Cairn.here(root)
    c.project.ensure_dir()
    sync.run(c)
    c.remember("All money amounts in shop/payments.py are integer cents, never floats", kind="convention")
    yield c
    c.close()
