"""PROTOTYPE (throwaway, not part of src/): compute a Containers view for a multi-repo product.

Read-only and deterministic: no model calls, no network, nothing written inside the repositories.
Reuses Cairn's own declarations() (packages each repo provides and depends on) from src/; the other
signals (compose, Dockerfile, routes, outbound HTTP, pub/sub, catalog) are the new extractors the spec
proposes, written here as regex passes so their output can be judged before the real build.

Usage: python extract.py <product-dir-with-system.yaml> <out.yaml>
"""
from __future__ import annotations
import json, os, re, subprocess, sys
from collections import defaultdict
from pathlib import Path
import yaml

sys.path.insert(0, "" + str(Path(__file__).resolve().parents[4] / 'src') + "")
from cairn.engines.graph.repos import declarations  # noqa: E402  (Cairn's own extraction)

HERE = Path(__file__).parent
CAT = json.loads((HERE / "catalog.json").read_text())

ROUTE_PY = re.compile(r'@(?:\w+)\.(get|post|put|patch|delete)\(\s*["\']([^"\']+)["\']')
ROUTE_JS = re.compile(r'\b(?:app|router)\.(get|post|put|patch|delete)\(\s*["\'`]([^"\'`]+)["\'`]')
FETCH_JS = re.compile(r'fetch\(\s*`?\$\{(\w+)\}([^`"\']*)`?(?:\s*,\s*\{[^}]*method:\s*["\'](\w+)["\'])?')
REQ_PY = re.compile(r'requests\.(get|post|put|patch|delete)\(\s*f?["\'](?:\{(\w+)\})?([^"\']*)["\']')
ENV_JS = re.compile(r'(?:const|let|var)\s+(\w+)\s*=\s*process\.env\.(\w+)')
ENV_ANY = re.compile(r'os\.environ(?:\.get)?[\[(]\s*["\'](\w+)["\']|process\.env\.(\w+)')
PUB = re.compile(r'\.publish\(\s*["\']([\w.\-:]+)["\']')
SUB = re.compile(r'\.subscribe\(\s*["\']([\w.\-:]+)["\']')
WRAP_DEF = re.compile(r'def (\w+)\((\w+)[^)]*\)\s*(?:->\s*[^:]+)?:[\s\S]{0,400}?\.publish\(\s*\2\b')
SQL = re.compile(r'\b(INSERT INTO|SELECT .*? FROM|UPDATE|DELETE FROM)\s+(\w+)', re.I)
IMPORT_PY = re.compile(r'^\s*(?:from|import)\s+([\w.]+)', re.M)
REQUIRE_JS = re.compile(r'require\(\s*["\']([@\w/.\-]+)["\']\)|from\s+["\']([@\w/.\-]+)["\']')


def git_files(repo: Path) -> list[str]:
    out = subprocess.run(["git", "-C", str(repo), "ls-files"], capture_output=True, text=True).stdout.split()
    return [f for f in out if not f.startswith(".cairn/")]


def lines_of(repo: Path, rel: str) -> list[str]:
    try:
        return (repo / rel).read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []


def find(repo: Path, files, rx, exts) -> list[tuple[str, int, re.Match]]:
    hits = []
    for f in files:
        if not f.endswith(exts):
            continue
        for i, ln in enumerate(lines_of(repo, f), 1):
            for m in rx.finditer(ln):
                hits.append((f, i, m))
    return hits


def norm_path(p: str) -> str:
    p = re.sub(r"\$\{[^}]+\}|\{[^}]+\}|:\w+", "{}", p.split("?")[0])
    return p.rstrip("/") or "/"


def main(product: str, out: str):
    P = Path(product)
    sysdef = yaml.safe_load((P / "system.yaml").read_text())
    name = sysdef["system"]
    repos = {r["path"]: P / r["path"] for r in sysdef["repos"]}
    facts = {}
    for rid, repo in repos.items():
        if not (repo / ".git").exists():
            facts[rid] = {"missing": True}
            continue
        files = git_files(repo)

        class Idx:  # Cairn's declarations() only needs the file list
            by_file = {f: 0 for f in files}
        decl = declarations(repo, Idx)
        F = {"files": files, "decl": decl}
        eco = "python" if decl["python"] else "js" if decl["js"] else None
        F["eco"] = eco
        F["provides"] = set((decl.get(eco) or {}).values()) if eco else set()
        F["deps"] = set(decl["deps"].get(eco, [])) if eco else set()
        # entry points
        F["dockerfile"] = {}
        for i, ln in enumerate(lines_of(repo, "Dockerfile"), 1):
            if ln.startswith("EXPOSE"):
                F["dockerfile"]["port"] = (ln.split()[1], f"{rid}/Dockerfile:{i}")
            if ln.startswith("CMD"):
                F["dockerfile"]["cmd"] = (ln[3:].strip(), f"{rid}/Dockerfile:{i}")
        scripts = []
        for i, ln in enumerate(lines_of(repo, "pyproject.toml"), 1):
            if ln.strip() == "[project.scripts]":
                scripts.append(f"{rid}/pyproject.toml:{i}")
        if (repo / "package.json").exists():
            pj = json.loads((repo / "package.json").read_text())
            if pj.get("scripts", {}).get("start") or pj.get("bin"):
                scripts.append(f"{rid}/package.json: scripts.start")
        F["scripts"] = scripts
        exts_py, exts_js = (".py",), (".js", ".ts", ".mjs", ".cjs", ".tsx")
        F["routes"] = [(m.group(1).upper(), norm_path(m.group(2)), f"{rid}/{f}:{i}")
                       for f, i, m in find(repo, files, ROUTE_PY, exts_py) + find(repo, files, ROUTE_JS, exts_js)]
        envmap = {m.group(1): m.group(2) for f, i, m in find(repo, files, ENV_JS, exts_js)}
        calls = []
        for f, i, m in find(repo, files, FETCH_JS, exts_js):
            calls.append((m.group(3).upper() if m.group(3) else "GET", norm_path(m.group(2)), envmap.get(m.group(1)), f"{rid}/{f}:{i}"))
        for f, i, m in find(repo, files, REQ_PY, exts_py):
            calls.append((m.group(1).upper(), norm_path(m.group(3)), None, f"{rid}/{f}:{i}"))
        F["calls"] = calls
        F["env"] = sorted({(m.group(1) or m.group(2)) for f, i, m in find(repo, files, ENV_ANY, exts_py + exts_js)})
        pubs = [(m.group(1), f"{rid}/{f}:{i}") for f, i, m in find(repo, files, PUB, exts_py + exts_js)]
        wrappers = set()
        for f in files:
            if f.endswith(".py"):
                for m in WRAP_DEF.finditer("\n".join(lines_of(repo, f))):
                    wrappers.add(m.group(1))
        for w in wrappers:
            for f, i, m in find(repo, files, re.compile(r'\b' + w + r'\(\s*["\']([\w.\-:]+)["\']'), exts_py):
                pubs.append((m.group(1), f"{rid}/{f}:{i}"))
        F["pubs"] = pubs
        F["subs"] = [(m.group(1), f"{rid}/{f}:{i}") for f, i, m in find(repo, files, SUB, exts_py + exts_js)]
        sql = defaultdict(set); sqlev = {}
        for f, i, m in find(repo, files, SQL, exts_py + exts_js):
            verb = m.group(1).split()[0].upper()
            sql[m.group(2)].add({"INSERT": "writes", "SELECT": "reads", "UPDATE": "writes", "DELETE": "writes"}[verb])
            sqlev.setdefault(m.group(2), f"{rid}/{f}:{i}")
        F["sql"], F["sqlev"] = sql, sqlev
        # library usage actually imported in code (dependency alone is not enough)
        used = {}
        for f, i, m in find(repo, files, IMPORT_PY, exts_py) + find(repo, files, REQUIRE_JS, exts_js):
            mod = (m.group(1) or "") if m.re is IMPORT_PY else (m.group(1) or m.group(2) or "")
            used.setdefault(mod.split(".")[0], f"{rid}/{f}:{i}")
        F["imports"] = used
        # docker-compose (this product keeps it in one repo; any repo may carry one)
        compose = {}
        for cf in ("docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml"):
            if (repo / cf).exists():
                c = yaml.safe_load((repo / cf).read_text()) or {}
                for sname, s in (c.get("services") or {}).items():
                    compose[sname] = {"image": s.get("image"), "build": s.get("build"), "ports": s.get("ports"),
                                      "env": s.get("environment") or {}, "ev": f"{rid}/{cf}: services.{sname}", "dir": repo}
        F["compose"] = compose
        facts[rid] = F

    # ---------------------------------------------------------------- elements
    E, R = {}, []
    sibling_pkgs = {}
    for rid, F in facts.items():
        for pkg in F.get("provides", ()):
            sibling_pkgs[pkg] = rid
    compose_all = {}
    for rid, F in facts.items():
        for sname, s in F.get("compose", {}).items():
            compose_all[sname] = s
    build_to_repo = {}
    for sname, s in compose_all.items():
        if s["build"]:
            target = (s["dir"] / s["build"]).resolve()
            for rid, repo in repos.items():
                if repo.resolve() == target:
                    build_to_repo[sname] = rid

    for rid, F in facts.items():
        if F.get("missing"):
            E[rid] = dict(id=rid, name=rid, type="container", tech="not mapped yet", desc="Run cairn init in this repository",
                          provenance="inferred", evidence=[f"system.yaml: {rid}"])
            continue
        fw = next((CAT["frameworks"].get(F["eco"] or "", {}).get(d) for d in F["deps"] if d in CAT["frameworks"].get(F["eco"] or "", {})), None)
        lang = {"python": "Python", "js": "Node.js"}.get(F["eco"], "unknown")
        runs = bool(F["dockerfile"] or F["scripts"])
        ev = [v[1] for v in F["dockerfile"].values()] + F["scripts"]
        if not runs:
            E[rid] = dict(id=rid, name=rid, type="library", tech=f"{lang} package", desc="Shared code used at build time",
                          evidence=[f"{rid}: provides {', '.join(sorted(F['provides']))}; no entry point or Dockerfile"])
            continue
        kind = "service" if F["routes"] else ("worker" if F["subs"] else "cli")
        port = F["dockerfile"].get("port", ("", ""))[0]
        tech = " · ".join(x for x in [lang, fw, (f"port {port}" if port else "")] if x)
        if F["routes"]:
            rs = sorted({f"{m} {p}" for m, p, _ in F["routes"]})
            desc = "Serves " + ", ".join(rs[:2]) + (f" (+{len(rs)-2})" if len(rs) > 2 else "")
            ev += [r[2] for r in F["routes"][:2]]
        elif F["subs"]:
            desc = "Consumes " + ", ".join(sorted({t for t, _ in F["subs"]}))
        else:
            desc = "Runs " + (F["dockerfile"].get("cmd", ("a command",))[0])
        E[rid] = dict(id=rid, name=rid, type="container", kind=kind, tech=tech, desc=desc, evidence=ev)

    def ensure(eid, **kw):
        if eid not in E:
            E[eid] = dict(id=eid, **kw)
        else:
            E[eid]["evidence"] = list(dict.fromkeys(E[eid]["evidence"] + kw.get("evidence", [])))
        return eid

    uses_pubsub = any(F.get("pubs") or F.get("subs") for F in facts.values() if not F.get("missing"))
    # stores and channels from compose images
    for sname, s in compose_all.items():
        img = (s["image"] or "").split(":")[0].split("/")[-1]
        c = CAT["images"].get(img)
        if c:
            el = "channel" if (c["element"] == "redis" and uses_pubsub) else ("data-store" if c["element"] == "redis" else c["element"])
            ensure(c["name"], name=c["name"], type=el, tech=f"{c['tech']} {(s['image'] or '').split(':')[-1]}".strip(),
                   desc={"data-store": "Stores data", "channel": "Carries messages"}[el], evidence=[s["ev"] + f" image {s['image']}"])

    for rid, F in facts.items():
        if F.get("missing") or E[rid]["type"] == "library":
            continue
        # libraries from the catalog that the code really imports
        for lib, where in F["imports"].items():
            c = CAT["libraries"].get(F["eco"] or "", {}).get(lib)
            if not c:
                continue
            if c["element"] in ("data-store", "redis"):
                el = "channel" if (c["element"] == "redis" and (F["pubs"] or F["subs"])) else "data-store"
                tgt = ensure(c["name"], name=c["name"], type=el, tech=c["tech"], desc="Stores data" if el == "data-store" else "Carries messages", evidence=[where])
                if el == "data-store":
                    tables = sorted(F["sql"]) if c["via"].startswith("SQL") else []
                    verbs = sorted({v for t in tables for v in F["sql"][t]})
                    what = (" and ".join(v.capitalize() if i == 0 else v for i, v in enumerate(reversed(verbs))) + " " + ", ".join(tables)) if tables else "Stores data"
                    R.append(dict(**{"from": rid}, to=tgt, what=what, how=c["via"], style="sync",
                                  evidence=[where] + [F["sqlev"][t] for t in tables]))
            elif c["element"] == "external-system":
                tgt = ensure(c["name"], name=c["name"], type="external-system", desc=c["desc"], evidence=[where])
                R.append(dict(**{"from": rid}, to=tgt, what=c["what"], how=c["via"], style="sync", evidence=[where]))
        # pub/sub on the channel
        chan = next((e for e in E.values() if e["type"] == "channel"), None)
        for verb, items in (("Publishes", F["pubs"]), ("Subscribes to", F["subs"])):
            for topic in sorted({t for t, _ in items}):
                if chan:
                    R.append(dict(**{"from": rid}, to=chan["id"], what=f"{verb} {topic}", how=f"{chan['name']} {'PUBLISH' if verb == 'Publishes' else 'SUBSCRIBE'}",
                                  style="async", evidence=[w for t, w in items if t == topic], **({"rank_reverse": True} if verb != "Publishes" else {})))
        # build-time dependency on a sibling package
        for d in sorted(F["deps"]):
            if d in sibling_pkgs and sibling_pkgs[d] != rid:
                lib = sibling_pkgs[d]
                where = F["imports"].get(d.replace("-", "_")) or f"{rid}: dependency {d}"
                R.append(dict(**{"from": rid}, to=lib, what="Uses shared types", how=f"package {d}", style="build", evidence=[where]))

    # cross-repo HTTP: outbound call (method, path) -> inbound route on another container
    routes = [(rid, m, p, ev) for rid, F in facts.items() if not F.get("missing") for m, p, ev in F["routes"]]
    by_pair = defaultdict(list)
    for rid, F in facts.items():
        if F.get("missing"):
            continue
        for m, p, envname, ev in F["calls"]:
            cands = sorted({r for r, rm, rp, _ in routes if rp == p and r != rid and (rm == m)})
            if not cands:  # same path, any method
                cands = sorted({r for r, rm, rp, _ in routes if rp == p and r != rid})
            # compose environment pins the target when the base URL names a compose service
            if envname and len(cands) != 1:
                for sname, s in compose_all.items():
                    if build_to_repo.get(sname) == rid and envname in s["env"]:
                        host = re.sub(r"^\w+://", "", str(s["env"][envname])).split(":")[0].split("/")[0]
                        if host in build_to_repo:
                            cands = [build_to_repo[host]]
            key = (rid, tuple(cands))
            by_pair[key].append((m, p, ev))
    for (rid, cands), items in by_pair.items():
        paths = sorted({f"{m} {p}" for m, p, _ in items})
        res = sorted({p.split("/")[2] if p.count("/") >= 2 else p.strip("/") for _, p, _ in items})
        what = "Calls the " + ", ".join(res) + " API"
        if len(cands) == 1:
            ev = [e for _, _, e in items] + [rv for r, rm, rp, rv in routes if r == cands[0] and any(rp == p for _, p, _ in items)][:2]
            R.append(dict(**{"from": rid}, to=cands[0], what=what, how="HTTP · " + _common(paths), style="sync", evidence=ev))
        else:
            tgt = ensure("unresolved-http", name="Unresolved HTTP target", type="external-system", desc="Call target could not be decided", provenance="ambiguous",
                         evidence=[e for _, _, e in items])
            R.append(dict(**{"from": rid}, to=tgt, what=what, how="HTTP · " + _common(paths), style="sync", provenance="ambiguous",
                          evidence=[e for _, _, e in items] + [f"candidates: {', '.join(cands) or 'none'}"]))

    for a in sysdef.get("actors", []):
        aid = a["name"].lower()
        ensure(aid, name=a["name"], type="person", desc=a.get("desc", ""), provenance="declared", evidence=["system.yaml: actors"])
        R.append(dict(**{"from": aid}, to=a["uses"], what=a["what"], how=a.get("how", ""), style="sync", provenance="declared", evidence=["system.yaml: actors"]))

    # dedupe relationships on (from, to, what)
    seen, rels = {}, []
    for r in R:
        k = (r["from"], r["to"], r["what"])
        if k in seen:
            seen[k]["evidence"] = list(dict.fromkeys(seen[k]["evidence"] + r["evidence"]))
        else:
            seen[k] = r; rels.append(r)
    inside = [e["id"] for e in E.values() if e["type"] in ("container", "data-store", "channel", "library")]
    d = dict(diagram="container", title=f"Containers of {name}", scope=f"{name} ({len(repos)} repositories, from system.yaml)",
             description=f"Computed by Cairn from code, manifests and deploy files with no model calls: {len(E)} elements and {len(rels)} relationships, each with evidence.",
             source_note="computed, 0 model calls", elements=list(E.values()),
             boundaries=[dict(id="sys", name=name, type="software system", contains=inside)], relationships=rels)
    Path(out).write_text(yaml.safe_dump(d, sort_keys=False, allow_unicode=True, width=200))
    print(f"{len(E)} elements, {len(rels)} relationships -> {out}")


def _common(paths):
    if len(paths) == 1:
        return paths[0]
    ps = [p.split(" ", 1)[1] for p in paths]
    pre = os.path.commonprefix(ps).rstrip("/{")
    return ", ".join(sorted({p.split(" ")[0] for p in paths})) + " " + (pre or ps[0])


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
