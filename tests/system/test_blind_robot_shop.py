"""Blind accuracy on a second real product (instana/robot-shop at a pinned commit).

The ground truth was committed on its own (0fb6895) before any extraction ran, and no extraction rule was
edited while this fixture was first scored: the blind run measured elements 0.69/0.82 and relationships
0.30/0.86 (2026-10-08, rules frozen). The rules that close those misses (nginx proxy config, PHP, env-built
service URLs, build-context stores, external config addresses, view collapse) were then developed against
THIS fixture, so it no longer measures blind accuracy — it is a regression floor, and an unbiased SC-001
number still needs a third product nobody has looked at (T002). See blind-accuracy.md.
"""
from __future__ import annotations

from pathlib import Path

from .fixtures.fetch import pinned_repo
from .scoring import load_truth, score, summary
from .test_views_fixtures import aliases
from .util import copy_fixture

FIXTURES = Path(__file__).parent / "fixtures"
PIN = "55292e2199f2fb00a165b1f7d3045fe7f8922038"
# measured 2026-10-13, after the T052-T057 fixes (floor: regressions show). Relationship precision
# 0.95 since the configured-address wiring pass (T058): the load generator the truth scopes out
# genuinely drives http://web:8080, so `load -> web` is a real edge this truth cannot credit.
FLOOR = {"element_recall": 1.00, "element_precision": 0.85, "relationship_recall": 1.00,
         "relationship_precision": 0.95}


def test_robot_shop_blind_accuracy_floor(tmp_path):
    """SC-001 regression floor (FR-006, FR-013): the product whose misses drove the T052-T057 rules."""
    from cairn.system.link import link
    from cairn.system.views import view
    src = pinned_repo("https://github.com/instana/robot-shop", PIN, "robot-shop")
    root = copy_fixture(src, tmp_path / "robot-shop", git=False)
    pm = link(root)
    v = view(root, "container", model=pm)
    s = score(v, load_truth(FIXTURES / "robot-shop" / "GROUND_TRUTH.yaml"), aliases(pm))
    print("\n" + summary("robot-shop (after T052-T057)", s))
    for k, floor in FLOOR.items():
        assert s[k] >= floor, (k, s[k], s)
