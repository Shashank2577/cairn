"""Cairn bundler — importable, Typer-free logic for the ``cairn spec bundle`` group.

This package holds the models, services, and helpers behind the ``cairn spec bundle``
subcommand. It is intentionally free of any Typer/CLI imports so the orchestration
logic can be unit-tested independently of the command surface (Constitution
Principle I). The CLI wiring lives in ``cairn.engines.workflow.bundles._commands`` and adjacent
``command_*.py`` modules.
"""
from __future__ import annotations

__all__ = ["BundlerError"]


class BundlerError(Exception):
    """Base class for all actionable bundler errors.

    Carrying a clean message lets the CLI layer print a single, user-facing line
    on stderr and exit non-zero without leaking a traceback (Constitution
    Principle V — explicit, actionable errors).
    """
