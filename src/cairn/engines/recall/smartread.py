"""Smart file reading: folded structural outlines, single-symbol unfolding and codebase symbol search.

A file is parsed into symbols (functions, classes, methods, interfaces, types, structs, enums, traits,
impls, mixins, and for Markdown: sections, code blocks, front matter and link references) with their
signature, doc comment, 0-based line range, exported flag and nested children, plus its import
statements. ``format_folded_view`` renders the outline with bodies folded, ``unfold_symbol`` returns
one symbol's full source, ``search_codebase`` ranks matching symbols across a directory tree and
``resolve_within_workspace`` keeps every caller-supplied path inside the workspace.

Parsing uses the ``tree_sitter`` bindings with whichever grammar packages are importable, running the
per-language queries below in process. A language without a usable grammar (or query) is read by a
line-oriented extractor that emits the same captures, so every function returns a useful outline and
never raises on odd input. The agent tools ``smart_search`` / ``smart_unfold`` / ``smart_outline``
are plain functions over these (``tool_smart_*``, ``TOOLS``, ``call_tool``).
"""
from __future__ import annotations

import importlib
import logging
import math
import os
import re
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

log = logging.getLogger("cairn.recall")

# ---- data model ------------------------------------------------------------------------------------
SYMBOL_KINDS = ("function", "class", "method", "interface", "type", "const", "variable", "export", "struct",
                "enum", "trait", "impl", "property", "getter", "setter", "mixin", "section", "code", "metadata",
                "reference")


@dataclass(eq=False)
class CodeSymbol:
    """One symbol; ``line_start``/``line_end`` are 0-based and inclusive."""
    name: str
    kind: str
    signature: str
    line_start: int
    line_end: int
    exported: bool
    jsdoc: str | None = None
    parent: str | None = None
    children: list[CodeSymbol] | None = None

    def to_dict(self) -> dict:
        d: dict[str, Any] = {"name": self.name, "kind": self.kind, "signature": self.signature,
                             "jsdoc": self.jsdoc, "line_start": self.line_start, "line_end": self.line_end,
                             "parent": self.parent, "exported": self.exported}
        if self.children is not None:
            d["children"] = [c.to_dict() for c in self.children]
        return d


@dataclass(eq=False)
class FoldedFile:
    file_path: str
    language: str
    symbols: list[CodeSymbol]
    imports: list[str]
    total_lines: int
    folded_token_estimate: int

    def to_dict(self) -> dict:
        return {"file_path": self.file_path, "language": self.language,
                "symbols": [s.to_dict() for s in self.symbols], "imports": list(self.imports),
                "total_lines": self.total_lines, "folded_token_estimate": self.folded_token_estimate}


@dataclass
class SymbolMatch:
    file_path: str
    symbol_name: str
    kind: str
    signature: str
    jsdoc: str | None
    line_start: int
    line_end: int
    match_reason: str

    def to_dict(self) -> dict:
        return dict(self.__dict__)


@dataclass
class SearchResult:
    folded_files: list[FoldedFile] = field(default_factory=list)
    matching_symbols: list[SymbolMatch] = field(default_factory=list)
    total_files_scanned: int = 0
    total_symbols_found: int = 0
    token_estimate: int = 0

    def to_dict(self) -> dict:
        return {"folded_files": [f.to_dict() for f in self.folded_files],
                "matching_symbols": [m.to_dict() for m in self.matching_symbols],
                "total_files_scanned": self.total_files_scanned, "total_symbols_found": self.total_symbols_found,
                "token_estimate": self.token_estimate}


# ---- JavaScript-compatible string helpers (outputs are sized and padded in UTF-16 units) -------------
_JS_WS = " \t\n\r\v\f\u00a0\u1680\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a" \
         "\u2028\u2029\u202f\u205f\u3000\ufeff"


def _trim(s: str) -> str:
    return s.strip(_JS_WS)


def _js_len(s: str) -> int:
    return len(s.encode("utf-16-le", "surrogatepass")) // 2


def _js_slice(s: str, n: int) -> str:
    return s.encode("utf-16-le", "surrogatepass")[: 2 * max(0, n)].decode("utf-16-le", "ignore")


def _pad_end(s: str, width: int) -> str:
    return s + " " * max(0, width - _js_len(s))


# ---- languages -------------------------------------------------------------------------------------
LANG_MAP: dict[str, str] = {
    ".js": "javascript", ".mjs": "javascript", ".cjs": "javascript", ".jsx": "tsx",
    ".ts": "typescript", ".tsx": "tsx", ".py": "python", ".pyw": "python", ".go": "go", ".rs": "rust",
    ".rb": "ruby", ".java": "java", ".c": "c", ".h": "c", ".cpp": "cpp", ".cc": "cpp", ".cxx": "cpp",
    ".hpp": "cpp", ".hh": "cpp", ".kt": "kotlin", ".kts": "kotlin", ".swift": "swift", ".php": "php",
    ".lua": "lua", ".scala": "scala", ".sc": "scala", ".sh": "bash", ".bash": "bash", ".zsh": "bash",
    ".hs": "haskell", ".zig": "zig", ".css": "css", ".scss": "scss", ".toml": "toml", ".yml": "yaml",
    ".yaml": "yaml", ".sql": "sql", ".md": "markdown", ".mdx": "markdown",
}


def _ext(name: str) -> str:
    """The text from the last dot (the last character when there is none), as extensions are read here."""
    i = name.rfind(".")
    return name[i:] if i >= 0 else name[-1:]


def detect_language(file_path: str) -> str:
    return LANG_MAP.get(_ext(file_path), "unknown")


# Python grammar packages per language: (module, language function).
GRAMMAR_MODULES: dict[str, tuple[str, str]] = {
    "javascript": ("tree_sitter_javascript", "language"),
    "typescript": ("tree_sitter_typescript", "language_typescript"),
    "tsx": ("tree_sitter_typescript", "language_tsx"),
    "python": ("tree_sitter_python", "language"),
    "go": ("tree_sitter_go", "language"),
    "rust": ("tree_sitter_rust", "language"),
    "ruby": ("tree_sitter_ruby", "language"),
    "java": ("tree_sitter_java", "language"),
    "c": ("tree_sitter_c", "language"),
    "cpp": ("tree_sitter_cpp", "language"),
    "kotlin": ("tree_sitter_kotlin", "language"),
    "swift": ("tree_sitter_swift", "language"),
    "php": ("tree_sitter_php", "language_php"),
    "lua": ("tree_sitter_lua", "language"),
    "scala": ("tree_sitter_scala", "language"),
    "bash": ("tree_sitter_bash", "language"),
    "haskell": ("tree_sitter_haskell", "language"),
    "zig": ("tree_sitter_zig", "language"),
    "css": ("tree_sitter_css", "language"),
    "scss": ("tree_sitter_scss", "language"),
    "toml": ("tree_sitter_toml", "language"),
    "yaml": ("tree_sitter_yaml", "language"),
    "sql": ("tree_sitter_sql", "language"),
    "markdown": ("tree_sitter_markdown", "language"),
}

QUERIES: dict[str, str] = {
    "jsts": """
(function_declaration name: (identifier) @name) @func
(lexical_declaration
  (variable_declarator name: (identifier) @name value: [(arrow_function) (function_expression)])) @const_func
(class_declaration name: (type_identifier) @name) @cls
(method_definition name: (property_identifier) @name) @method
(interface_declaration name: (type_identifier) @name) @iface
(type_alias_declaration name: (type_identifier) @name) @tdef
(enum_declaration name: (identifier) @name) @enm
(import_statement) @imp
(export_statement) @exp
""",
    # Plain JavaScript has no type_identifier / interface / type alias / enum nodes, so it cannot share
    # the TypeScript query (one unknown node type fails the whole query); class names are identifiers.
    "js": """
(function_declaration name: (identifier) @name) @func
(lexical_declaration
  (variable_declarator name: (identifier) @name value: [(arrow_function) (function_expression)])) @const_func
(class_declaration name: (identifier) @name) @cls
(method_definition name: (property_identifier) @name) @method
(import_statement) @imp
(export_statement) @exp
""",
    "python": """
(function_definition name: (identifier) @name) @func
(class_definition name: (identifier) @name) @cls
(import_statement) @imp
(import_from_statement) @imp
""",
    "go": """
(function_declaration name: (identifier) @name) @func
(method_declaration name: (field_identifier) @name) @method
(type_declaration (type_spec name: (type_identifier) @name)) @tdef
(import_declaration) @imp
""",
    "rust": """
(function_item name: (identifier) @name) @func
(struct_item name: (type_identifier) @name) @struct_def
(enum_item name: (type_identifier) @name) @enm
(trait_item name: (type_identifier) @name) @trait_def
(impl_item type: (type_identifier) @name) @impl_def
(use_declaration) @imp
""",
    "ruby": """
(method name: (identifier) @name) @func
(class name: (constant) @name) @cls
(module name: (constant) @name) @cls
(call method: (identifier) @name) @imp
""",
    "java": """
(method_declaration name: (identifier) @name) @method
(class_declaration name: (identifier) @name) @cls
(interface_declaration name: (identifier) @name) @iface
(enum_declaration name: (identifier) @name) @enm
(import_declaration) @imp
""",
    "kotlin": """
(function_declaration (simple_identifier) @name) @func
(class_declaration (type_identifier) @name) @cls
(object_declaration (type_identifier) @name) @cls
(import_header) @imp
""",
    "swift": """
(function_declaration name: (simple_identifier) @name) @func
(class_declaration name: (type_identifier) @name) @cls
(protocol_declaration name: (type_identifier) @name) @iface
(import_declaration) @imp
""",
    "php": """
(function_definition name: (name) @name) @func
(class_declaration name: (name) @name) @cls
(interface_declaration name: (name) @name) @iface
(trait_declaration name: (name) @name) @trait_def
(method_declaration name: (name) @name) @method
(namespace_use_declaration) @imp
""",
    "lua": """
(function_declaration name: (identifier) @name) @func
(function_declaration name: (dot_index_expression) @name) @func
(function_declaration name: (method_index_expression) @name) @func
""",
    "scala": """
(function_definition name: (identifier) @name) @func
(class_definition name: (identifier) @name) @cls
(object_definition name: (identifier) @name) @cls
(trait_definition name: (identifier) @name) @trait_def
(import_declaration) @imp
""",
    "bash": """
(function_definition name: (word) @name) @func
""",
    "haskell": """
(function name: (variable) @name) @func
(type_synomym name: (name) @name) @tdef
(newtype name: (name) @name) @tdef
(data_type name: (name) @name) @tdef
(class name: (name) @name) @cls
(import) @imp
""",
    "zig": """
(function_declaration name: (identifier) @name) @func
(test_declaration) @func
""",
    "css": """
(rule_set (selectors) @name) @func
(media_statement) @cls
(keyframes_statement (keyframes_name) @name) @cls
(import_statement) @imp
""",
    "scss": """
(rule_set (selectors) @name) @func
(media_statement) @cls
(keyframes_statement (keyframes_name) @name) @cls
(import_statement) @imp
(mixin_statement name: (identifier) @name) @mixin_def
(function_statement name: (identifier) @name) @func
(include_statement) @imp
""",
    "toml": """
(table (bare_key) @name) @cls
(table (dotted_key) @name) @cls
(table_array_element (bare_key) @name) @cls
(table_array_element (dotted_key) @name) @cls
""",
    "yaml": """
(block_mapping_pair key: (flow_node) @name) @func
""",
    "sql": """
(create_table (object_reference) @name) @cls
(create_function (object_reference) @name) @func
(create_view (object_reference) @name) @cls
""",
    "markdown": """
(atx_heading heading_content: (inline) @name) @heading
(setext_heading heading_content: (paragraph) @name) @heading
(fenced_code_block (info_string (language) @name)) @code_block
(fenced_code_block) @code_block
(minus_metadata) @frontmatter
(link_reference_definition (link_label) @name) @ref
""",
    "generic": """
(function_declaration name: (identifier) @name) @func
(function_definition name: (identifier) @name) @func
(class_declaration name: (identifier) @name) @cls
(class_definition name: (identifier) @name) @cls
(import_statement) @imp
(import_declaration) @imp
""",
}

_C_QUERY = """
(function_definition declarator: (function_declarator declarator: (identifier) @name)) @func
(function_definition
  declarator: (pointer_declarator declarator: (function_declarator declarator: (identifier) @name))) @func
(struct_specifier name: (type_identifier) @name body: (field_declaration_list)) @struct_def
(enum_specifier name: (type_identifier) @name body: (enumerator_list)) @enm
(type_definition declarator: (type_identifier) @name) @tdef
(preproc_include) @imp
"""

# Queries for grammar versions whose node names differ from the ones the main query was written for
# (tried in order when the main query does not compile against the installed grammar).
QUERY_VARIANTS: dict[str, tuple[str, ...]] = {
    "kotlin": ("""
(function_declaration name: (identifier) @name) @func
(class_declaration name: (identifier) @name) @cls
(object_declaration name: (identifier) @name) @cls
(import) @imp
""",),
    "c": (_C_QUERY,),
    "cpp": (_C_QUERY + """
(function_definition declarator: (function_declarator declarator: (field_identifier) @name)) @func
(function_definition declarator: (function_declarator declarator: (qualified_identifier) @name)) @func
(function_definition declarator: (function_declarator declarator: (destructor_name) @name)) @func
(function_definition declarator: (reference_declarator (function_declarator declarator: (_) @name))) @func
(class_specifier name: (type_identifier) @name body: (field_declaration_list)) @cls
""",),
}

_QUERY_KEYS = {"javascript": "js", "typescript": "jsts", "tsx": "jsts"}
_OWN_QUERY = {"python", "go", "rust", "ruby", "java", "kotlin", "swift", "php", "lua", "scala", "bash", "haskell",
              "zig", "css", "scss", "toml", "yaml", "sql", "markdown"}


def get_query_key(language: str) -> str:
    if language in _QUERY_KEYS:
        return _QUERY_KEYS[language]
    return language if language in _OWN_QUERY else "generic"


# ---- tree-sitter backend -------------------------------------------------------------------------
_grammar_cache: dict[str, list] = {}
_backend_cache: dict[str, tuple[Any, list] | None] = {}
_parser_cache: dict[str, Any] = {}


def _load_grammars(language: str) -> list:
    """Every importable grammar for ``language``: its own package first, then the bundled language pack."""
    if language in _grammar_cache:
        return _grammar_cache[language]
    found: list = []
    spec = GRAMMAR_MODULES.get(language)
    if spec:
        try:
            from tree_sitter import Language
            found.append(Language(getattr(importlib.import_module(spec[0]), spec[1])()))
        except Exception as exc:  # package missing, or built for an incompatible ABI
            log.debug("smart read: no grammar package for %s (%s)", language, exc)
        try:
            from tree_sitter_language_pack import get_language
            found.append(get_language(language))
        except Exception as exc:
            log.debug("smart read: language pack has no %s (%s)", language, exc)
    _grammar_cache[language] = found
    return found


def _compile(lang: Any, source: str) -> Any | None:
    try:
        from tree_sitter import Query
        return Query(lang, source)
    except Exception:
        return None


def split_patterns(query: str) -> list[str]:
    """The top-level patterns of a query (each with its trailing captures)."""
    patterns: list[str] = []
    buf: list[str] = []
    depth, in_str = 0, False
    for i, ch in enumerate(query):
        if in_str:
            in_str = not (ch == '"' and query[i - 1] != "\\")
        elif ch == '"':
            in_str = True
        elif ch in "([":
            if depth == 0 and "".join(buf).strip():
                patterns.append("".join(buf).strip())
                buf = []
            depth += 1
        elif ch in ")]":
            depth -= 1
        buf.append(ch)
    if "".join(buf).strip():
        patterns.append("".join(buf).strip())
    return patterns


def _defines_symbols(query: Any) -> bool:
    return any(query.capture_name(i) in KIND_MAP for i in range(query.capture_count))


def _backend(language: str) -> tuple[Any, list] | None:
    """(grammar, compiled queries) for a language, or None when no grammar/query pair applies.

    The main query is preferred on any grammar, then a variant written for other grammar versions,
    then the main query's patterns that compile one by one (an unknown node type fails a whole query)."""
    if language in _backend_cache:
        return _backend_cache[language]
    grammars = _load_grammars(language)
    main = QUERIES[get_query_key(language)]
    chosen: tuple[Any, list] | None = None
    for source in (main, *QUERY_VARIANTS.get(language, ())):
        for lang in grammars:
            q = _compile(lang, source)
            if q is not None:
                chosen = (lang, [q])
                break
        if chosen:
            break
    if chosen is None:
        for lang in grammars:
            salvaged = [q for q in (_compile(lang, pat) for pat in split_patterns(main)) if q is not None]
            if any(_defines_symbols(q) for q in salvaged):
                chosen = (lang, salvaged)
                break
    _backend_cache[language] = chosen
    return chosen


class _Cap:
    """One captured node: a tag, its 0-based row span and (for single-row captures) its text."""
    __slots__ = ("end_row", "start_col", "start_row", "tag", "text")

    def __init__(self, tag: str, start_row: int, end_row: int, text: str | None = None, start_col: int = 0):
        self.tag, self.start_row, self.end_row, self.text, self.start_col = tag, start_row, end_row, text, start_col


def _treesitter_matches(content: str, language: str) -> list[list[_Cap]] | None:
    backend = _backend(language)
    if backend is None:
        return None
    lang, queries = backend
    try:
        from tree_sitter import Parser, QueryCursor
        parser = _parser_cache.get(language)
        if parser is None:
            parser = _parser_cache[language] = Parser(lang)
        tree = parser.parse(content.encode("utf-8", "replace"))
        matches: list[list[_Cap]] = []
        for q in queries:
            for _pattern, caps in QueryCursor(q).matches(tree.root_node):
                m: list[_Cap] = []
                for tag, nodes in caps.items():
                    for node in nodes:
                        sp, ep = node.start_point, node.end_point
                        # Text is recorded only for captures on a single row, as the outline has always read.
                        text = node.text.decode("utf-8", "replace") if sp.row == ep.row else None
                        m.append(_Cap(tag, sp.row, ep.row, text, sp.column))
                m.sort(key=lambda c: (c.start_row, c.start_col))
                matches.append(m)
        if len(queries) > 1:
            matches.sort(key=lambda m: (m[0].start_row, m[0].start_col) if m else (0, 0))
        return matches
    except Exception as exc:
        log.debug("smart read: tree-sitter query failed for %s (%s)", language, exc)
        return None


# ---- line-oriented extractors (languages without a usable grammar) ----------------------------------
def _kind(tag: str, start: int, end: int, name: str | None = None, name_row: int | None = None) -> list[_Cap]:
    caps = [_Cap(tag, start, end)]
    if name is not None:
        row = start if name_row is None else name_row
        caps.append(_Cap("name", row, row, name))
    return caps


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip(" \t"))


def _trim_back(lines: list[str], row: int, floor: int) -> int:
    while row > floor and not lines[row].strip():
        row -= 1
    return row


@dataclass
class _Lex:
    line_comments: tuple[str, ...] = ("//",)
    block: tuple[str, str] | None = ("/*", "*/")
    quotes: str = "\"'"
    multiline_quotes: tuple[str, ...] = ()
    hash_at_word_start: bool = False


@dataclass
class _Scan:
    tokens: list[tuple[int, str]]
    row_first: list[int]
    depth: list[int]
    opener: list[int]
    in_literal: list[bool]


def _scan(lines: list[str], lex: _Lex) -> _Scan:
    """Brackets and semicolons outside strings/comments, plus the brace depth, the row of the innermost
    open ``{`` and whether the row starts inside a comment or string, for every row."""
    tokens: list[tuple[int, str]] = []
    row_first: list[int] = []
    depth: list[int] = []
    opener: list[int] = []
    in_lit: list[bool] = []
    stack: list[int] = []
    state: tuple | None = None
    for r, line in enumerate(lines):
        row_first.append(len(tokens))
        depth.append(len(stack))
        opener.append(stack[-1] if stack else -1)
        in_lit.append(state is not None)
        i, n = 0, len(line)
        while i < n:
            if state is not None:
                if state[0] == "block":
                    j = line.find(state[1], i)
                    if j < 0:
                        i = n
                        break
                    i, state = j + len(state[1]), None
                    continue
                q, j = state[1], i
                while j < n and not line.startswith(q, j):
                    j += 2 if line[j] == "\\" else 1
                if j >= n:
                    i = n
                    break
                i, state = j + len(q), None
                continue
            c = line[i]
            if any(line.startswith(lc, i) for lc in lex.line_comments) and not (
                    lex.hash_at_word_start and c == "#" and i > 0 and not line[i - 1].isspace()):
                break
            if lex.block and line.startswith(lex.block[0], i):
                state = ("block", lex.block[1])
                i += len(lex.block[0])
                continue
            mq = next((q for q in lex.multiline_quotes if line.startswith(q, i)), None)
            if mq:
                state = ("str", mq, True)
                i += len(mq)
                continue
            if c in lex.quotes:
                state = ("str", c, False)
                i += 1
                continue
            if c in "{}()[];":
                tokens.append((r, c))
                if c == "{":
                    stack.append(r)
                elif c == "}" and stack:
                    stack.pop()
            i += 1
        if state is not None and state[0] == "str" and not state[2]:
            state = None  # an unterminated single-line string ends with its line
    return _Scan(tokens, row_first, depth, opener, in_lit)


_CONT_END = (",", "(", "[", "{", "=", "=>", "->", ":", "|", "&", "+", ".", "?", "<", "\\")
_CONT_START = ("{", ".", "?", ":", "=", "|", "&", ")", "]", "->", "=>", "where", "extends", "implements", "throws",
               "+")


def _continues(lines: list[str], row: int) -> bool:
    """Whether a statement at bracket depth zero carries on from ``row`` to the next row."""
    cur = lines[row].rstrip()
    if not cur.strip() or row + 1 >= len(lines):
        return False
    nxt = lines[row + 1].strip()
    if not nxt:
        return False
    return cur.endswith(_CONT_END) or nxt.startswith(_CONT_START)


def _decl_end(lines: list[str], scan: _Scan, start: int, body: bool) -> tuple[int, int]:
    """(last row, row of the body's opening brace or -1) of the declaration starting at ``start``.

    With ``body`` the declaration ends at the brace closing its ``{`` body; otherwise (and when no body
    opens) at a ``;`` or a line break at bracket depth zero that does not continue the statement."""
    toks = scan.tokens
    i = scan.row_first[start] if start < len(scan.row_first) else len(toks)
    depth, open_row, last = 0, -1, start
    while i < len(toks):
        r, ch = toks[i]
        while last < r:
            if depth == 0 and not _continues(lines, last):
                return last, open_row
            last += 1
        if ch in "([{":
            if ch == "{" and depth == 0 and body and open_row < 0:
                open_row = r
            depth += 1
        elif ch in ")]}":
            depth -= 1
            if depth < 0:  # the enclosing block closed: the declaration ended before this row
                return _trim_back(lines, r - 1 if r > start else r, start), open_row
            if depth == 0 and ch == "}" and open_row >= 0:
                return r, open_row
        elif ch == ";" and depth == 0:
            return r, open_row
        i += 1
    while last < len(lines) - 1 and (depth > 0 or _continues(lines, last)):
        last += 1
    return _trim_back(lines, last, start), open_row


@dataclass
class _Rule:
    tag: str
    rx: re.Pattern
    mode: str = "body"          # body | stmt | line
    scope: str = "any"          # any | member (inside a host body) | nonmember | top (brace depth 0)
    requires_body: bool = False
    host: bool = False          # members of this declaration's body may match "member" rules
    also: bool = False          # keep trying the other rules on the same row
    exclude: frozenset = frozenset()
    check: Callable[[list[str], int, int], bool] | None = None
    name_at_end: re.Pattern | None = None  # read the name from the statement's last row instead


def _rx(p: str, flags: int = 0) -> re.Pattern:
    return re.compile(p, flags)


_JS_ID = r"[A-Za-z_$][\w$]*"
_JS_FN_VALUE = _rx(r"=\s*(?:async\s+)?(?:function\b|(?:<[^>]*>\s*)?(?:\([^)]*\)|[A-Za-z_$][\w$]*)\s*"
                   r"(?::\s*[^=]+?)?=>)", re.DOTALL)


def _js_function_value(lines: list[str], start: int, end: int) -> bool:
    text = "\n".join(lines[start:min(end, start + 5) + 1])
    m = re.search(r"(?:const|let)\s+[A-Za-z_$][\w$]*\s*(?::[^=]+)?(=.*)", text, re.DOTALL)
    return bool(m and _JS_FN_VALUE.match(m.group(1)))


_KEYWORD_CALLS = frozenset({"if", "for", "while", "switch", "catch", "return", "function", "new", "super", "this",
                            "typeof", "await", "yield", "else", "do", "with", "throw", "delete", "void", "in", "of",
                            "sizeof", "case", "goto", "foreach", "elseif", "match", "using", "synchronized"})


def _js_rules(ts: bool) -> list[_Rule]:
    pre = r"^\s*(?:export\s+)?(?:default\s+)?(?:declare\s+)?"
    rules = [
        _Rule("exp", _rx(r"^\s*export\b"), mode="stmt", also=True),
        _Rule("imp", _rx(r"^\s*import\s*(?:type\s+)?[\w${*\"'`]"), mode="stmt"),
        _Rule("func", _rx(pre + rf"(?:async\s+)?function\s*\*?\s*(?P<name>{_JS_ID})"), requires_body=True),
        _Rule("const_func", _rx(rf"^\s*(?:export\s+)?(?:declare\s+)?(?:const|let)\s+(?P<name>{_JS_ID})\s*"
                                r"(?::[^=]+)?=(?!=)"), mode="stmt", check=_js_function_value),
        _Rule("cls", _rx(pre + rf"class\s+(?P<name>{_JS_ID})"), host=True),
    ]
    if ts:
        rules += [
            _Rule("iface", _rx(pre + r"interface\s+(?P<name>[A-Za-z_$][\w$]*)")),
            _Rule("tdef", _rx(pre + r"type\s+(?P<name>[A-Za-z_$][\w$]*)\b[^=]*=(?!=)"), mode="stmt"),
            _Rule("enm", _rx(pre + r"(?:const\s+)?enum\s+(?P<name>[A-Za-z_$][\w$]*)")),
        ]
    rules.append(_Rule("method", _rx(r"^\s*(?:(?:public|private|protected|static|readonly|async|override|abstract|"
                                     r"declare|get|set|accessor)\s+)*\*?\s*(?P<name>#?[A-Za-z_$][\w$]*)\s*"
                                     r"(?:<[^>]*>)?\s*\("), scope="member", requires_body=True,
                       exclude=_KEYWORD_CALLS))
    return rules


_RS_VIS = r"(?:pub(?:\s*\([^)]*\))?\s+)?"
_KT_MODS = r"(?:(?:public|private|internal|protected|override|open|abstract|final|suspend|inline|infix|operator|" \
           r"tailrec|external|actual|expect|data|enum|sealed|annotation|inner|value|const|lateinit)\s+)*"
_SW_MODS = r"(?:@\w+(?:\([^)]*\))?\s+)*(?:(?:public|private|fileprivate|internal|open|static|class|final|override|" \
           r"mutating|nonmutating|convenience|required|dynamic|indirect|prefix|postfix|infix|nonisolated)\s+)*"
_JAVA_MOD_WORDS = r"(?:public|private|protected|static|final|abstract|synchronized|native|default|strictfp|sealed|" \
                  r"non-sealed|transient|volatile)"
_JAVA_MODS = r"(?:@[\w.]+(?:\([^)]*\))?\s+)*(?:" + _JAVA_MOD_WORDS + r"\s+)*"
_SCALA_MODS = r"(?:(?:override|private|protected|final|implicit|inline|transparent|lazy|abstract|sealed|open|" \
              r"case)(?:\[[^\]]*\])?\s+)*"
_C_TYPEDEF_NAME = re.compile(r"(?<![\w(*])(?P<name>[A-Za-z_]\w*)\s*(?:\[[^\]]*\])?\s*;\s*(?://.*|/\*.*)?$")
_ANNOTATION_ROW = re.compile(r"^\s*@[\w.]+(?:\(.*\))?\s*$")
_C_SKIP = r"(?!(?:return|if|else|for|while|switch|case|do|goto|sizeof|typedef|using|namespace|struct|class|enum|" \
          r"union|template|delete|new|throw)\b)"


def _scala_has_body(lines: list[str], start: int, end: int) -> bool:
    return "=" in "\n".join(lines[start:end + 1]) or "{" in "\n".join(lines[start:end + 1])


_BRACE_RULES: dict[str, list[_Rule]] = {
    "javascript": _js_rules(False),
    "typescript": _js_rules(True),
    "tsx": _js_rules(True),
    "go": [
        _Rule("imp", _rx(r"^\s*import\b"), mode="stmt"),
        _Rule("method", _rx(r"^func\s*\([^)]*\)\s*(?P<name>\w+)"), requires_body=True),
        _Rule("func", _rx(r"^func\s+(?P<name>\w+)"), requires_body=True),
        _Rule("tdef", _rx(r"^type\s+(?P<name>[A-Za-z_]\w*)"), mode="stmt"),
    ],
    "rust": [
        _Rule("imp", _rx(r"^\s*" + _RS_VIS + r"use\b"), mode="stmt"),
        _Rule("func", _rx(r"^\s*" + _RS_VIS + r"(?:default\s+)?(?:const\s+)?(?:async\s+)?(?:unsafe\s+)?"
                          r"(?:extern\s+(?:\"[^\"]*\"\s+)?)?fn\s+(?P<name>\w+)"), requires_body=True),
        _Rule("struct_def", _rx(r"^\s*" + _RS_VIS + r"struct\s+(?P<name>\w+)"), mode="stmt"),
        _Rule("enm", _rx(r"^\s*" + _RS_VIS + r"enum\s+(?P<name>\w+)")),
        _Rule("trait_def", _rx(r"^\s*" + _RS_VIS + r"(?:unsafe\s+)?(?:auto\s+)?trait\s+(?P<name>\w+)")),
        _Rule("impl_def", _rx(r"^\s*(?:unsafe\s+)?impl\b(?:\s*<[^{]*?>)?\s+(?:!?[\w:]+(?:<[^{]*?>)?\s+for\s+)?"
                              r"(?P<name>\w+)\s*(?:\{|where\b|$)")),
    ],
    "java": [
        _Rule("imp", _rx(r"^\s*import\s"), mode="stmt"),
        _Rule("cls", _rx(r"^\s*" + _JAVA_MODS + r"class\s+(?P<name>\w+)"), host=True),
        _Rule("iface", _rx(r"^\s*" + _JAVA_MODS + r"@?interface\s+(?P<name>\w+)"), host=True),
        _Rule("enm", _rx(r"^\s*" + _JAVA_MODS + r"enum\s+(?P<name>\w+)"), host=True),
        _Rule("method", _rx(r"^\s*" + _JAVA_MODS + r"(?:<[^>]+>\s+)?(?!" + _JAVA_MOD_WORDS + r"\b)[\w.$]+"
                            r"(?:<[^()]*?>)?(?:\[\])*\s+"
                            r"(?P<name>[A-Za-z_$][\w$]*)\s*\("), scope="member", exclude=_KEYWORD_CALLS),
    ],
    "kotlin": [
        _Rule("imp", _rx(r"^\s*import\s"), mode="line"),
        _Rule("func", _rx(r"^\s*" + _KT_MODS + r"fun\s+(?:<[^>]*>\s*)?(?:[\w.<>?, ]+\.)?(?P<name>\w+)\s*\(")),
        _Rule("cls", _rx(r"^\s*" + _KT_MODS + r"(?:class|interface)\s+(?P<name>\w+)"), host=True),
        _Rule("cls", _rx(r"^\s*" + _KT_MODS + r"object\s+(?P<name>\w+)"), host=True),
    ],
    "swift": [
        _Rule("imp", _rx(r"^\s*(?:@\w+\s+)?import\s"), mode="line"),
        _Rule("func", _rx(r"^\s*" + _SW_MODS + r"func\s+(?P<name>\w+)"), requires_body=True),
        _Rule("cls", _rx(r"^\s*" + _SW_MODS + r"(?:final\s+)?(?:class|struct|enum|actor)\s+(?P<name>\w+)"),
              host=True, exclude=frozenset({"func", "var", "let"})),
        _Rule("iface", _rx(r"^\s*" + _SW_MODS + r"protocol\s+(?P<name>\w+)"), host=True),
    ],
    "php": [
        _Rule("imp", _rx(r"^\s*use\s+[\\\w]"), mode="stmt", scope="nonmember"),
        _Rule("cls", _rx(r"^\s*(?:(?:abstract|final|readonly)\s+)*class\s+(?P<name>\w+)"), host=True),
        _Rule("iface", _rx(r"^\s*interface\s+(?P<name>\w+)"), host=True),
        _Rule("trait_def", _rx(r"^\s*trait\s+(?P<name>\w+)"), host=True),
        _Rule("method", _rx(r"^\s*(?:(?:public|private|protected|static|abstract|final)\s+)*function\s+&?"
                            r"(?P<name>\w+)"), scope="member"),
        _Rule("func", _rx(r"^\s*function\s+&?(?P<name>\w+)"), scope="nonmember", requires_body=True),
    ],
    "scala": [
        _Rule("imp", _rx(r"^\s*import\s"), mode="line"),
        _Rule("func", _rx(r"^\s*" + _SCALA_MODS + r"def\s+(?P<name>[\w$]+)"), check=_scala_has_body),
        _Rule("cls", _rx(r"^\s*" + _SCALA_MODS + r"class\s+(?P<name>\w+)"), host=True),
        _Rule("cls", _rx(r"^\s*" + _SCALA_MODS + r"object\s+(?P<name>\w+)"), host=True),
        _Rule("trait_def", _rx(r"^\s*" + _SCALA_MODS + r"trait\s+(?P<name>\w+)"), host=True),
    ],
    "bash": [
        _Rule("func", _rx(r"^\s*function\s+(?P<name>[\w.:@-]+)")),
        _Rule("func", _rx(r"^\s*(?P<name>[A-Za-z_][\w.:@-]*)\s*\(\s*\)")),
    ],
    "zig": [
        _Rule("func", _rx(r"^\s*(?:pub\s+)?(?:export\s+|extern\s+(?:\"[^\"]*\"\s+)?)?(?:inline\s+|noinline\s+)?"
                          r"fn\s+(?P<name>\w+)"), requires_body=True),
        _Rule("func", _rx(r"^\s*test\b(?:\s+(?:\"[^\"]*\"|\w+))?\s*\{")),
    ],
    "c": [
        _Rule("imp", _rx(r"^\s*#\s*include\b"), mode="line"),
        _Rule("struct_def", _rx(r"^\s*(?:typedef\s+)?struct\s+(?P<name>\w+)\s*(?:\{|$)"), requires_body=True,
              also=True),
        _Rule("enm", _rx(r"^\s*(?:typedef\s+)?enum\s+(?P<name>\w+)\s*(?:\{|$)"), requires_body=True, also=True),
        _Rule("tdef", _rx(r"^\s*typedef\b"), mode="stmt", name_at_end=_C_TYPEDEF_NAME),
        _Rule("func", _rx(r"^\s*" + _C_SKIP + r"(?:[\w*&\[\]]+\s+|[\w]+[*&]+\s*)+\**(?P<name>[A-Za-z_]\w*)\s*\("),
              requires_body=True, exclude=_KEYWORD_CALLS),
    ],
    "cpp": [
        _Rule("imp", _rx(r"^\s*#\s*include\b"), mode="line"),
        _Rule("struct_def", _rx(r"^\s*(?:typedef\s+)?struct\s+(?P<name>\w+)\s*(?:final\s*)?(?::[^;{]*)?(?:\{|$)"),
              requires_body=True, host=True),
        _Rule("enm", _rx(r"^\s*(?:typedef\s+)?enum\s+(?:class\s+|struct\s+)?(?P<name>\w+)"), requires_body=True),
        _Rule("cls", _rx(r"^\s*(?:template\s*<[^>]*>\s*)?class\s+(?:\w+\s+)?(?P<name>\w+)\s*(?:final\s*)?"
                         r"(?::[^;{]*)?(?:\{|$)"), requires_body=True, host=True),
        _Rule("func", _rx(r"^\s*" + _C_SKIP + r"(?:[\w:<>,*&\[\]]+\s+|[\w:<>]+[*&]+\s*)+[*&]*"
                          r"(?P<name>~?[A-Za-z_]\w*(?:::~?[A-Za-z_]\w*)*)\s*\("),
              requires_body=True, exclude=_KEYWORD_CALLS),
        _Rule("func", _rx(r"^\s*(?P<name>~?[A-Za-z_]\w*(?:::~?[A-Za-z_]\w*)+)\s*\("), requires_body=True),
        _Rule("func", _rx(r"^\s*(?P<name>~?[A-Za-z_]\w*)\s*\("), scope="member", requires_body=True,
              exclude=_KEYWORD_CALLS),
    ],
}

_LEX: dict[str, _Lex] = {
    "javascript": _Lex(multiline_quotes=("`",)),
    "typescript": _Lex(multiline_quotes=("`",)),
    "tsx": _Lex(multiline_quotes=("`",)),
    "go": _Lex(multiline_quotes=("`",)),
    "rust": _Lex(quotes='"'),
    "java": _Lex(multiline_quotes=('"""',)),
    "kotlin": _Lex(multiline_quotes=('"""',)),
    "swift": _Lex(multiline_quotes=('"""',)),
    "scala": _Lex(multiline_quotes=('"""',)),
    "php": _Lex(line_comments=("//", "#")),
    "bash": _Lex(line_comments=("#",), block=None, hash_at_word_start=True),
    "zig": _Lex(block=None),
    "c": _Lex(),
    "cpp": _Lex(),
}


def _brace_matches(lines: list[str], language: str) -> list[list[_Cap]]:
    scan = _scan(lines, _LEX[language])
    rules = _BRACE_RULES[language]
    hosts: set[int] = set()
    out: list[list[_Cap]] = []
    for r, line in enumerate(lines):
        if scan.in_literal[r] or not line.strip():
            continue
        for rule in rules:
            m = rule.rx.match(line)
            if not m:
                continue
            if rule.scope == "top" and scan.depth[r] != 0:
                continue
            if rule.scope == "member" and scan.opener[r] not in hosts:
                continue
            if rule.scope == "nonmember" and scan.opener[r] in hosts:
                continue
            name = m.groupdict().get("name")
            if name is not None and name.lstrip("#") in rule.exclude:
                continue
            if rule.mode == "line":
                end, open_row = r, -1
            else:
                end, open_row = _decl_end(lines, scan, r, body=rule.mode == "body")
            if rule.requires_body and open_row < 0:
                continue
            if rule.check and not rule.check(lines, r, end):
                continue
            name_row = r
            if rule.name_at_end is not None:
                nm = rule.name_at_end.search(lines[end])
                if not nm:
                    continue
                name, name_row = nm.group("name"), end
            if rule.tag in ("imp", "exp"):
                out.append([_Cap(rule.tag, r, end, _trim(line) if end == r else None)])
            else:
                start = r
                while language == "java" and start > 0 and _ANNOTATION_ROW.match(lines[start - 1]):
                    start -= 1  # annotations belong to the declaration's modifiers
                out.append(_kind(rule.tag, start, end, name, name_row))
            if rule.host and open_row >= 0:
                hosts.add(open_row)
            if not rule.also:
                break
        if language == "go" and re.match(r"^type\s*\(", line):
            end, _ = _decl_end(lines, scan, r, body=False)
            for k in range(r + 1, end):
                gm = re.match(r"^\s+(?P<name>[A-Za-z_]\w*)\s+\S", lines[k])
                if gm and scan.depth[k] == scan.depth[r] and not scan.in_literal[k]:
                    out.append(_kind("tdef", r, end, gm.group("name"), k))
    return out


# Python: blocks end where the indentation returns to the declaration's level.
_PY_TOKEN = re.compile(r"\"\"\"|'''|#|\"(?:\\.|[^\"\\])*\"?|'(?:\\.|[^'\\])*'?")


def _python_rows(lines: list[str]) -> tuple[list[bool], list[str]]:
    """(row starts inside a triple-quoted string, the row's code with strings and comments blanked)."""
    in_str: list[bool] = []
    code: list[str] = []
    state: str | None = None
    for line in lines:
        in_str.append(state is not None)
        out, i = [], 0
        while i < len(line):
            if state:
                j = line.find(state, i)
                if j < 0:
                    i = len(line)
                    break
                i, state = j + 3, None
                continue
            m = _PY_TOKEN.search(line, i)
            if not m:
                out.append(line[i:])
                break
            out.append(line[i:m.start()])
            tok = m.group(0)
            if tok == "#":
                break
            if tok in ('"""', "'''"):
                state = tok
            out.append('""')
            i = m.end()
        code.append("".join(out))
    return in_str, code


def _python_block_end(lines: list[str], start: int, in_str: list[bool], code: list[str]) -> int:
    base = _indent(lines[start])
    header, depth = start, 0
    for r in range(start, min(len(lines), start + 60)):
        c = code[r]
        depth += sum(c.count(ch) for ch in "([{") - sum(c.count(ch) for ch in ")]}")
        header = r
        if depth <= 0 and not c.rstrip().endswith("\\"):
            break
    end = header
    for r in range(header + 1, len(lines)):
        if in_str[r]:
            end = r
            continue
        s = lines[r].strip()
        if not s or s.startswith("#"):
            continue
        if _indent(lines[r]) <= base:
            break
        end = r
    return end


def _python_stmt_end(lines: list[str], start: int, code: list[str]) -> int:
    depth = 0
    for r in range(start, len(lines)):
        c = code[r]
        depth += sum(c.count(ch) for ch in "([{") - sum(c.count(ch) for ch in ")]}")
        if depth <= 0 and not c.rstrip().endswith("\\"):
            return r
    return len(lines) - 1


def _python_matches(lines: list[str], language: str) -> list[list[_Cap]]:
    in_str, code = _python_rows(lines)
    out: list[list[_Cap]] = []
    for r, line in enumerate(lines):
        if in_str[r]:
            continue
        m = re.match(r"^\s*(?:async\s+)?def\s+(?P<name>\w+)", line)
        if m:
            out.append(_kind("func", r, _python_block_end(lines, r, in_str, code), m.group("name")))
            continue
        m = re.match(r"^\s*class\s+(?P<name>\w+)", line)
        if m:
            out.append(_kind("cls", r, _python_block_end(lines, r, in_str, code), m.group("name")))
            continue
        if re.match(r"^\s*(?:import\s+[\w.]|from\s+[\w.]+\s+import\b)", line):
            end = _python_stmt_end(lines, r, code)
            out.append([_Cap("imp", r, end, _trim(line) if end == r else None)])
    return out


# Ruby and Lua: blocks are closed by ``end``.
def _strip_simple(line: str, comment: str) -> str:
    line = re.sub(r"\"(?:\\.|[^\"\\])*\"|'(?:\\.|[^'\\])*'", '""', line)
    i = line.find(comment)
    return line if i < 0 else line[:i]


_RB_OPEN = re.compile(r"^\s*(?:def|class|module|if|unless|while|until|case|begin|for)\b|(?:=|\()\s*"
                      r"(?:if|unless|case|begin|while|until)\b|\bdo\b(?:\s*\|[^|]*\|)?\s*$")
_RB_END = re.compile(r"(?<![.\w])end\b")
_LUA_OPEN = re.compile(r"\b(?:function|do|if|repeat)\b")
_LUA_END = re.compile(r"\b(?:end|until)\b")


def _end_block_end(lines: list[str], start: int, language: str) -> int:
    depth = 0
    for r in range(start, len(lines)):
        if language == "ruby":
            c = _strip_simple(lines[r], "#")
            if r > start and re.match(r"^=begin\b", lines[r]):
                continue
            opens = len(_RB_OPEN.findall(c))
            closes = len(_RB_END.findall(c))
        else:
            c = _strip_simple(lines[r], "--")
            opens = len(_LUA_OPEN.findall(c))
            closes = len(_LUA_END.findall(c))
        depth += opens - closes
        if depth <= 0:
            return r
    return _trim_back(lines, len(lines) - 1, start)


def _end_matches(lines: list[str], language: str) -> list[list[_Cap]]:
    out: list[list[_Cap]] = []
    for r, line in enumerate(lines):
        if language == "ruby":
            m = re.match(r"^\s*def\s+(?P<name>[A-Za-z_]\w*[?!]?)(?=[\s(;]|$)", line)
            if m:
                out.append(_kind("func", r, _end_block_end(lines, r, language), m.group("name")))
                continue
            m = re.match(r"^\s*(?:class|module)\s+(?P<name>[A-Z]\w*)(?![\w:])", line)
            if m:
                out.append(_kind("cls", r, _end_block_end(lines, r, language), m.group("name")))
                continue
            if re.match(r"^\s*(?:require|require_relative|load|include|extend|prepend|using)\b", line):
                out.append([_Cap("imp", r, r, _trim(line))])
        else:
            m = re.match(r"^\s*(?:local\s+)?function\s+(?P<name>[\w.:]+)\s*\(", line)
            if m:
                out.append(_kind("func", r, _end_block_end(lines, r, language), m.group("name")))
    return out


# Haskell: a top-level declaration runs until the next line that starts in column zero.
_HS_RESERVED = frozenset({"import", "module", "data", "type", "newtype", "class", "instance", "where", "let", "in",
                          "infix", "infixl", "infixr", "deriving", "foreign", "default", "if", "then", "else",
                          "case", "of", "do"})


def _haskell_matches(lines: list[str], language: str) -> list[list[_Cap]]:
    out: list[list[_Cap]] = []
    tops = [r for r, s in enumerate(lines) if s and not s[0].isspace()]
    for idx, r in enumerate(tops):
        line = lines[r]
        if line.startswith(("--", "{-")):
            continue
        nxt = tops[idx + 1] if idx + 1 < len(tops) else len(lines)
        end = _trim_back(lines, nxt - 1, r)
        if re.match(r"import\b", line):
            out.append([_Cap("imp", r, end, _trim(line) if end == r else None)])
            continue
        m = re.match(r"(?:type|newtype|data)\s+(?P<name>[A-Z][\w']*)", line)
        if m:
            out.append(_kind("tdef", r, end, m.group("name")))
            continue
        m = re.match(r"class\s+(?:\([^)]*\)\s*=>\s*|[A-Z][\w']*\s+[a-z][\w']*\s*=>\s*)?(?P<name>[A-Z][\w']*)", line)
        if m:
            out.append(_kind("cls", r, end, m.group("name")))
            continue
        m = re.match(r"(?P<name>[a-z_][\w']*)(?![\w'])(?!\s*::)\s+[^\s=|][^=]*?(?:=|\|)", line)
        if m and m.group("name") not in _HS_RESERVED:
            out.append(_kind("func", r, end, m.group("name")))
    return out


# YAML: every block mapping pair, at any depth.
_YAML_KEY = re.compile(r"^(?P<lead>\s*(?:-\s+)*)(?P<key>\"(?:[^\"\\]|\\.)*\"|'(?:[^']|'')*'|"
                       r"[^\s#&*!|>'\"%@`{\[\]},?:-][^#]*?|-[^\s#][^#]*?)\s*:(?:\s|$)")


def _yaml_matches(lines: list[str], language: str) -> list[list[_Cap]]:
    out: list[list[_Cap]] = []
    scalar_indent: int | None = None
    for r, line in enumerate(lines):
        s = line.strip()
        if scalar_indent is not None:
            if not s or _indent(line) > scalar_indent:
                continue
            scalar_indent = None
        if not s or s in ("---", "...") or s.startswith(("#", "{", "[")):
            continue
        m = _YAML_KEY.match(line)
        if not m:
            continue
        col = len(m.group("lead"))
        end, closed = r, False
        for k in range(r + 1, len(lines)):
            t = lines[k].strip()
            if not t or t.startswith("#"):
                continue
            if t in ("---", "...") or _indent(lines[k]) <= col:
                closed = True
                break
            end = k
        if end > r and not closed:
            end = len(lines) - 1  # nested content at the end of the document runs to its last row
        out.append(_kind("func", r, end, m.group("key")))
        if re.match(r"^[|>][-+0-9]*\s*(?:#.*)?$", line[m.end():].strip()):
            scalar_indent = col
    return out


# TOML: tables and arrays of tables named by a bare or dotted key.
_TOML_PART = r"(?:[A-Za-z0-9_-]+|\"[^\"]*\"|'[^']*')"
_TOML_TABLE = re.compile(r"^\s*\[\[?\s*(?P<key>" + _TOML_PART + r"(?:\s*\.\s*" + _TOML_PART + r")+|[A-Za-z0-9_-]+)"
                         r"\s*\]\]?")


def _toml_matches(lines: list[str], language: str) -> list[list[_Cap]]:
    out: list[list[_Cap]] = []
    heads = [r for r, s in enumerate(lines) if re.match(r"^\s*\[", s)]
    for idx, r in enumerate(heads):
        m = _TOML_TABLE.match(lines[r])
        if not m:
            continue
        end = heads[idx + 1] if idx + 1 < len(heads) else len(lines) - 1
        out.append(_kind("cls", r, end, m.group("key")))
    return out


# SQL: CREATE TABLE / FUNCTION / VIEW statements, to their terminating semicolon.
_SQL_NAME = r"(?P<name>[\w.\"`\[\]]+)"
_SQL_RULES = [
    ("cls", re.compile(r"^\s*create\s+(?:or\s+replace\s+)?(?:(?:global|local)\s+)?(?:temp(?:orary)?\s+)?"
                       r"(?:unlogged\s+)?table\s+(?:if\s+not\s+exists\s+)?" + _SQL_NAME, re.IGNORECASE)),
    ("func", re.compile(r"^\s*create\s+(?:or\s+replace\s+)?function\s+(?:if\s+not\s+exists\s+)?" + _SQL_NAME,
                        re.IGNORECASE)),
    ("cls", re.compile(r"^\s*create\s+(?:or\s+replace\s+)?(?:temp(?:orary)?\s+)?(?:recursive\s+)?view\s+"
                       r"(?:if\s+not\s+exists\s+)?" + _SQL_NAME, re.IGNORECASE)),
]


def _sql_stmt_end(lines: list[str], start: int) -> int:
    state: str | None = None
    for r in range(start, len(lines)):
        line, i = lines[r], 0
        while i < len(line):
            if state is not None:
                j = line.find(state, i)
                if j < 0:
                    i = len(line)
                    break
                i, state = j + len(state), None
                continue
            if line.startswith("--", i):
                break
            if line.startswith("/*", i):
                state, i = "*/", i + 2
                continue
            dm = re.match(r"\$\w*\$", line[i:])
            if dm:
                state, i = dm.group(0), i + len(dm.group(0))
                continue
            if line[i] in "'\"":
                state, i = line[i], i + 1
                continue
            if line[i] == ";":
                return r
            i += 1
    return _trim_back(lines, len(lines) - 1, start)


def _sql_matches(lines: list[str], language: str) -> list[list[_Cap]]:
    out: list[list[_Cap]] = []
    for r, line in enumerate(lines):
        for tag, rx in _SQL_RULES:
            m = rx.match(line)
            if m:
                out.append(_kind(tag, r, _sql_stmt_end(lines, r), m.group("name")))
                break
    return out


# CSS / SCSS: rule sets, @media, @keyframes, @import (and SCSS @mixin / @function / @include).
def _css_matches(lines: list[str], language: str) -> list[list[_Cap]]:
    scss = language == "scss"
    text = "\n".join(lines)
    out: list[list[_Cap]] = []
    stack: list[tuple[str, int, str | None]] = []
    row, i, n = 0, 0, len(text)
    pstart: int | None = None
    prow = 0

    def prelude(end: int) -> str:
        return text[pstart:end].strip() if pstart is not None else ""

    while i < n:
        c = text[i]
        if text.startswith("/*", i):
            j = text.find("*/", i + 2)
            j = n if j < 0 else j + 2
            row += text.count("\n", i, j)
            i = j
            continue
        if scss and text.startswith("//", i) and (i == 0 or text[i - 1] != ":"):
            j = text.find("\n", i)
            i = n if j < 0 else j
            continue
        if c in "'\"":
            j = i + 1
            while j < n and text[j] != c and text[j] != "\n":
                j += 2 if text[j] == "\\" else 1
            i = j + 1
            continue
        if scss and text.startswith("#{", i):
            j = text.find("}", i)
            i = n if j < 0 else j + 1
            continue
        if c == "\n":
            row += 1
            i += 1
            continue
        if c == "{":
            p = prelude(i)
            single = prow == row
            parent = stack[-1][0] if stack else ""
            low = p.lower()
            if parent == "keyframes":
                kind, name = "skip", None
            elif low.startswith("@media"):
                kind, name = "cls", None
            elif re.match(r"@(?:-[a-z]+-)?keyframes\b", low):
                nm = re.match(r"@(?:-[a-z]+-)?keyframes\s+([^\s{]+)", p)
                kind, name = "keyframes", (nm.group(1) if nm else None)
            elif scss and low.startswith("@mixin"):
                nm = re.match(r"@mixin\s+([\w-]+)", p)
                kind, name = "mixin_def", (nm.group(1) if nm else None)
            elif scss and low.startswith("@function"):
                nm = re.match(r"@function\s+([\w-]+)", p)
                kind, name = "func", (nm.group(1) if nm else None)
            elif scss and low.startswith("@include"):
                kind, name = "imp", None
            elif p.startswith("@") or not p:
                kind, name = "skip", None
            else:
                kind, name = "func", (p if single else None)
            stack.append((kind, prow, name))
            pstart = None
            i += 1
            continue
        if c == "}":
            if stack:
                kind, start, name = stack.pop()
                if kind == "imp":
                    out.append([_Cap("imp", start, row, None)])
                elif kind == "keyframes":
                    out.append(_kind("cls", start, row, name))
                elif kind != "skip":
                    out.append(_kind(kind, start, row, name))
            pstart = None
            i += 1
            continue
        if c == ";":
            p = prelude(i)
            low = p.lower()
            if low.startswith("@import") or (scss and low.startswith("@include")):
                out.append([_Cap("imp", prow, row, (p + ";") if prow == row else None)])
            pstart = None
            i += 1
            continue
        if pstart is None and not c.isspace():
            pstart, prow = i, row
        i += 1
    out.sort(key=lambda m: m[0].start_row)
    return out


# Markdown: ATX / setext headings, fenced code blocks, YAML front matter, link reference definitions.
_MD_ATX = re.compile(r"^ {0,3}(#{1,6})(?:[ \t]+.*)?$")
_MD_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")
_MD_SETEXT = re.compile(r"^ {0,3}(?:=+|-+)[ \t]*$")
_MD_REF = re.compile(r"^ {0,3}(\[[^\]]+\]):[ \t]*\S")
_MD_BLOCK_START = re.compile(r"^ {0,3}(?:[-*+][ \t]|\d+[.)][ \t]|>|#{1,6}(?:[ \t]|$)|(?:`{3,}|~{3,}))")


def _markdown_matches(lines: list[str], language: str) -> list[list[_Cap]]:
    """Block ranges end on the row after the block (its closing line break), as the grammar reports them."""
    out: list[list[_Cap]] = []
    n, i = len(lines), 0

    def after(row: int) -> int:
        return min(row + 1, n - 1)

    if n and lines[0].rstrip() == "---":
        for j in range(1, n):
            if lines[j].rstrip() == "---":
                out.append([_Cap("frontmatter", 0, after(j))])
                i = j + 1
                break
    fence: tuple[str, int, int, str | None] | None = None
    para: int | None = None
    while i < n:
        line = lines[i].rstrip("\r")
        if fence is not None:
            ch, size, start, lang = fence
            close = re.match(r"^ {0,3}(" + re.escape(ch) + r"{" + str(size) + r",})[ \t]*$", line)
            if close:
                out.append(_kind("code_block", start, after(i), lang))
                fence = None
            i += 1
            continue
        fm = _MD_FENCE.match(line)
        if fm and not (fm.group(1)[0] == "`" and "`" in fm.group(2)):
            info = fm.group(2).strip()
            fence = (fm.group(1)[0], len(fm.group(1)), i, info.split()[0] if info else None)
            para = None
            i += 1
            continue
        am = _MD_ATX.match(line)
        if am:
            content = line[am.end(1):].strip()  # a closing hash sequence stays part of the heading text
            if content:
                out.append(_kind("heading", i, after(i), content))
            para = None
            i += 1
            continue
        if para is not None and _MD_SETEXT.match(line):
            # the heading text is the paragraph including its line break, so it never sits on one row
            out.append([_Cap("heading", para, after(i))])
            para = None
            i += 1
            continue
        if not line.strip():
            para = None
        elif para is None:
            rm = _MD_REF.match(line)
            if rm:
                out.append(_kind("ref", i, after(i), rm.group(1)))
            elif not _MD_BLOCK_START.match(line):
                para = i
        i += 1
    if fence is not None:
        out.append(_kind("code_block", fence[2], n - 1, fence[3]))
    out.sort(key=lambda m: m[0].start_row)
    return out


_FALLBACKS: dict[str, Callable[[list[str], str], list[list[_Cap]]]] = {
    **{lang: _brace_matches for lang in _BRACE_RULES},
    "python": _python_matches,
    "ruby": _end_matches,
    "lua": _end_matches,
    "haskell": _haskell_matches,
    "yaml": _yaml_matches,
    "toml": _toml_matches,
    "sql": _sql_matches,
    "css": _css_matches,
    "scss": _css_matches,
    "markdown": _markdown_matches,
}


def _extract_matches(content: str, language: str) -> list[list[_Cap]] | None:
    """Captures for a file, or None when the language is not one this reader understands."""
    if language == "unknown":
        return None
    matches = _treesitter_matches(content, language)
    if matches is not None:
        return matches
    extractor = _FALLBACKS.get(language)
    if extractor is None:
        return None
    try:
        return extractor(content.split("\n"), language)
    except Exception as exc:  # an extractor must never break a read
        log.debug("smart read: line extractor failed for %s (%s)", language, exc)
        return []


# ---- symbols -------------------------------------------------------------------------------------
KIND_MAP: dict[str, str] = {
    "func": "function", "const_func": "function", "cls": "class", "method": "method", "iface": "interface",
    "tdef": "type", "enm": "enum", "struct_def": "struct", "trait_def": "trait", "impl_def": "impl",
    "mixin_def": "mixin", "heading": "section", "code_block": "code", "frontmatter": "metadata", "ref": "reference",
}

CONTAINER_KINDS = frozenset({"class", "struct", "impl", "trait"})
_COMMENT_PREFIXES = ("/**", "*", "*/", "//", "///", "//!", "#", "@")
_HASHES = re.compile(r"^(#{1,6})\s")


def _line(lines: list[str], row: int) -> str | None:
    return lines[row] if 0 <= row < len(lines) else None


def _extract_signature(lines: list[str], start_row: int, end_row: int, max_len: int = 200) -> str:
    sig = _line(lines, start_row) or ""
    tail = sig.rstrip(_JS_WS)
    if not tail.endswith("{") and not tail.endswith(":"):
        chunk = "\n".join(lines[max(0, start_row):min(start_row + 10, end_row + 1)])
        brace = chunk.find("{")
        if brace != -1 and brace < 500:
            sig = _trim(re.sub(r"\s+", " ", chunk[:brace].replace("\n", " ")))
    sig = _trim(re.sub(r"\s*[{:]\s*$", "", sig))
    if _js_len(sig) > max_len:
        sig = _js_slice(sig, max_len - 3) + "..."
    return sig


def _find_comment_above(lines: list[str], start_row: int) -> str | None:
    found: list[str] = []
    for i in range(min(start_row, len(lines)) - 1, -1, -1):
        t = _trim(lines[i])
        if t == "":
            if found:
                break
            continue
        if t.startswith(_COMMENT_PREFIXES):
            found.insert(0, lines[i])
        else:
            break
    return _trim("\n".join(found)) if found else None


def _find_python_docstring(lines: list[str], start_row: int, end_row: int) -> str | None:
    for i in range(start_row + 1, min(start_row + 3, end_row) + 1):
        raw = _line(lines, i)
        t = _trim(raw) if raw is not None else ""
        if not t:
            continue
        if t.startswith(('"""', "'''")):
            return t
        break
    return None


def _is_exported(name: str, start_row: int, end_row: int, export_ranges: list[tuple[int, int]],
                 lines: list[str], language: str) -> bool:
    if language in ("javascript", "typescript", "tsx"):
        return any(start_row >= s and end_row <= e for s, e in export_ranges)
    if language == "python":
        return not name.startswith("_")
    if language == "go":
        return len(name) > 0 and name[0] == name[0].upper() and name[0] != name[0].lower()
    if language == "rust":
        first = _line(lines, start_row)
        return first is not None and first.lstrip(_JS_WS).startswith("pub")
    return True


def _heading_level(signature_or_line: str) -> int:
    m = _HASHES.match(signature_or_line)
    return len(m.group(1)) if m else 1


def build_symbols(matches: list[list[_Cap]], lines: list[str], language: str) -> tuple[list[CodeSymbol], list[str]]:
    """Turn raw captures into symbols (containers holding their members) and import statements."""
    symbols: list[CodeSymbol] = []
    imports: list[str] = []
    export_ranges: list[tuple[int, int]] = []
    containers: list[tuple[CodeSymbol, int, int]] = []

    for match in matches:
        for cap in match:
            if cap.tag == "exp":
                export_ranges.append((cap.start_row, cap.end_row))
            if cap.tag == "imp":
                first = _line(lines, cap.start_row)
                imports.append(cap.text or (_trim(first) if first is not None else "") or "")

    for match in matches:
        kind_cap = next((c for c in match if c.tag in KIND_MAP), None)
        name_cap = next((c for c in match if c.tag == "name"), None)
        if kind_cap is None:
            continue
        start, end = kind_cap.start_row, kind_cap.end_row
        kind = KIND_MAP[kind_cap.tag]
        name = (name_cap.text if name_cap else None) or "anonymous"

        if language == "markdown" and kind == "section":
            signature = f"{'#' * _heading_level(_line(lines, start) or '')} {name}"
        elif language == "markdown" and kind == "code":
            lang_tag = name if name != "anonymous" else ""
            signature = "```" + lang_tag if lang_tag else "```"
        elif language == "markdown" and kind == "metadata":
            signature = "---frontmatter---"
        elif language == "markdown" and kind == "reference":
            first = _line(lines, start)
            signature = (_trim(first) if first is not None else "") or name
        else:
            signature = _extract_signature(lines, start, end)

        comment = None if language == "markdown" else _find_comment_above(lines, start)
        docstring = _find_python_docstring(lines, start, end) if language == "python" else None
        sym = CodeSymbol(name=name, kind=kind, signature=signature, jsdoc=comment or docstring, line_start=start,
                         line_end=end, exported=_is_exported(name, start, end, export_ranges, lines, language))
        if kind in CONTAINER_KINDS:
            sym.children = []
            containers.append((sym, start, end))
        symbols.append(sym)

    if language == "markdown":
        by_range: dict[tuple[int, int], CodeSymbol] = {}
        duplicates: set[int] = set()
        for sym in symbols:
            if sym.kind != "code":
                continue
            key = (sym.line_start, sym.line_end)
            existing = by_range.get(key)
            if existing is not None:
                if sym.name != "anonymous":
                    duplicates.add(id(existing))
                    by_range[key] = sym
                else:
                    duplicates.add(id(sym))
            else:
                by_range[key] = sym
        if duplicates:
            symbols = [s for s in symbols if id(s) not in duplicates]

    nested: set[int] = set()
    for container, c_start, c_end in containers:
        for sym in symbols:
            if sym is container:
                continue
            if sym.line_start > c_start and sym.line_end <= c_end:
                if sym.kind == "function":
                    sym.kind = "method"
                sym.parent = container.name
                container.children.append(sym)  # type: ignore[union-attr]
                nested.add(id(sym))
    return [s for s in symbols if id(s) not in nested], imports


# ---- parsing -------------------------------------------------------------------------------------
def _folded(file_path: str, language: str, lines: list[str], matches: list[list[_Cap]] | None) -> FoldedFile:
    if matches is None:
        return FoldedFile(file_path, language, [], [], len(lines), 50)
    symbols, imports = build_symbols(matches, lines, language)
    view = format_folded_view(FoldedFile(file_path, language, symbols, imports, len(lines), 0))
    return FoldedFile(file_path, language, symbols, imports, len(lines), math.ceil(_js_len(view) / 4))


def parse_file(content: str, file_path: str) -> FoldedFile:
    """The folded structure of one file (no symbols for a language this reader does not know)."""
    language = detect_language(file_path)
    return _folded(file_path, language, content.split("\n"), _extract_matches(content, language))


def parse_files_batch(files: list[dict]) -> dict[str, FoldedFile]:
    """Parse ``[{absolute_path, relative_path, content}]``, keyed by relative path (grouped by language)."""
    groups: dict[str, list[dict]] = {}
    for f in files:
        groups.setdefault(detect_language(f["relative_path"]), []).append(f)
    results: dict[str, FoldedFile] = {}
    for language, group in groups.items():
        for f in group:
            content = f["content"]
            results[f["relative_path"]] = _folded(f["relative_path"], language, content.split("\n"),
                                                  _extract_matches(content, language))
    return results


# ---- folded views ----------------------------------------------------------------------------------
_ICONS = {
    "function": "ƒ", "method": "ƒ", "class": "◆", "interface": "◇", "type": "◇", "const": "●", "variable": "○",
    "export": "→", "struct": "◆", "enum": "▣", "trait": "◇", "impl": "◈", "property": "○", "getter": "⇢",
    "setter": "⇠", "mixin": "◈", "section": "§", "code": "⌘", "metadata": "◊", "reference": "↗",
}


def _symbol_icon(kind: str) -> str:
    return _ICONS.get(kind, "·")


def _line_range(sym: CodeSymbol) -> str:
    return f"L{sym.line_start + 1}" if sym.line_start == sym.line_end else f"L{sym.line_start + 1}-{sym.line_end + 1}"


_LEAD_MARKS = re.compile(r"^[\s*/]+")


def _strip_doc_marks(line: str) -> str:
    return re.sub(r"^['\"`]{3}", "", _LEAD_MARKS.sub("", line))


def format_symbol(sym: CodeSymbol, indent: str) -> str:
    exported = " [exported]" if sym.exported else ""
    parts = [f"{indent}{_symbol_icon(sym.kind)} {sym.name}{exported} ({_line_range(sym)})",
             f"{indent}  {sym.signature}"]
    if sym.jsdoc:
        first = next((ln for ln in sym.jsdoc.split("\n")
                      if (t := _trim(_strip_doc_marks(ln))) and not t.startswith("/**")), None)
        if first is not None:
            cleaned = _trim(re.sub(r"['\"`]{3}$", "", _strip_doc_marks(first)))
            if cleaned:
                parts.append(f"{indent}  💬 {cleaned}")
    for child in sym.children or []:
        parts.append(format_symbol(child, indent + "  "))
    return "\n".join(parts)


def _containing_heading_level(symbols: list[CodeSymbol], line_start: int) -> int:
    best = 0
    for sym in symbols:
        if sym.kind == "section" and sym.line_start < line_start:
            best = _heading_level(sym.signature)
    return best


def _format_markdown_folded_view(file: FoldedFile) -> str:
    col = 56
    parts = [f"📄 {file.file_path} ({file.language}, {file.total_lines} lines)"]
    for sym in file.symbols:
        if sym.kind == "section":
            content = f"{'  ' * _heading_level(sym.signature)}{sym.signature}"
            parts.append(f"{_pad_end(content, col)}L{sym.line_start + 1}")
        elif sym.kind == "code":
            indent = "  " * (_containing_heading_level(file.symbols, sym.line_start) + 1)
            parts.append(f"{_pad_end(indent + sym.signature, col)}{_line_range(sym)}")
        elif sym.kind == "metadata":
            parts.append(f"{_pad_end('  ' + sym.signature, col)}{_line_range(sym)}")
        elif sym.kind == "reference":
            indent = "  " * (_containing_heading_level(file.symbols, sym.line_start) + 1)
            parts.append(f"{_pad_end(f'{indent}↗ {sym.name}', col)}L{sym.line_start + 1}")
    return "\n".join(parts)


def format_folded_view(file: FoldedFile) -> str:
    """The outline of a file: imports, then every symbol with its signature and first doc line."""
    if file.language == "markdown":
        return _format_markdown_folded_view(file)
    parts = [f"📁 {file.file_path} ({file.language}, {file.total_lines} lines)", ""]
    if file.imports:
        parts.append(f"  📦 Imports: {len(file.imports)} statements")
        for imp in file.imports[:10]:
            parts.append(f"    {imp}")
        if len(file.imports) > 10:
            parts.append(f"    ... +{len(file.imports) - 10} more")
        parts.append("")
    for sym in file.symbols:
        parts.append(format_symbol(sym, "  "))
    return "\n".join(parts)


# ---- unfolding -----------------------------------------------------------------------------------
_UNFOLD_PREFIXES = ("*", "/**", "///", "//", "#", "@")


def unfold_symbol(content: str, file_path: str, symbol_name: str) -> str | None:
    """The full source of the first symbol named ``symbol_name`` (with the comments above it), or None."""
    file = parse_file(content, file_path)

    def find(symbols: list[CodeSymbol]) -> CodeSymbol | None:
        for sym in symbols:
            if sym.name == symbol_name:
                return sym
            if sym.children:
                hit = find(sym.children)
                if hit is not None:
                    return hit
        return None

    symbol = find(file.symbols)
    if symbol is None:
        return None
    lines = content.split("\n")

    if file.language == "markdown" and symbol.kind == "section":
        level = _heading_level(symbol.signature)
        start = symbol.line_start
        end = len(lines) - 1
        for sym in file.symbols:
            if sym.kind == "section" and sym.line_start > start and _heading_level(sym.signature) <= level:
                end = sym.line_start - 1
                while end > start and _trim(lines[end]) == "":
                    end -= 1
                break
        extracted = "\n".join(lines[start:end + 1])
        return f"<!-- 📍 {file_path} L{start + 1}-{end + 1} -->\n{extracted}"

    start = symbol.line_start
    for i in range(symbol.line_start - 1, -1, -1):
        t = _trim(lines[i])
        if t == "" or t.startswith(_UNFOLD_PREFIXES) or t == "*/":
            start = i
        else:
            break
    extracted = "\n".join(lines[start:symbol.line_end + 1])
    return f"// 📍 {file_path} L{start + 1}-{symbol.line_end + 1}\n{extracted}"


# ---- codebase search -------------------------------------------------------------------------------
CODE_EXTENSIONS = frozenset({
    ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".py", ".pyw", ".go", ".rs", ".rb", ".java", ".cs",
    ".cpp", ".cc", ".cxx", ".c", ".h", ".hpp", ".hh", ".swift", ".kt", ".kts", ".php", ".vue", ".svelte", ".lua",
    ".scala", ".sc", ".sh", ".bash", ".zsh", ".hs", ".zig", ".css", ".scss", ".toml", ".yml", ".yaml", ".sql",
    ".md", ".mdx",
})

IGNORE_DIRS = frozenset({
    "node_modules", ".git", "dist", "build", ".next", "__pycache__", ".venv", "venv", "env", ".env", "target",
    "vendor", ".cache", ".turbo", "coverage", ".nyc_output", ".claude", ".cairn",
})

MAX_FILE_SIZE = 512 * 1024


def _walk_dir(directory: str, max_depth: int = 20) -> Iterator[str]:
    """Code files under ``directory`` (hidden entries, ignored folders and symlinks are skipped)."""
    if max_depth <= 0:
        return
    try:
        with os.scandir(directory) as it:
            entries = sorted(it, key=lambda e: e.name)
    except OSError as exc:
        log.debug("smart search: cannot read directory %s (%s)", directory, exc)
        return
    for entry in entries:
        if entry.name.startswith(".") or entry.name in IGNORE_DIRS:
            continue
        try:
            if entry.is_dir(follow_symlinks=False):
                yield from _walk_dir(entry.path, max_depth - 1)
            elif entry.is_file(follow_symlinks=False) and _ext(entry.name) in CODE_EXTENSIONS:
                yield entry.path
        except OSError:
            continue


def _safe_read_file(path: str) -> str | None:
    """File text, or None when it is empty, larger than 512 KB, binary or unreadable."""
    try:
        size = os.stat(path).st_size
        if size > MAX_FILE_SIZE or size == 0:
            return None
        with open(path, encoding="utf-8", errors="replace", newline="") as fh:
            content = fh.read()
    except OSError as exc:
        log.debug("smart search: cannot read %s (%s)", path, exc)
        return None
    if "\0" in content[:1000]:
        return None
    return content


def _query_parts(query: str) -> list[str]:
    return [p for p in re.split(r"[\s_\-./]+", query.lower()) if p]


def match_score(text: str, query_parts: list[str]) -> int:
    """10 per exact part, 5 per contained part, 1 per part whose letters appear in order."""
    score = 0
    for part in query_parts:
        if text == part:
            score += 10
        elif part in text:
            score += 5
        else:
            ti = matched = 0
            for ch in part:
                idx = text.find(ch, ti)
                if idx != -1:
                    matched += 1
                    ti = idx + 1
            if matched == _js_len(part):
                score += 1
    return score


def _count_symbols(file: FoldedFile) -> int:
    return len(file.symbols) + sum(len(s.children) for s in file.symbols if s.children)


def _symbol_matches(symbols: list[CodeSymbol], rel: str, parts: list[str], query_lower: str,
                    parent: str | None = None) -> list[SymbolMatch]:
    """Symbols (children included, named ``Parent.child``) matching by name, signature or doc comment."""
    found: list[SymbolMatch] = []
    for sym in symbols:
        score, reason = 0, ""
        name_score = match_score(sym.name.lower(), parts)
        if name_score > 0:
            score += name_score * 3
            reason = "name match"
        if query_lower in sym.signature.lower():
            score += 2
            reason = f"{reason} + signature" if reason else "signature match"
        if sym.jsdoc and query_lower in sym.jsdoc.lower():
            score += 1
            reason = f"{reason} + jsdoc" if reason else "jsdoc match"
        if score > 0:
            found.append(SymbolMatch(rel, f"{parent}.{sym.name}" if parent else sym.name, sym.kind, sym.signature,
                                     sym.jsdoc, sym.line_start, sym.line_end, reason))
        if sym.children:
            found.extend(_symbol_matches(sym.children, rel, parts, query_lower, sym.name))
    return found


def search_codebase(root_dir: str, query: str, *, max_results: int | None = None, include_imports: bool = False,
                    file_pattern: str | None = None) -> SearchResult:
    """Symbols under ``root_dir`` matching ``query`` (name, signature, doc comment), best name match first."""
    limit = int(max_results or 20)
    query_lower = query.lower()
    parts = _query_parts(query)
    to_parse: list[dict] = []
    for path in _walk_dir(root_dir, 20):
        rel = os.path.relpath(path, root_dir).replace(os.sep, "/")
        if file_pattern and file_pattern.lower() not in rel.lower():
            continue
        content = _safe_read_file(path)
        if not content:
            continue
        to_parse.append({"absolute_path": path, "relative_path": rel, "content": content})

    parsed_files = parse_files_batch(to_parse)
    folded_files: list[FoldedFile] = []
    matching: list[SymbolMatch] = []
    total_symbols = 0

    for rel, parsed in parsed_files.items():
        total_symbols += _count_symbols(parsed)
        file_matches = _symbol_matches(parsed.symbols, rel, parts, query_lower)
        if file_matches or match_score(rel.lower(), parts) > 0:
            folded_files.append(parsed)
            matching.extend(file_matches)

    matching.sort(key=lambda m: match_score(m.symbol_name.lower(), parts), reverse=True)
    trimmed = matching[:limit]
    relevant = {m.file_path for m in trimmed}
    files = [f for f in folded_files if f.file_path in relevant][:limit]
    return SearchResult(folded_files=files, matching_symbols=trimmed, total_files_scanned=len(to_parse),
                        total_symbols_found=total_symbols,
                        token_estimate=sum(f.folded_token_estimate for f in files))


def format_search_results(result: SearchResult, query: str) -> str:
    parts = [f'🔍 Smart Search: "{query}"',
             f"   Scanned {result.total_files_scanned} files, found {result.total_symbols_found} symbols",
             (f"   {len(result.matching_symbols)} matches across {len(result.folded_files)} files "
              f"(~{result.token_estimate} tokens for folded view)"), ""]
    if not result.matching_symbols:
        parts.append("   No matching symbols found.")
        return "\n".join(parts)
    parts += ["── Matching Symbols ──", ""]
    for m in result.matching_symbols:
        parts.append(f"  {m.kind} {m.symbol_name} ({m.file_path}:{m.line_start + 1})")
        parts.append(f"    {m.signature}")
        if m.jsdoc:
            first = next((ln for ln in m.jsdoc.split("\n") if _trim(_LEAD_MARKS.sub("", ln))), None)
            if first is not None:
                parts.append(f"    💬 {_trim(_LEAD_MARKS.sub('', first))}")
        parts.append("")
    parts += ["── Folded File Views ──", ""]
    for f in result.folded_files:
        parts.append(format_folded_view(f))
        parts.append("")
    parts.append("── Actions ──")
    parts.append("  To see full implementation: use smart_unfold with file path and symbol name")
    return "\n".join(parts)


# ---- workspace jail --------------------------------------------------------------------------------
def _expand_leading_tilde(file_path: str) -> str:
    if file_path == "~":
        return str(Path.home())
    if file_path.startswith(("~/", "~\\")):
        return os.path.join(str(Path.home()), file_path[2:])
    return file_path


def resolve_within_workspace(file_path: Any, workspace_cwd: str | os.PathLike | None = None) -> str:
    """Resolve a caller-supplied path and refuse anything outside the workspace.

    Both sides are resolved through symlinks before the comparison, so a link pointing out of the
    workspace is refused too. A target that does not exist keeps its lexical path (the caller then
    gets a natural file-not-found), but only once that path is known not to escape."""
    if not isinstance(file_path, str) or not file_path.strip():
        raise ValueError("file_path is required")
    root = os.path.realpath(os.path.abspath(os.fspath(workspace_cwd) if workspace_cwd is not None
                                            else os.getcwd()), strict=True)
    lexical = os.path.normpath(os.path.join(root, _expand_leading_tilde(file_path.strip())))
    try:
        resolved = os.path.realpath(lexical, strict=True)
    except OSError:
        resolved = lexical
    if resolved != root and not resolved.startswith(root + os.sep):
        raise PermissionError(f'Access denied: "{file_path}" resolves outside the workspace ({root}). '
                              "MCP file tools can only read files within the current project.")
    return resolved


# ---- agent tools -----------------------------------------------------------------------------------
TOOLS: list[dict] = [
    {
        "name": "smart_search",
        "description": "Search codebase for symbols, functions, classes using tree-sitter AST parsing. Returns folded "
                       "structural views with token counts. Use path parameter to scope the search.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string",
                          "description": "Search term — matches against symbol names, file names, and file content"},
                "path": {"type": "string",
                         "description": "Root directory to search (default: current working directory)"},
                "max_results": {"type": "number", "description": "Maximum results to return (default: 20)"},
                "file_pattern": {"type": "string",
                                 "description": 'Substring filter for file paths (e.g. ".ts", "src/services")'},
            },
            "required": ["query"],
        },
    },
    {
        "name": "smart_unfold",
        "description": "Expand a specific symbol (function, class, method) from a file. Returns the full source code "
                       "of just that symbol. Use after smart_search or smart_outline to read specific code.",
        "input_schema": {
            "type": "object",
            "properties": {
                "file_path": {"type": "string", "description": "Path to the source file"},
                "symbol_name": {"type": "string",
                                "description": "Name of the symbol to unfold (function, class, method, etc.)"},
            },
            "required": ["file_path", "symbol_name"],
        },
    },
    {
        "name": "smart_outline",
        "description": "Get structural outline of a file — shows all symbols (functions, classes, methods, types) "
                       "with signatures but bodies folded. Much cheaper than reading the full file.",
        "input_schema": {
            "type": "object",
            "properties": {"file_path": {"type": "string", "description": "Path to the source file"}},
            "required": ["file_path"],
        },
    },
]


def _read_text(path: str) -> str:
    with open(path, encoding="utf-8", errors="replace", newline="") as fh:
        return fh.read()


def tool_smart_search(args: dict, cwd: str) -> str:
    query = args.get("query")
    if not isinstance(query, str):
        raise ValueError("query is required")
    root = resolve_within_workspace(args.get("path") or cwd, cwd)
    result = search_codebase(root, query, max_results=args.get("max_results") or 20,
                             file_pattern=args.get("file_pattern"))
    return format_search_results(result, query)


def tool_smart_unfold(args: dict, cwd: str) -> str:
    path = resolve_within_workspace(args.get("file_path"), cwd)
    content = _read_text(path)
    unfolded = unfold_symbol(content, path, str(args.get("symbol_name")))
    if unfolded:
        return unfolded
    parsed = parse_file(content, path)
    if parsed.symbols:
        available = "\n".join(f"  - {s.name} ({s.kind})" for s in parsed.symbols)
        return (f'Symbol "{args.get("symbol_name")}" not found in {args.get("file_path")}.\n\n'
                f"Available symbols:\n{available}")
    return f"Could not parse {args.get('file_path')}. File may be unsupported or empty."


def tool_smart_outline(args: dict, cwd: str) -> str:
    path = resolve_within_workspace(args.get("file_path"), cwd)
    parsed = parse_file(_read_text(path), path)
    if parsed.symbols:
        return format_folded_view(parsed)
    return f"Could not parse {args.get('file_path')}. File may use an unsupported language or be empty."


TOOL_HANDLERS: dict[str, Callable[[dict, str], str]] = {
    "smart_search": tool_smart_search,
    "smart_unfold": tool_smart_unfold,
    "smart_outline": tool_smart_outline,
}


def call_tool(name: str, args: dict | None, cwd: str) -> dict:
    """Run one tool as the agent-tool server does: ``{"text", "is_error"}``, errors reported as text."""
    handler = TOOL_HANDLERS.get(name)
    if handler is None:
        raise KeyError(f"Unknown tool: {name}")
    try:
        return {"text": handler(args or {}, cwd), "is_error": False}
    except Exception as exc:
        log.error("Tool execution failed (%s): %s", name, exc)
        return {"text": f"Tool execution failed: {exc}", "is_error": True}
