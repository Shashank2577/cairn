"""PROTOTYPE: re-check a stored model against a fresh extraction; mark vanished claims stale with the reason."""
import sys, yaml
old, new, out = (yaml.safe_load(open(p)) for p in sys.argv[1:3]) + (sys.argv[3],) if False else (yaml.safe_load(open(sys.argv[1])), yaml.safe_load(open(sys.argv[2])), sys.argv[3])
key = lambda r: (r["from"], r["to"], r["what"])
now = {key(r): r for r in new["relationships"]}
merged, report = [], []
for r in old["relationships"]:
    if key(r) in now:
        merged.append(now[key(r)])
    else:
        merged.append(dict(r, provenance="stale")); report.append(f"STALE {r['from']} -> {r['to']} '{r['what']}': evidence {r['evidence']} no longer found")
for k, r in now.items():
    if k not in {key(x) for x in old["relationships"]}:
        merged.append(r); report.append(f"NEW   {r['from']} -> {r['to']} '{r['what']}'")
new["relationships"] = merged
new["title"] = new["title"] + ", after re-check"
yaml.safe_dump(new, open(out, "w"), sort_keys=False, allow_unicode=True, width=200)
print("\n".join(report) or "no changes")
