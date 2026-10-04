"""Graph rebuilds on git events, and the graph.json merge driver.

Cairn installs its own git hooks (``cairn init``); they call into this module
in-process, so the graph engine never writes git hooks of its own:

* :func:`post_commit` — re-extract only the files the last commit changed.
* :func:`post_checkout` — full code rebuild after a branch switch.

Both skip while a rebase/merge/cherry-pick is in progress, inside a linked
worktree (the primary checkout owns the graph), when only files inside the
output directory changed, and when ``CAIRN_GRAPH_SKIP_HOOK=1``. After a rebuild
the work-memory lessons doc is refreshed when saved Q&A outcomes exist.

The optional merge driver (``install``/``uninstall``/``status``) lets a team that
commits its graph union-merge ``graph.json`` instead of hand-resolving conflicts.
"""
from __future__ import annotations
import os
import re
import subprocess
import sys
from pathlib import Path

from cairn.engines.graph import paths as _paths

# Cairn's own git hooks carry this marker (cairn.hooks.MARK).
_CAIRN_HOOK_MARKER = "# cairn-hook"
_MERGE_DRIVER = "cairn-graph"


def _git(root: Path, *args: str) -> str | None:
    try:
        res = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, timeout=60, encoding="utf-8", errors="replace")
    except (OSError, subprocess.TimeoutExpired):
        return None
    return res.stdout.strip() if res.returncode == 0 else None


def _git_dir(root: Path) -> Path | None:
    out = _git(root, "rev-parse", "--git-dir")
    if not out:
        return None
    p = Path(out)
    return (p if p.is_absolute() else root / p).resolve()


def _in_progress_operation(root: Path) -> str | None:
    """Name of a rebase/merge/cherry-pick in progress (rebuilding mid-operation
    would block ``--continue`` with unstaged changes)."""
    gd = _git_dir(root)
    if gd is None:
        return None
    for name, kind in (("rebase-merge", "rebase"), ("rebase-apply", "rebase"),
                       ("MERGE_HEAD", "merge"), ("CHERRY_PICK_HEAD", "cherry-pick")):
        if (gd / name).exists():
            return kind
    return None


def _is_linked_worktree(root: Path) -> bool:
    """A linked worktree (``git worktree add``) has git-dir != git-common-dir; the
    graph belongs to the primary checkout, so rebuilding there writes a rogue
    delta-only graph and races ``git clean`` in CI."""
    gd = _git(root, "rev-parse", "--git-dir")
    common = _git(root, "rev-parse", "--git-common-dir")
    if not gd or not common:
        return False
    a = (Path(gd) if Path(gd).is_absolute() else root / gd).resolve()
    b = (Path(common) if Path(common).is_absolute() else root / common).resolve()
    return a != b


def _saved_root(root: Path) -> Path:
    """The scan root recorded by the last full build (``<out>/.graph_root``), when it
    lies inside this repository; otherwise the repository root."""
    marker = root / _paths.GRAPH_OUT / ".graph_root"
    try:
        txt = marker.read_text(encoding="utf-8-sig").strip()
    except OSError:
        return root
    if not txt:
        return root
    candidate = Path(txt)
    if not candidate.is_absolute():
        candidate = root / candidate
    try:
        resolved = candidate.resolve()
        base = root.resolve()
        if (resolved == base or base in resolved.parents) and resolved.is_dir():
            return resolved
    except (OSError, RuntimeError):
        pass
    print(f"[cairn graph] ignoring out-of-repo {_paths.out_rel()}/.graph_root: {txt}")
    return root


def _viz_limit(root: Path) -> int | None:
    """``[map] viz_node_limit`` from .cairn/config.toml, else ``viz_node_limit`` in
    .cairn/graphrc."""
    cfg = root / ".cairn" / "config.toml"
    if cfg.is_file():
        try:
            import tomllib
            val = (tomllib.loads(cfg.read_text(encoding="utf-8")).get("map") or {}).get("viz_node_limit")
            if isinstance(val, int) and val >= 0:
                return val
        except (OSError, ValueError):
            pass
    try:
        return _load_graphrc(root).get("viz_node_limit")  # type: ignore[return-value]
    except ValueError as exc:
        print(f"[cairn graph] {exc}", file=sys.stderr)
        return None


def _refresh_lessons(build_root: Path) -> None:
    """Re-aggregate saved Q&A outcomes into reflections/LESSONS.md (best effort)."""
    try:
        out = build_root / _paths.GRAPH_OUT
        memory = out / "memory"
        if memory.is_dir() and any(memory.glob("*.md")):
            from cairn.engines.graph.reflect import reflect as _reflect
            gj = out / "graph.json"
            _reflect(memory_dir=memory, out_path=out / "reflections" / "LESSONS.md",
                     graph_path=gj if gj.exists() else None)
    except Exception:  # noqa: BLE001 — never fails the hook
        pass


def _skip_reason(root: Path) -> str | None:
    if os.environ.get("CAIRN_GRAPH_SKIP_HOOK", "0") == "1":
        return "CAIRN_GRAPH_SKIP_HOOK=1"
    op = _in_progress_operation(root)
    if op:
        return f"{op} in progress"
    if _is_linked_worktree(root):
        return "linked worktree"
    return None


def _rebuild(build_root: Path, root: Path, *, changed_paths: "list[Path] | None", force: bool | None) -> bool:
    from cairn.engines.graph.watch import _rebuild_code
    if force is None:
        force = os.environ.get("CAIRN_GRAPH_FORCE", "").lower() in ("1", "true", "yes")
    limit = _viz_limit(root)
    restore = None
    if limit is not None and "CAIRN_GRAPH_VIZ_NODE_LIMIT" not in os.environ:
        os.environ["CAIRN_GRAPH_VIZ_NODE_LIMIT"] = str(limit)
        restore = "CAIRN_GRAPH_VIZ_NODE_LIMIT"
    try:
        return _rebuild_code(build_root, changed_paths=changed_paths, force=force)
    finally:
        from cairn.engines.graph.cache import release_stat_index
        release_stat_index()
        if restore:
            os.environ.pop(restore, None)


def post_commit(path: Path = Path("."), changed: "list[str] | None" = None, *, force: bool | None = None,
                out: "str | Path | None" = None) -> dict:
    """Rebuild the code graph for the files changed by the last commit.

    ``changed`` defaults to ``git diff --name-only HEAD~1 HEAD`` (or the root
    commit's files); ``out`` is an explicit output directory. Returns
    ``{"status": "rebuilt"|"skipped"|"failed", ...}``.
    """
    if out is not None:
        with _paths.output_dir(Path(out).resolve()):
            return post_commit(path, changed, force=force)
    root = _git_root(Path(path))
    if root is None:
        return {"status": "skipped", "reason": "not a git repository"}
    reason = _skip_reason(root)
    if reason:
        return {"status": "skipped", "reason": reason}
    if changed is None:
        out = _git(root, "diff", "--name-only", "HEAD~1", "HEAD")
        if out is None:
            out = _git(root, "diff", "--name-only", "HEAD") or ""
        changed = [ln.strip() for ln in out.splitlines() if ln.strip()]
    if not changed:
        return {"status": "skipped", "reason": "no changed files"}
    prefix = _paths.out_rel().rstrip("/") + "/"
    changed = [f for f in changed if not f.replace("\\", "/").startswith(prefix)]
    if not changed:
        return {"status": "skipped", "reason": "only graph output changed"}
    build_root = _saved_root(root)
    print(f"[cairn graph] {len(changed)} file(s) changed - rebuilding graph...")
    try:
        ok = _rebuild(build_root, root, changed_paths=[root / f for f in changed], force=force)
    except Exception as exc:  # noqa: BLE001
        print(f"[cairn graph] Rebuild failed: {exc}")
        return {"status": "failed", "error": str(exc)}
    _refresh_lessons(build_root)
    return {"status": "rebuilt" if ok else "failed", "changed": len(changed)}


def post_checkout(path: Path = Path("."), prev_head: str = "", new_head: str = "",
                  branch_switch: "str | bool" = "1", *, force: bool | None = None,
                  out: "str | Path | None" = None) -> dict:
    """Full code rebuild after a branch switch (git's post-checkout arguments).

    File checkouts (``branch_switch`` false) and no-op switches (``prev == new``)
    are skipped, as is a repository whose graph was never built. ``out`` is an
    explicit output directory."""
    if out is not None:
        with _paths.output_dir(Path(out).resolve()):
            return post_checkout(path, prev_head, new_head, branch_switch, force=force)
    root = _git_root(Path(path))
    if root is None:
        return {"status": "skipped", "reason": "not a git repository"}
    if str(branch_switch) not in ("1", "True", "true"):
        return {"status": "skipped", "reason": "file checkout"}
    if prev_head and prev_head == new_head:
        return {"status": "skipped", "reason": "no-op checkout"}
    if not (root / _paths.GRAPH_OUT).is_dir():
        return {"status": "skipped", "reason": "graph not built yet"}
    reason = _skip_reason(root)
    if reason:
        return {"status": "skipped", "reason": reason}
    build_root = _saved_root(root)
    print("[cairn graph] Branch switched - rebuilding graph...")
    try:
        ok = _rebuild(build_root, root, changed_paths=None, force=force)
    except Exception as exc:  # noqa: BLE001
        print(f"[cairn graph] Rebuild failed: {exc}")
        return {"status": "failed", "error": str(exc)}
    _refresh_lessons(build_root)
    return {"status": "rebuilt" if ok else "failed"}


def _load_graphrc(root: Path) -> dict[str, str | int]:
    """Load key/value options from <root>/.cairn/graphrc if present.

    Supported options:
      viz_node_limit: integer >= 0 (e.g. viz_node_limit=0)
    """
    rc_path = root / ".cairn/graphrc"
    if not rc_path.is_file():
        return {}

    cfg: dict[str, str | int] = {}
    content = rc_path.read_text(encoding="utf-8")
    for line_num, raw in enumerate(content.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise ValueError(f"Invalid line {line_num} in {rc_path}: {raw!r} (expected key=value)")
        key, val = line.split("=", 1)
        key = key.strip()
        val = val.strip()
        if key == "viz_node_limit":
            try:
                parsed_val = int(val)
                if parsed_val < 0:
                    raise ValueError("must be a non-negative integer")
                cfg["viz_node_limit"] = parsed_val
            except ValueError as exc:
                raise ValueError(
                    f"Invalid viz_node_limit in {rc_path} at line {line_num}: {val!r}. "
                    f"Must be a non-negative integer."
                ) from exc
    return cfg


def _git_root(path: Path) -> Path | None:
    """Walk up to find .git directory."""
    current = path.resolve()
    for parent in [current, *current.parents]:
        if (parent / ".git").exists():
            return parent
    return None


_WINDOWS_DRIVE_RE = re.compile(r"^[A-Za-z]:[\\/]")


def _reject_windows_path(value: str, source: str) -> None:
    """Raise if a hooks path looks like a Windows absolute path (#1385).

    On POSIX/WSL ``Path("C:\\Users\\...").is_absolute()`` is False, so an absolute
    Windows hooks path gets joined under the repo root and mkdir'd as a literal
    junk directory (backslashes and all), while install reports success and the
    real ``.git/hooks`` gets nothing. Fail loudly instead so the user can fix it.
    """
    if os.name == "nt":
        return
    if _WINDOWS_DRIVE_RE.match(value) or "\\" in value:
        raise RuntimeError(
            f"git hooks path from {source} looks like a Windows path: {value!r}. "
            f"On WSL/POSIX this can't resolve to a real directory. Unset it with "
            f"`git config --local --unset core.hooksPath`, or set a POSIX path."
        )


def _hooks_dir(root: Path) -> Path:
    """Return the git hooks directory, respecting core.hooksPath if set (e.g. Husky).

    Asks git itself via ``rev-parse --git-path hooks`` rather than parsing
    ``.git/config`` with configparser: git legally allows duplicate keys and
    sections (VS Code writes such configs), which a strict configparser rejects
    with DuplicateOptionError/DuplicateSectionError, so every hook command
    printed a spurious "could not read core.hooksPath" warning (#1907). git
    resolves core.hooksPath, includeIf, and linked worktrees (where .git is a
    file, not a directory) correctly in one place. Genuinely corrupt configs
    are still surfaced: git itself fails on them, and its stderr is printed.
    """
    # NOTE: do NOT pass --path-format=absolute — added in git 2.31; older git
    # echoes it back as a literal argument, contaminating stdout and causing a
    # phantom directory to be created (#907). git -C <root> already returns an
    # absolute path for worktree/external-gitdir cases, and a path relative to
    # <root> for normal repos — anchoring on root covers both.
    import subprocess as _sp
    try:
        res = _sp.run(
            ["git", "-C", str(root), "rev-parse", "--git-path", "hooks"],
            capture_output=True, text=True,
            encoding="utf-8", errors="replace")
        if res.returncode != 0:
            # git failing here is a real signal (corrupt .git/config, tampering,
            # permission flips by another tool). Surface git's own stderr rather
            # than silently falling through to the default hooks directory.
            err = (res.stderr or "").strip()
            print(
                f"[cairn graph hooks] git could not resolve the hooks path for "
                f"{root}: {err or f'git exited with code {res.returncode}'}",
                file=sys.stderr,
            )
        else:
            raw = res.stdout.strip()
            # A valid hooks path can never contain newlines or NUL. Their presence
            # means git echoed an unrecognised flag back (old git behaviour).
            if raw and not any(c in raw for c in ("\n", "\r", "\x00")):
                _reject_windows_path(raw, "git rev-parse --git-path hooks")
                d = (root / raw).resolve()
                d.mkdir(parents=True, exist_ok=True)
                return d
    except (OSError, FileNotFoundError):
        pass
    d = root / ".git" / "hooks"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _pinned_python() -> str:
    """Return sys.executable if its path is shell-safe, else an empty string.

    git runs the merge-driver command line through a shell, so the interpreter
    path is pinned only when it contains plain filesystem-path characters (no
    ``$(...)``, backticks, quotes or semicolons). ``:`` and ``\\`` are allowed so
    Windows paths work, and a plain space so profile paths such as
    ``C:\\Users\\First Last\\...`` do; the driver double-quotes the value. An
    interpreter under a rotating prefix (snap revisions) is not pinned either. An
    empty return means the driver falls back to the ``cairn`` launcher on PATH.
    """
    if re.search(r"[^a-zA-Z0-9/_.@: \\-]", sys.executable):
        return ""
    if _is_rotating_prefix(sys.executable):
        return ""
    return sys.executable


# ``~/snap/<app>/<revision>/`` — snap swaps <revision> on every package update and
# prunes the old tree, so anything under it is a path with an expiry date.
_ROTATING_PREFIX_RE = re.compile(r"/snap/[^/]+/(\d+|current)/")


def _is_rotating_prefix(path: str) -> bool:
    """True if `path` lives under a directory the packaging system rotates.

    A pin is only worth writing if it will still resolve tomorrow. An interpreter
    inside a snap revision will not: the revision number changes on update and the
    old tree is removed, which silently kills every hook pinned to it — observed
    across 15 repositories at once when an editor snap moved past its revision.

    Returning "" here is the documented safe degradation: the hook falls through to
    its other probes, including the uv-tools scan, which searches snap-confined
    homes too.
    """
    return bool(_ROTATING_PREFIX_RE.search(path.replace("\\", "/")))


def _merge_attr_line() -> str:
    """The .gitattributes line assigning the graph merge driver to graph.json.

    The graph lives under the configured output directory (cairn.engines.graph.paths,
    CAIRN_GRAPH_OUT env override). gitattributes patterns are repo-relative, so an
    absolute output-dir override cannot be expressed there — fall back to the
    default name in that case.
    """
    out = _paths.GRAPH_OUT
    if not out or Path(out).is_absolute() or "\\" in out:
        out = ".cairn/graph"
    return f"{out.rstrip('/')}/graph.json merge={_MERGE_DRIVER}"


def _has_merge_attr(content: str) -> bool:
    """True if a (non-comment) `<...>graph.json ... merge=cairn-graph` line exists."""
    for raw in content.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        fields = line.split()
        if fields and fields[0].endswith("graph.json") and f"merge={_MERGE_DRIVER}" in fields[1:]:
            return True
    return False


def _register_merge_driver(root: Path) -> str:
    """Register the graph.json union merge driver in git config + .gitattributes.

    Writes go through `git config` (never hand-edit .git/config — in a linked
    worktree the effective config is not at root/.git/config). The interpreter is
    pinned so the driver works even when the ``cairn`` launcher is not on PATH at
    merge time.
    """
    import subprocess as _sp
    pinned = _pinned_python()
    if pinned:
        # Double-quoted: the allowlist in _pinned_python() permits a space (Windows
        # profile paths), and git runs this driver string through a shell, so an
        # unquoted "C:\\Users\\First Last\\...\\python.exe" would split into two
        # words and the driver would never run (#2166). The same allowlist keeps
        # '$' and backticks out, so double quotes cannot introduce expansion.
        driver = f'"{pinned}" -m cairn.engines.graph merge-driver %O %A %B'
    else:
        driver = "cairn graph merge-driver %O %A %B"
    try:
        for key, value in (
            (f"merge.{_MERGE_DRIVER}.name", "Cairn graph.json union merge"),
            (f"merge.{_MERGE_DRIVER}.driver", driver),
        ):
            _sp.run(
                ["git", "-C", str(root), "config", key, value],
                check=True, capture_output=True, text=True,
                encoding="utf-8", errors="replace")
    except (OSError, _sp.CalledProcessError) as exc:
        return f"not registered (git config failed: {exc})"

    line = _merge_attr_line()
    attrs = root / ".gitattributes"
    if attrs.exists():
        content = attrs.read_text(encoding="utf-8")
        if _has_merge_attr(content):
            return f"already registered ({line})"
        # Never clobber other entries; preserve a trailing newline.
        if content and not content.endswith("\n"):
            content += "\n"
        attrs.write_text(content + line + "\n", encoding="utf-8", newline="\n")
    else:
        attrs.write_text(line + "\n", encoding="utf-8", newline="\n")
    return f"registered ({line})"


def _unregister_merge_driver(root: Path) -> str:
    """Remove the merge-driver git config keys and the .gitattributes line."""
    import subprocess as _sp
    for key in (f"merge.{_MERGE_DRIVER}.name", f"merge.{_MERGE_DRIVER}.driver"):
        try:
            # --unset exits nonzero if the key is absent; that is fine.
            _sp.run(
                ["git", "-C", str(root), "config", "--unset", key],
                capture_output=True, text=True,
                encoding="utf-8", errors="replace")
        except OSError:
            pass
    attrs = root / ".gitattributes"
    if not attrs.exists():
        return "not registered - nothing to remove."
    content = attrs.read_text(encoding="utf-8")
    kept = [
        raw for raw in content.splitlines()
        if not _has_merge_attr(raw)
    ]
    if kept == content.splitlines():
        return "gitattributes entry not found - nothing to remove."
    if kept:
        # Other entries survive; the file stays.
        attrs.write_text("\n".join(kept) + "\n", encoding="utf-8", newline="\n")
        return "removed from .gitattributes (other entries preserved)"
    attrs.unlink()
    return "removed (.gitattributes deleted - no other entries)"


def _merge_driver_status(root: Path) -> str:
    """Report whether the merge driver is registered (config + gitattributes)."""
    import subprocess as _sp
    try:
        res = _sp.run(
            ["git", "-C", str(root), "config", "--get", f"merge.{_MERGE_DRIVER}.driver"],
            capture_output=True, text=True,
            encoding="utf-8", errors="replace")
        cfg_ok = res.returncode == 0 and bool(res.stdout.strip())
    except OSError:
        cfg_ok = False
    attrs = root / ".gitattributes"
    attr_ok = attrs.exists() and _has_merge_attr(attrs.read_text(encoding="utf-8"))
    if cfg_ok and attr_ok:
        return "registered"
    if cfg_ok:
        return "partially registered (git config set, .gitattributes line missing)"
    if attr_ok:
        return "partially registered (.gitattributes line set, git config missing)"
    return "not registered"


def _cairn_hooks_installed(root: Path) -> bool:
    hooks = _hooks_dir(root)
    for name in ("post-commit", "post-checkout"):
        p = hooks / name
        try:
            if p.exists() and _CAIRN_HOOK_MARKER in p.read_text(encoding="utf-8"):
                return True
        except OSError:
            continue
    return False


def install(path: Path = Path(".")) -> str:
    """Register the graph.json merge driver. Rebuilds on commit/checkout come from
    Cairn's own git hooks (``cairn init``), which call :func:`post_commit` /
    :func:`post_checkout`."""
    root = _git_root(path)
    if root is None:
        raise RuntimeError(f"No git repository found at or above {path.resolve()}")
    rebuild = ("installed (Cairn git hooks)" if _cairn_hooks_installed(root)
               else "not installed - run `cairn init` to install Cairn's git hooks")
    return f"rebuild on commit/checkout: {rebuild}\nmerge driver: {_register_merge_driver(root)}"


def uninstall(path: Path = Path(".")) -> str:
    """Remove the graph.json merge driver."""
    root = _git_root(path)
    if root is None:
        raise RuntimeError(f"No git repository found at or above {path.resolve()}")
    return f"merge driver: {_unregister_merge_driver(root)}"


def status(path: Path = Path(".")) -> str:
    """Report whether rebuild hooks and the merge driver are in place."""
    root = _git_root(path)
    if root is None:
        return "Not in a git repository."
    rebuild = "installed (Cairn git hooks)" if _cairn_hooks_installed(root) else "not installed"
    res = f"rebuild on commit/checkout: {rebuild}\nmerge driver: {_merge_driver_status(root)}"
    limit = _viz_limit(root)
    if limit is not None:
        res += f"\nviz node limit: {limit}"
    return res
