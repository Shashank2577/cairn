"""Blind accuracy on a second real product (instana/robot-shop at a pinned commit).

The ground truth was committed on its own (0fb6895) before any extraction ran, and no extraction rule was
edited while this fixture was scored. The measured numbers are BELOW SC-001; this test pins them as a floor
so regressions show, and records the gap honestly instead of loosening the truth. Raising them is work for
slice 004 (catalog coverage: nginx proxy config, PHP, env-built service URLs, build-context stores).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from .fixtures.fetch import pinned_repo
from .scoring import load_truth, score, summary
from .test_views_fixtures import aliases
from .util import copy_fixture

FIXTURES = Path(__file__).parent / "fixtures"
PIN = "55292e2199f2fb00a165b1f7d3045fe7f8922038"
# measured 2026-10-08, extraction rules frozen
FLOOR = {"element_recall": 0.69, "element_precision": 0.81, "relationship_recall": 0.30,
         "relationship_precision": 0.85}


def test_robot_shop_blind_accuracy_floor(tmp_path):
    """SC-001 (blind, below target, recorded honestly), FR-006, FR-013: scores a product nobody tuned for."""
    from cairn.system.link import link
    from cairn.system.views import view
    src = pinned_repo("https://github.com/instana/robot-shop", PIN, "robot-shop")
    root = copy_fixture(src, tmp_path / "robot-shop", git=False)
    pm = link(root)
    v = view(root, "container", model=pm)
    s = score(v, load_truth(FIXTURES / "robot-shop" / "GROUND_TRUTH.yaml"), aliases(pm))
    print("\n" + summary("robot-shop (blind)", s))
    for k, floor in FLOOR.items():
        assert s[k] >= floor, (k, s[k], s)
    if s["relationship_recall"] >= 0.90 and s["element_recall"] >= 0.90:
        pytest.fail("robot-shop now meets SC-001: update FLOOR and the blind report in specs/004-system-extraction")
