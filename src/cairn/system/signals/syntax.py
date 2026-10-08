"""Language facts for the system model, read from the tree-sitter grammars Cairn already bundles.

The graph engine keeps decorator and annotation *names* but not their arguments, and has no notion of
call arguments at all, so this module re-parses the files that can matter (see ``wanted``) and reduces
each one to a small, language-neutral record:

- ``Call``: every call, constructor and Go composite literal, with its dotted callee, positional and
  keyword arguments (as ``Val``), the enclosing function and the fluent chain before it;
- ``Deco``: Python decorators and Java annotations with their arguments and target;
- bindings (``name = <value>``, typed fields and parameters), imports, function signatures, exports,
  class bases, SQL-looking string literals and whether the file is a program entry point.

Catalog rules (``cairn.system.catalog``) are matched against these records, never against raw text.
Language knowledge kept here, because it is syntax rather than catalog data: how each grammar spells a
call, an import, a binding, a string or template, and how each language reads an environment variable.
"""
from __future__ import annotations

import importlib
import re
import threading
from dataclasses import dataclass, field

LANG_BY_EXT = {
    ".py": "python", ".js": "javascript", ".mjs": "javascript", ".cjs": "javascript", ".jsx": "javascript",
    ".ts": "typescript", ".mts": "typescript", ".cts": "typescript", ".tsx": "tsx", ".java": "java", ".go": "go",
    ".cs": "csharp", ".sh": "bash", ".bash": "bash",
}
# Catalog rules name a language family; TypeScript and TSX read like JavaScript.
FAMILY = {"python": "python", "javascript": "javascript", "typescript": "javascript", "tsx": "javascript",
          "java": "java", "go": "go", "csharp": "csharp", "bash": "shell"}
_GRAMMAR = {
    "python": ("tree_sitter_python", "language"), "javascript": ("tree_sitter_javascript", "language"),
    "typescript": ("tree_sitter_typescript", "language_typescript"),
    "tsx": ("tree_sitter_typescript", "language_tsx"), "java": ("tree_sitter_java", "language"),
    "go": ("tree_sitter_go", "language"), "csharp": ("tree_sitter_c_sharp", "language"),
    "bash": ("tree_sitter_bash", "language"),
}
# How each language reads an environment variable (call forms; JS also reads ``process.env.X``).
ENV_CALLS = {
    "python": {"os.environ.get", "os.getenv", "environ.get", "getenv", "os.environ.setdefault"},
    "javascript": set(), "java": {"System.getenv"}, "go": {"os.Getenv", "os.LookupEnv"},
    "csharp": {"Environment.GetEnvironmentVariable"}, "shell": set(),
}
ENV_SUBSCRIPTS = {"os.environ", "environ", "process.env"}
_SQL = re.compile(r"^\s*\(?\s*(select|insert|update|delete|with|replace|merge|upsert)\s", re.I)  # SQL starts so
MAX_STR = 400          # string literals kept for SQL scanning are cut to this
MAX_CALLS = 20_000     # per file


@dataclass
class Val:
    """An expression reduced to what the rules need.

    kind: ``str`` (text is the literal), ``tmpl`` (parts of ("lit", s) / ("var", name)), ``name`` (dotted
    name), ``env`` (text is the variable name), ``config`` (a configuration key, e.g. Spring ``@Value``),
    ``call`` (text is the callee; ``call`` holds it), ``obj`` (``fields``), ``list`` (``items``),
    ``module`` (a JS ``require``), ``type`` (a declared type), ``other``.
    """
    kind: str
    text: str = ""
    parts: tuple = ()
    fields: dict = field(default_factory=dict)
    items: tuple = ()
    call: "Call | None" = None


@dataclass
class Call:
    line: int
    callee: str                       # dotted, calls in a chain rendered "a.b().c"
    args: list = field(default_factory=list)
    kwargs: dict = field(default_factory=dict)
    func: str | None = None           # enclosing function
    new: bool = False                 # constructor or composite literal
    end_line: int = 0

    @property
    def name(self) -> str:
        return self.callee.rsplit(".", 1)[-1].rstrip("()")

    @property
    def recv(self) -> str:
        return self.callee.rsplit(".", 1)[0] if "." in self.callee else ""

    @property
    def chain(self) -> tuple[str, ...]:
        """Method names called earlier in a fluent chain: ``w.post().uri`` -> ("post",)."""
        segs = self.callee.split(".")[:-1]
        return tuple(s[:-2] for s in segs if s.endswith("()"))


@dataclass
class Deco:
    line: int
    name: str
    args: list = field(default_factory=list)
    kwargs: dict = field(default_factory=dict)
    target: str = ""
    target_kind: str = "function"     # function | method | class | interface | field
    cls: str | None = None
    cls_decos: list = field(default_factory=list)
    cls_kind: str | None = None       # class | interface


@dataclass
class FileFacts:
    path: str
    lang: str
    calls: list = field(default_factory=list)
    decos: list = field(default_factory=list)
    binds: dict = field(default_factory=dict)       # name -> [Val] (``self.x`` and ``this.x`` keep the prefix)
    imports: dict = field(default_factory=dict)     # local name -> origin ("redis.Redis", "./routes/orders", Go path)
    namespaces: set = field(default_factory=set)    # C# ``using`` namespaces
    funcs: dict = field(default_factory=dict)       # name -> (params, line, cls)
    exports: set = field(default_factory=set)       # JS named exports
    default_export: str | None = None
    bases: dict = field(default_factory=dict)       # class/interface -> ([base type names], line)
    env_reads: set = field(default_factory=set)     # (line, name) of environment / configuration reads
    sql: list = field(default_factory=list)         # (line, text) of SQL-looking string literals
    commands: list = field(default_factory=list)    # shell: (line, name, [args])
    is_main: bool = False
    package: str | None = None
    error: str | None = None
    qcache: dict = field(default_factory=dict)

    @property
    def family(self) -> str:
        return FAMILY.get(self.lang, self.lang)


_parsers: dict[str, object] = {}
_lock = threading.Lock()


def parser_for(lang: str):
    """A cached tree-sitter parser, or None when the grammar is not installed."""
    with _lock:
        if lang in _parsers:
            return _parsers[lang]
        p = None
        try:
            from tree_sitter import Language, Parser
            mod_name, fn = _GRAMMAR[lang]
            p = Parser(Language(getattr(importlib.import_module(mod_name), fn)()))
        except Exception:  # noqa: BLE001 — a missing or mismatched grammar only loses that language
            p = None
        _parsers[lang] = p
        return p


def lang_of(path: str) -> str | None:
    dot = path.rfind(".")
    return LANG_BY_EXT.get(path[dot:].lower()) if dot >= 0 else None


def parse(path: str, text: str, lang: str | None = None) -> FileFacts:
    lang = lang or lang_of(path) or ""
    facts = FileFacts(path=path, lang=lang)
    p = parser_for(lang) if lang in _GRAMMAR else None
    if p is None:
        facts.error = f"no parser for {lang or path}"
        return facts
    try:
        tree = p.parse(text.encode("utf-8", "replace"))
    except Exception as exc:  # noqa: BLE001
        facts.error = f"parse failed: {type(exc).__name__}"
        return facts
    walker = {"python": _Py, "javascript": _Js, "typescript": _Js, "tsx": _Js, "java": _Java, "go": _Go,
              "csharp": _Cs, "bash": _Sh}[lang](facts)
    try:
        walker.walk(tree.root_node)
    except RecursionError:
        facts.error = "nested too deeply"
    return facts


# ---------------------------------------------------------------------------------------------- helpers
def _t(node) -> str:
    return node.text.decode("utf-8", "replace") if node is not None else ""


def _line(node) -> int:
    return node.start_point[0] + 1


def _named(node):
    return [c for c in node.children if c.is_named]


class _Walker:
    FUNCS: tuple = ()
    CLASSES: tuple = ()
    TYPES: frozenset = frozenset()   # node types ``on`` handles; every other node is only descended into

    def __init__(self, facts: FileFacts):
        self.f = facts
        self.env_calls = ENV_CALLS.get(facts.family, set())
        self.func: str | None = None
        self.cls: str | None = None

    # -- traversal: iterative pre-order over named nodes (no recursion limit on deep files)
    def walk(self, root) -> None:
        on, types, funcs, classes = self.on, self.TYPES, self.FUNCS, self.CLASSES
        stack: list = [root]
        while stack:
            n = stack.pop()
            if n.__class__ is tuple:          # leaving a scope: restore it, run its exit hook
                if len(n) == 2:
                    self.func, self.cls = n
                else:
                    n[0]()
                continue
            t = n.type
            if t in types:
                r = on(n)
                if r == "skip":
                    continue
                if r is not None:
                    stack.append((r,))
            if t in funcs or t in classes:
                stack.append((self.func, self.cls))
                name = n.child_by_field_name("name")
                if name is not None:
                    if t in funcs:
                        self.func = _t(name)
                    else:
                        self.cls = _t(name)
            kids = n.named_children
            if kids:
                stack.extend(reversed(kids))

    def on(self, node):  # pragma: no cover - overridden
        return None

    # -- recording
    def add_call(self, line: int, callee: str, args, kwargs, *, new: bool = False, end: int = 0) -> Call:
        c = Call(line=line, callee=callee, args=list(args), kwargs=dict(kwargs), func=self.func, new=new,
                 end_line=end or line)
        if len(self.f.calls) < MAX_CALLS:
            self.f.calls.append(c)
        return c

    def bind(self, name: str, val: Val) -> None:
        if name and val is not None:
            vals = self.f.binds.setdefault(name, [])
            if len(vals) < 8:
                vals.append(val)

    def env(self, node, name: str) -> Val:
        if name and len(self.f.env_reads) < 5000:
            self.f.env_reads.add((_line(node), name))
        return Val("env", name)

    def string(self, node, text: str) -> None:
        if text and _SQL.search(text[:300]) and len(self.f.sql) < 2000:
            self.f.sql.append((_line(node), text[:MAX_STR]))


# ---------------------------------------------------------------------------------------------- Python
class _Py(_Walker):
    FUNCS = ("function_definition",)
    CLASSES = ("class_definition",)
    TYPES = frozenset({"call", "decorated_definition", "assignment", "import_statement", "import_from_statement",
                       "function_definition", "class_definition", "if_statement", "string"})

    def on(self, n):
        t = n.type
        if t == "call":
            fn = n.child_by_field_name("function")
            args, kwargs = self.arglist(n.child_by_field_name("arguments"))
            self.add_call(_line(n), self.dotted(fn), args, kwargs, end=n.end_point[0] + 1)
        elif t == "decorated_definition":
            self.decorated(n)
        elif t == "assignment":
            left, right = n.child_by_field_name("left"), n.child_by_field_name("right")
            if left is not None and right is not None:
                if left.type in ("identifier", "attribute"):
                    self.bind(_t(left), self.val(right))
                elif left.type in ("pattern_list", "tuple_pattern") and right.type in ("expression_list", "tuple"):
                    for a, b in zip(_named(left), _named(right)):
                        self.bind(_t(a), self.val(b))
        elif t == "import_statement":
            for c in _named(n):
                if c.type == "dotted_name":
                    self.f.imports[_t(c).split(".")[0]] = _t(c).split(".")[0]
                elif c.type == "aliased_import":
                    self.f.imports[_t(c.child_by_field_name("alias"))] = _t(c.child_by_field_name("name"))
        elif t == "import_from_statement":
            mod = _t(n.child_by_field_name("module_name"))
            for c in n.children_by_field_name("name"):
                if c.type == "aliased_import":
                    nm, al = _t(c.child_by_field_name("name")), _t(c.child_by_field_name("alias"))
                else:
                    nm = al = _t(c)
                self.f.imports[al] = f"{mod}.{nm}" if not mod.endswith(".") else f"{mod}{nm}"
        elif t == "function_definition":
            params = []
            ps = n.child_by_field_name("parameters")
            for p in _named(ps) if ps is not None else []:
                nm = p.child_by_field_name("name") if p.type != "identifier" else p
                if nm is None and p.named_child_count:
                    nm = p.named_children[0]
                params.append(_t(nm) if nm is not None else "")
            self.f.funcs.setdefault(_t(n.child_by_field_name("name")), (params, _line(n), self.cls))
        elif t == "class_definition":
            sup = n.child_by_field_name("superclasses")
            if sup is not None:
                self.f.bases[_t(n.child_by_field_name("name"))] = ([_t(x) for x in _named(sup)], _line(n))
        elif t == "if_statement":
            cond = _t(n.child_by_field_name("condition"))
            if "__name__" in cond and "__main__" in cond:
                self.f.is_main = True
        elif t == "string":
            self.string(n, self.val(n).text)
            return "skip"
        return None

    def decorated(self, n) -> None:
        d = n.child_by_field_name("definition")
        target = _t(d.child_by_field_name("name")) if d is not None else ""
        kind = "class" if d is not None and d.type == "class_definition" else ("method" if self.cls else "function")
        for c in _named(n):
            if c.type != "decorator":
                continue
            expr = c.named_children[0] if c.named_child_count else None
            if expr is None:
                continue
            if expr.type == "call":
                args, kwargs = self.arglist(expr.child_by_field_name("arguments"))
                name = self.dotted(expr.child_by_field_name("function"))
            else:
                args, kwargs, name = [], {}, self.dotted(expr)
            self.f.decos.append(Deco(line=_line(c), name=name, args=args, kwargs=kwargs, target=target,
                                     target_kind=kind, cls=self.cls))

    def arglist(self, node):
        args, kwargs = [], {}
        for c in _named(node) if node is not None else []:
            if c.type == "keyword_argument":
                kwargs[_t(c.child_by_field_name("name"))] = self.val(c.child_by_field_name("value"))
            elif c.type not in ("comment", "list_splat", "dictionary_splat"):
                args.append(self.val(c))
        return args, kwargs

    def dotted(self, n) -> str:
        if n is None:
            return "?"
        if n.type == "identifier":
            return _t(n)
        if n.type == "attribute":
            return self.dotted(n.child_by_field_name("object")) + "." + _t(n.child_by_field_name("attribute"))
        if n.type == "call":
            return self.dotted(n.child_by_field_name("function")) + "()"
        if n.type == "subscript":
            return self.dotted(n.child_by_field_name("value")) + "[]"
        if n.type in ("parenthesized_expression", "await"):
            return self.dotted(n.named_children[-1]) if n.named_child_count else "?"
        return "?"

    def val(self, n) -> Val:
        if n is None:
            return Val("other")
        t = n.type
        if t == "string":
            parts, lit = [], True
            for c in n.children:
                if c.type == "string_content":
                    parts.append(("lit", _t(c)))
                elif c.type == "interpolation":
                    lit = False
                    e = c.child_by_field_name("expression")
                    parts.append(("var", self.dotted(e) if e is not None else "?"))
                elif c.type == "escape_sequence":
                    parts.append(("lit", _t(c)))
            if lit:
                return Val("str", "".join(p[1] for p in parts))
            return Val("tmpl", _render(parts), parts=tuple(parts))
        if t == "concatenated_string":
            vals = [self.val(c) for c in _named(n)]
            return _concat(vals)
        if t in ("identifier", "attribute"):
            return Val("name", self.dotted(n))
        if t == "call":
            fn = self.dotted(n.child_by_field_name("function"))
            args, kwargs = self.arglist(n.child_by_field_name("arguments"))
            if fn in self.env_calls and args and args[0].kind == "str":
                return self.env(n, args[0].text)
            return Val("call", fn, call=Call(_line(n), fn, args, kwargs, self.func))
        if t == "subscript":
            base = self.dotted(n.child_by_field_name("value"))
            sub = n.child_by_field_name("subscript")
            sv = self.val(sub) if sub is not None else Val("other")
            if base in ENV_SUBSCRIPTS and sv.kind == "str":
                return self.env(n, sv.text)
            return Val("name", base + "[]")
        if t == "binary_operator":
            op = n.child_by_field_name("operator")
            if op is not None and _t(op) == "+":
                return _concat([self.val(n.child_by_field_name("left")), self.val(n.child_by_field_name("right"))])
            if op is not None and _t(op) == "%":
                left = self.val(n.child_by_field_name("left"))
                if left.kind == "str":
                    return _printf(left.text, [self.val(n.child_by_field_name("right"))])
        if t == "boolean_operator":
            return _prefer(self.val(n.child_by_field_name("left")), self.val(n.child_by_field_name("right")))
        if t == "dictionary":
            fields = {}
            for p in _named(n):
                if p.type == "pair":
                    k = self.val(p.child_by_field_name("key"))
                    if k.kind == "str":
                        fields[k.text] = self.val(p.child_by_field_name("value"))
            return Val("obj", fields=fields)
        if t in ("list", "tuple", "set"):
            return Val("list", items=tuple(self.val(c) for c in _named(n)))
        if t in ("parenthesized_expression", "await"):
            return self.val(n.named_children[-1]) if n.named_child_count else Val("other")
        if t == "integer":
            return Val("num", _t(n))
        return Val("other", t)


# ---------------------------------------------------------------------------------------------- JS / TS
class _Js(_Walker):
    FUNCS = ("function_declaration", "method_definition", "generator_function_declaration")
    CLASSES = ("class_declaration",)
    TYPES = frozenset({"call_expression", "new_expression", "variable_declarator", "assignment_expression",
                       "import_statement", "export_statement", "function_declaration", "method_definition",
                       "generator_function_declaration", "string", "template_string"})

    def on(self, n):
        t = n.type
        if t == "call_expression":
            fn = n.child_by_field_name("function")
            args = self.args(n.child_by_field_name("arguments"))
            if fn is not None and fn.type == "identifier" and _t(fn) == "require":
                return None
            if fn is not None and fn.type == "import":
                return None
            self.add_call(_line(n), self.dotted(fn), args, {}, end=n.end_point[0] + 1)
        elif t == "new_expression":
            ctor = n.child_by_field_name("constructor")
            self.add_call(_line(n), self.dotted(ctor), self.args(n.child_by_field_name("arguments")), {}, new=True,
                          end=n.end_point[0] + 1)
        elif t == "variable_declarator":
            name, value = n.child_by_field_name("name"), n.child_by_field_name("value")
            if name is None or value is None:
                return None
            v = self.val(value)
            if name.type == "identifier":
                self.bind(_t(name), v)
                if v.kind == "module":
                    self.f.imports[_t(name)] = v.text
                if value.type in ("arrow_function", "function_expression", "function"):
                    self.function_def(_t(name), value)
            elif name.type == "object_pattern" and v.kind == "module":
                for p in _named(name):
                    if p.type == "shorthand_property_identifier_pattern":
                        self.f.imports[_t(p)] = f"{v.text}.{_t(p)}"
                    elif p.type == "pair_pattern":
                        k, al = p.child_by_field_name("key"), p.child_by_field_name("value")
                        self.f.imports[_t(al)] = f"{v.text}.{_t(k)}"
        elif t == "assignment_expression":
            left, right = n.child_by_field_name("left"), n.child_by_field_name("right")
            lt = _t(left)
            if lt == "module.exports" and right is not None:
                if right.type == "identifier":
                    self.f.default_export = _t(right)
                elif right.type == "object":
                    for p in _named(right):
                        if p.type == "shorthand_property_identifier":
                            self.f.exports.add(_t(p))
            elif lt.startswith(("exports.", "module.exports.")):
                self.f.exports.add(lt.rsplit(".", 1)[-1])
            elif left is not None and left.type in ("identifier", "member_expression"):
                self.bind(lt, self.val(right))
        elif t == "import_statement":
            src = n.child_by_field_name("source")
            mod = self.val(src).text if src is not None else ""
            for c in _named(n):
                if c.type != "import_clause":
                    continue
                for x in _named(c):
                    if x.type == "identifier":
                        self.f.imports[_t(x)] = mod
                    elif x.type == "namespace_import":
                        for y in _named(x):
                            self.f.imports[_t(y)] = mod
                    elif x.type == "named_imports":
                        for s in _named(x):
                            if s.type == "import_specifier":
                                nm, al = s.child_by_field_name("name"), s.child_by_field_name("alias")
                                self.f.imports[_t(al or nm)] = f"{mod}.{_t(nm)}"
        elif t == "export_statement":
            d = n.child_by_field_name("declaration")
            if d is not None:
                if d.type in ("function_declaration", "generator_function_declaration", "class_declaration"):
                    self.f.exports.add(_t(d.child_by_field_name("name")))
                elif d.type in ("lexical_declaration", "variable_declaration"):
                    for v in _named(d):
                        if v.type == "variable_declarator":
                            self.f.exports.add(_t(v.child_by_field_name("name")))
            elif any(c.type == "default" for c in n.children):
                v = n.child_by_field_name("value")
                if v is not None and v.type == "identifier":
                    self.f.default_export = _t(v)
        elif t in ("function_declaration", "method_definition", "generator_function_declaration"):
            self.function_def(_t(n.child_by_field_name("name")), n)
        elif t in ("string", "template_string"):
            v = self.val(n)
            self.string(n, v.text if v.kind == "str" else "".join(p[1] for p in v.parts if p[0] == "lit"))
            return None if t == "template_string" else "skip"
        return None

    def function_def(self, name: str, n) -> None:
        ps = n.child_by_field_name("parameters")
        params = []
        for p in _named(ps) if ps is not None else []:
            nm = p if p.type == "identifier" else (p.child_by_field_name("pattern") or p.child_by_field_name("left"))
            params.append(_t(nm) if nm is not None else "")
        self.f.funcs.setdefault(name, (params, _line(n), self.cls))

    def args(self, node):
        return [self.val(c) for c in _named(node)] if node is not None and node.type == "arguments" else (
            [self.val(node)] if node is not None else [])

    def dotted(self, n) -> str:
        if n is None:
            return "?"
        t = n.type
        if t in ("identifier", "property_identifier", "this", "super"):
            return _t(n)
        if t == "member_expression":
            return self.dotted(n.child_by_field_name("object")) + "." + _t(n.child_by_field_name("property"))
        if t == "call_expression":
            fn = n.child_by_field_name("function")
            if fn is not None and fn.type == "identifier" and _t(fn) == "require":
                a = self.args(n.child_by_field_name("arguments"))
                return a[0].text if a and a[0].kind == "str" else "?"
            return self.dotted(fn) + "()"
        if t == "new_expression":
            return self.dotted(n.child_by_field_name("constructor")) + "()"
        if t in ("await_expression", "parenthesized_expression", "non_null_expression"):
            return self.dotted(n.named_children[-1]) if n.named_child_count else "?"
        if t == "subscript_expression":
            return self.dotted(n.child_by_field_name("object")) + "[]"
        return "?"

    def val(self, n) -> Val:
        if n is None:
            return Val("other")
        t = n.type
        if t == "string":
            return Val("str", "".join(_t(c) for c in n.children if c.type in ("string_fragment", "escape_sequence")))
        if t == "template_string":
            parts = []
            for c in n.children:
                if c.type in ("string_fragment", "escape_sequence"):
                    parts.append(("lit", _t(c)))
                elif c.type == "template_substitution":
                    inner = c.named_children[0] if c.named_child_count else None
                    iv = self.val(inner) if inner is not None else Val("other")
                    parts.append(("env", iv.text) if iv.kind == "env" else ("var", self.dotted(inner)))
            if all(p[0] == "lit" for p in parts):
                return Val("str", "".join(p[1] for p in parts))
            return Val("tmpl", _render(parts), parts=tuple(parts))
        if t == "member_expression":
            obj = n.child_by_field_name("object")
            if obj is not None and _t(obj) in ("process.env", "import.meta.env"):
                return self.env(n, _t(n.child_by_field_name("property")))
            return Val("name", self.dotted(n))
        if t == "subscript_expression":
            obj, idx = n.child_by_field_name("object"), n.child_by_field_name("index")
            iv = self.val(idx) if idx is not None else Val("other")
            if obj is not None and _t(obj) in ENV_SUBSCRIPTS and iv.kind == "str":
                return self.env(n, iv.text)
            return Val("name", self.dotted(n))
        if t in ("identifier", "this"):
            return Val("name", _t(n))
        if t == "call_expression":
            fn = n.child_by_field_name("function")
            args = self.args(n.child_by_field_name("arguments"))
            if fn is not None and fn.type == "identifier" and _t(fn) == "require":
                return Val("module", args[0].text) if args and args[0].kind == "str" else Val("other")
            callee = self.dotted(fn)
            return Val("call", callee, call=Call(_line(n), callee, args, {}, self.func))
        if t == "new_expression":
            callee = self.dotted(n.child_by_field_name("constructor"))
            args = self.args(n.child_by_field_name("arguments"))
            return Val("call", callee, call=Call(_line(n), callee, args, {}, self.func, new=True))
        if t == "binary_expression":
            op = n.child_by_field_name("operator")
            ops = _t(op) if op is not None else ""
            if ops == "+":
                return _concat([self.val(n.child_by_field_name("left")), self.val(n.child_by_field_name("right"))])
            if ops in ("||", "??"):
                return _prefer(self.val(n.child_by_field_name("left")), self.val(n.child_by_field_name("right")))
        if t == "object":
            fields = {}
            for p in _named(n):
                if p.type == "pair":
                    k = p.child_by_field_name("key")
                    key = self.val(k).text if k is not None and k.type == "string" else _t(k)
                    fields[key] = self.val(p.child_by_field_name("value"))
                elif p.type == "shorthand_property_identifier":
                    fields[_t(p)] = Val("name", _t(p))
            return Val("obj", fields=fields)
        if t == "array":
            return Val("list", items=tuple(self.val(c) for c in _named(n)))
        if t in ("await_expression", "parenthesized_expression", "non_null_expression", "as_expression",
                 "satisfies_expression"):
            return self.val(n.named_children[0]) if n.named_child_count else Val("other")
        if t == "number":
            return Val("num", _t(n))
        return Val("other", t)


# ---------------------------------------------------------------------------------------------- Java
class _Java(_Walker):
    FUNCS = ("method_declaration", "constructor_declaration")
    CLASSES = ("class_declaration", "interface_declaration", "enum_declaration", "record_declaration")
    TYPES = frozenset({"method_invocation", "object_creation_expression", "import_declaration", "method_declaration",
                       "constructor_declaration", "field_declaration", "local_variable_declaration",
                       "assignment_expression", "interface_declaration", "class_declaration", "record_declaration",
                       "package_declaration", "string_literal", "text_block"})

    def __init__(self, facts):
        super().__init__(facts)
        self.cls_stack: list[tuple[str, str, list]] = []

    def enter_class(self, node):
        """Class annotations (``@RequestMapping``, ``@FeignClient``) apply to its methods until it ends."""
        name = _t(node.child_by_field_name("name"))
        kind = "interface" if node.type == "interface_declaration" else "class"
        decos = self.annotations(node, name, kind, None, None)
        self.f.decos.extend(decos)
        self.cls_stack.append((name, kind, decos))
        return self.cls_stack.pop

    def annotations(self, node, target, kind, cls, cls_decos):
        out = []
        for m in node.children:
            if m.type != "modifiers":
                continue
            for a in m.children:
                if a.type not in ("annotation", "marker_annotation"):
                    continue
                args, kwargs = [], {}
                al = a.child_by_field_name("arguments")
                for x in _named(al) if al is not None else []:
                    if x.type == "element_value_pair":
                        kwargs[_t(x.child_by_field_name("key"))] = self.val(x.child_by_field_name("value"))
                    else:
                        args.append(self.val(x))
                name = _t(a.child_by_field_name("name"))
                out.append(Deco(line=_line(a), name=name, args=args, kwargs=kwargs, target=target, target_kind=kind,
                                cls=cls, cls_decos=list(cls_decos or []),
                                cls_kind=self.cls_stack[-1][1] if self.cls_stack else None))
        return out

    def on(self, n):
        t = n.type
        exit_hook = self.enter_class(n) if t in ("class_declaration", "interface_declaration",
                                                  "record_declaration") else None
        self.java_node(n, t)
        return exit_hook if t not in ("string_literal", "text_block") else "skip"

    def java_node(self, n, t):
        if t == "method_invocation":
            obj = n.child_by_field_name("object")
            callee = (self.dotted(obj) + "." if obj is not None else "") + _t(n.child_by_field_name("name"))
            self.add_call(_line(n), callee, self.args(n.child_by_field_name("arguments")), {},
                          end=n.end_point[0] + 1)
        elif t == "object_creation_expression":
            self.add_call(_line(n), self.typename(n.child_by_field_name("type")),
                          self.args(n.child_by_field_name("arguments")), {}, new=True, end=n.end_point[0] + 1)
        elif t == "import_declaration":
            path = next((_t(c) for c in _named(n) if c.type in ("scoped_identifier", "identifier")), "")
            if path and not any(c.type == "asterisk" for c in n.children):
                self.f.imports[path.rsplit(".", 1)[-1]] = path
            elif path:
                self.f.namespaces.add(path)
        elif t in ("method_declaration", "constructor_declaration"):
            name = _t(n.child_by_field_name("name"))
            cls = self.cls_stack[-1] if self.cls_stack else (None, None, [])
            for d in self.annotations(n, name, "method", cls[0], cls[2]):
                self.f.decos.append(d)
            params = []
            ps = n.child_by_field_name("parameters")
            for p in _named(ps) if ps is not None else []:
                pn, pt = p.child_by_field_name("name"), p.child_by_field_name("type")
                params.append(_t(pn))
                if pn is not None and pt is not None:
                    self.bind(_t(pn), Val("type", self.typename(pt)))
            self.f.funcs.setdefault(name, (params, _line(n), cls[0]))
            mods = next((c for c in n.children if c.type == "modifiers"), None)
            if name == "main" and mods is not None and "static" in _t(mods):
                self.f.is_main = True
        elif t == "field_declaration":
            ty = self.typename(n.child_by_field_name("type"))
            cls = self.cls_stack[-1] if self.cls_stack else (None, None, [])
            for d in n.children_by_field_name("declarator"):
                name = _t(d.child_by_field_name("name"))
                decos = self.annotations(n, name, "field", cls[0], cls[2])
                cfg = next((d2 for d2 in decos if d2.name == "Value" and d2.args and d2.args[0].kind == "str"), None)
                if cfg is not None:
                    key = _spring_key(cfg.args[0].text)
                    self.f.env_reads.add((cfg.line, key))
                    self.bind(name, Val("config", key))
                v = d.child_by_field_name("value")
                self.bind(name, self.val(v) if v is not None else Val("type", ty))
                for bv in self.f.binds.get(name, []):
                    self.bind("this." + name, bv)
        elif t == "local_variable_declaration":
            ty = self.typename(n.child_by_field_name("type"))
            for d in n.children_by_field_name("declarator"):
                v = d.child_by_field_name("value")
                self.bind(_t(d.child_by_field_name("name")), self.val(v) if v is not None else Val("type", ty))
        elif t == "assignment_expression":
            left, right = n.child_by_field_name("left"), n.child_by_field_name("right")
            if left is not None and right is not None:
                self.bind(_t(left), self.val(right))
        elif t == "interface_declaration":
            ext = next((c for c in n.children if c.type == "extends_interfaces"), None)
            if ext is not None:
                self.f.bases[_t(n.child_by_field_name("name"))] = ([
                    self.typename(x) for x in _named(ext.named_children[0])] if ext.named_child_count else [], _line(n))
        elif t == "class_declaration":
            sup = n.child_by_field_name("superclass")
            if sup is not None and sup.named_child_count:
                self.f.bases[_t(n.child_by_field_name("name"))] = ([self.typename(sup.named_children[0])], _line(n))
        elif t == "package_declaration":
            self.f.package = next((_t(c) for c in _named(n)), None)
        elif t in ("string_literal", "text_block"):
            self.string(n, self.val(n).text)

    def typename(self, n) -> str:
        if n is None:
            return "?"
        if n.type == "generic_type":
            return self.typename(n.named_children[0]) if n.named_child_count else "?"
        return _t(n).split("<")[0].strip()

    def args(self, node):
        return [self.val(c) for c in _named(node)] if node is not None else []

    def dotted(self, n) -> str:
        if n is None:
            return "?"
        t = n.type
        if t in ("identifier", "type_identifier", "this", "super"):
            return _t(n)
        if t == "field_access":
            return self.dotted(n.child_by_field_name("object")) + "." + _t(n.child_by_field_name("field"))
        if t == "scoped_identifier":
            return _t(n)
        if t == "method_invocation":
            obj = n.child_by_field_name("object")
            return (self.dotted(obj) + "." if obj is not None else "") + _t(n.child_by_field_name("name")) + "()"
        if t == "object_creation_expression":
            return self.typename(n.child_by_field_name("type")) + "()"
        if t == "parenthesized_expression":
            return self.dotted(n.named_children[0]) if n.named_child_count else "?"
        return "?"

    def val(self, n) -> Val:
        if n is None:
            return Val("other")
        t = n.type
        if t == "string_literal":
            return Val("str", "".join(_t(c) for c in n.children if c.type in ("string_fragment", "escape_sequence")))
        if t == "text_block":
            return Val("str", _t(n).strip('"'))
        if t in ("identifier", "field_access", "scoped_identifier"):
            return Val("name", self.dotted(n))
        if t == "binary_expression":
            op = n.child_by_field_name("operator")
            if op is not None and _t(op) == "+":
                return _concat([self.val(n.child_by_field_name("left")), self.val(n.child_by_field_name("right"))])
        if t == "method_invocation":
            obj = n.child_by_field_name("object")
            callee = (self.dotted(obj) + "." if obj is not None else "") + _t(n.child_by_field_name("name"))
            args = self.args(n.child_by_field_name("arguments"))
            if callee in self.env_calls and args and args[0].kind == "str":
                return self.env(n, args[0].text)
            if callee == "String.format" and args and args[0].kind == "str":
                return _printf(args[0].text, args[1:])
            return Val("call", callee, call=Call(_line(n), callee, args, {}, self.func))
        if t == "object_creation_expression":
            callee = self.typename(n.child_by_field_name("type"))
            return Val("call", callee, call=Call(_line(n), callee, self.args(n.child_by_field_name("arguments")), {},
                                                  self.func, new=True))
        if t in ("element_value_array_initializer", "array_initializer"):
            return Val("list", items=tuple(self.val(c) for c in _named(n)))
        if t == "parenthesized_expression":
            return self.val(n.named_children[0]) if n.named_child_count else Val("other")
        return Val("other", t)


def _spring_key(text: str) -> str:
    """``${inventory.url:default}`` -> ``inventory.url``."""
    m = re.match(r"\$\{([^}:]+)", text.strip())
    return m.group(1).strip() if m else text.strip()


# ---------------------------------------------------------------------------------------------- Go
class _Go(_Walker):
    FUNCS = ("function_declaration", "method_declaration")
    TYPES = frozenset({"call_expression", "composite_literal", "short_var_declaration", "assignment_statement",
                       "var_spec", "const_spec", "import_spec", "package_clause", "function_declaration",
                       "method_declaration", "field_declaration", "interpreted_string_literal", "raw_string_literal"})

    def on(self, n):
        t = n.type
        if t == "call_expression":
            fn = n.child_by_field_name("function")
            self.add_call(_line(n), self.dotted(fn), self.args(n.child_by_field_name("arguments")), {},
                          end=n.end_point[0] + 1)
        elif t == "composite_literal":
            fields = self.literal_fields(n.child_by_field_name("body"))
            self.add_call(_line(n), self.typename(n.child_by_field_name("type")), [], fields, new=True,
                          end=n.end_point[0] + 1)
        elif t in ("short_var_declaration", "assignment_statement"):
            left, right = n.child_by_field_name("left"), n.child_by_field_name("right")
            if left is not None and right is not None:
                ls, rs = _named(left), _named(right)
                if len(rs) == 1 and len(ls) >= 1:
                    self.bind(_t(ls[0]), self.val(rs[0]))
                else:
                    for a, b in zip(ls, rs):
                        self.bind(_t(a), self.val(b))
        elif t in ("var_spec", "const_spec"):
            names = n.children_by_field_name("name")
            ty = n.child_by_field_name("type")
            value = n.child_by_field_name("value")
            vals = _named(value) if value is not None else []
            for i, nm in enumerate(names):
                if i < len(vals):
                    self.bind(_t(nm), self.val(vals[i]))
                elif ty is not None:
                    self.bind(_t(nm), Val("type", self.typename(ty)))
        elif t == "import_spec":
            path = self.val(n.child_by_field_name("path")).text
            alias = n.child_by_field_name("name")
            local = _t(alias) if alias is not None else _go_pkg(path)
            if local not in ("_", "."):
                self.f.imports[local] = path
        elif t == "package_clause":
            self.f.package = next((_t(c) for c in _named(n)), None)
        elif t in ("function_declaration", "method_declaration"):
            name = _t(n.child_by_field_name("name"))
            params = []
            ps = n.child_by_field_name("parameters")
            for p in _named(ps) if ps is not None else []:
                ty = p.child_by_field_name("type")
                for pn in p.children_by_field_name("name"):
                    params.append(_t(pn))
                    if ty is not None:
                        self.bind(_t(pn), Val("type", self.typename(ty)))
            self.f.funcs.setdefault(name, (params, _line(n), None))
            if t == "function_declaration" and name == "main" and self.f.package == "main":
                self.f.is_main = True
        elif t == "field_declaration":
            ty = n.child_by_field_name("type")
            for nm in n.children_by_field_name("name"):
                if ty is not None:
                    self.bind(_t(nm), Val("type", self.typename(ty)))
        elif t in ("interpreted_string_literal", "raw_string_literal"):
            self.string(n, self.val(n).text)
            return "skip"
        return None

    def literal_fields(self, body) -> dict:
        out = {}
        for el in _named(body) if body is not None else []:
            if el.type == "keyed_element":
                kids = _named(el)
                if len(kids) == 2:
                    k = kids[0].named_children[0] if kids[0].named_child_count else kids[0]
                    v = kids[1].named_children[0] if kids[1].named_child_count else kids[1]
                    out[_t(k)] = self.val(v)
        return out

    def typename(self, n) -> str:
        if n is None:
            return "?"
        if n.type in ("pointer_type", "unary_expression"):
            return self.typename(n.named_children[-1]) if n.named_child_count else "?"
        if n.type == "generic_type":
            return self.typename(n.child_by_field_name("type"))
        return _t(n)

    def args(self, node):
        return [self.val(c) for c in _named(node)] if node is not None else []

    def dotted(self, n) -> str:
        if n is None:
            return "?"
        t = n.type
        if t in ("identifier", "package_identifier", "field_identifier", "type_identifier"):
            return _t(n)
        if t == "selector_expression":
            return self.dotted(n.child_by_field_name("operand")) + "." + _t(n.child_by_field_name("field"))
        if t == "call_expression":
            return self.dotted(n.child_by_field_name("function")) + "()"
        if t in ("parenthesized_expression", "unary_expression"):
            return self.dotted(n.named_children[-1]) if n.named_child_count else "?"
        if t == "composite_literal":
            return self.typename(n.child_by_field_name("type")) + "()"
        return "?"

    def val(self, n) -> Val:
        if n is None:
            return Val("other")
        t = n.type
        if t == "interpreted_string_literal":
            return Val("str", "".join(_t(c) for c in n.children
                                      if c.type in ("interpreted_string_literal_content", "escape_sequence")))
        if t == "raw_string_literal":
            return Val("str", _t(n).strip("`"))
        if t in ("identifier", "selector_expression"):
            return Val("name", self.dotted(n))
        if t == "binary_expression":
            op = n.child_by_field_name("operator")
            if op is not None and _t(op) == "+":
                return _concat([self.val(n.child_by_field_name("left")), self.val(n.child_by_field_name("right"))])
        if t == "call_expression":
            callee = self.dotted(n.child_by_field_name("function"))
            args = self.args(n.child_by_field_name("arguments"))
            if callee in self.env_calls and args and args[0].kind == "str":
                return self.env(n, args[0].text)
            if callee == "fmt.Sprintf" and args and args[0].kind == "str":
                return _printf(args[0].text, args[1:])
            return Val("call", callee, call=Call(_line(n), callee, args, {}, self.func))
        if t == "composite_literal":
            body = n.child_by_field_name("body")
            fields = self.literal_fields(body)
            if not fields and body is not None and body.named_child_count:
                items = [x.named_children[0] if x.named_child_count else x for x in _named(body)
                         if x.type == "literal_element"]
                if items:
                    return Val("list", items=tuple(self.val(x) for x in items))
            callee = self.typename(n.child_by_field_name("type"))
            return Val("call", callee, call=Call(_line(n), callee, [], fields, self.func, new=True))
        if t == "unary_expression":
            return self.val(n.named_children[-1]) if n.named_child_count else Val("other")
        if t == "parenthesized_expression":
            return self.val(n.named_children[0]) if n.named_child_count else Val("other")
        if t == "int_literal":
            return Val("num", _t(n))
        return Val("other", t)


def _go_pkg(path: str) -> str:
    """The package name a Go import path is used under, by convention: the last element without a
    version suffix or a ``go-`` prefix / ``-go`` suffix (``github.com/segmentio/kafka-go`` -> ``kafka``)."""
    parts = [p for p in path.split("/") if p]
    while len(parts) > 1 and re.fullmatch(r"v\d+", parts[-1]):
        parts.pop()
    last = parts[-1] if parts else path
    for pre in ("go-", "golang-"):
        if last.startswith(pre):
            last = last[len(pre):]
    for suf in ("-go", ".go", "-golang"):
        if last.endswith(suf):
            last = last[: -len(suf)]
    return re.sub(r"[^A-Za-z0-9_]", "", last) or last


# ---------------------------------------------------------------------------------------------- C#
class _Cs(_Walker):
    FUNCS = ("method_declaration", "local_function_statement", "constructor_declaration")
    CLASSES = ("class_declaration", "interface_declaration", "record_declaration", "struct_declaration")
    TYPES = frozenset({"invocation_expression", "object_creation_expression", "using_directive",
                       "variable_declaration", "assignment_expression", "method_declaration",
                       "local_function_statement", "string_literal", "verbatim_string_literal", "raw_string_literal"})

    def on(self, n):
        t = n.type
        if t == "invocation_expression":
            fn = n.child_by_field_name("function")
            self.add_call(_line(n), self.dotted(fn), self.args(n.child_by_field_name("arguments")), {},
                          end=n.end_point[0] + 1)
        elif t == "object_creation_expression":
            self.add_call(_line(n), _t(n.child_by_field_name("type")).split("<")[0],
                          self.args(n.child_by_field_name("arguments")), {}, new=True, end=n.end_point[0] + 1)
        elif t == "using_directive":
            name = next((_t(c) for c in _named(n) if c.type in ("qualified_name", "identifier")), "")
            if name:
                self.f.namespaces.add(name)
        elif t == "variable_declaration":
            ty = n.child_by_field_name("type")
            for d in _named(n):
                if d.type != "variable_declarator":
                    continue
                name = d.child_by_field_name("name")
                at = name.start_byte if name is not None else -1
                value = next((c for c in _named(d) if c.start_byte != at and c.type != "bracketed_argument_list"),
                             None)
                if value is not None and value.type == "equals_value_clause":
                    value = value.named_children[0] if value.named_child_count else None
                if name is not None:
                    if value is not None:
                        self.bind(_t(name), self.val(value))
                    elif ty is not None and ty.type != "implicit_type":
                        self.bind(_t(name), Val("type", _t(ty).split("<")[0]))
        elif t == "assignment_expression":
            left, right = n.child_by_field_name("left"), n.child_by_field_name("right")
            if left is not None and right is not None:
                self.bind(_t(left), self.val(right))
        elif t in ("method_declaration", "local_function_statement"):
            name = _t(n.child_by_field_name("name"))
            params = []
            ps = n.child_by_field_name("parameters")
            for p in _named(ps) if ps is not None else []:
                pn, pt = p.child_by_field_name("name"), p.child_by_field_name("type")
                params.append(_t(pn))
                if pn is not None and pt is not None:
                    self.bind(_t(pn), Val("type", _t(pt).split("<")[0]))
            self.f.funcs.setdefault(name, (params, _line(n), self.cls))
            if name == "Main":
                self.f.is_main = True
        elif t in ("string_literal", "verbatim_string_literal", "raw_string_literal"):
            self.string(n, self.val(n).text)
            return "skip"
        return None

    def args(self, node):
        out = []
        for a in _named(node) if node is not None else []:
            if a.type == "argument":
                inner = [c for c in _named(a) if c.type != "name_colon"]
                out.append(self.val(inner[-1]) if inner else Val("other"))
        return out

    def dotted(self, n) -> str:
        if n is None:
            return "?"
        t = n.type
        if t in ("identifier", "this_expression", "predefined_type"):
            return _t(n)
        if t == "member_access_expression":
            return self.dotted(n.child_by_field_name("expression")) + "." + _t(n.child_by_field_name("name"))
        if t == "invocation_expression":
            return self.dotted(n.child_by_field_name("function")) + "()"
        if t == "generic_name":
            return _t(n).split("<")[0]
        if t == "qualified_name":
            return _t(n)
        if t in ("await_expression", "parenthesized_expression"):
            return self.dotted(n.named_children[-1]) if n.named_child_count else "?"
        if t == "object_creation_expression":
            return _t(n.child_by_field_name("type")).split("<")[0] + "()"
        return "?"

    def val(self, n) -> Val:
        if n is None:
            return Val("other")
        t = n.type
        if t in ("string_literal", "verbatim_string_literal", "raw_string_literal"):
            inner = "".join(_t(c) for c in n.children if c.type in ("string_literal_content", "escape_sequence",
                                                                    "raw_string_content"))
            return Val("str", inner if inner or t == "string_literal" else _t(n).lstrip("@").strip('"'))
        if t == "interpolated_string_expression":
            parts = []
            for c in n.children:
                if c.type == "string_content":
                    parts.append(("lit", _t(c)))
                elif c.type == "interpolation":
                    e = next((x for x in _named(c) if x.type != "interpolation_brace"), None)
                    parts.append(("var", self.dotted(e)))
            return Val("tmpl", _render(parts), parts=tuple(parts))
        if t in ("identifier", "member_access_expression"):
            return Val("name", self.dotted(n))
        if t == "binary_expression":
            op = n.child_by_field_name("operator")
            if op is not None and _t(op) == "+":
                return _concat([self.val(n.child_by_field_name("left")), self.val(n.child_by_field_name("right"))])
            if op is not None and _t(op) == "??":
                return _prefer(self.val(n.child_by_field_name("left")), self.val(n.child_by_field_name("right")))
        if t == "invocation_expression":
            callee = self.dotted(n.child_by_field_name("function"))
            args = self.args(n.child_by_field_name("arguments"))
            if callee in self.env_calls and args and args[0].kind == "str":
                return self.env(n, args[0].text)
            return Val("call", callee, call=Call(_line(n), callee, args, {}, self.func))
        if t == "object_creation_expression":
            callee = _t(n.child_by_field_name("type")).split("<")[0]
            return Val("call", callee, call=Call(_line(n), callee, self.args(n.child_by_field_name("arguments")), {},
                                                  self.func, new=True))
        if t in ("await_expression", "parenthesized_expression"):
            return self.val(n.named_children[-1]) if n.named_child_count else Val("other")
        return Val("other", t)


# ---------------------------------------------------------------------------------------------- shell
class _Sh(_Walker):
    TYPES = frozenset({"command"})

    def on(self, n):
        if n.type == "command":
            name = n.child_by_field_name("name")
            args = []
            for a in n.children_by_field_name("argument"):
                if a.type in ("string", "raw_string"):
                    args.append("".join(_t(c) for c in a.children if c.type == "string_content")
                                if a.type == "string" else _t(a).strip("'"))
                else:
                    args.append(_t(a))
            if name is not None and len(self.f.commands) < MAX_CALLS:
                self.f.commands.append((_line(n), _t(name).rsplit("/", 1)[-1], args))
        return None


# ---------------------------------------------------------------------------------------------- values
def _render(parts) -> str:
    return "".join(p[1] if p[0] == "lit" else "{}" for p in parts)


def _concat(vals) -> Val:
    parts: list = []
    for v in vals:
        if v.kind == "str":
            parts.append(("lit", v.text))
        elif v.kind == "tmpl":
            parts.extend(v.parts)
        elif v.kind == "env":
            parts.append(("env", v.text))
        elif v.kind == "config":
            parts.append(("config", v.text))
        elif v.kind == "name":
            parts.append(("var", v.text))
        else:
            parts.append(("var", "?"))
    if all(p[0] == "lit" for p in parts):
        return Val("str", "".join(p[1] for p in parts))
    return Val("tmpl", _render(parts), parts=tuple(parts))


def _printf(fmt: str, args) -> Val:
    """``"%s/api/x" % base`` and ``fmt.Sprintf("%s/api/x", base)`` -> a template."""
    pieces = re.split(r"(%[-+ #0]*\d*(?:\.\d+)?[sdvqxfg])", fmt)
    vals, i = [], 0
    for p in pieces:
        if re.fullmatch(r"%[-+ #0]*\d*(?:\.\d+)?[sdvqxfg]", p):
            vals.append(args[i] if i < len(args) else Val("other"))
            i += 1
        elif p:
            vals.append(Val("str", p))
    return _concat(vals) if vals else Val("str", fmt)


def _prefer(left: Val, right: Val) -> Val:
    """``a or b`` / ``a || b`` / ``a ?? b``: the configured side when there is one, else the left."""
    if left.kind in ("env", "config"):
        return left
    if right.kind in ("env", "config") and left.kind not in ("str", "tmpl"):
        return right
    return left if left.kind != "other" else right
