"""Cairn's code & document knowledge graph ("Map"): extract · build · cluster · analyze · report.

Code in 30+ languages is parsed locally with tree-sitter (no model); documents,
papers and images are extracted through Cairn's model router. Output lives in
``<project>/.cairn/graph/`` (graph.json, GRAPH_REPORT.md, graph.html, caches).
Start with :mod:`cairn.engines.graph.api` (in-process calls) or
:func:`cairn.engines.graph.cli.main` (``cairn graph <command>``).
"""


def __getattr__(name):
    # Lazy imports: importing the package stays cheap until a symbol is used.
    _map = {
        "extract": ("cairn.engines.graph.extract", "extract"),
        "collect_files": ("cairn.engines.graph.extract", "collect_files"),
        "build_from_json": ("cairn.engines.graph.build", "build_from_json"),
        "cluster": ("cairn.engines.graph.cluster", "cluster"),
        "score_all": ("cairn.engines.graph.cluster", "score_all"),
        "cohesion_score": ("cairn.engines.graph.cluster", "cohesion_score"),
        "god_nodes": ("cairn.engines.graph.analyze", "god_nodes"),
        "surprising_connections": ("cairn.engines.graph.analyze", "surprising_connections"),
        "suggest_questions": ("cairn.engines.graph.analyze", "suggest_questions"),
        "generate": ("cairn.engines.graph.report", "generate"),
        "to_json": ("cairn.engines.graph.export", "to_json"),
        "to_html": ("cairn.engines.graph.export", "to_html"),
        "to_svg": ("cairn.engines.graph.export", "to_svg"),
        "to_canvas": ("cairn.engines.graph.export", "to_canvas"),
        "to_wiki": ("cairn.engines.graph.wiki", "to_wiki"),
        "reflect": ("cairn.engines.graph.reflect", "reflect"),
        "save_query_result": ("cairn.engines.graph.ingest", "save_query_result"),
    }
    if name in _map:
        import importlib
        mod_name, attr = _map[name]
        mod = importlib.import_module(mod_name)
        return getattr(mod, attr)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
