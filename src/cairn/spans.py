"""The function a line sits in: a citation says ``file:line``, and an agent that cannot tell where that
function ends reads the whole file. ``enclosing`` returns the 1-based inclusive line range of the
outermost function or method that contains the line (a class only when no function does), from the same
tree-sitter grammars the system model uses. No grammar, a line outside any function, or an unparsable
file all give None: no range is claimed rather than a wrong one."""
from __future__ import annotations

import re

_FUNCTION = re.compile(r"^(?:function|method|constructor|func)_(?:definition|declaration|item)$|"
                       r"^(?:arrow_function|method_definition|function_expression|func_literal)$")
_CLASS = re.compile(r"^(?:class|interface|struct|trait|enum|impl)_(?:definition|declaration|item|specifier)$")
MAX_BYTES = 1_000_000


def enclosing(text: str, path: str, line: int) -> tuple[int, int] | None:
    from .system.signals import syntax
    lang = syntax.lang_of(path)
    if not lang or line < 1 or len(text) > MAX_BYTES:
        return None
    parser = syntax.parser_for(lang)
    if parser is None:
        return None
    try:
        data = text.encode("utf-8", "replace")
        tree = parser.parse(data)
    except Exception:           # a grammar failure is "no range", never an error for the caller
        return None
    node = tree.root_node.descendant_for_point_range((line - 1, 0), (line - 1, 0))
    outer_fn = outer_cls = None
    while node is not None:
        if _FUNCTION.match(node.type):
            outer_fn = node
        elif _CLASS.match(node.type):
            outer_cls = node
        node = node.parent
    best = outer_fn or outer_cls
    if best is None:
        return None
    return best.start_point[0] + 1, best.end_point[0] + 1


_REF = re.compile(r"^(?P<path>.+):(?P<line>\d+)$")


def annotate(root, ref: str) -> str:
    """``repo/src/app.js:362`` -> ``repo/src/app.js:362 (lines 360-374)``: the citation of a system view,
    widened to the function it points into. The leading segment is the repository name, so the file is
    looked for under the project root and, for a sibling repository, beside it. Unchanged when the file,
    a grammar or an enclosing function is missing."""
    from pathlib import Path
    m = _REF.match(ref)
    if not m:
        return ref
    first, _, rest = m["path"].partition("/")
    root = Path(root)
    for cand in (root / rest, root.parent / first / rest, root / m["path"]):
        try:
            if rest and cand.is_file():
                text = cand.read_text(encoding="utf-8", errors="replace")
                break
        except OSError:
            continue
    else:
        return ref
    span = enclosing(text, rest, int(m["line"]))
    return f"{ref} (lines {span[0]}-{span[1]})" if span else ref
