"""Cairn — institutional memory for coding agents."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("cairn-brain")
except PackageNotFoundError:  # running from a source tree without an installed dist
    __version__ = "0.0.0.dev"
