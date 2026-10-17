"""The system diagram on the page (spec 002: T030, T031, T035).

Two halves. The unit half needs no server: the page draws the diagram standard's own output, so
``render_inline`` has to keep that promise — no embedded stylesheet, every colour pointing at the page's
``--dg-*`` tokens, and the same geometry the exported file gets. The browser half runs against a live
server and checks the thing that was actually broken before: every level draws its own diagram, and
selecting an element opens its evidence.

Run the browser half with ``CAIRN_UI_TESTS=1 pytest tests/ui/test_map_views.py`` and a server on
``CAIRN_UI_URL`` (default http://127.0.0.1:4747). It skips without them.
"""
from __future__ import annotations

import json
import os
import re

import pytest

from cairn.system.diagram.svg import render, render_inline
from cairn.system.diagram import load_tokens

EXAMPLE = {
    "diagram": "container",
    "title": "Containers of Shop",
    "scope": "Shop (software system, 4 repositories) · commit abc123 · 0 model calls",
    "description": "The storefront calls the orders API.",
    "source_note": "computed, 0 model calls",
    "elements": [
        {"id": "web", "name": "shop-web", "type": "container", "kind": "web-app", "tech": "Node.js",
         "desc": "Storefront pages", "provenance": "extracted", "evidence": ["shop-web/Dockerfile:1"]},
        {"id": "db", "name": "PostgreSQL", "type": "data-store", "tech": "postgres 16",
         "desc": "Stores orders", "provenance": "extracted", "evidence": ["shop-api/compose.yml:4"]},
        {"id": "ext", "name": "SendGrid", "type": "external-system",
         "desc": "Delivers email", "provenance": "extracted", "evidence": ["shop-worker/mailer.py:6"]},
    ],
    "boundaries": [{"id": "shop", "name": "Shop", "type": "software system",
                    "contains": ["web", "db"], "provenance": "extracted", "evidence": ["system.yaml"]}],
    "relationships": [
        {"from": "web", "to": "db", "what": "Creates and reads orders", "how": "SQL · psycopg",
         "style": "sync", "provenance": "extracted", "rule": "store-use", "evidence": ["shop-web/api.js:9"]},
        {"from": "web", "to": "ext", "what": "Sends confirmation email", "how": "HTTPS · SDK",
         "style": "async", "provenance": "extracted", "rule": "catalog-sdk", "evidence": ["shop-web/mail.js:3"]},
    ],
}

LEVELS = ["context", "containers", "components", "flows", "code"]


# ------------------------------------------------------------------ render_inline: the page's copy
def test_inline_is_the_same_drawing_as_the_export():
    """One renderer: the page shows what ``cairn docs export`` writes, not a second implementation."""
    T = load_tokens()
    a, b = render(EXAMPLE, tokens=T), render_inline(EXAMPLE, tokens=T)
    strip = lambda s: re.sub(r"<style>.*?</style>", "", s, flags=re.S).replace("dg-", "")
    assert strip(b) == strip(a)


def test_inline_defers_colour_to_the_page_instead_of_carrying_its_own():
    """A standalone file must carry its palette; embedded, the page already has every role."""
    svg = render_inline(EXAMPLE)
    assert "<style>" not in svg, "an embedded stylesheet would fight the page's theme"
    left = re.findall(r"var\(--(?!dg-)[a-z0-9-]+\)", svg)
    assert left == [], f"colour roles the page does not define: {sorted(set(left))}"
    assert "var(--dg-node-fill)" in svg and "var(--dg-ink)" in svg


def test_inline_still_draws_shapes_boundaries_and_legend():
    """Stripping the stylesheet must not cost any of the drawing."""
    svg = render_inline(EXAMPLE)
    assert 'data-id="web"' in svg and 'data-id="db"' in svg      # elements keep their hook
    assert "<ellipse" in svg                                     # the data store is still a cylinder
    assert "Creates and reads orders" in svg                      # arrows keep their labels
    assert "LEGEND" in svg


def test_every_colour_role_the_renderer_uses_exists_in_the_page_stylesheet():
    """The page's generated tokens and the renderer's roles are one list; a new role must break here.

    ``scripts/sync_tokens.py`` writes ``--dg-*`` into tokens.css from the same tokens.json, so this is
    the join between the two copies: a role the renderer emits but the page never defines would render
    as an unresolved variable, which is invisible until someone looks at it.
    """
    from pathlib import Path
    css = (Path(__file__).resolve().parents[2] / "src/cairn/ui/styles/tokens.css").read_text(encoding="utf-8")
    defined = set(re.findall(r"--dg-([a-z0-9-]+)\s*:", css))
    used = {m for m in re.findall(r"var\(--dg-([a-z0-9-]+)\)", render_inline(EXAMPLE))}
    assert used <= defined, f"used by the renderer, missing from tokens.css: {sorted(used - defined)}"


# ------------------------------------------------------------------ the page: needs a server
pytestmark_browser = pytest.mark.skipif(
    not os.environ.get("CAIRN_UI_TESTS"),
    reason="browser half needs CAIRN_UI_TESTS=1 and a running Cairn server",
)


@pytest.fixture(scope="session")
def base_url():
    return os.environ.get("CAIRN_UI_URL", "http://127.0.0.1:4747")


@pytest.fixture(scope="session")
def browser():
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        b = p.chromium.launch()
        yield b
        b.close()


@pytest.fixture
def page(browser, base_url):
    pg = browser.new_page(viewport={"width": 1500, "height": 1000})
    pg.goto(base_url)
    pg.wait_for_selector(".page", timeout=20000)
    yield pg
    pg.close()


def _pid(page):
    return page.evaluate("location.hash.split('/')[2]")


def _goto(page, level):
    pid = _pid(page)
    page.goto(f"{page.url.split('#')[0].rstrip('/')}/#/p/{pid}/map/system/{level}")


EXPECTED_TITLE = {
    "context": "context",
    "containers": "Containers",
    "components": "Components",
    "flows": "Flow",
    "code": "Code",
}


def _settled(page, level):
    """Wait for *this level's* drawing, not merely for a drawing.

    Waiting on ``.sysfig svg`` alone passes against the previous level's picture: the fetch has not
    started yet, so nothing is marked stale and the check is satisfied by what is already on screen.
    That is exactly the bug these tests exist to catch, so the wait has to name the answer.
    """
    want = EXPECTED_TITLE[level].lower()
    page.wait_for_function(
        "want => { const t = document.querySelector('.sysfig svg title');"
        " return t && t.textContent.toLowerCase().includes(want); }",
        arg=want, timeout=30000)
    return page.eval_on_selector(".sysfig svg title", "e=>e.textContent")


@pytestmark_browser
@pytest.mark.parametrize("level", LEVELS)
def test_every_level_draws_its_own_diagram(page, level):
    """The bug this replaces: every tab showed the context diagram whatever you picked."""
    _goto(page, level)
    title = _settled(page, level)
    assert EXPECTED_TITLE[level].lower() in title.lower(), f"{level} drew {title!r}"


@pytestmark_browser
def test_selecting_an_element_opens_its_evidence(page):
    """Click opens the evidence panel with the references that back the claim."""
    _goto(page, "containers")
    _settled(page, "containers")
    page.locator(".sysfig g.node").first.click()
    page.wait_for_selector(".sys-ev", timeout=8000)
    assert page.locator(".sys-ev h3").text_content().strip()
    assert page.locator(".sys-ev code").count() >= 1, "no evidence reference shown"


@pytestmark_browser
def test_elements_are_reachable_from_the_keyboard(page):
    """Every element is focusable and selectable without a mouse."""
    _goto(page, "containers")
    _settled(page, "containers")
    nodes = page.locator(".sysfig g.node")
    assert nodes.count() >= 1
    assert page.eval_on_selector_all(".sysfig g.node", 'els=>els.every(e=>e.getAttribute("tabindex")==="0")')
    page.locator(".sysfig g.node").nth(1).focus()
    page.keyboard.press("Enter")
    page.wait_for_selector(".sys-ev", timeout=8000)


@pytestmark_browser
def test_the_drawing_never_widens_the_page(page):
    """A wide diagram scrolls in its own box; it must not push the page sideways."""
    _goto(page, "components")
    _settled(page, "components")
    page.get_by_role("button", name="Actual size").click()
    page.wait_for_timeout(300)
    assert page.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth") == 0


@pytestmark_browser
def test_the_page_draws_no_model_calls_and_says_so(page):
    """The scope line says where the picture came from; a reader can see it was computed."""
    _goto(page, "containers")
    _settled(page, "containers")
    assert "0 model calls" in page.eval_on_selector(".sysfig svg", "e=>e.textContent")