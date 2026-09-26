"""``python -m cairn.engines.graph <command>`` — the graph engine's command line.

Users run it as ``cairn graph <command>``; this entry exists so the engine can
re-run a clustering command under a pinned hash seed and so git can invoke the
graph.json merge driver with a pinned interpreter.
"""
from cairn.engines.graph.cli import main

if __name__ == "__main__":
    main()
