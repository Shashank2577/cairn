"""Text search: `cairn search` matches names through the index, and this finds what names cannot, a
word in a string, a port, a header, an environment variable or a cookie. It scans the repository's own
files (tracked, plus untracked that are not ignored) on demand, so the index does not grow by a copy of
the code. Deterministic, no model. A hit is a line that contains every query word, case-insensitively.

Never searched: environment and key files, binaries, large files, anything under .cairn. A line that
assigns a secret-looking name shows the name and not the value."""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

MAX_FILE_BYTES = 512_000
MAX_FILES = 20_000
SKIP_NAMES = re.compile(r"(^|/)(\.env(\..*)?|.*\.(pem|key|p12|pfx|jks|lock|min\.js|min\.css|map)|id_[a-z0-9]+|"
                        r"package-lock\.json|yarn\.lock|pnpm-lock\.yaml)$", re.I)
SKIP_DIRS = (".cairn/", ".git/", "node_modules/", "vendor/", "dist/", ".venv/")
SECRET = re.compile(r"(?i)\b(\w*(?:pass(?:word|wd)?|secret|token|api[_-]?key|credential)\w*['\"]?)(\s*[:=]\s*)(\S.*)$")
CODE_FIRST = {".py": 0, ".js": 0, ".ts": 0, ".go": 0, ".java": 0, ".php": 0, ".cs": 0, ".rb": 0, ".rs": 0}


def _files(root: Path) -> list[str]:
    try:
        out = subprocess.run(["git", "-C", str(root), "ls-files", "-co", "--exclude-standard", "-z"],
                             capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30,
                             check=True).stdout
        paths = [p for p in out.split("\0") if p]
    except (subprocess.SubprocessError, OSError):
        paths = [p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()][:MAX_FILES]
    return [p for p in paths[:MAX_FILES] if not p.startswith(SKIP_DIRS) and not SKIP_NAMES.search(p)]


def _terms(q: str) -> list[str]:
    return [t.lower() for t in re.findall(r"[A-Za-z0-9_.\-/:]+", q) if len(t) > 1]


def shown(line: str, width: int = 110) -> str:
    """The matched line, trimmed, with the value of a secret-looking assignment replaced."""
    s = " ".join(line.strip().split())
    s = SECRET.sub(lambda m: f"{m.group(1)}{m.group(2)}…", s)
    return s if len(s) <= width else s[: width - 1] + "…"


def search(root: Path, q: str, limit: int = 20) -> list[dict]:
    from .spans import enclosing
    terms = _terms(q)
    if not terms:
        return []
    found: list[tuple[int, str, int, str, str]] = []
    for rel in _files(root):
        p = root / rel
        try:
            if p.stat().st_size > MAX_FILE_BYTES:
                continue
            data = p.read_bytes()
        except OSError:
            continue
        if b"\0" in data[:4096]:
            continue
        text = data.decode("utf-8", "replace")
        low = text.lower()
        if not all(t in low for t in terms):
            continue
        rank = CODE_FIRST.get(Path(rel).suffix.lower(), 1)
        for n, line in enumerate(text.splitlines(), 1):
            ll = line.lower()
            if all(t in ll for t in terms):
                found.append((rank, rel, n, line, text))
    found.sort(key=lambda f: (f[0], f[1], f[2]))
    hits, per_file = [], {}
    for rank, rel, n, line, text in found:
        if per_file.get(rel, 0) >= 3:                 # a file that mentions it ten times is one answer
            continue
        per_file[rel] = per_file.get(rel, 0) + 1
        span = enclosing(text, rel, n)
        hit = {"id": f"text:{rel}:{n}", "kind": "text", "title": shown(line), "score": 1.0 / (1 + rank),
               "path": rel, "line": n}
        if span:
            hit["lines"] = f"{span[0]}-{span[1]}"
        hits.append(hit)
        if len(hits) >= limit:
            break
    return hits


def cite(h: dict) -> str:
    """``path`` or, for a text hit, ``path:line (lines a-b)``: where to read, and how much of it."""
    path = h.get("path") or ""
    if h.get("kind") != "text":
        return path
    return f"{path}:{h['line']}" + (f" (lines {h['lines']})" if h.get("lines") else "")
