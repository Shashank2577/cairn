"""`cairn review`: the terminal face of the review stage. Thin: everything real lives in this package."""
from __future__ import annotations

import json
import sys
from typing import Optional

import typer
from typer.core import TyperGroup

import functools

from . import diff as diffmod, pack, pipeline, store


class _ReviewGroup(TyperGroup):
    """`cairn review [target]` runs a review; `cairn review list|show` are the other commands."""

    def parse_args(self, ctx, args):
        if not args or (args[0].startswith("-") and args[0] not in ("--help", "-h")):
            args = ["run", *args]
        return super().parse_args(ctx, args)

    def resolve_command(self, ctx, args):
        if args and args[0] not in self.commands and not args[0].startswith("-"):
            args = ["run", *args]
        return super().resolve_command(ctx, args)


review_app = typer.Typer(cls=_ReviewGroup, add_completion=False, no_args_is_help=False,
                         help="Review a change against the map, specs, history and conventions.")


def _guard(fn):
    """Exit status 2 for any error, with a short message instead of a traceback (0 clean, 1 findings, 2 error)."""
    @functools.wraps(fn)
    def wrapper(*a, **kw):
        try:
            return fn(*a, **kw)
        except (typer.Exit, typer.Abort, SystemExit):
            raise
        except (diffmod.DiffError, store.AmbiguousReview) as exc:
            typer.echo(str(exc), err=True)
        except Exception as exc:  # noqa: BLE001 - the contract says an error is exit 2, never a traceback
            typer.echo(f"Review failed: {type(exc).__name__}: {str(exc)[:200]}", err=True)
        raise typer.Exit(2)
    return wrapper


def _open():
    from ..cli import _cairn, _need_init
    c = _cairn()
    _need_init(c)
    return c


def _out(text: str) -> None:
    sys.stdout.write(text + "\n")


@review_app.command("run")
@_guard
def run(target: Optional[str] = typer.Argument(None, help="Branch or pull request number (default: current branch)."),
        base: Optional[str] = typer.Option(None, "--base", help="Compare against this ref (default: main)."),
        staged: bool = typer.Option(False, "--staged", help="Review staged changes."),
        ci: bool = typer.Option(False, "--ci", help="Machine-readable output; exit 1 on findings at --fail-on."),
        fmt: Optional[str] = typer.Option(None, "--format", help="text or json."),
        fail_on: Optional[str] = typer.Option(None, "--fail-on", help="With --ci: high, medium, low or never."),
        forget: Optional[str] = typer.Option(None, "--forget", help="Remove a stored review by id.")) -> None:
    """Review a branch, a pull request number or staged changes. Works with no model and no keys."""
    c = _open()
    if forget:
        ok = store.forget(c.brain, forget)
        _out(f"Removed review {forget}." if ok else f"No review {forget}.")
        raise typer.Exit(0 if ok else 2)
    fmt = fmt or ("json" if ci else "text")
    fail_on = fail_on or str(c.project.cfg("review.fail_on", "high"))
    if fmt not in ("text", "json") or fail_on not in ("high", "medium", "low", "never"):
        typer.echo("--format is text or json; --fail-on is high, medium, low or never.", err=True)
        raise typer.Exit(2)
    try:
        out = pipeline.run(c, target, base, staged, trigger="ci" if ci else "cli")
    except diffmod.DiffError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(2)
    if out.empty:
        _out(json.dumps({"review": None, "findings": [], "failed": False, "note": out.note}) if fmt == "json" else out.note)
        raise typer.Exit(0)
    failed = ci and pipeline.should_fail(out.findings, fail_on)
    if fmt == "json":
        _out(json.dumps(pack.as_json(out.review, out.findings, fail_on, failed), indent=2, default=str))
    else:
        _out(pack.text(out.review, out.findings))
    raise typer.Exit(1 if failed else 0)


@review_app.command("list")
@_guard
def list_(branch: Optional[str] = typer.Option(None, "--branch"), limit: int = typer.Option(20, "--limit")) -> None:
    """Stored reviews, newest first."""
    c = _open()
    rows = store.list_reviews(c.brain, branch, limit)
    if not rows:
        _out("No reviews yet. Run: cairn review")
    for r in rows:
        n = r["counts"]
        _out(f"{r['id']}  {r['branch']}  {r['head_sha'][:8]}  {r['status']}  "
             f"high {n['high']} · medium {n['medium']} · low {n['low']} · note {n['note']}  ({r['mode']})")


@review_app.command("show")
@_guard
def show(review_id: str = typer.Argument(...)) -> None:
    """One review with its findings."""
    c = _open()
    r = store.get_review(c.brain, review_id)
    if not r:
        typer.echo(f"No review {review_id}.", err=True)
        raise typer.Exit(2)
    _out(pack.text(r, store.findings_of(c.brain, r["id"])))
