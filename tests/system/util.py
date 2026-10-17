"""Helpers for system-model tests: throwaway repositories, stored models and linked products."""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

FIXTURES = Path(__file__).parent / "fixtures"
GIT_ENV = {**os.environ, "GIT_AUTHOR_NAME": "Ada", "GIT_AUTHOR_EMAIL": "ada@example.com",
           "GIT_COMMITTER_NAME": "Ada", "GIT_COMMITTER_EMAIL": "ada@example.com"}


def write(root: Path, files: dict[str, str | bytes]) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    for rel, body in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(body, bytes):
            p.write_bytes(body)
        else:
            p.write_text(body, encoding="utf-8")
    return root


def git_init(root: Path) -> str:
    for args in (["init", "-q", "-b", "main"], ["add", "-A"], ["commit", "-qm", "init", "--no-gpg-sign"]):
        subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True, env=GIT_ENV)
    return subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"], check=True, capture_output=True, text=True,
                          env=GIT_ENV).stdout.strip()


def repo(tmp: Path, name: str, files: dict, *, git: bool = True) -> Path:
    root = write(tmp / name, files)
    if git:
        git_init(root)
    return root


def copy_fixture(src: Path, dest: Path, *, git: bool = True) -> Path:
    shutil.copytree(src, dest)
    if git:
        git_init(dest)
    return dest


def build(root: Path, name: str | None = None):
    from cairn.system.build import build_repo
    return build_repo(root, name or root.name, "c0ffee")


def store(root: Path, name: str | None = None):
    """Build and save a repository's model into its own .cairn/brain.db (what sync's step does)."""
    from cairn.store import Brain
    from cairn.system.build import build_repo, head_commit
    from cairn.system.model import save
    name = name or root.name
    model = build_repo(root, name, head_commit(root))
    (root / ".cairn").mkdir(exist_ok=True)
    brain = Brain(root / ".cairn" / "brain.db")
    try:
        save(brain, model, name)
    finally:
        brain.close()
    return model


def product(tmp: Path, fixture: str, viewer: str, names: list[str], *, system_yaml: str | None = None) -> Path:
    """Materialise a multi-repository fixture as git repositories, store each model, and declare the product
    in the viewing repository's .cairn/system.yaml. Returns the viewing repository's root."""
    src = FIXTURES / fixture
    for n in names:
        copy_fixture(src / n, tmp / n)
        store(tmp / n)
    text = system_yaml if system_yaml is not None else (src / "system.yaml").read_text(encoding="utf-8")
    text = text.replace("path: ", "path: ../")
    (tmp / viewer / ".cairn" / "system.yaml").write_text(text, encoding="utf-8")
    return tmp / viewer


def aliases(model) -> dict[str, list[str]]:
    return {e.id: list(e.meta.get("aliases") or []) for e in model.elements.values()}


def rels(model, *, level: str = "container"):
    return [r for r in model.relationships.values() if r.level == level]


def pairs(model) -> set[tuple[str, str]]:
    names = {e.id: e.name for e in model.elements.values()}
    return {(names.get(r.from_id, r.from_id), names.get(r.to_id, r.to_id)) for r in model.relationships.values()}


def containers(model) -> dict[str, object]:
    return {e.name: e for e in model.elements.values() if e.type == "container" and e.level == "container"}


def code(model, kind: str) -> list:
    return [e for e in model.elements.values() if e.kind == kind and e.level == "code"]
