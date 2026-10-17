"""Golden Mermaid tests (T050): every example and computed view round-trips without loss, and a corpus of
real-world files imports with warnings rather than a crash or a silent drop.

FR-031 (export re-imports without loss), FR-032 (import a Mermaid subset, warn on what it ignores) and
FR-033 (state what Mermaid cannot guarantee). Covers US2.

The no-loss claim is stronger than "it imported": a description that comes back with fewer ids, edges,
labels, evidence or provenance than went in is a silent drop, and a silent drop is a bug. Where a corpus
file uses syntax the standard cannot represent, the test asserts the loss arrives as a named warning
instead, so the importer can never quietly start swallowing data without this file noticing.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

from cairn.system.diagram import check as ck
from cairn.system.diagram import mermaid_in, mermaid_out, schema
from cairn.system.link import link
from cairn.system.views import view

from .test_diagram_common import EXAMPLES
from .util import product

ROOT = Path(__file__).resolve().parents[2]
CORPUS = sorted((Path(__file__).parent / "fixtures" / "mermaid").glob("*.mmd"))
THEMES = ["light", "dark"]


def canon(d: dict) -> str:
    """The description as one canonical JSON string, so a comparison shows every difference at once."""
    return json.dumps(d, sort_keys=True, ensure_ascii=False)


def edges(d: dict) -> list[tuple]:
    """(from, to) of every arrow or message, in order."""
    return [(r["from"], r["to"]) for r in schema.relationships(d)]


# ------------------------------------------------------------------ every example
@pytest.mark.parametrize("path", EXAMPLES, ids=lambda p: p.stem)
@pytest.mark.parametrize("theme", THEMES)
def test_every_example_round_trips_without_loss(path, theme):
    """FR-031: description -> Mermaid -> description keeps ids, names, edges, labels, evidence and
    provenance, and still passes the checker. Covers FR-032, FR-033."""
    before = schema.load_file(path)
    after = mermaid_in.parse(mermaid_out.to_mermaid(before, theme=theme)).description
    assert canon(after) == canon(before)
    assert [e.get("evidence") for e in after["elements"]] == [e.get("evidence") for e in before["elements"]]
    assert [r.get("evidence") for r in schema.relationships(after)] == \
           [r.get("evidence") for r in schema.relationships(before)]
    assert [e.get("provenance") for e in after["elements"]] == [e.get("provenance") for e in before["elements"]]
    assert ck.check(after) == []


@pytest.mark.parametrize("path", EXAMPLES, ids=lambda p: p.stem)
def test_every_example_exports_without_loss(path):
    """FR-031: nothing in a description is left only in memory: every element and every arrow reaches the
    file as a `%% cairn` comment, so the facts survive a Mermaid rendering that knows nothing about them."""
    d = schema.load_file(path)
    m = mermaid_out.to_mermaid(d)
    assert m.count("%% cairn:element") == len(d["elements"])
    assert m.count("%% cairn:rel") + m.count("%% cairn:msg") == len(schema.relationships(d))
    assert mermaid_in.parse(m).description == d


def test_every_example_re_exports_identically():
    """FR-031: exporting an import again gives the same file, so the round trip is stable, not lossy in
    a way a second pass happens to paper over. Covers FR-032."""
    for path in EXAMPLES:
        d = schema.load_file(path)
        once = mermaid_out.to_mermaid(d)
        twice = mermaid_out.to_mermaid(mermaid_in.parse(once).description)
        assert twice == once, path.stem


# ------------------------------------------------------------------ computed views
def _view_product(tmp_path: Path, fixture: str, viewer: str, repos: list[str]) -> Path:
    return product(tmp_path, fixture, viewer, repos)


@pytest.fixture(scope="module")
def shop(tmp_path_factory):
    return _view_product(tmp_path_factory.mktemp("shop"), "shop", "shop-api",
                         ["shop-web", "shop-api", "shop-worker", "shop-contracts"])


@pytest.fixture(scope="module")
def orders(tmp_path_factory):
    return _view_product(tmp_path_factory.mktemp("orders"), "orders", "checkout-spring",
                         ["checkout-spring", "inventory-go"])


def _levels(root: Path, code_scope: str | None) -> list[tuple[str, dict]]:
    """Every view of this product that computes here. Components need a mapped code graph, so a level that
    cannot be computed for this fixture is skipped rather than failed: this file is about the Mermaid
    round trip, not about which levels a fixture happens to support."""
    out = []
    for level, scope in (("context", None), ("container", None), ("code", code_scope)):
        try:
            out.append((level, schema.normalize(view(root, level, scope))))
        except (ValueError, KeyError, AttributeError):
            continue
    return out


@pytest.mark.parametrize("theme", THEMES)
def test_computed_views_round_trip_without_loss(shop, orders, theme):
    """FR-031, SC-006: the same guarantee for computed views, not just for the hand-written examples: what
    the system model computes is what a Mermaid export carries and a re-import gives back. Covers FR-033."""
    seen = 0
    for root, code_scope in ((shop, "shop-api"), (orders, "checkout-spring")):
        for level, before in _levels(root, code_scope):
            after = mermaid_in.parse(mermaid_out.to_mermaid(before, theme=theme)).description
            # A view that draws no boundary normalises to an empty list; the import omits the key. The
            # facts are the same either way, so compare them as sets of claims rather than as raw dicts.
            assert canon({k: v for k, v in after.items() if k != "boundaries"}) == \
                   canon({k: v for k, v in before.items() if k != "boundaries"}), (root.name, level, theme)
            assert after.get("boundaries", []) == before.get("boundaries", []), (root.name, level)
            assert ck.check(after) == [], [str(v) for v in ck.check(after)]
            seen += 1
    assert seen >= 6, "the fixture products must yield several views"


def test_computed_container_view_keeps_every_evidence_reference(shop):
    """FR-031, SC-002: evidence is the fact that most easily goes missing in a round trip, so it is asserted
    element by element against the model the view was computed from. Covers FR-033."""
    before = schema.normalize(view(shop, "container"))
    after = mermaid_in.parse(mermaid_out.to_mermaid(before)).description
    by_id = {e["id"]: e for e in after["elements"]}
    for e in before["elements"]:
        assert by_id[e["id"]]["evidence"] == e["evidence"], e["id"]
        assert by_id[e["id"]]["provenance"] == e["provenance"], e["id"]
    pairs = {(r["from"], r["to"]) for r in before["relationships"]}
    assert pairs == {(r["from"], r["to"]) for r in after["relationships"]}
    for r in before["relationships"]:
        got = next(x for x in after["relationships"] if (x["from"], x["to"]) == (r["from"], r["to"]))
        assert got["evidence"] == r["evidence"] and got.get("what") == r.get("what")


def test_a_model_computed_view_is_the_same_whether_linked_once_or_twice(tmp_path):
    """FR-005, SC-003: the round trip reads the same stored model the view was computed from, with no second
    build and no model call; a fresh link must not change a byte of the exported file."""
    root = _view_product(tmp_path, "shop", "shop-api", ["shop-web", "shop-api", "shop-worker", "shop-contracts"])
    first = mermaid_out.to_mermaid(schema.normalize(view(root, "container")))
    link(root)
    assert mermaid_out.to_mermaid(schema.normalize(view(root, "container"))) == first


# ------------------------------------------------------------------ the corpus
def test_the_corpus_is_not_empty_and_is_mermaid():
    """The corpus is the point of this file: it must exist, and every file must look like Mermaid, or the
    CLI would read it as YAML and the "imports with warnings" claim would be untested."""
    assert len(CORPUS) >= 10
    for p in CORPUS:
        assert mermaid_in.looks_like_mermaid(p.read_text(encoding="utf-8")), p.name


# Each corpus file, and what it must produce. ``warnings`` are substrings: every file states the loss it
# expects to be told about, so a new silent drop shows up as a warning that no longer arrives.
EXPECTED: dict[str, dict] = {
    "flowchart-basic.mmd": {
        "elements": ["shopper", "web", "api", "db", "bus", "worker", "mail"],
        "types": {"shopper": "person", "db": "data-store", "bus": "channel"},
        "edges": [("shopper", "web"), ("web", "api"), ("api", "db"), ("api", "bus"), ("worker", "bus"),
                  ("api", "mail")],
        "styles": {("api", "bus"): "async"},
        "labels": {("web", "api"): ("Creates orders", None), ("api", "db"): ("Reads and writes orders", None)},
    },
    "flowchain-and-edges.mmd": {
        "elements": ["src", "cart", "pay", "promo", "total", "gateway", "report", "vault", "legacy"],
        "edges": [("src", "cart"), ("cart", "pay"), ("cart", "total"), ("promo", "total"), ("pay", "gateway"),
                  ("total", "pay"), ("report", "pay"), ("src", "cart"), ("vault", "pay"), ("legacy", "vault")],
        "labels": {("pay", "gateway"): ("Authorises", None), ("total", "pay"): ("Revalidates", None),
                   ("legacy", "vault"): ("Nightly batch", None)},
        "styles": {("total", "pay"): "async", ("legacy", "vault"): "async"},
        "warnings": [],
    },
    "flowchart-subgraphs.mmd": {
        "boundaries": {"shop": ("Shop", "software system"), "inner": ("Runtime", None),
                       "net": ("Compose network", "network isolation"), "unclosed": ("Reporting", None)},
        "edges": [("web", "api"), ("api", "db"), ("api", "redis"), ("worker", "redis"), ("sched", "web")],
        "warnings": ["nested subgraph 'inner' flattened", "edge to or from a subgraph ignored",
                     "subgraph 'unclosed' is never closed"],
    },
    "flowchart-labels.mmd": {
        "names": {"a": 'He said "hello"', "b": "a]b", "c": "c|d", "d": "100% & rising",
                  "e": "Ünïcödé ñ 日本語 \U0001f389", "f": "line one", "g": "escaped <arrow> && more",
                  "h": "A &amp; B", "i": "50% off; today only", "j": "x;y", "p": "Bracket ] inside",
                  "q": "Brace } inside"},
        "tech": {"k": "Kafka", "l": "S3"},
        "descs": {"f": "line two"},
        "warnings": ["shape '((' treated as a plain box"],
    },
    "flowchart-styled.mmd": {
        "elements": ["web", "api", "db"],
        "edges": [("web", "api"), ("api", "db")],
        "warnings": ["'click' ignored", "'links' ignored", "styling statement(s)"],
    },
    "sequence-auth.mmd": {
        "title": "Sign in with a token",
        "participants": ["User", "Web", "Gate", "Auth"],
        "messages": [("User", "Web", "sync"), ("Web", "Gate", "sync"), ("Gate", "Auth", "sync"),
                     ("Auth", "Gate", "return"), ("Gate", "Web", "return"), ("Web", "User", "return"),
                     ("Gate", "User", "async"), ("Web", "Gate", "sync"), ("Gate", "Web", "return"),
                     ("Web", "Gate", "sync"), ("Gate", "Web", "return"), ("Gate", "Auth", "sync")],
        "fragments": [("alt", "password accepted", "rejected"), ("opt", "refresh soon", None),
                      ("loop", "every 5 minutes while the tab is open", None)],
        "labels": {("User", "Web"): ("Sign in with email", "POST /session"),
                   ("Gate", "Auth"): ("Verify the password", "principal()")},
        "warnings": ["notes are ignored"],
    },
    "sequence-messy.mmd": {
        "title": "Refund a card payment",
        "participants": ["Web", "Pay", "Ledger"],
        "messages": [("Web", "Pay", "sync"), ("Pay", "Ledger", "sync"), ("Pay", "Web", "return"), ("Pay", "Ledger"),
                     ("Pay", "Ledger"), ("Web", "Pay", "sync"), ("Pay", "Ledger"), ("Pay", "Web", "return")],
        "fragments": [("critical", "the ledger must accept", None)],
        "warnings": ["lost-message arrow imported as a synchronous message",
                     "a fragment is never closed; it is dropped",
                     "%% cairn comment for message 99 ignored"],
    },
    "c4-container.mmd": {
        "title": "Containers of Shop",
        "types": {"customer": "person", "web": "container", "api": "container", "db": "data-store",
                  "bus": "channel", "worker": "container", "mail": "external-system"},
        "tech": {"web": "Node.js", "db": "PostgreSQL 16", "bus": "Redis pub/sub"},
        "boundaries": {"shop": ("Shop", "software system", ["web", "api", "db", "bus", "worker"])},
        "edges": [("customer", "web"), ("web", "api"), ("api", "db"), ("api", "bus"), ("worker", "bus"),
                  ("worker", "mail"), ("api", "web")],
        "labels": {("api", "db"): ("Reads and writes orders", "SQL"), ("api", "bus"): ("Publishes order.created",
                                                                                      "PUBLISH")},
        "warnings": [],
    },
    "c4-context.mmd": {
        "title": "System context of Shop",
        "types": {"customer": "person", "ops": "person", "shop": "system", "mail": "external-system",
                  "payments": "external-system"},
        "edges": [("customer", "shop"), ("shop", "mail"), ("shop", "payments"), ("ops", "shop")],
        "no_how": True,
        "warnings": [],
    },
    "flowchart-cairn-comments.mmd": {
        "title": "Containers of Shop",
        "scope": "Shop",
        "elements": ["web", "api", "db"],
        "types": {"web": "container", "api": "container", "db": "data-store"},
        "kind": {"web": "web-app", "api": "service"},
        "tech": {"web": "Node.js", "api": "Python", "db": "postgres"},
        "evidence": {"web": ["shop-web/server.js:5"], "api": ["shop-api/main.py:3"],
                     "db": ["shop-api/compose.yml: db"]},
        "focus": ["db"],
        "how": {("api", "db"): "SQL"},
        "boundaries": {"shop": ("Shop", "software system")},
        "warnings": ["%% cairn:ghost comment carries no JSON object, ignored",
                     "unreadable %% cairn:rel comment ignored",
                     "%% cairn comment for relationship web->db ignored",
                     "%% cairn comment for relationship nothing->nowhere ignored"],
    },
}


@pytest.mark.parametrize("path", CORPUS, ids=lambda p: p.stem)
def test_corpus_file_imports_with_warnings_not_a_crash(path):
    """FR-032: every file in the corpus imports, the description is loadable and every later stage runs on
    it. Covers FR-033."""
    imp = mermaid_in.parse(path.read_text(encoding="utf-8"))
    assert isinstance(imp.warnings, list)
    d = schema.loads(json.dumps(imp.description))
    assert d["elements"], path.name
    ck.check(d)


@pytest.mark.parametrize("path", CORPUS, ids=lambda p: p.stem)
def test_corpus_file_declares_what_it_produces(path):
    """Every corpus file says what it must produce, so it is a test and not decoration. A file with no
    entry here is a hole: it would import whatever the importer happened to do."""
    assert path.name in EXPECTED, f"{path.name} has no entry in EXPECTED"


@pytest.mark.parametrize("name", sorted(EXPECTED), ids=lambda n: n[:-4])
def test_corpus_file_produces_exactly_what_it_declares(name):
    """The declaration: ids, shapes, technologies, arrows, labels, boundaries, fragments, provenance and
    evidence, plus every loss arriving as a warning. Anything dropped without a warning fails here."""
    want = EXPECTED[name]
    imp = mermaid_in.parse((Path(__file__).parent / "fixtures" / "mermaid" / name).read_text(encoding="utf-8"))
    d = imp.description
    els = {e["id"]: e for e in d["elements"]}

    if "title" in want:
        assert d.get("title") == want["title"]
    if "scope" in want:
        assert d.get("scope") == want["scope"]
    if "elements" in want:
        assert list(els) == want["elements"]
    if "participants" in want:
        assert d["flow"]["participants"] == want["participants"]
    for eid, etype in want.get("types", {}).items():
        assert els[eid].get("type") == etype, eid
    for eid, tech in want.get("tech", {}).items():
        assert els[eid].get("tech") == tech, eid
    for eid, name_ in want.get("names", {}).items():
        assert els[eid].get("name") == name_, eid
    for eid, kind in want.get("kind", {}).items():
        assert els[eid].get("kind") == kind, eid
    for eid, ev in want.get("evidence", {}).items():
        assert els[eid].get("evidence") == ev, eid
    for eid in want.get("focus", []):
        assert els[eid].get("focus") is True, eid
    for eid, desc in want.get("descs", {}).items():
        assert els[eid].get("desc") == desc, eid

    if "edges" in want:
        assert edges(d) == want["edges"]
    if "messages" in want:
        want_msgs = want["messages"]
        got = [(m["from"], m["to"], m["style"]) for m in d["flow"]["messages"]]
        assert got == [(t[0], t[1], t[2] if len(t) > 2 else "sync") for t in want_msgs]
    for (a, b), (what, how) in want.get("labels", {}).items():
        r = next(x for x in schema.relationships(d) if (x["from"], x["to"]) == (a, b))
        assert r.get("what") == what and r.get("how") == how, (a, b)
    for (a, b), style in want.get("styles", {}).items():
        assert next(x for x in schema.relationships(d) if (x["from"], x["to"]) == (a, b))["style"] == style
    if want.get("no_how"):
        assert all("how" not in r for r in d["relationships"])

    if "boundaries" in want:
        got = {b["id"]: (b["name"], b.get("type")) for b in d.get("boundaries", [])}
        assert got == {k: v[:2] for k, v in want["boundaries"].items()}
        for bid, spec in want["boundaries"].items():
            if len(spec) > 2:
                assert next(b for b in d["boundaries"] if b["id"] == bid)["contains"] == spec[2]
    if "fragments" in want:
        assert [(f["type"], f.get("label"), f.get("else_label")) for f in d["flow"]["fragments"]] == want["fragments"]

    for fragment in want.get("warnings", []):
        assert any(fragment in w for w in imp.warnings), (name, fragment, imp.warnings)


@pytest.mark.parametrize("path", CORPUS, ids=lambda p: p.stem)
def test_corpus_file_survives_a_second_export_and_import(path):
    """FR-031 applied to foreign input: whatever the importer did understand must be exportable and stable,
    so a corpus file can be handed on without its understood half decaying on the way."""
    once = mermaid_in.parse(path.read_text(encoding="utf-8")).description
    again = mermaid_in.parse(mermaid_out.to_mermaid(once)).description
    assert canon({k: v for k, v in again.items() if k != "boundaries"}) == \
           canon({k: v for k, v in once.items() if k != "boundaries"})
    assert mermaid_out.to_mermaid(again) == mermaid_out.to_mermaid(once)


@pytest.mark.parametrize("path", CORPUS, ids=lambda p: p.stem)
def test_corpus_file_reaches_the_cli_as_mermaid(path):
    """FR-032: `cairn diagram check` must read a `.mmd` as Mermaid (not as YAML), report the standard's
    violations and print the importer's warnings, so a hand-made chart has one command. Covers FR-033."""
    from typer.testing import CliRunner

    from cairn.system.diagram.cli import diagram_app

    r = CliRunner().invoke(diagram_app, ["check", str(path)])
    assert r.exit_code in (0, 1), r.output
    assert "cannot be read" not in r.output
    assert "violation(s)" in r.output or "OK" in r.output
    r2 = CliRunner().invoke(diagram_app, ["from-mermaid", str(path)])
    assert r2.exit_code == 0, r2.output
    assert schema.loads(r2.stdout) == mermaid_in.parse(path.read_text(encoding="utf-8")).description


def test_the_corpus_is_covered_by_the_render_pipeline():
    """FR-031, FR-033: the same importer runs inside the render pipeline, so every corpus file produces an
    SVG and an evidence table without a traceback. This is what a hand-made diagram actually does next."""
    from cairn.system.diagram import alt, layout, svg

    for path in CORPUS:
        d = mermaid_in.parse(path.read_text(encoding="utf-8")).description
        geometry = layout.layout(d)
        assert svg.render(d, geometry).startswith("<svg"), path.name
        assert alt.evidence_table(d).startswith("**"), path.name


# ------------------------------------------------------------------ the known hole
_BAD_FRAGMENT = {"type": "b--xa: refunds card", "start": 1, "end": 1, "label": "retry"}


def _flow_with_fragment(ftype: str) -> dict:
    return schema.normalize({"diagram": "flow", "title": "Flow of refunds", "scope": "payments",
                             "description": "A payment is charged and refunded.",
                             "elements": [{"id": "a", "name": "A", "type": "container", "desc": "x",
                                           "evidence": ["a:1"]},
                                          {"id": "b", "name": "B", "type": "container", "desc": "y",
                                           "evidence": ["a:2"]}],
                             "flow": {"participants": ["a", "b"],
                                      "messages": [{"from": "a", "to": "b", "what": "Charges", "how": "HTTP",
                                                    "evidence": ["a:3"]}],
                                      "fragments": [{"type": ftype, "start": 1, "end": 1, "label": "retry"}]}})


def test_a_fragment_type_that_is_not_mermaid_is_reported_not_swallowed():
    """The recorded defect, pinned as it stands: a fragment whose type is not Mermaid syntax is lost on
    round-trip. The loss must be loud.

    The type is written into the chart verbatim by ``mermaid_out``, and on the way back it is
    indistinguishable from a real ``--x`` lost-message arrow, so the importer cannot repair it here and
    does not try. What it must do is refuse to swallow it: two warnings name the unreadable arrow and the
    block that is left open. The remaining gap is that the bogus line also reads as a message, so the
    description gains one; fixing that means validating the type where it enters the model
    (``schema._fragment``) or escaping it on export (``mermaid_out``), both outside this task. Covered:
    FR-032.
    """
    bad = _flow_with_fragment(_BAD_FRAGMENT["type"])
    imp = mermaid_in.parse(mermaid_out.to_mermaid(bad))
    assert imp.description["flow"]["fragments"] == [], "a type Mermaid cannot read must not come back as a fragment"
    assert any("lost-message arrow" in w for w in imp.warnings), imp.warnings
    assert any("stray 'end'" in w for w in imp.warnings), imp.warnings


def test_a_well_formed_fragment_type_round_trips():
    """The counterpart: a type Mermaid does read comes back as the same fragment with no warning at all, so
    naming the bad ones costs nothing on the good path. Covers FR-031, FR-032."""
    for ftype in ("alt", "opt", "loop", "par", "critical", "break"):
        d = _flow_with_fragment(ftype)
        back = mermaid_in.parse(mermaid_out.to_mermaid(d))
        assert back.description["flow"]["fragments"] == d["flow"]["fragments"], ftype
        assert back.description == d, ftype
        assert back.warnings == [], ftype


# ------------------------------------------------------------------ CI renders them
def test_the_mermaid_rendering_workflow_covers_the_corpus():
    """FR-031: "it parses" is only ever true for Cairn's parser unless the real Mermaid CLI also sees it.
    The workflow renders every export with the CLI in CI; read, not run, because it needs Node and a
    browser."""
    text = (ROOT / ".github/workflows/diagrams.yml").read_text(encoding="utf-8")
    assert "mermaid-cli" in text
    assert "examples/*.yaml" in text and "export_fixture_views.py" in text
    assert "fixtures/mermaid" in text


# ------------------------------------------------------------------ the published standard
DOCS = ROOT / "docs/diagram-standard"
GENERATED = ["rules.md", "mermaid.md", "taazaa.md", "tokens.json"]


def test_the_published_docs_are_generated_from_the_canonical_sources():
    """T026: the published standard must be what the code says, so the generated pages are checked rather
    than trusted. Running the generator with --check is the same gate CI gets. Covers FR-020, FR-021, FR-023."""
    r = subprocess.run([sys.executable, str(ROOT / "scripts/sync_diagram_docs.py"), "--check"],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    r2 = subprocess.run([sys.executable, str(ROOT / "scripts/sync_tokens.py"), "--check"],
                        capture_output=True, text=True)
    assert r2.returncode == 0, r2.stdout + r2.stderr
    for name in GENERATED:
        assert (DOCS / name).exists(), name
        assert (DOCS / name).read_text(encoding="utf-8").strip(), name


def test_every_rule_the_checker_reports_is_documented():
    """T026, FR-022: a rule nobody can read is a rule nobody follows. The rule ids come from check.py and
    the prose from the generated page, so a new rule without a description fails here."""
    rules_md = (DOCS / "rules.md").read_text(encoding="utf-8")
    checker = (ROOT / "src/cairn/system/diagram/check.py").read_text(encoding="utf-8")
    reported = {m for m in re.findall(r'add\("(R\d+)"', checker)}
    assert reported, "the checker's rule ids could not be read"
    for rid in sorted(reported, key=lambda r: int(r[1:])):
        assert f"**{rid}**" in rules_md, f"{rid} is reported by the checker but not documented"


def test_the_published_taazaa_counts_are_measured_not_asserted():
    """T026, FR-023: the mapping claims 47 and 53 violations. Those numbers must be what the checker says
    today, or the claim is a rumour."""
    taazaa_md = (DOCS / "taazaa.md").read_text(encoding="utf-8")
    for path in sorted((DOCS / "taazaa-original").glob("*.yaml")):
        violations = ck.check(schema.load_file(path))
        assert f"| {len(violations)} |" in taazaa_md, path.name
        for rid in ck.rule_ids(violations):
            assert rid in taazaa_md, (path.name, rid)


def test_every_diagram_under_docs_is_checked():
    """T026: the checker runs over every diagram YAML in docs, not just the examples a test happened to
    list, so a diagram added later cannot skip the standard."""
    import glob

    found = sorted(glob.glob(str(ROOT / "docs/**/*.yaml"), recursive=True))
    assert found, "no diagram YAML under docs"
    for f in found:
        d = schema.load_file(f)
        if "taazaa-original" in f:
            assert ck.check(d), f"{Path(f).name} is a transcription and is meant to fail"
        else:
            assert ck.check(d) == [], [str(v) for v in ck.check(d)]