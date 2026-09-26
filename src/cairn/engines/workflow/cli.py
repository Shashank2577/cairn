"""Mount point for the workflow CLI: ``cairn spec <subcommand> ...``.

``main(argv)`` runs one workflow command in-process and returns its exit code, so the
top-level ``cairn`` CLI can forward ``cairn spec ...`` here without a subprocess.
"""
from __future__ import annotations

import os
import sys
from collections.abc import Sequence
from pathlib import Path

PROG_NAME = "cairn spec"


def main(argv: Sequence[str] | None = None, *, cwd: str | os.PathLike[str] | None = None) -> int:
    """Run ``cairn spec <argv...>`` and return the process exit code.

    *argv* excludes the program name (``["init", "--here", "--integration", "claude"]``).
    *cwd* runs the command as if started from that directory (the workflow commands
    operate on the current working directory, or ``CAIRN_INIT_DIR`` when set).
    """
    from . import app

    args = list(sys.argv[1:] if argv is None else argv)
    if sys.platform == "win32":  # the banner and box-drawing glyphs need UTF-8 output
        for stream in (sys.stdout, sys.stderr):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except (AttributeError, ValueError, OSError):
                pass
    old_cwd = Path.cwd() if cwd is not None else None
    old_argv = sys.argv
    try:
        if cwd is not None:
            os.chdir(cwd)
        # a few commands inspect sys.argv (e.g. to suppress the banner on --help)
        sys.argv = [PROG_NAME, *args]
        app(args=args, prog_name=PROG_NAME, standalone_mode=True)
    except SystemExit as exc:
        code = exc.code
        if code is None:
            return 0
        if isinstance(code, int):
            return code
        print(code, file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
    finally:
        sys.argv = old_argv
        if old_cwd is not None:
            os.chdir(old_cwd)
    return 0


if __name__ == "__main__":  # python -m cairn.engines.workflow.cli ...
    raise SystemExit(main())
