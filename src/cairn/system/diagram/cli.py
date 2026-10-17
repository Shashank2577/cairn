"""``cairn diagram``: check, render and convert hand-made diagram descriptions (YAML, JSON or Mermaid)."""
from __future__ import annotations

import re
from pathlib import Path
from typing import List, Optional

import typer

from ..limits import MAX_DIAGRAM_BYTES
from . import alt, mermaid_in, mermaid_out, schema, svg
from . import check as checker
from . import layout as layout_mod

diagram_app = typer.Typer(help="Check, render and convert diagrams against the Cairn diagram standard.",
                          no_args_is_help=True)

_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,100}$")


def _read(path: Path, kind: str | None = None) -> tuple[dict, list[str]]:
    """A description from a YAML, JSON or Mermaid file; raises ValueError with a readable message."""
    try:
        if path.stat().st_size > MAX_DIAGRAM_BYTES:
            raise ValueError(f"{path.name} is larger than {MAX_DIAGRAM_BYTES} bytes")
        data = path.read_bytes()
    except OSError as exc:
        raise ValueError(f"cannot read {path.name}: {exc.strerror or exc}") from exc
    text = data.decode("utf-8", "replace")
    if path.suffix.lower() in (".mmd", ".mermaid") or mermaid_in.looks_like_mermaid(text):
        imp = mermaid_in.parse(text, kind)
        return imp.description, imp.warnings
    return schema.loads(data), []


def _roots(values: list[str]):
    mapping: dict[str, Path] = {}
    single: list[Path] = []
    for v in values:
        name, eq, rest = v.partition("=")
        if eq and name and "/" not in name and "\\" not in name:
            mapping[name] = Path(rest)
        else:
            single.append(Path(v))
    if mapping and single:
        raise typer.BadParameter("give either --root NAME=PATH (several repositories) or --root PATH (one), not both")
    if len(single) > 1:
        raise typer.BadParameter("more than one plain --root; name them with NAME=PATH")
    return mapping or (single[0] if single else None)


@diagram_app.command("check")
def check_cmd(files: List[Path] = typer.Argument(..., help="Diagram descriptions (.yaml, .json) or Mermaid files."),
              hand_made: bool = typer.Option(False, "--hand-made", help="Use the tighter hand-made element budget."),
              root: Optional[List[str]] = typer.Option(None, "--root", help="Resolve repo/path:line evidence: PATH, or NAME=PATH for each repository.")) -> None:
    """Report every rule a diagram breaks. Exit status 1 if any diagram has violations or cannot be read."""
    roots = _roots(root or [])
    bad = 0
    for f in files:
        try:
            desc, warns = _read(f)
        except ValueError as exc:
            typer.echo(f"{f.name}: cannot be read: {exc}")
            bad += 1
            continue
        violations = checker.check(desc, hand_made=hand_made, roots=roots)
        typer.echo(f"{f.name}: {'OK' if not violations else str(len(violations)) + ' violation(s)'}")
        for w in warns:
            typer.echo(f"    warning: {w}")
        for v in violations:
            typer.echo(f"    {v}")
        bad += bool(violations)
    raise typer.Exit(1 if bad else 0)


@diagram_app.command("render")
def render_cmd(file: Path = typer.Argument(..., help="A diagram description or Mermaid file."),
               out: Optional[Path] = typer.Option(None, "--out", help="Directory for the .svg and .md (default: the current directory)."),
               theme: str = typer.Option("auto", "--theme", help="auto (follows the viewer), light or dark."),
               name: Optional[str] = typer.Option(None, "--name", help="Output file name without extension (default: the input's name)."),
               strict: bool = typer.Option(False, "--strict", help="Exit 1 when the diagram breaks a rule.")) -> None:
    """Write <name>.svg and the evidence table <name>.md; never writes outside --out."""
    if theme not in ("auto", "light", "dark"):
        raise typer.BadParameter("theme must be auto, light or dark")
    base = name if name is not None else file.stem
    if not _NAME.match(base) or ".." in base:
        typer.echo(f"refusing output name {base!r}: use letters, digits, '.', '_' and '-' only, no path separators")
        raise typer.Exit(1)
    target = (out or Path.cwd())
    try:
        target.mkdir(parents=True, exist_ok=True)
        root = target.resolve()
        svg_path, md_path = (root / (base + ".svg")).resolve(), (root / (base + ".md")).resolve()
        if svg_path.parent != root or md_path.parent != root:
            raise ValueError("output path leaves the output directory")
        desc, warns = _read(file)
        geometry = layout_mod.layout(desc)
        problems = layout_mod.self_test(geometry)
        violations = checker.check(desc)
        svg_path.write_text(svg.render(desc, geometry, theme=theme), encoding="utf-8", newline="\n")
        md_path.write_text(alt.evidence_table(desc), encoding="utf-8", newline="\n")
    except (ValueError, OSError) as exc:
        typer.echo(f"{file.name}: {exc}")
        raise typer.Exit(1) from exc
    typer.echo(f"wrote {svg_path.name} and {md_path.name} in {root}")
    for w in warns:
        typer.echo(f"warning: {w}")
    for v in violations:
        typer.echo(f"standard: {v}")
    for p in problems:
        typer.echo(f"layout: {p}")
    if strict and (violations or problems):
        raise typer.Exit(1)


@diagram_app.command("to-mermaid")
def to_mermaid_cmd(file: Path = typer.Argument(...), theme: str = typer.Option("light", "--theme")) -> None:
    """Print a Mermaid flowchart (sequence diagram for flows) with the facts kept in %% cairn comments."""
    if theme not in ("light", "dark"):
        raise typer.BadParameter("theme must be light or dark")
    try:
        desc, _ = _read(file)
    except ValueError as exc:
        typer.echo(f"{file.name}: {exc}", err=True)
        raise typer.Exit(1) from exc
    typer.echo(mermaid_out.to_mermaid(desc, theme=theme), nl=False)


@diagram_app.command("from-mermaid")
def from_mermaid_cmd(file: Path = typer.Argument(...),
                     kind: Optional[str] = typer.Option(None, "--kind", help="Diagram kind when the file does not say (default: container).")) -> None:
    """Print the YAML description of a Mermaid file; warnings for ignored syntax go to stderr."""
    try:
        if file.stat().st_size > MAX_DIAGRAM_BYTES:
            raise ValueError(f"larger than {MAX_DIAGRAM_BYTES} bytes")
        imp = mermaid_in.parse(file.read_bytes(), kind)
    except (ValueError, OSError) as exc:
        typer.echo(f"{file.name}: {exc}", err=True)
        raise typer.Exit(1) from exc
    for w in imp.warnings:
        typer.echo(f"warning: {w}", err=True)
    typer.echo(schema.dumps(imp.description), nl=False)
