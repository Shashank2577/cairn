"""Step 1: what the ledger says about session-observation calls (task recall_observe).

    python ledger_report.py <brain.db> [<brain.db> ...]      # use a snapshot copy, not the live file

Prices are Anthropic's published Haiku 4.5 list prices ($1 / MTok input, $5 output, cache read 0.1x, cache
write 1.25x for a 5-minute entry or 2x for a 1-hour entry). The ledger does not record which TTL a call used,
so both are shown; the dollar figures are ESTIMATED from measured token counts, the token counts are MEASURED.
"""
import sqlite3
import sys


def pct(xs, p):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(len(xs) * p))]


for path in sys.argv[1:]:
    db = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    total = db.execute("SELECT count(*), sum(input_tokens+output_tokens+cache_read+cache_write), "
                       "sum(cache_write), sum(output_tokens) FROM ledger WHERE ok=1").fetchone()
    rows = db.execute("SELECT ts, input_tokens, output_tokens, cache_read, cache_write FROM ledger "
                      "WHERE task='recall_observe' AND ok=1 AND model LIKE '%haiku%' ORDER BY ts").fetchall()
    n = len(rows)
    inp, out, cr, cw = (sum(r[i] for r in rows) for i in (1, 2, 3, 4))
    gaps = [rows[i][0] - rows[i - 1][0] for i in range(1, n)]
    print(f"== {path}")
    obs_tok = inp + out + cr + cw
    print(f"recall_observe (haiku, ok): {n} calls = {obs_tok:,} of {total[1]:,} model tokens in the ledger ({obs_tok/total[1]:.0%})")
    print(f"  per call: input {inp/n:,.0f}  output {out/n:,.0f}  cache_write {cw/n:,.0f}  cache_read {cr/n:,.0f}")
    print(f"  cache_read / (read+write) = {cr/(cr+cw):.2%};  calls with any cache read: {sum(r[3] > 0 for r in rows)} ({sum(r[3] > 0 for r in rows)/n:.1%})")
    print(f"  cache_write p10/p50/p90 = {pct([r[4] for r in rows], .1):,} / {pct([r[4] for r in rows], .5):,} / {pct([r[4] for r in rows], .9):,}")
    print(f"  output      p10/p50/p90 = {pct([r[2] for r in rows], .1):,} / {pct([r[2] for r in rows], .5):,} / {pct([r[2] for r in rows], .9):,}")
    print("  gap to the previous observe call: " + "  ".join(f"<={s}s {sum(g <= s for g in gaps)/len(gaps):.0%}" for s in (30, 60, 300, 3600)))
    for mult, label in ((1.25, "5-minute write"), (2.0, "1-hour write")):
        cost = (inp + cr * 0.1 + cw * mult) / 1e6 + out / 1e6 * 5
        print(f"  ESTIMATED cost with {label}: ${cost:,.2f} (${cost/n:.4f}/call; output is {out/1e6*5/cost:.0%} of it)")
    other = db.execute("SELECT task, count(*), sum(input_tokens+output_tokens+cache_read+cache_write) FROM ledger WHERE ok=1 AND "
                       "task != 'recall_observe' GROUP BY 1 ORDER BY 3 DESC LIMIT 4").fetchall()
    print("  next largest tasks: " + "; ".join(f"{t} {c} calls {s:,} tok" for t, c, s in other))
