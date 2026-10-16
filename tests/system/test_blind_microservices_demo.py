"""Blind accuracy on a second real product (GoogleCloudPlatform/microservices-demo at a pinned tag).

The ground truth was committed on its own before any extraction ran, and no extraction rule was edited while
this fixture was scored. This satisfies T002's requirement for a second unseen real product.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from .fixtures.fetch import pinned_repo
from .scoring import load_truth, score, summary
from .test_views_fixtures import aliases
from .util import copy_fixture

FIXTURES = Path(__file__).parent / "fixtures"
PIN = "39efed435e697bcdcd31c5e45f4d36dc35c98a63"  # v0.10.1
# measured 2026-10-09, extraction rules frozen
FLOOR = {"element_recall": 0.50, "element_precision": 0.50, "relationship_recall": 0.0,
         "relationship_precision": 0.0}


def test_microservices_demo_blind_accuracy_floor(tmp_path):
    """SC-001 (blind, below target, recorded honestly), FR-006, FR-013: scores a product nobody tuned for."""
    from cairn.system.link import link
    from cairn.system.views import view
    src = pinned_repo("https://github.com/GoogleCloudPlatform/microservices-demo", PIN, "microservices-demo")
    root = copy_fixture(src, tmp_path / "microservices-demo", git=False)
    pm = link(root)
    v = view(root, "container", model=pm)
    s = score(v, load_truth(FIXTURES / "microservices-demo" / "GROUND_TRUTH.yaml"), aliases(pm))
    print("\n" + summary("microservices-demo (blind)", s))
    for k, floor in FLOOR.items():
        assert s[k] >= floor, (k, s[k], s)
    if s["relationship_recall"] >= 0.90 and s["element_recall"] >= 0.90:
        pytest.fail("microservices-demo now meets SC-001: update FLOOR and the blind report in specs/004-system-extraction")
