"""Checker: rules R1 to R13, golden examples, the Taazaa originals, evidence resolution."""
from __future__ import annotations

import copy
import os
import sys

import pytest

from cairn.system.diagram import check as ck
from cairn.system.diagram import schema

from .test_diagram_common import BASE, EXAMPLES, TAAZAA, valid


def rules(desc, **kw):
    return ck.rule_ids(ck.check(desc, **kw))


def test_base_description_passes():
    """FR-022: the reference description breaks no rule. Covers US2-AS2."""
    assert ck.check(valid()) == []


@pytest.mark.parametrize("path", EXAMPLES, ids=lambda p: p.stem)
def test_every_example_passes(path):
    """FR-022, FR-023: every published example follows every rule. Covers US2-AS2."""
    assert ck.check(schema.load_file(path)) == []


def test_examples_exist():
    """Examples exist. Covers FR-022, US2-AS2."""
    assert len(EXAMPLES) >= 9


def test_taazaa_architecture_fails_expected_rules():
    """FR-023: the layered template breaks R1, R3, R4, R7, R9 (47 violations in the standard). Covers US2-AS3."""
    d = schema.load_file(next(p for p in TAAZAA if p.stem == "architecture"))
    v = ck.check(d)
    assert {"R1", "R3", "R4", "R7", "R9"} <= ck.rule_ids(v)
    assert len(v) == 47


def test_taazaa_data_flow_fails_expected_rules():
    """FR-023: the mixed-level template breaks R2 (processing steps among systems) and R4, R7. Covers US2-AS3."""
    d = schema.load_file(next(p for p in TAAZAA if p.stem == "data-flow"))
    v = ck.check(d)
    assert {"R1", "R2", "R3", "R4", "R7"} <= ck.rule_ids(v)
    assert len(v) == 53


# one trigger per rule: (rule, mutation of the base description)
def _no_title(d): d.pop("title")
def _wrong_prefix(d): d["title"] = "Something else"
def _no_scope(d): d.pop("scope")
def _bad_level(d): d["elements"].append({"id": "s", "name": "S", "type": "state", "desc": "x", "evidence": ["a:1"]})
def _no_tech(d): d["elements"][0].pop("tech")
def _no_type(d): d["elements"][0].pop("type")
def _dup_id(d): d["elements"].append(dict(d["elements"][0]))
def _no_how(d): d["relationships"][0].pop("how")
def _vague(d): d["relationships"][0]["what"] = "Uses"
def _long_what(d): d["relationships"][0]["what"] = "x" * 40
def _how_at_context(d): d["diagram"], d["title"] = "context", "System context for X"
def _bad_style(d): d["relationships"][0]["style"] = "telepathy"
def _return_outside_flow(d): d["relationships"][0]["style"] = "return"
def _bidir(d): d["relationships"][0]["bidirectional"] = True
def _reverse(d): d["relationships"].append({"from": "api", "to": "web", "what": "Creates orders", "how": "HTTP", "evidence": ["a:1"]})
def _no_evidence(d): d["elements"][1].pop("evidence")
def _bad_prov(d): d["elements"][1]["provenance"] = "guessed"
def _credential(d): d["elements"][1]["evidence"] = ["api/.env: SENDGRID_API_KEY"]
def _too_many(d): d["elements"] += [{"id": f"x{i}", "name": "x", "type": "container", "tech": "t", "desc": "d", "evidence": ["a:1"]} for i in range(10)]
def _too_many_rels(d): d["relationships"] += [{"from": "web", "to": "api", "what": f"Call {i}", "how": "HTTP", "evidence": ["a:1"]} for i in range(15)]
def _no_boundary_type(d): d["boundaries"] = [{"id": "b", "name": "B", "contains": ["web"]}]
def _band(d): d["boundaries"] = [{"id": "b", "name": "Service Layer", "type": "tier", "contains": ["web"]}]
def _four_boundaries(d): d["boundaries"] = [{"id": f"b{i}", "name": f"B{i}", "type": "network", "contains": []} for i in range(4)]
def _legend_off(d): d["legend"] = False
def _accents(d):
    for e in d["elements"]:
        e["focus"] = True
def _unknown_rel(d): d["relationships"][0]["to"] = "ghost"
def _unknown_member(d): d["boundaries"] = [{"id": "b", "name": "B", "type": "network", "contains": ["ghost"]}]
def _no_desc(d): d.pop("description")


CASES = [
    ("R1", _no_title), ("R1", _wrong_prefix), ("R1", _no_scope),
    ("R2", _bad_level), ("R3", _no_tech), ("R3", _no_type), ("R3", _dup_id),
    ("R4", _no_how), ("R4", _vague), ("R4", _long_what), ("R4", _how_at_context),
    ("R5", _bad_style), ("R5", _return_outside_flow),
    ("R6", _bidir), ("R6", _reverse),
    ("R7", _no_evidence), ("R7", _bad_prov), ("R7", _credential),
    ("R8", _too_many), ("R8", _too_many_rels),
    ("R9", _no_boundary_type), ("R9", _band), ("R9", _four_boundaries),
    ("R10", _legend_off), ("R11", _accents),
    ("R12", _unknown_rel), ("R12", _unknown_member), ("R13", _no_desc),
]


@pytest.mark.parametrize("rule,mutate", CASES, ids=lambda x: x if isinstance(x, str) else x.__name__)
def test_rule_is_triggered(rule, mutate):
    """FR-022: each rule R1..R13 reports a description that breaks it. Covers US2-AS2."""
    d = copy.deepcopy(BASE)
    mutate(d)
    assert rule in rules(schema.normalize(d)), mutate.__name__


@pytest.mark.parametrize("rule", [f"R{i}" for i in range(1, 14)])
def test_rule_passes_on_conforming_description(rule):
    """FR-022: each rule R1..R13 stays quiet on a conforming description. Covers US2-AS2."""
    assert rule not in rules(valid())


def test_flow_rules():
    """FR-022: flow budgets (R8), references (R12), fragments. Covers US2-AS2."""
    d = schema.normalize({
        "diagram": "flow", "title": "Flow of x", "scope": "s", "description": "d",
        "elements": [{"id": f"p{i}", "name": f"p{i}", "type": "container", "tech": "t", "desc": "d", "evidence": ["a:1"]} for i in range(8)],
        "flow": {"participants": [f"p{i}" for i in range(8)] + ["ghost"],
                 "messages": [{"from": "p0", "to": "p1", "what": f"Step {i}", "how": "x", "evidence": ["a:1"]} for i in range(13)]
                 + [{"from": "p0", "to": "zz", "what": "Lost", "how": "x", "evidence": ["a:1"]}],
                 "fragments": [{"type": "alt", "start": 1, "end": 99}, {"type": "opt", "start": 1, "end": 2}]}})
    v = rules(d)
    assert {"R8", "R12"} <= v


def test_flow_return_style_needs_no_how():
    """Flow return style needs no how. Covers FR-022, US2-AS2."""
    d = schema.normalize({
        "diagram": "flow", "title": "Flow of x", "scope": "s", "description": "d",
        "elements": [{"id": "a", "name": "a", "type": "container", "tech": "t", "desc": "d", "evidence": ["a:1"]},
                     {"id": "b", "name": "b", "type": "container", "tech": "t", "desc": "d", "evidence": ["a:1"]}],
        "flow": {"participants": ["a", "b"], "messages": [
            {"from": "a", "to": "b", "what": "Ask", "how": "RPC", "evidence": ["a:1"]},
            {"from": "b", "to": "a", "what": "Answer", "style": "return", "evidence": ["a:1"]}]}})
    assert ck.check(d) == []


def test_hand_made_budget_is_tighter():
    """Hand made budget is tighter. Covers FR-022, US2-AS2."""
    d = valid()
    d["elements"] += [{"id": f"x{i}", "name": "x", "type": "container", "tech": "t", "desc": "d", "evidence": ["a:1"]} for i in range(7)]
    assert "R8" not in rules(d)
    assert "R8" in rules(d, hand_made=True)


def test_unknown_kind_is_reported_not_raised():
    """Unknown kind is reported not raised. Covers FR-022, US2-AS2."""
    d = valid(diagram="mystery")
    assert ck.check(d)[0].rule == "R1"


def test_violation_text_names_the_rule():
    """Violation text names the rule. Covers FR-022, US2-AS2."""
    d = valid()
    d.pop("scope")
    assert any(str(v).startswith("R1 ") for v in ck.check(d))


# ------------------------------------------------------------------ evidence resolution
@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "shop"
    (r / "src").mkdir(parents=True)
    (r / "src" / "app.py").write_text("a\nb\nc\n", encoding="utf-8")
    return r


def _with_ref(ref):
    d = valid()
    for x in d["elements"] + d["relationships"]:
        x["evidence"] = [ref]
    return d


def test_evidence_resolves_with_single_root(repo):
    """FR-022: a repo/path:line reference that exists passes. Covers US2-AS2."""
    assert ck.check(_with_ref("src/app.py:3"), roots=repo) == []
    assert ck.check(_with_ref("shop/src/app.py:2"), roots=repo) == []


def test_evidence_with_mapping_roots(repo):
    """Evidence with mapping roots. Covers FR-022, US2-AS2."""
    assert ck.check(_with_ref("shop/src/app.py:2"), roots={"shop": repo}) == []
    assert "R7" in rules(_with_ref("shop/src/missing.py:2"), roots={"shop": repo})
    assert ck.check(_with_ref("other/src/x.py:2"), roots={"shop": repo}) == []   # repository not present: not tested


def test_evidence_missing_file_and_line_beyond_end(repo):
    """Evidence missing file and line beyond end. Covers FR-022, US2-AS2."""
    assert "R7" in rules(_with_ref("src/nope.py:1"), roots=repo)
    v = ck.check(_with_ref("src/app.py:99"), roots=repo)
    assert any("beyond the end" in str(x) for x in v)
    assert "R7" in rules(_with_ref("src/app.py:0"), roots=repo)


def test_non_line_references_are_not_resolved(repo):
    """Non line references are not resolved. Covers FR-022, US2-AS2."""
    for ref in ("system.yaml: actors", "docker-compose.yml: db image postgres:16", "pyproject.toml: shop-contracts",
                "https://example.com:80"):
        assert ck.check(_with_ref(ref), roots=repo) == []


def test_evidence_refuses_escape(repo, tmp_path):
    """FR-022, security: ../, absolute paths and backslashes are rejected without being read. Covers US2-AS2."""
    (tmp_path / "secret.txt").write_text("x\n", encoding="utf-8")
    for ref in ("../secret.txt:1", "src/../../secret.txt:1", f"{tmp_path}/secret.txt:1", "/etc/passwd:1", "..\\x:1", "C:/x/y.py:1"):
        v = ck.check(_with_ref(ref), roots=repo)
        assert any("unsafe path" in str(x) for x in v), ref


@pytest.mark.skipif(sys.platform == "win32", reason="symlinks need privileges on Windows")
def test_evidence_refuses_symlink_outside_root(repo, tmp_path):
    """Evidence refuses symlink outside root. Covers FR-022, US2-AS2."""
    outside = tmp_path / "outside.txt"
    outside.write_text("1\n2\n", encoding="utf-8")
    try:
        os.symlink(outside, repo / "src" / "link.py")
    except OSError:
        pytest.skip("cannot create symlinks")
    v = ck.check(_with_ref("src/link.py:1"), roots=repo)
    assert any("outside the repository root" in str(x) for x in v)
    os.symlink(tmp_path, repo / "dirlink")
    assert "R7" in rules(_with_ref("dirlink/outside.txt:1"), roots=repo)


def test_resolve_ref_is_none_for_ok_and_non_refs(repo):
    """Resolve ref is none for ok and non refs. Covers FR-022, US2-AS2."""
    assert ck.resolve_ref("src/app.py:1", repo) is None
    assert ck.resolve_ref("just text", repo) is None
    assert ck.resolve_ref("src/app.py:1", None) is None


def test_credentials_are_flagged_in_labels():
    """Credentials are flagged in labels. Covers FR-022, US2-AS2."""
    d = valid()
    d["relationships"][0]["how"] = "uses STRIPE_SECRET_KEY"
    assert "R7" in rules(d)
