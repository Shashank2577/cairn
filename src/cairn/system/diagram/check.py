"""The diagram standard's checker: description rules R1 to R13 (docs/diagram-standard/README.md, section 7a).

R1 title and scope, R2 one level, R3 element fields, R4 labels, R5 line styles, R6 one direction per arrow,
R7 evidence and provenance (references resolved against the working tree when roots are given), R8 budgets,
R9 boundaries, R10 legend on, R11 accents, R12 references to known elements, R13 text alternative.

Layout rules (overlapping labels, text size) belong to the renderer's self-test, not to this module.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Iterable, Mapping

from . import load_tokens
from .schema import element_map

BARE_LABELS = {"uses", "use", "calls", "call", "talks to", "connects", "connects to", "->", "data", "request"}
LAYOUT_BAND = re.compile(r"\b(layer|tier|zone|band|lane)s?\b", re.I)
CREDENTIAL = re.compile(r"\b[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)*_(?:KEY|TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIALS?)\b")
_REF = re.compile(r"^(\S+?):(\d+)$")
_WIN_DRIVE = re.compile(r"^[A-Za-z]:")
MAX_COUNT_READ = 64 * 1024 * 1024


@dataclass(frozen=True)
class Violation:
    rule: str
    message: str

    def __str__(self) -> str:
        return f"{self.rule} {self.message}"



# ------------------------------------------------------------------ evidence references
def parse_ref(ref: str) -> tuple[str, int] | None:
    """``repo/path:line`` -> (path, line); anything else (manifest entries, documents, people) -> None."""
    if "://" in ref:
        return None
    m = _REF.match(ref.strip())
    if not m:
        return None
    return m.group(1), int(m.group(2))


def _unsafe_path(rel: str) -> bool:
    if not rel or "\\" in rel or "\x00" in rel or _WIN_DRIVE.match(rel):
        return True
    p = PurePosixPath(rel)
    return p.is_absolute() or ".." in p.parts


def _line_count(path: Path) -> int:
    n, last, read = 0, b"\n", 0
    with open(path, "rb") as fh:
        while True:
            chunk = fh.read(1 << 20)
            if not chunk:
                break
            n += chunk.count(b"\n")
            last = chunk[-1:]
            read += len(chunk)
            if read > MAX_COUNT_READ:
                return 1 << 62          # too large to count: any line number is plausible
    return n + (0 if last == b"\n" else 1)


def _candidates(rel: str, roots) -> list[tuple[Path, str]]:
    """(root, relative path) pairs a reference may live at, or [] when its repository is not present."""
    if roots is None:
        return []
    if isinstance(roots, Mapping):
        repo, _, rest = rel.partition("/")
        if repo in roots and rest:
            return [(Path(roots[repo]), rest)]
        return []
    root = Path(roots)
    out = [(root, rel)]
    first, _, rest = rel.partition("/")
    if rest and first == root.name:
        out.append((root, rest))
    return out


def resolve_ref(ref: str, roots=None, cache: dict | None = None) -> str | None:
    """Why an evidence reference does not resolve, or None when it does (or cannot be tested).

    Never reads outside the given roots: absolute paths, ``..`` and symlinks that leave the root are
    rejected without being opened.
    """
    parsed = parse_ref(ref)
    if parsed is None:
        return None
    rel, line = parsed
    if _unsafe_path(rel):
        return "unsafe path rejected (absolute, '..' or a backslash); references are relative to a repository root"
    cands = _candidates(rel, roots)
    if not cands:
        return None
    last = "file not found"
    for root, sub in cands:
        try:
            base = root.resolve()
            target = (base / sub).resolve()
        except (OSError, RuntimeError):
            last = "path cannot be resolved"
            continue
        if base != target and base not in target.parents:
            return "path resolves outside the repository root (symlink); not read"
        try:
            if not target.is_file():
                last = "file not found"
                continue
        except OSError:
            last = "file not readable"
            continue
        if line < 1:
            return "line numbers start at 1"
        key = str(target)
        if cache is not None and key in cache:
            total = cache[key]
        else:
            try:
                total = _line_count(target)
            except OSError:
                return "file not readable"
            if cache is not None:
                cache[key] = total
        if line > total:
            return f"line {line} is beyond the end of the file ({total} lines)"
        return None
    return last


# ------------------------------------------------------------------ the rules
class _Checker:
    def __init__(self, desc: dict, tokens: dict, hand_made: bool, roots):
        self.d, self.T, self.hand_made, self.roots = desc, tokens, hand_made, roots
        self.v: list[Violation] = []
        self.cache: dict = {}
        self.kind = desc.get("diagram")
        self.K = tokens["diagram_kinds"].get(self.kind)
        self.B = tokens["budget"]

    def add(self, rule: str, message: str) -> None:
        self.v.append(Violation(rule, message))

    # -- R1, R13, R10
    def header(self) -> bool:
        d, K = self.d, self.K
        if not K:
            self.add("R1", f"unknown diagram kind {self.kind!r} (one of {', '.join(self.T['diagram_kinds'])})")
            return False
        title = (d.get("title") or "").strip()
        if not title:
            self.add("R1", "diagram has no title")
        elif not title.lower().startswith(K["title_prefix"].lower()):
            self.add("R1", f"title must start with the diagram type: '{K['title_prefix']} …'")
        if not d.get("scope"):
            self.add("R1", "diagram has no scope (what system or container it describes)")
        if not (d.get("description") or "").strip():
            self.add("R13", "no one-sentence description (text alternative)")
        if d.get("legend") is False:
            self.add("R10", "legend switched off; every diagram carries a legend")
        return True

    # -- R3, R7, R2
    def elements(self, els_all: list[dict]) -> dict[str, dict]:
        K, kind, T = self.K, self.kind, self.T
        seen: dict[str, dict] = {}
        for e in els_all:
            if e["id"] in seen:
                self.add("R3", f"element id {e['id']!r} is used more than once")
                continue
            seen[e["id"]] = e
        if not seen:
            self.add("R2", f"no elements for a {kind} diagram")
        allowed = set(K.get("primary", [])) | set(K.get("supporting", []))
        if kind == "flow":
            allowed = {t for t, ET in T["element_types"].items() if "flow" in ET["levels"]}
        elif seen and not any(e.get("type") in K.get("primary", []) for e in seen.values()):
            self.add("R2", f"no primary element for a {kind} diagram ({', '.join(K.get('primary', []))})")
        for e in seen.values():
            tag = f"element {e['id']!r}"
            etype = e.get("type")
            ET = T["element_types"].get(etype) if etype else None
            if not ET:
                self.add("R3", f"{tag} has no valid type (got {etype!r})")
                self.evidence(tag, e)
                continue
            if etype not in allowed:
                self.add("R2", f"{tag} is a {etype}, which belongs to another level than {kind}")
            if kind in ET["levels"] and ET.get("requires_tech") and not e.get("tech"):
                self.add("R3", f"{tag} has no technology")
            if not e.get("name"):
                self.add("R3", f"{tag} has no name")
            if not e.get("desc") and etype not in ("entity", "state", "code"):
                self.add("R3", f"{tag} has no one-line responsibility")
            if e.get("kind") and ET.get("kinds") and e["kind"] not in ET["kinds"]:
                self.add("R3", f"{tag} has unknown kind {e['kind']!r} (one of {', '.join(ET['kinds'])})")
            if "count" in e and e["count"] < 1:
                self.add("R3", f"{tag} has a count below 1")
            self.evidence(tag, e)
        return seen

    def evidence(self, tag: str, x: dict) -> None:
        prov = x.get("provenance", "extracted")
        if prov not in self.T["provenance"]:
            self.add("R7", f"{tag} has unknown provenance {prov!r}")
        ev = x.get("evidence") or []
        if not ev:
            hint = " (hand-made: cite the document or person that declares it)" if self.hand_made else ""
            self.add("R7", f"{tag} has no evidence reference{hint}")
        for ref in ev:
            problem = resolve_ref(ref, self.roots, self.cache)
            if problem:
                self.add("R7", f"{tag} evidence {ref!r} does not resolve: {problem}")
        for text in [x.get("name"), x.get("desc"), x.get("tech"), x.get("what"), x.get("how"), *ev]:
            m = CREDENTIAL.search(text or "")
            if m:
                self.add("R7", f"{tag} names a credential ({m.group(0)}); say \"a <service> credential\" instead")
                break

    # -- R4, R5, R6, R12 on relationships and messages
    def labels(self, tag: str, r: dict) -> None:
        K, B = self.K, self.B
        what, how = (r.get("what") or "").strip(), (r.get("how") or "").strip()
        computed_ok = bool(how) and r.get("provenance", "extracted") == "extracted" and r.get("computed")
        if not what and not computed_ok:
            self.add("R4", f"{tag} has no 'what' label")
        elif what.lower() in BARE_LABELS:
            self.add("R4", f"{tag} label {what!r} is too vague; say what is exchanged or why")
        elif len(what) > B["label_what_chars"]:
            self.add("R4", f"{tag} 'what' longer than {B['label_what_chars']} characters")
        if K.get("how") == "required" and not how and r.get("style") != "return":
            self.add("R4", f"{tag} has no 'how' (protocol, route or mechanism)")
        if K.get("how") == "forbidden" and how:
            self.add("R4", f"{tag} names a technology at a level that must not ({how!r})")
        if how and len(how) > B["label_how_chars"]:
            self.add("R4", f"{tag} 'how' longer than {B['label_how_chars']} characters")

    def style(self, tag: str, r: dict) -> None:
        st = r.get("style", "sync")
        if st not in self.T["relationship_styles"]:
            self.add("R5", f"{tag} has unknown style {st!r} (one of {', '.join(self.T['relationship_styles'])})")
        elif st == "return" and self.kind != "flow":
            self.add("R5", f"{tag} uses the reply style, which is for flows only")
        elif st == "build" and self.kind == "flow":
            self.add("R5", f"{tag} uses the build-time style inside a flow")

    def relationships(self, els: dict[str, dict], rels: Iterable[dict], endpoints: set[str] | None = None) -> None:
        seen: set[tuple] = set()
        known = endpoints if endpoints is not None else set(els)
        for r in rels:
            tag = f"relationship {r.get('from')}→{r.get('to')}"
            if r["from"] not in known or r["to"] not in known:
                self.add("R12", f"{tag} joins an unknown element")
            if (r["to"], r["from"], r.get("what")) in seen:
                self.add("R6", f"{tag} duplicates its reverse; one arrow per direction with its own label")
            seen.add((r["from"], r["to"], r.get("what")))
            if r.get("bidirectional"):
                self.add("R6", f"{tag} is bidirectional; draw two labelled arrows")
            self.style(tag, r)
            self.labels(tag, r)
            self.evidence(tag, r)

    # -- R8, R11, R9
    def boxes(self, els: dict[str, dict]) -> None:
        B = self.B
        cap = B["nodes_hand_made"] if self.hand_made else B["nodes_soft"]
        if len(els) > cap:
            self.add("R8", f"{len(els)} elements (budget {cap}); split into overview and detail or collapse")
        rels = self.d.get("relationships", [])
        if len(rels) > B["relationships"]:
            self.add("R8", f"{len(rels)} relationships (budget {B['relationships']})")
        self.relationships(els, rels)
        focus = sum(1 for e in els.values() if e.get("focus"))
        if focus > B["accents"]:
            self.add("R11", f"{focus} accented elements (budget {B['accents']})")
        self.boundaries(els)

    def boundaries(self, els: dict[str, dict]) -> None:
        bds = self.d.get("boundaries", [])
        if len(bds) > self.B["boundaries"]:
            self.add("R9", f"{len(bds)} boundaries (budget {self.B['boundaries']})")
        ids: set[str] = set()
        owner: dict[str, str] = {}
        for b in bds:
            bid = b["id"]
            if bid in ids:
                self.add("R9", f"boundary id {bid!r} is used more than once")
            ids.add(bid)
            if not b.get("name") or not b.get("type"):
                self.add("R9", f"boundary {bid!r} needs a name and a type (what it isolates or owns)")
            elif LAYOUT_BAND.search(b["name"]):
                self.add("R9", f"boundary {bid!r} ({b['name']!r}) looks like a layout band; use one only for ownership, runtime or isolation")
            for c in b.get("contains", []):
                if c not in els:
                    self.add("R12", f"boundary {bid!r} contains unknown {c!r}")
                    continue
                if c in owner and owner[c] != bid:
                    self.add("R9", f"element {c!r} is in boundaries {owner[c]!r} and {bid!r}; boundaries do not overlap")
                owner[c] = bid
                if (b.get("type") or "").lower() == "software system" and els[c].get("type") in ("person", "external-system"):
                    self.add("R9", f"{els[c]['type']} {c!r} is drawn inside the software system {bid!r}; keep it outside")

    # -- flows
    def flow(self, els: dict[str, dict]) -> None:
        f, B = self.d.get("flow") or {}, self.B
        parts = f.get("participants", [])
        msgs = f.get("messages", [])
        if not parts:
            self.add("R2", "a flow needs participants")
        for p in parts:
            if p not in els:
                self.add("R12", f"participant {p!r} is not an element")
        if len(set(parts)) != len(parts):
            self.add("R3", "a participant is listed twice")
        if len(parts) > B["sequence_participants"]:
            self.add("R8", f"{len(parts)} participants (budget {B['sequence_participants']}); split the flow")
        if len(msgs) > B["sequence_messages"]:
            self.add("R8", f"{len(msgs)} messages (budget {B['sequence_messages']}); split the flow")
        pset = set(parts)
        for i, m in enumerate(msgs, 1):
            tag = f"message {i}"
            if m["from"] not in pset or m["to"] not in pset:
                self.add("R12", f"{tag} joins a non-participant")
            self.style(tag, m)
            self.labels(tag, m)
            self.evidence(tag, m)
        frs = f.get("fragments", [])
        if len(frs) > B["sequence_fragments"]:
            self.add("R8", f"{len(frs)} fragments (budget {B['sequence_fragments']})")
        for i, fr in enumerate(frs, 1):
            s, e = fr.get("start"), fr.get("end")
            ok = isinstance(s, int) and isinstance(e, int) and 1 <= s <= e <= len(msgs)
            if not ok:
                self.add("R12", f"fragment {i} refers to messages that do not exist")
            elif fr.get("else_at") is not None and not s < fr["else_at"] <= e:
                self.add("R12", f"fragment {i} has an else branch outside its messages")
            if not fr.get("type"):
                self.add("R3", f"fragment {i} has no type (alt, opt, loop ...)")
        self.focus(els)

    def focus(self, els: dict[str, dict]) -> None:
        n = sum(1 for e in els.values() if e.get("focus"))
        if n > self.B["accents"]:
            self.add("R11", f"{n} accented elements (budget {self.B['accents']})")


def check(desc: dict, tokens: dict | None = None, *, hand_made: bool = False, roots=None) -> list[Violation]:
    """Every rule the description breaks, in rule order of discovery. An empty list means it conforms.

    ``roots`` turns on evidence-reference resolution: a mapping ``{repo: path}`` (the first path segment of
    a ``repo/path:line`` reference names the repository) or a single repository root.
    """
    c = _Checker(desc, tokens or load_tokens(), hand_made, roots)
    if not c.header():
        return c.v
    els = c.elements(desc.get("elements", []))
    if c.kind == "flow":
        c.flow(els)
    else:
        c.boxes(els)
    return c.v


def rule_ids(violations: Iterable[Violation]) -> set[str]:
    return {v.rule for v in violations}


__all__ = ["Violation", "check", "parse_ref", "resolve_ref", "rule_ids", "element_map"]
