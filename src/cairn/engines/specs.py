"""Specs layer: intent. Sets up the spec-driven workflow (``cairn.engines.workflow``) and parses its
artifacts into features → user stories → requirements → tasks → files.
"""
from __future__ import annotations

import contextlib
import hashlib
import io
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

from ..project import Project
from ..store import Brain
from . import process_dialect

WORKFLOW_DIR = Path(".cairn") / "workflow"
CONSTITUTION = WORKFLOW_DIR / "memory" / "constitution.md"

TASK_RE = re.compile(r"^\s*[-*]\s+\[(?P<done>[ xX])\]\s+(?P<id>T\d{3,4})\b(?P<rest>.*)$")
STORY_RE = re.compile(r"^###\s+User Story\s+(?P<n>\d+)\s*[-—–:]\s*(?P<title>.+?)\s*\(Priority:\s*(?P<p>P\d)\)", re.I)
REQ_RE = re.compile(r"^\s*[-*]\s+\*\*(?P<id>(?:FR|NFR|SC)-\d{3})\*\*:?\s*(?P<text>.+)$")
PATHISH = re.compile(r"`([^`\s]+)`|(?<![\w/.-])((?:[\w.-]+/)+[\w.-]+\.[A-Za-z0-9]{1,6}|[\w-]+\.(?:py|ts|tsx|js|jsx|go|"
                     r"rs|java|kt|rb|php|cs|swift|md|sql|yaml|yml|toml|json|sh|html|css))(?![\w/])")


# ---- tooling ---------------------------------------------------------------------------------------
def cli() -> list[str]:
    """How to invoke the workflow CLI from a shell."""
    if shutil.which("cairn"):
        return ["cairn", "spec"]
    return [sys.executable, "-m", "cairn.engines.workflow.cli"]


def run(args: list[str], root: Path, *, stdin: str | None = None) -> tuple[int, str]:
    """Run ``cairn spec <args>`` in-process from ``root``; return (exit code, captured output)."""
    from .workflow.cli import main

    buf = io.StringIO()
    old_stdin = sys.stdin
    try:
        if stdin is not None:
            sys.stdin = io.StringIO(stdin)
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
            code = main(args, cwd=root)
    finally:
        sys.stdin = old_stdin
    return code, buf.getvalue()


def _last_line(output: str) -> str:
    lines = [ln.strip(" │╭╮╰╯─") for ln in output.splitlines()]
    lines = [ln for ln in lines if ln]
    for ln in reversed(lines):
        if ln.lower().startswith("error"):
            return ln
    return lines[-1] if lines else "failed"


def workflow_dir(root: Path) -> Path:
    return root / WORKFLOW_DIR


def initialized(root: Path) -> bool:
    return (root / WORKFLOW_DIR).is_dir()


def bootstrap(project: Project, integration: str = "claude") -> tuple[bool, str]:
    """Add the workflow to the repo (in-process, offline). Idempotent.

    A repository still on the older workflow layout is migrated in place first (see
    :func:`migrate_legacy`), so its constitution, templates and settings carry over.
    """
    root = project.root
    if legacy_layout(root):
        done = migrate_legacy(root)
        return initialized(root), f"migrated to .cairn/workflow ({len(done)} changes)"
    if initialized(root):
        return True, "already set up"
    code, out = run(["init", "--here", "--integration", integration, "--non-interactive",
                     "--ignore-agent-tools", "--force"], root)
    ok = code == 0 and initialized(root)
    return ok, "workflow added" if ok else _last_line(out)


def install_extension(project: Project) -> tuple[bool, str]:
    """Kept for callers of the former add-on install: the memory steps are built into the commands."""
    if not initialized(project.root):
        return False, "workflow not set up"
    return True, "built into /cairn.plan, /cairn.tasks, /cairn.clarify and /cairn.implement"


def _script_variant(root: Path) -> str:
    try:
        opts = json.loads((root / WORKFLOW_DIR / "init-options.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        opts = {}
    script = opts.get("script") if isinstance(opts, dict) else None
    return script if script in ("sh", "ps", "py") else ("ps" if sys.platform == "win32" else "sh")


def set_task_done(root: Path, feature_id: str, task_id: str, done: bool) -> dict:
    """Tick or untick one task's checkbox in `specs/<feature>/tasks.md`; returns the parsed task."""
    path = root / "specs" / feature_id / "tasks.md"
    if not re.fullmatch(r"[\w.-]+", feature_id) or not path.is_file():
        raise LookupError(f"no tasks for {feature_id}")
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    pattern = re.compile(rf"^(\s*[-*]\s+\[)([ xX])(\]\s+{re.escape(task_id)}\b)")
    for i, ln in enumerate(lines):
        m = pattern.match(ln)
        if m:
            lines[i] = f"{m.group(1)}{'x' if done else ' '}{m.group(3)}{ln[m.end():]}"
            path.write_text("".join(lines), encoding="utf-8")
            feature = next(f for f in features(root) if f["id"] == feature_id)
            return next(t for t in feature["tasks"] if t["id"] == task_id)
    raise LookupError(f"{task_id} not found in {feature_id}")


def new_feature(project: Project, description: str, short_name: str | None = None) -> dict:
    """Create the next ``specs/NNN-name/`` folder with a spec from the template.

    Runs the project's installed create-new-feature script (Python, Bash or PowerShell
    variant, whichever the project was initialised with) and returns its JSON result
    (``BRANCH_NAME``, ``SPEC_FILE``, ``FEATURE_NUM`` ...).
    """
    scripts = project.root / WORKFLOW_DIR / "scripts"
    variants = {
        "py": (scripts / "python" / "create_new_feature.py", [sys.executable]),
        "sh": (scripts / "bash" / "create-new-feature.sh", [shutil.which("bash") or "bash"]),  # Windows would pick WSL's System32 stub for a bare name
        "ps": (scripts / "powershell" / "create-new-feature.ps1", ["pwsh", "-NoProfile", "-File"]),
    }
    order = [_script_variant(project.root), "py", "sh", "ps"]
    for key in order:
        script, runner = variants[key]
        if script.exists() and (key != "ps" or shutil.which("pwsh")):
            break
    else:
        raise RuntimeError("spec workflow not set up — run `cairn init`")
    json_flag = "-Json" if key == "ps" else "--json"
    extra = []
    if short_name:
        extra = ["-ShortName", short_name] if key == "ps" else ["--short-name", short_name]
    res = subprocess.run([*runner, str(script), json_flag, *extra, description], cwd=project.root,
                         capture_output=True, text=True, timeout=60, encoding="utf-8", errors="replace")
    line = next((ln for ln in res.stdout.splitlines() if ln.startswith("{")), "")
    if res.returncode != 0 or not line:
        raise RuntimeError((res.stderr or res.stdout).strip() or "could not create the feature")
    return json.loads(line)


# ---- parsing ---------------------------------------------------------------------------------------
def _unwrap(text: str) -> list[str]:
    """Lines, with a list item's indented continuation lines joined back onto it."""
    out: list[str] = []
    for ln in text.splitlines():
        if ln[:1] in (" ", "\t") and ln.strip() and out and re.match(r"\s*[-*]\s", out[-1]) and \
                not re.match(r"\s*[-*]\s", ln):
            out[-1] = f"{out[-1].rstrip()} {ln.strip()}"
        else:
            out.append(ln)
    return out


def _read(p: Path) -> str:
    try:
        return p.read_text(errors="replace", encoding="utf-8")
    except OSError:
        return ""


def paths_in(text: str, root: Path) -> list[tuple[str, bool]]:
    out: dict[str, bool] = {}
    for m in PATHISH.finditer(text):
        raw = (m.group(1) or m.group(2) or "").strip().strip(".,;:()")
        if not raw or raw.startswith(("http", "#", "$")) or "*" in raw or len(raw) > 200:
            continue
        if "/" not in raw and "." not in raw:
            continue
        rel = raw.lstrip("./")
        if rel.startswith("/"):
            continue
        out.setdefault(rel, (root / rel).exists())
    return list(out.items())


def parse_feature(fdir: Path, root: Path) -> dict:
    spec = _read(fdir / "spec.md")
    tasks_md = _read(fdir / "tasks.md")
    plan = _read(fdir / "plan.md")
    title = next((ln.split(":", 1)[1].strip() for ln in spec.splitlines() if ln.startswith("# ") and ":" in ln),
                 fdir.name)
    status = next((ln.split(":", 1)[1].strip(" *") for ln in spec.splitlines() if ln.startswith("**Status**")), "")
    stories = [{"id": f"US{m['n']}", "title": m["title"].strip(), "priority": m["p"]}
               for ln in spec.splitlines() if (m := STORY_RE.match(ln))]
    reqs = [{"id": m["id"], "text": m["text"].strip()} for ln in _unwrap(spec) if (m := REQ_RE.match(ln))]
    clar = len(re.findall(r"^- Q:", spec, re.M))
    tasks = []
    phase = ""
    for ln in tasks_md.splitlines():
        if ln.startswith("## "):
            phase = ln[3:].strip()
        m = TASK_RE.match(ln)
        if not m:
            continue
        rest = m["rest"]
        story = re.search(r"\[(US\d+)\]", rest)
        text = re.sub(r"\[(P|US\d+)\]\s*", "", rest).strip()
        tasks.append({"id": m["id"], "done": m["done"].lower() == "x", "parallel": "[P]" in rest,
                      "story": story.group(1) if story else None, "text": text, "phase": phase,
                      "reqs": sorted(set(re.findall(r"\b(?:FR|NFR|SC)-\d{3}\b", rest))),
                      "files": paths_in(rest, root)})
    done = sum(t["done"] for t in tasks)
    artifacts = sorted(p.name for p in fdir.iterdir() if p.is_file() or p.is_dir())
    return {"id": fdir.name, "title": title, "status": status, "stories": stories, "requirements": reqs,
            "tasks": tasks, "progress": {"done": done, "total": len(tasks)}, "clarifications": clar,
            "artifacts": artifacts, "has_plan": bool(plan), "path": fdir.relative_to(root).as_posix(),
            "plan_mentions": sorted(set(re.findall(r"\b(?:FR|NFR|SC)-\d{3}\b", plan)))}


def constitution(root: Path) -> dict | None:
    text = _read(root / CONSTITUTION)
    if not text or "[PROJECT_NAME]" in text:
        return None
    version = re.search(r"\*\*Version\*\*:\s*([\d.]+)", text)
    principles = [ln[4:].strip() for ln in text.splitlines() if ln.startswith("### ")]
    return {"version": version.group(1) if version else "", "principles": principles[:12]}


def _iso_ts(value) -> float:
    from datetime import datetime

    try:
        return datetime.fromisoformat(str(value)).timestamp()
    except (TypeError, ValueError):
        return 0.0


def _manifest_meta(path: Path, section: str) -> dict:
    try:
        import yaml

        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, ValueError, ImportError):
        return {}
    meta = data.get(section) if isinstance(data, dict) else None
    return meta if isinstance(meta, dict) else {}


def workflow_state(root: Path) -> dict:
    """What the workflow has installed, for the UI and the read model (reads files only, no CLI).

    ``{"initialized", "legacy_layout", "integration", "integrations", "script", "engine_version",
    "extensions": [{id, name, version, enabled, priority, commands}], "presets": [...same...],
    "workflows": [{id, name, version, source}], "runs": [{id, workflow, status, created_at,
    updated_at, current_step, error}], "constitution": bool, "feature": "specs/..." | None}``
    """
    root = Path(root)
    wf = root / WORKFLOW_DIR
    opts = _load_json(wf / "init-options.json")
    state = _load_json(wf / "integration.json")

    def installed(kind: str, manifest: str, section: str) -> list[dict]:
        reg = _load_json(wf / kind / ".registry").get(kind)
        out = []
        for item_id, meta in (reg or {}).items():
            if not isinstance(meta, dict):
                continue
            info = _manifest_meta(wf / kind / item_id / manifest, section)
            cmds = sorted({c for names in (meta.get("registered_commands") or {}).values()
                           if isinstance(names, list) for c in names if isinstance(c, str)})
            out.append({"id": item_id, "name": info.get("name", item_id), "version": meta.get("version", ""),
                        "enabled": meta.get("enabled", True), "priority": meta.get("priority"), "commands": cmds})
        return sorted(out, key=lambda e: (e["priority"] if isinstance(e["priority"], int) else 99, e["id"]))

    workflows = [{"id": k, "name": v.get("name", k), "version": v.get("version", ""), "source": v.get("source", "")}
                 for k, v in (_load_json(wf / "workflows" / "workflow-registry.json").get("workflows") or {}).items()
                 if isinstance(v, dict)]
    runs = []
    runs_dir = wf / "workflows" / "runs"
    if runs_dir.is_dir():
        for sf in runs_dir.glob("*/state.json"):
            s = _load_json(sf)
            if s:
                runs.append({"id": s.get("run_id", sf.parent.name), "workflow": s.get("workflow_id"),
                             "status": s.get("status"), "created_at": s.get("created_at"),
                             "updated_at": s.get("updated_at"), "current_step": s.get("current_step_id"),
                             "error": s.get("error")})
    runs.sort(key=lambda r: r.get("updated_at") or "", reverse=True)
    feature = _load_json(wf / "feature.json").get("feature_directory")
    return {"initialized": wf.is_dir(), "legacy_layout": legacy_layout(root),
            "integration": state.get("default_integration") or opts.get("integration"),
            "integrations": [k for k in state.get("installed_integrations") or [] if isinstance(k, str)],
            "script": opts.get("script"), "engine_version": opts.get("workflow_version"),
            "extensions": installed("extensions", "extension.yml", "extension"),
            "presets": installed("presets", "preset.yml", "preset"),
            "workflows": sorted(workflows, key=lambda w: w["id"]), "runs": runs,
            "constitution": constitution(root) is not None,
            "feature": feature if isinstance(feature, str) else None}


def features(root: Path) -> list[dict]:
    base = root / "specs"
    out = [parse_feature(d, root) for d in sorted(base.iterdir()) if d.is_dir() and (d / "spec.md").exists()] \
        if base.is_dir() else []
    if out or not process_dialect.detect(root):
        return out
    return process_dialect.features(root)  # a repo running its own process dialect (see that module)


def ingest(project: Project, brain: Brain) -> dict:
    """Parse all features into the read model. Skips work when artifacts are unchanged."""
    feats = features(project.root)
    digest = hashlib.sha1(json.dumps(feats, sort_keys=True, default=str).encode()).hexdigest()
    const = constitution(project.root)
    wstate = workflow_state(project.root)
    brain.set_kv("specs.workflow", json.dumps(wstate))
    brain.add_events({"id": f"wfrun:{r['id']}", "ts": _iso_ts(r.get("updated_at") or r.get("created_at")),
                      "kind": "workflow_run", "title": f"Workflow {r['workflow']} {r['status']}",
                      "meta": r, "source": "specs"} for r in wstate["runs"])

    # One deterministic progress fact per feature, rewritten on every ingest: add_events
    # replaces rows by id, so the newest sync wins and every consumer of the read model
    # (overview, brief, drift, ask) sees current progress — never a stale per-count row.
    def _mtime(f: dict) -> float:
        return max(((project.root / f["path"] / a).stat().st_mtime for a in ("spec.md", "tasks.md", "plan.md")
                    if (project.root / f["path"] / a).exists()), default=0)

    brain.add_events({"id": f"spec:{f['id']}:progress", "ts": _mtime(f), "kind": "spec",
                      "title": f"{f['title']} — {f['progress']['done']}/{f['progress']['total']} tasks",
                      "refs": [f"spec:{f['id']}"], "meta": f["progress"], "source": "specs"} for f in feats)
    with brain.tx() as db:
        db.execute("DELETE FROM events WHERE id LIKE 'specstate:%'")  # superseded ids from older syncs
    if brain.get_kv("specs.digest") == digest:
        return {"features": len(feats), "note": "unchanged"}
    brain.drop_source("specs", kinds=("spec", "story", "req", "task"))
    ents, links = [], []
    for f in feats:
        sid = f"spec:{f['id']}"
        ents.append((sid, "spec", f["title"], f["path"], {k: f[k] for k in ("status", "progress", "clarifications",
                                                                            "artifacts")}, "specs",
                     " ".join(r["text"] for r in f["requirements"])))
        for s in f["stories"]:
            ent = f"story:{f['id']}/{s['id']}"
            ents.append((ent, "story", s["title"], f["path"], {"priority": s["priority"]}, "specs", ""))
            links.append((ent, sid, "part_of", "EXTRACTED", 1.0, "specs"))
        for r in f["requirements"]:
            ent = f"req:{f['id']}/{r['id']}"
            ents.append((ent, "req", f"{r['id']} {r['text'][:120]}", f["path"], {}, "specs", r["text"]))
            links.append((ent, sid, "part_of", "EXTRACTED", 1.0, "specs"))
        for t in f["tasks"]:
            ent = f"task:{f['id']}/{t['id']}"
            ents.append((ent, "task", f"{t['id']} {t['text'][:120]}", f["path"],
                         {"done": t["done"], "story": t["story"], "phase": t["phase"], "parallel": t["parallel"],
                          "files": t["files"]}, "specs", t["text"]))
            links.append((ent, sid, "part_of", "EXTRACTED", 1.0, "specs"))
            if t["story"]:
                links.append((ent, f"story:{f['id']}/{t['story']}", "implements", "EXTRACTED", 1.0, "specs"))
            for rid in t["reqs"]:
                links.append((ent, f"req:{f['id']}/{rid}", "implements", "EXTRACTED", 1.0, "specs"))
            for path, exists in t["files"]:
                links.append((ent, f"file:{path}", "owns", "EXTRACTED", 1.0 if exists else 0.5, "specs"))
    brain.put_entities(ents)
    brain.link(links)
    brain.set_kv("specs.digest", digest)
    brain.set_kv("specs.constitution", json.dumps(const))
    return {"features": len(feats), "tasks": sum(len(f["tasks"]) for f in feats)}


# ---- migration from the older workflow layout ------------------------------------------------------
# Repositories set up with the workflow tool this engine descends from keep their state in a
# different folder and name every command with a different prefix. The old names are assembled here
# at runtime (and nowhere else) because the migration has to recognise them while the shipped
# source stays free of them.
_NS = "spec" + "kit"                     # old command namespace / skill prefix
_DIR = "." + "spec" + "ify"              # old project folder
_ENV = "SPEC" + "IFY_"                   # old env-var prefix
_ADDON = "cairn"                         # id of Cairn's former add-on extension (now built in)
_NEW_START, _NEW_END = "<!-- CAIRN WORKFLOW START -->", "<!-- CAIRN WORKFLOW END -->"

_TEXT_RULES: list[tuple[re.Pattern, str]] = [(re.compile(p), r) for p, r in (
    (rf"<!-- {_NS.upper()} START -->", _NEW_START),
    (rf"<!-- {_NS.upper()} END -->", _NEW_END),
    (rf"{re.escape(_DIR)}-dev", ".cairn-dev"),
    (rf"(?<![\w\\]){re.escape(_DIR)}\b", ".cairn/workflow"),
    (rf"\\{re.escape(_DIR)}\b", r"\\.cairn/workflow"),
    (rf"{_NS}_version", "workflow_version"),
    ("(?<!COMMAND_)" + _ENV, "CAIRN_"),
    (_NS.upper(), "CAIRN"),
    ("Spec" + "Kit|Spec" + "kit", "Cairn"),
    ("spec" + "-kit", "cairn"),
    ("(?i)spec[ -]kit", "Cairn"),
    (_NS, "cairn"),
    ("specify" + "-rules", "cairn-rules"),
)]

_CONTEXT_FILES = ("CLAUDE.md", "AGENTS.md", "GEMINI.md", "QWEN.md", "CODEBUDDY.md", "QODER.md", "SHAI.md",
                  "TABNINE.md", "ZCODE.md", "ALQUIMIA.md", ".github/copilot-instructions.md", ".junie/AGENTS.md",
                  ".trae/rules/project_rules.md", ".vscode/settings.json")


def _rewrite(text: str) -> str:
    for rx, repl in _TEXT_RULES:
        text = rx.sub(repl, text)
    return text


def _rename(rel: str) -> str:
    parts = []
    for part in rel.split("/"):
        if part == _NS:
            part = "cairn"
        elif part.startswith((f"{_NS}.", f"{_NS}-")):
            part = "cairn" + part[len(_NS):]
        part = part.replace(f"{_DIR}-dev", ".cairn-dev").replace("specify" + "-rules", "cairn-rules")
        parts.append(part)
    return "/".join(parts)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _is_text(path: Path) -> bool:
    try:
        path.read_bytes().decode("utf-8")
        return True
    except (UnicodeDecodeError, OSError):
        return False


def _load_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _legacy_agent_artifacts(root: Path) -> list[Path]:
    """Files and folders in agent directories that carry the old command prefix."""
    from .workflow._agent_config import AGENT_CONFIG

    folders = {".agents"}
    folders.update(str(cfg.get("folder") or "").strip("/") for cfg in AGENT_CONFIG.values())
    # .github also holds the user's CI: only look where agent commands are generated
    folders.discard(".github")
    folders.update({".github/agents", ".github/prompts", ".github/skills"})
    found: list[Path] = []
    for folder in sorted(f for f in folders if f):
        base = root / folder
        if not base.is_dir() or base.is_symlink():
            continue
        for p in sorted(base.rglob("*")):
            if _NS in p.name and not any(_NS in parent.name for parent in p.relative_to(base).parents
                                         if parent != Path(".")):
                found.append(p)
    return found


def legacy_layout(root: Path) -> bool:
    """True when the repository still uses the older workflow folder or command names."""
    root = Path(root)
    return (root / _DIR).is_dir() or bool(_legacy_agent_artifacts(root))


def migrate_legacy(root: Path) -> list[str]:
    """Convert a repository from the older workflow layout to Cairn's, keeping every user edit.

    * ``<old folder>/`` moves to ``.cairn/workflow/``; the constitution, custom and overridden
      templates, feature pointer, extension/preset/workflow installs, catalog configs and extension
      data move with their references (folder, command names, env vars) rewritten.
    * Files the old tool generated and the user never touched (their hash still matches the old
      install manifest) are not copied: the Cairn versions are installed fresh instead.
    * Generated files the user edited are kept (rewritten) and the original is saved under
      ``.cairn/workflow/migration-backup/``.
    * Agent commands and skills with the old prefix are replaced by the ``cairn`` ones; edited or
      hand-made ones are renamed (content rewritten) and backed up.
    * Cairn's former add-on extension is removed — its steps are built into the commands.
    * Agent context files (CLAUDE.md, AGENTS.md, ...) get their managed-block markers and paths updated.

    Returns a list of human-readable actions. Returns ``[]`` when there is nothing to migrate.
    """
    root = Path(root).resolve()
    old = root / _DIR
    agent_items = _legacy_agent_artifacts(root)
    if not old.is_dir() and not agent_items:
        return []
    new = root / WORKFLOW_DIR
    backup = new / "migration-backup"
    actions: list[str] = []

    def _backup(src: Path, rel: str) -> None:
        dst = backup / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)

    # What the old tool installed, and the hash it recorded for each file.
    managed: dict[str, str] = {}
    if (old / "integrations").is_dir():
        for mf in sorted((old / "integrations").glob("*.manifest.json")):
            for rel, digest in (_load_json(mf).get("files") or {}).items():
                if isinstance(rel, str) and isinstance(digest, str):
                    managed[rel.replace("\\", "/")] = digest
    opts = _load_json(old / "init-options.json")
    state = _load_json(old / "integration.json")
    skip_rel = {"init-options.json", "integration.json"}

    # 1. the project folder
    if old.is_dir():
        for f in sorted(p for p in old.rglob("*") if p.is_file() or p.is_symlink()):
            rel = f.relative_to(old).as_posix()
            parts = rel.split("/")
            legacy_rel = f"{_DIR}/{rel}"
            if f.is_symlink():
                actions.append(f"skipped symlink {legacy_rel}")
                continue
            if rel in skip_rel or (parts[0] == "integrations" and rel.endswith(".manifest.json")):
                continue  # regenerated by the Cairn install
            if parts[:2] == ["extensions", _ADDON]:
                continue  # the add-on is built into the commands now
            customized = legacy_rel in managed and _sha256(f) != managed[legacy_rel]
            if legacy_rel in managed and not customized:
                continue  # untouched generated file: Cairn installs its own version
            dest = new / _rename(rel)
            if customized:
                _backup(f, legacy_rel)
            if dest.exists():
                _backup(f, legacy_rel)
                actions.append(f"kept existing {dest.relative_to(root).as_posix()} (old copy backed up)")
                continue
            dest.parent.mkdir(parents=True, exist_ok=True)
            if _is_text(f):
                dest.write_text(_rewrite(f.read_text(encoding="utf-8")), encoding="utf-8")
                shutil.copymode(f, dest)
            else:
                shutil.copy2(f, dest)
            actions.append(f"moved {legacy_rel} -> {dest.relative_to(root).as_posix()}"
                           + (" (customized; original backed up)" if customized else ""))
        _drop_addon_state(new, actions)
        _refresh_bundled_workflows(new, actions)

    # 2. agent commands and skills
    for item in agent_items:
        if not item.exists():
            continue
        paths = [item] if item.is_file() else sorted(p for p in item.rglob("*") if p.is_file())
        for f in paths:
            rel = f.relative_to(root).as_posix()
            if rel in managed and _sha256(f) == managed[rel]:
                f.unlink()  # untouched generated file: the Cairn install writes its replacement
                continue
            _backup(f, rel)
            if f"{_NS}.{_ADDON}." in rel or f"{_NS}-{_ADDON}-" in rel:
                f.unlink()  # the former add-on's commands are built into the core commands now
                continue
            dest = root / _rename(rel)
            dest.parent.mkdir(parents=True, exist_ok=True)
            if not dest.exists():
                if _is_text(f):
                    dest.write_text(_rewrite(f.read_text(encoding="utf-8")), encoding="utf-8")
                else:
                    shutil.copy2(f, dest)
                actions.append(f"renamed {rel} -> {dest.relative_to(root).as_posix()} (original backed up)")
            f.unlink()
        if item.is_dir():
            for d in sorted((p for p in item.rglob("*") if p.is_dir()), key=lambda p: -len(p.parts)):
                with contextlib.suppress(OSError):
                    d.rmdir()
            with contextlib.suppress(OSError):
                item.rmdir()
        actions.append(f"removed {item.relative_to(root).as_posix()}")

    # 3. agent context files: managed-block markers and references
    for rel in _CONTEXT_FILES:
        f = root / rel
        if f.is_file() and not f.is_symlink() and _is_text(f):
            text = f.read_text(encoding="utf-8")
            new_text = _rewrite(text)
            if new_text != text:
                f.write_text(new_text, encoding="utf-8")
                actions.append(f"updated references in {rel}")
    for f in sorted(root.glob(f".*/rules/{'specify' + '-rules'}.*")):
        dest = f.with_name(f.name.replace("specify" + "-rules", "cairn-rules"))
        if not dest.exists():
            dest.write_text(_rewrite(f.read_text(encoding="utf-8")), encoding="utf-8")
            f.unlink()
            actions.append(f"renamed {f.relative_to(root).as_posix()} -> {dest.relative_to(root).as_posix()}")

    # 4. remove the old folder
    if old.is_dir():
        shutil.rmtree(old)
        actions.append(f"removed {_DIR}/")

    # 5. install the Cairn versions of the generated files for the same agent(s) and settings
    actions.extend(_reinstall(root, opts, state, agent_items))
    return actions


def _drop_addon_state(new: Path, actions: list[str]) -> None:
    """Remove the former add-on from the extension registry and hook config."""
    reg_path = new / "extensions" / ".registry"
    reg = _load_json(reg_path)
    if isinstance(reg.get("extensions"), dict) and _ADDON in reg["extensions"]:
        del reg["extensions"][_ADDON]
        reg_path.write_text(json.dumps(reg, indent=2), encoding="utf-8")
        actions.append("removed the former Cairn add-on from the extension registry")
    if isinstance(reg.get("extensions"), dict):
        # manifests were rewritten during the move: re-hash so update checks see them as current
        from .workflow.extensions import ExtensionManifest, ValidationError

        changed = False
        for ext_id, meta in reg["extensions"].items():
            manifest = new / "extensions" / ext_id / "extension.yml"
            if isinstance(meta, dict) and manifest.is_file():
                try:
                    meta["manifest_hash"] = ExtensionManifest(manifest).get_hash()
                    changed = True
                except ValidationError:
                    pass
        if changed:
            reg_path.write_text(json.dumps(reg, indent=2), encoding="utf-8")
    hooks_path = new / "extensions.yml"
    if hooks_path.is_file():
        import yaml

        try:
            cfg = yaml.safe_load(hooks_path.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError:
            return
        if not isinstance(cfg, dict):
            return
        installed = cfg.get("installed")
        if isinstance(installed, list) and _ADDON in installed:
            cfg["installed"] = [x for x in installed if x != _ADDON]
        hooks = cfg.get("hooks")
        if isinstance(hooks, dict):
            for event, entries in list(hooks.items()):
                if isinstance(entries, list):
                    kept = [e for e in entries if not (isinstance(e, dict) and e.get("extension") == _ADDON)]
                    if kept:
                        hooks[event] = kept
                    else:
                        del hooks[event]
        hooks_path.write_text(yaml.safe_dump(cfg, sort_keys=False), encoding="utf-8")
        actions.append("removed the former Cairn add-on hooks (the steps are built into the commands)")


def _refresh_bundled_workflows(new: Path, actions: list[str]) -> None:
    """Replace untouched copies of bundled workflows with the packaged versions."""
    from .workflow._assets import _locate_bundled_workflow

    reg = _load_json(new / "workflows" / "workflow-registry.json")
    for wf_id, meta in (reg.get("workflows") or {}).items():
        if not isinstance(meta, dict) or meta.get("source") != "bundled":
            continue
        bundled = _locate_bundled_workflow(wf_id)
        dest = new / "workflows" / wf_id / "workflow.yml"
        if bundled and dest.is_file():
            shutil.copy2(bundled / "workflow.yml", dest)
            actions.append(f"refreshed bundled workflow '{wf_id}'")


def _reinstall(root: Path, opts: dict, state: dict, agent_items: list[Path]) -> list[str]:
    from .workflow._agent_config import AGENT_CONFIG

    installed = [k for k in (state.get("installed_integrations") or []) if isinstance(k, str) and k in AGENT_CONFIG]
    default = next((k for k in (state.get("default_integration"), state.get("integration"), opts.get("integration"),
                                opts.get("ai")) if isinstance(k, str) and k in AGENT_CONFIG), None)
    if default is None:  # infer from the agent folder that held the old commands
        by_folder = {str(cfg.get("folder") or "").strip("/"): key for key, cfg in AGENT_CONFIG.items()}
        for item in agent_items:
            key = by_folder.get(item.relative_to(root).parts[0])
            if key:
                default = key
                break
    if default is None:
        return ["no agent integration recorded — run `cairn spec init --here --integration <agent>`"]
    settings = (state.get("integration_settings") or {}) if isinstance(state.get("integration_settings"), dict) else {}

    def _args(key: str) -> list[str]:
        cfg = settings.get(key) if isinstance(settings.get(key), dict) else {}
        out = []
        script = cfg.get("script") or opts.get("script")
        if script in ("sh", "ps", "py"):
            out += ["--script", script]
        raw = cfg.get("raw_options")
        if isinstance(raw, str) and raw.strip():
            out += ["--integration-options", raw]
        return out

    actions = []
    # Confirming the merge (instead of --force) keeps every existing file: only missing ones are added.
    code, out = run(["init", "--here", "--integration", default, "--ignore-agent-tools", *_args(default)], root,
                    stdin="y\n")
    actions.append(f"installed the Cairn workflow for {default}" if code == 0 else
                   f"init for {default} failed: {_last_line(out)}")
    for key in installed:
        if key != default:
            code, out = run(["integration", "install", key, "--force", *_args(key)], root)
            actions.append(f"installed integration {key}" if code == 0 else
                           f"integration {key} failed: {_last_line(out)}")
    # carry over the user's choices (feature numbering, ...); the fresh install owns the rest
    new_opts_path = root / WORKFLOW_DIR / "init-options.json"
    new_opts = _load_json(new_opts_path)
    if new_opts:
        for k, v in opts.items():
            k = _rewrite(k)
            if k not in {"workflow_version", "integration", "ai", "ai_skills", "script"}:
                new_opts[k] = v
        new_opts_path.write_text(json.dumps(new_opts, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    # re-render installed extensions and presets for the agent under the new names
    from .workflow.integrations._helpers import _register_extensions_for_agent, _register_presets_for_agent

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
        _register_extensions_for_agent(root, default, force=True, continuing="re-run `cairn spec extension update`")
        _register_presets_for_agent(root, default, continuing="re-run `cairn spec preset update`")
    actions.append("re-registered installed extensions and presets")
    return actions
