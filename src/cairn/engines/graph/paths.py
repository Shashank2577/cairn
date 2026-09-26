"""Single source of truth for the graph engine's output directory.

The output directory is ``.cairn/graph`` (relative to the scanned project root) by
default and overridable with the ``CAIRN_GRAPH_OUT`` env var (worktrees or
shared-output setups). It accepts a relative path (``".cairn/graph-feature"``) or an
absolute path (``"/shared/graph"``).

Every module reads :data:`GRAPH_OUT` / :data:`GRAPH_OUT_NAME` through this module at
call time (``_paths.GRAPH_OUT``), so :func:`output_dir` can point a single call — an
in-process build, query or export — at an explicit directory without touching the
environment.

The default output directory is two levels deep (``.cairn/graph``), so code that needs
the project root for an output directory or a ``graph.json`` inside it must use
:func:`out_root` / :func:`graph_root` rather than assuming ``.parent``.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import stat
import tempfile
import threading
from pathlib import Path, PurePosixPath, PureWindowsPath

DEFAULT_OUT = ".cairn/graph"
GRAPH_OUT = os.environ.get("CAIRN_GRAPH_OUT", DEFAULT_OUT)

class _SharedExclusive:
    """Many callers may use the configured output location at once; an explicit
    override (which rewrites the process-wide value) waits for them and excludes
    everyone else until it ends. Re-entrant for the owning thread, which may also
    take the shared side while it holds the exclusive one."""

    def __init__(self) -> None:
        self._cond = threading.Condition(threading.Lock())
        self._shared: dict[int, int] = {}
        self._owner: int | None = None
        self._depth = 0

    def acquire_shared(self) -> None:
        me = threading.get_ident()
        with self._cond:
            while self._owner is not None and self._owner != me:
                self._cond.wait()
            self._shared[me] = self._shared.get(me, 0) + 1

    def release_shared(self) -> None:
        me = threading.get_ident()
        with self._cond:
            n = self._shared.get(me, 0) - 1
            if n > 0:
                self._shared[me] = n
            else:
                self._shared.pop(me, None)
            self._cond.notify_all()

    def acquire_exclusive(self) -> None:
        me = threading.get_ident()
        with self._cond:
            if self._owner == me:
                self._depth += 1
                return
            while self._owner is not None or any(t != me for t in self._shared):
                self._cond.wait()
            self._owner, self._depth = me, 1

    def release_exclusive(self) -> None:
        with self._cond:
            self._depth -= 1
            if self._depth == 0:
                self._owner = None
                self._cond.notify_all()


_OUT_LOCK = _SharedExclusive()


def cairn_home() -> Path:
    """User-level Cairn directory (``~/.cairn``; ``CAIRN_HOME`` relocates it).

    Holds cross-project state: the global multi-repo graph, cloned repositories and
    logs. Never inside a project checkout."""
    env = os.environ.get("CAIRN_HOME")
    return Path(env).expanduser() if env else Path.home() / ".cairn"


def logs_dir() -> Path:
    """``<cairn_home>/logs`` — rebuild and query logs."""
    return cairn_home() / "logs"


def _set_out(value: str) -> None:
    global GRAPH_OUT, GRAPH_OUT_NAME
    GRAPH_OUT = value
    GRAPH_OUT_NAME = os.path.basename(os.path.normpath(value))


@contextlib.contextmanager
def output_dir(path: "str | Path | None"):
    """Point every reader of :data:`GRAPH_OUT` at ``path`` for the duration of the block.

    ``None`` uses the configured location and only takes the shared side of the
    lock, so builds and queries at the normal location run side by side. A path
    is an override: it waits for those to finish and holds everyone else off until
    it ends, because the value it rewrites is process-wide. An absolute path makes
    ``<root> / GRAPH_OUT`` resolve to exactly that directory. The environment
    variable is set for the duration too, so extraction worker processes started
    inside the block read and write the same cache.
    """
    if path is None:
        _OUT_LOCK.acquire_shared()
        try:
            yield Path(GRAPH_OUT)
        finally:
            _OUT_LOCK.release_shared()
        return
    _OUT_LOCK.acquire_exclusive()
    try:
        previous = GRAPH_OUT
        previous_env = os.environ.get("CAIRN_GRAPH_OUT")
        _set_out(str(path))
        os.environ["CAIRN_GRAPH_OUT"] = str(path)
        try:
            yield Path(path)
        finally:
            _set_out(previous)
            if previous_env is None:
                os.environ.pop("CAIRN_GRAPH_OUT", None)
            else:
                os.environ["CAIRN_GRAPH_OUT"] = previous_env
    finally:
        _OUT_LOCK.release_exclusive()


def out_rel_parts() -> tuple[str, ...]:
    """Components of the configured output dir as it appears under a project root
    (``(".cairn", "graph")`` by default; just the basename for an absolute override)."""
    norm = os.path.normpath(GRAPH_OUT).replace("\\", "/")
    if is_absolute_any_platform(GRAPH_OUT):
        return (PurePosixPath(norm).name,)
    return tuple(p for p in PurePosixPath(norm).parts if p not in ("", "."))


def out_rel() -> str:
    """The output dir as a ``/``-joined relative marker, e.g. ``.cairn/graph``."""
    return "/".join(out_rel_parts())


def is_out_dir(path: "str | Path") -> bool:
    """Whether ``path`` is an output directory (its trailing components match the
    configured output dir, e.g. ``<anything>/.cairn/graph``)."""
    want = out_rel_parts()
    try:
        have = PurePosixPath(str(path).replace("\\", "/")).parts
    except (TypeError, ValueError):
        return False
    return bool(want) and len(have) >= len(want) and tuple(have[-len(want):]) == want


def out_root(out_dir: "str | Path") -> Path:
    """The project root an output directory belongs to.

    ``<root>/.cairn/graph`` -> ``<root>``. A directory that is not a recognised
    output dir (a custom ``--out`` or a backup folder) is treated as one level deep,
    which is how such layouts have always been read."""
    p = Path(out_dir)
    if is_out_dir(p):
        for _ in out_rel_parts():
            p = p.parent
        return p
    return p.parent


def graph_root(graph_json: "str | Path") -> Path:
    """The project root for a ``graph.json`` written in an output directory."""
    return out_root(Path(graph_json).parent)


def os_replace_with_fallback(src: "str | Path", dst: "str | Path") -> None:
    """``os.replace(src, dst)``, falling back to a copy for a known set of
    Windows quirks (#3508) that raise even when ``src``/``dst`` are the same
    directory on the same drive: ``PermissionError`` (WinError 5/32 --
    destination briefly locked by another handle, antivirus, an open reader)
    and WinError 17 ("cannot move to a different disk drive", observed on some
    Windows/filesystem combinations despite textbook same-volume semantics).
    WinError 17 maps to a plain ``OSError`` in Python, not ``PermissionError``,
    so it's checked via ``winerror`` rather than the exception type. Any other
    failure is a real one and is re-raised.

    ``os.replace`` atomically swaps whatever sits at ``dst`` -- including a
    symlink, which it REPLACES in place rather than following (some callers,
    e.g. install.py's managed skill symlinks, #3286, rely on exactly this).
    A naive ``shutil.copy2(src, dst)`` does the opposite when ``dst`` is a
    symlink: opening it for writing follows the link and overwrites its
    TARGET's content instead. So the fallback copies to a fresh temp file in
    ``dst``'s directory first, renames whatever is currently at ``dst`` (link
    or file) aside as a backup rather than deleting it outright, and only
    then renames the temp copy into ``dst``'s place -- matching replace's
    "whatever was there is gone, a plain file replaces it" semantics. If that
    final rename fails, the backup is renamed straight back so a mid-swap
    failure leaves the original in place rather than leaving ``dst`` missing.
    """
    try:
        os.replace(src, dst)
        return
    except OSError as exc:
        if not isinstance(exc, PermissionError) and getattr(exc, "winerror", None) != 17:
            raise
    import shutil
    dst = os.fspath(dst)
    if os.path.normcase(os.path.abspath(os.fspath(src))) == os.path.normcase(os.path.abspath(dst)):
        # Replacing a path with itself needs no swap at all; the rename-aside-
        # then-back sequence below would rename src out from under itself via
        # the "back up dst" step and then crash unlinking a path that no
        # longer exists at the end.
        return
    dst_dir = os.path.dirname(dst) or "."
    fd, tmp_copy = tempfile.mkstemp(dir=dst_dir, prefix=".gfy-replace-", suffix=".tmp")
    os.close(fd)
    try:
        shutil.copy2(src, tmp_copy)
        backup = None
        if os.path.lexists(dst):
            bfd, backup = tempfile.mkstemp(dir=dst_dir, prefix=".gfy-replace-bak-", suffix=".tmp")
            os.close(bfd)
            os.unlink(backup)  # reserve the name only; rename needs it free on Windows
            os.rename(dst, backup)  # a plain rename moves a symlink itself, never its target
        try:
            os.rename(tmp_copy, dst)
        except BaseException:
            if backup is not None:
                try:
                    os.rename(backup, dst)
                except OSError:
                    pass  # best-effort restore; the swap failure below still propagates
            raise
        if backup is not None:
            try:
                os.unlink(backup)
            except OSError:
                pass
    except BaseException:
        try:
            os.unlink(tmp_copy)
        except OSError:
            pass
        raise
    os.unlink(src)


def _atomic_replace(path: "str | Path", write_fn) -> None:
    """Atomically replace ``path`` with content written by ``write_fn(f)``.

    Writes a temp file in the SAME directory, then ``os.replace``s it into place
    (an atomic rename on one filesystem). A process kill (SIGKILL/Ctrl-C), OOM, or
    ENOSPC mid-write leaves the previous file intact — the destination is
    untouched until the rename. This is NOT a power-loss durability guarantee:
    there is no fsync (matching the rest of the codebase), so an OS/hardware crash
    right after the rename can still expose unflushed bytes on some filesystems.
    The temp file is removed if the write fails.

    A symlinked destination is resolved first so the write goes THROUGH the link
    to its target (rather than replacing the link with a regular file), keeping
    the shared-output/worktree symlink setups this module documents working.
    """
    # Resolve symlinks so the temp lands on the target's filesystem (same-fs
    # atomic rename) and the replace writes through the link, not over it.
    real = Path(os.path.realpath(str(path)))
    real.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(real.parent), prefix=".gfy-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            write_fn(f)
        # mkstemp creates the temp file 0600; match the destination's existing
        # mode (or the umask default for a new file) so an atomic replace never
        # silently tightens a previously group/world-readable output to
        # owner-only. Best-effort — a chmod failure must not fail the write.
        try:
            mode = stat.S_IMODE(os.stat(real).st_mode)
        except OSError:
            umask = os.umask(0)
            os.umask(umask)
            mode = 0o666 & ~umask
        try:
            os.chmod(tmp, mode)
        except OSError:
            pass
        os_replace_with_fallback(tmp, str(real))
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            # The temp was chmod'd to match the destination above, so when the
            # destination is read-only the temp is too — and Windows refuses to
            # unlink a read-only file. Clear the bit and retry, or every failed
            # write leaks a `.gfy-*.tmp` into the output directory.
            try:
                os.chmod(tmp, stat.S_IWRITE)
                os.unlink(tmp)
            except OSError:
                pass
        raise


def write_text_atomic(path: "str | Path", text: str) -> None:
    """Atomically write ``text`` (UTF-8) to ``path``. See :func:`_atomic_replace`."""
    _atomic_replace(path, lambda f: f.write(text))


def write_text_atomic_if_changed(path: "str | Path", text: str) -> bool:
    """Atomically write ``text`` only if it differs from what is already on disk;
    return ``True`` if a write happened, ``False`` if the file was left untouched.

    Exporters regenerate their whole page set on every ``graph.json`` change and
    used to rewrite every file unconditionally — tens of thousands of identical
    pages per run, which also fires inotify / re-index / sync on unchanged
    content (#3060). Skipping the atomic replace when nothing changed avoids the
    rename entirely, so mtime and inode are preserved.

    The comparison is on DECODED text, never bytes: :func:`write_text_atomic`
    writes in text mode and (on Windows) translates ``\\n`` to ``\\r\\n`` while
    :func:`Path.read_text` translates it back, so a byte compare would report
    every file as changed. A missing or non-UTF-8 destination counts as changed."""
    try:
        if Path(os.path.realpath(str(path))).read_text(encoding="utf-8") == text:
            return False
    except (OSError, UnicodeDecodeError):
        pass
    write_text_atomic(path, text)
    return True


def write_json_atomic(path: "str | Path", obj, *, indent: "int | None" = None, ensure_ascii: bool = True) -> None:
    """Atomically write ``obj`` as JSON to ``path``, streaming the encode into the
    temp file rather than materializing the whole string first (matters for very
    large graphs). ``ensure_ascii`` mirrors ``json.dump`` so callers that emit raw
    UTF-8 (non-ASCII labels/paths) keep byte-for-byte output. See :func:`_atomic_replace`."""
    _atomic_replace(path, lambda f: json.dump(obj, f, indent=indent, ensure_ascii=ensure_ascii))

# Directory segments that, when they appear as a whole path component, mark the
# whole path as a test location. Matched against path *segments* (not raw
# substrings) so "src/contest.py" / "latest/x.py" / "src/greatest/x.py" do NOT
# match — only a segment that *equals* one of these names (case-insensitively).
_TEST_DIR_SEGMENTS = frozenset({"tests", "test", "spec", "specs", "__tests__"})

# Filename patterns marking a file as a test, matched against the *filename*
# only (case-insensitive). These are conventions across ecosystems:
#   test_*.py            pytest / unittest
#   *_test.*             Go / Python / Rust
#   *.test.*             JS/TS (jest, vitest)
#   *.spec.* / *_spec.*  Jasmine / RSpec / Karma
#   *.Tests.ps1          PowerShell Pester
#   *Test.java / *Tests.cs (case-sensitive convention, handled below)
_TEST_FILENAME_PATTERNS = (
    re.compile(r"^test_.*", re.IGNORECASE),
    re.compile(r".*_test\..+$", re.IGNORECASE),
    re.compile(r".*\.test\..+$", re.IGNORECASE),
    re.compile(r".*\.spec\..+$", re.IGNORECASE),
    re.compile(r".*_spec\..+$", re.IGNORECASE),
    re.compile(r".*\.tests\.ps1$", re.IGNORECASE),
    # Java `FooTest.java` / `FooTests.java`, C# `FooTests.cs` style. Require an
    # uppercase-led `Test`/`Tests` immediately before the extension so plain
    # words like "greatest"/"contest.cs" do not match.
    re.compile(r".*Test\.java$"),
    re.compile(r".*Tests\.java$"),
    re.compile(r".*Tests\.cs$"),
)


def _is_test_path(path: str) -> bool:
    """Classify a source path as a test path (case-insensitive, segment-aware).

    Shared by extract.py and symbol_resolution.py so cross-file call resolution
    treats test mocks/stubs identically. A path is a test path when:
      * any whole path segment equals a known test dir name
        (``tests``/``test``/``spec``/``specs``/``__tests__``), or
      * the filename matches a known test-file naming convention.

    Conservative on purpose: matches segments/filenames, never raw substrings,
    so ``latest.py``, ``src/contest.py`` and ``src/greatest/x.py`` are NON-test.
    """
    if not path:
        return False
    # Accept both POSIX and Windows separators regardless of host OS so the
    # classifier is stable across the mixed paths that flow through extraction.
    norm = str(path).replace("\\", "/")
    pure = PurePosixPath(norm)
    segments = list(pure.parts)
    # Strip a leading drive/anchor segment (e.g. "C:/") that PureWindowsPath
    # would surface; with the manual "\\"->"/" swap above PurePosixPath keeps
    # the path body intact, but guard against a Windows drive embedded as a
    # segment just in case.
    for segment in segments:
        if segment.lower() in _TEST_DIR_SEGMENTS:
            return True
        # A drive-letter colon segment like "c:" is never a test dir.
    filename = pure.name
    if not filename:
        return False
    for pattern in _TEST_FILENAME_PATTERNS:
        if pattern.match(filename):
            return True
    return False


def _path_proximity_winner(call_site_file: str, candidate_files: dict[str, str]) -> str | None:
    """Pick the candidate whose source file is closest to the call site.

    ``candidate_files`` maps candidate id -> its source_file. Returns a single
    winning candidate id, or ``None`` when no proximity tier yields a unique
    winner. Tiers, in order:

      1. same file as the call site,
      2. same directory,
      3. longest common path-prefix (must be a strict, unique maximum).

    Used only as a secondary tie-break after the test/non-test filter, so the
    god-node guard still holds when proximity is genuinely ambiguous.
    """
    if not call_site_file:
        return None
    call_norm = str(call_site_file).replace("\\", "/")
    call_dir = PurePosixPath(call_norm).parent

    # Tier 1: exact same file.
    same_file = [cid for cid, f in candidate_files.items()
                 if str(f).replace("\\", "/") == call_norm]
    if len(same_file) == 1:
        return same_file[0]
    if len(same_file) > 1:
        return None  # genuinely ambiguous within one file; bail

    # Tier 2: same directory.
    same_dir = [cid for cid, f in candidate_files.items()
                if PurePosixPath(str(f).replace("\\", "/")).parent == call_dir]
    if len(same_dir) == 1:
        return same_dir[0]
    if len(same_dir) > 1:
        return None

    # Tier 3: longest common path-prefix, computed over path segments. The
    # winner must be a strict unique maximum, else we bail (guard holds).
    call_parts = call_dir.parts

    def _common_prefix_len(f: str) -> int:
        parts = PurePosixPath(str(f).replace("\\", "/")).parent.parts
        n = 0
        for a, b in zip(call_parts, parts):
            if a != b:
                break
            n += 1
        return n

    scored = sorted(
        ((cid, _common_prefix_len(f)) for cid, f in candidate_files.items()),
        key=lambda kv: kv[1],
        reverse=True,
    )
    if not scored:
        return None
    best = scored[0][1]
    winners = [cid for cid, score in scored if score == best]
    if len(winners) == 1 and best > 0:
        return winners[0]
    return None


def disambiguate_ambiguous_candidates(
    candidates: list[str],
    candidate_files: dict[str, str],
    call_site_file: str,
) -> str | None:
    """Resolve an ambiguous bare-name call to one candidate, or ``None``.

    Shared god-node tie-breaker (#1553) used by both the inline cross-file call
    pass in ``extract.py`` and ``symbol_resolution.resolve_cross_file_raw_calls``
    so the heuristics stay aligned across languages. ``candidates`` is the list
    of node ids sharing the callee's name; ``candidate_files`` maps each id ->
    its source_file. Returns the surviving candidate id only when exactly one
    survives; otherwise ``None`` (caller keeps the god-node guard / ``continue``).

    Tie-breakers, in order:
      1. NON-TEST preference. Classify the call site and each candidate as
         test/non-test. When the call site is NON-test, drop test candidates.
         When the call site IS a test file, prefer test-local candidates
         (same file first, then any test candidate); fall back to the full set
         only if no test candidate exists.
      2. PATH PROXIMITY over whatever survived step 1.
    """
    if not candidates:
        return None
    if len(candidates) == 1:
        return candidates[0]

    call_is_test = _is_test_path(call_site_file)
    test_cands = [c for c in candidates if _is_test_path(candidate_files.get(c, ""))]
    nontest_cands = [c for c in candidates if c not in set(test_cands)]

    if call_is_test:
        # Prefer a test-local definition (same file) first.
        call_norm = str(call_site_file).replace("\\", "/")
        same_file_test = [
            c for c in test_cands
            if str(candidate_files.get(c, "")).replace("\\", "/") == call_norm
        ]
        if len(same_file_test) == 1:
            return same_file_test[0]
        if test_cands:
            survivors = test_cands
        else:
            survivors = nontest_cands or candidates
    else:
        # Non-test call site: drop test mocks/stubs entirely.
        survivors = nontest_cands

    if len(survivors) == 1:
        return survivors[0]
    if not survivors:
        return None

    # Step 2: path proximity over the survivors.
    return _path_proximity_winner(
        call_site_file,
        {c: candidate_files.get(c, "") for c in survivors},
    )

# Bare directory name even when CAIRN_GRAPH_OUT is an absolute path. Used by path
# guards that walk parents looking for the output directory by name.
GRAPH_OUT_NAME = os.path.basename(os.path.normpath(GRAPH_OUT))


def out_path(*parts: str) -> Path:
    """A path inside the configured output dir, e.g. ``out_path("cache")``.

    ``Path(CAIRN_GRAPH_OUT) / ...`` resolves correctly for both a relative name
    (".cairn/graph") and an absolute override ("/shared/.cairn/graph").
    """
    return Path(GRAPH_OUT, *parts)


def default_graph_json() -> str:
    """Default ``graph.json`` path under the configured output dir.

    The package-wide fallback used by serve/build/benchmark/prs and the CLI read
    commands so a ``CAIRN_GRAPH_OUT`` override is honoured everywhere, not just where
    the path is passed explicitly (#1423).
    """
    return str(out_path("graph.json"))


def is_absolute_any_platform(p: "str | Path | None") -> bool:
    """Whether *p* is absolute under POSIX **or** Windows rules.

    ``Path.is_absolute()`` and ``os.path.isabs()`` answer for the HOST os only,
    which is the wrong question for a path that was *stored* — a ``source_file``
    in ``graph.json``, a ``prune_sources`` entry, a cache key. Those travel
    between machines (build in Docker/CI, update on a Windows workstation, or
    the reverse), so the host's rules do not describe the string in hand:

    - On Windows, ``WindowsPath("/home/ci/repo/docs/a.md").is_absolute()`` is
      False — no drive letter — so a Linux-built graph's absolute paths read as
      relative and get baked into node IDs or joined under the scan root (#2618).
    - On POSIX, ``PosixPath("C:/Users/u/a.md").is_absolute()`` is False for the
      mirror-image reason (#2197, #1789).

    ``os.path.isabs`` is additionally not stable across supported interpreters:
    Python 3.13 changed ``ntpath.isabs`` so a path starting with a single slash
    is no longer absolute, where 3.10–3.12 said it was. The project supports
    >=3.10, so a guard written on it silently means different things per version.

    Answering for both platforms is the conservative choice for stored paths:
    treating a path as absolute at worst declines to relativize it (the string is
    kept as-is), whereas treating an absolute path as relative corrupts identity.
    Covers drive-letter, UNC, and POSIX-root forms with either separator.

    NOTE: this is for STORED/portable paths. Code resolving a path against the
    real local filesystem (``cli``, ``detect``, ``hooks``) must keep using
    ``Path.is_absolute()`` — there the host's rules are exactly right.
    """
    if not p:
        return False
    s = str(p)
    return PurePosixPath(s).is_absolute() or PureWindowsPath(s).is_absolute()


# Legacy Windows path ceiling. Unless long-path support is enabled *and* every
# consumer opts in, the ENTIRE path — drive, directories, filename, and the
# terminating NUL — must fit in MAX_PATH (260) characters, so the usable budget
# is 259. POSIX has no equivalent whole-path ceiling in practice; its limit is
# per-component (NAME_MAX, conventionally 255 bytes).
_WINDOWS_MAX_PATH = 260

# Floor for the stem budget below. A directory deep enough to push the budget
# under this cannot host readable filenames anyway; keep enough room for
# _cap_filename's "_" + 8-char digest so a truncated stem stays collision-safe
# and deterministic rather than degenerating into a bare prefix.
_MIN_STEM_BUDGET = 16


def stem_filename_budget(output_dir: "str | Path", *, reserve: int = 0, limit: int = 200) -> int:
    """Largest filename stem an exporter may write directly into ``output_dir``.

    Exporters cap note/article filenames so they stay under the filesystem's
    per-component limit (conventionally NAME_MAX=255 bytes, hence the 200
    default). That is the right question on POSIX and the wrong one on Windows,
    where the constraint is on the WHOLE path, not the component: a 200-char
    stem under a perfectly ordinary vault directory such as
    ``C:\\Users\\me\\projects\\svc\\.cairn/graph\\obsidian`` exceeds MAX_PATH and
    the write dies with ``FileNotFoundError``, aborting the export mid-vault.

    Returns ``limit`` unchanged on POSIX, so existing output is byte-for-byte
    stable there. On Windows it returns the smaller of ``limit`` and whatever
    still fits inside MAX_PATH once ``output_dir``, the separator, ``reserve``
    (room for caller-added prefixes/collision suffixes) and the ``.md``
    extension are accounted for.

    The budget is a CHARACTER count, but callers that cap UTF-8 BYTES may pass
    it straight through: a string's UTF-8 length is never below its character
    length, so a byte-capped stem always satisfies the character ceiling too.
    """
    if os.name != "nt":
        return limit
    try:
        base = os.path.abspath(str(output_dir))
    except (OSError, ValueError):
        return limit
    # An extended-length path ("\\?\C:\...", "\\?\UNC\...") opts out of MAX_PATH
    # entirely, so nothing needs shrinking.
    if base.startswith("\\\\?\\"):
        return limit
    budget = (_WINDOWS_MAX_PATH - 1) - len(base) - len(os.sep) - reserve - len(".md")
    return max(_MIN_STEM_BUDGET, min(limit, budget))


def nfc(s: str) -> str:
    """NFC-normalize a path string.

    macOS (HFS+/APFS) reports filenames in NFD while manifests, graph
    ``source_file`` entries and user input are typically NFC. Comparing raw
    strings makes the same file look like two different paths, so any path
    membership test must normalize BOTH sides (#2210, #2221/#2224).
    """
    import unicodedata
    return unicodedata.normalize("NFC", s)


def load_node_link_graph(path_or_data):
    """Load a graph.json into a networkx graph, accepting both writers.

    The clustered writer stores edges under ``links`` (networkx's node-link
    default); the raw ``--no-cluster`` writer stores them under ``edges``.
    Consumers that call ``node_link_graph(data, edges="links")`` directly
    raise ``KeyError: 'links'`` on a raw graph (#2212) — the ``except
    TypeError`` fallback only covers old networkx without the ``edges``
    kwarg, not the missing key. Normalize before parsing, same idiom as
    affected.py/serve.py.

    Accepts a path (size-cap-checked via the security module, then parsed)
    or an already-parsed dict (no size check — the caller owns any cap).
    """
    from networkx.readwrite import json_graph
    data = path_or_data
    if not isinstance(data, dict):
        p = Path(data)
        from cairn.engines.graph.security import check_graph_file_size_cap  # lazy: security imports paths
        check_graph_file_size_cap(p)
        data = json.loads(p.read_text(encoding="utf-8"))
    if isinstance(data, dict) and "links" not in data and "edges" in data:
        data = dict(data, links=data["edges"])
    try:
        return json_graph.node_link_graph(data, edges="links")
    except TypeError:  # networkx too old for the edges kwarg; default is "links"
        return json_graph.node_link_graph(data)
