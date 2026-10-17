"""Dollar figures for the Overview and Settings: list prices only for models whose price is confirmed, tokens only
for the rest, and the savings fields the Overview bars read."""
from __future__ import annotations

from cairn.router import cost_usd, price_for
from test_surfaces import _client


def test_price_lookup_matches_dated_ids_and_never_guesses():
    assert price_for("claude-haiku-4-5-20251001")["input"] == 1.0
    assert price_for("claude-haiku-5-5")["input"] == 0.10  # not the shorter claude-haiku-4-5 row
    p = price_for("claude-sonnet-5")
    assert (p["input"], p["output"], p["cache_read"], p["cache_write"]) == (2.0, 10.0, 0.2, 4.0)
    assert price_for("claude-opus-5-5") is None and price_for("") is None
    assert cost_usd("claude-opus-5-5", 1000, 1000) is None


def test_cost_adds_cache_rates_separately():
    # 1M fresh in + 1M out + 1M cache read + 1M cache write at Sonnet: 2 + 10 + 0.2 + 4
    assert abs(cost_usd("claude-sonnet-5", 10**6, 10**6, 10**6, 10**6) - 16.2) < 1e-9


def test_models_endpoint_prices_known_models_and_flags_the_rest(cairn, tmp_path):
    c, p = _client(cairn, tmp_path)
    cairn.brain.log_call("ask", "deep", "claude-opus-5-5", {"input": 1000, "output": 100}, True)
    cairn.brain.log_call("classify", "fast", "claude-haiku-4-5-20251001", {"input": 1_000_000}, True)
    m = c.get(f"{p}/models").json()
    by = {t["model"]: t for t in m["totals"]}
    assert by["claude-opus-5-5"]["cost_usd"] is None
    assert abs(by["claude-haiku-4-5-20251001"]["cost_usd"] - 1.0) < 1e-9
    assert m["cost_usd"] == by["claude-haiku-4-5-20251001"]["cost_usd"]  # the total leaves out the unpriced model
    assert m["unpriced_models"] == ["claude-opus-5-5"] and m["priced_as_of"]


def test_savings_totals_carry_what_the_overview_bars_read(cairn, tmp_path):
    c, p = _client(cairn, tmp_path)
    cairn.brain.log_query("mcp", "impact", "a.py", 300, 30_000, 2)
    s = c.get(f"{p}/savings").json()
    row = s["totals"][0]
    assert (row["sent_with_source"], row["source"], row["n"]) == (300, 30_000, 1)
    assert s["pricing"] is None or {"model", "input", "cache_read", "as_of"} <= set(s["pricing"])
