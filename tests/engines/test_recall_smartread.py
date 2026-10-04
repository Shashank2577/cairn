"""Smart file reading: outlines, unfolding, codebase search, the workspace jail and the agent tools."""
from __future__ import annotations

import os
import textwrap

import pytest

from cairn.engines.recall import smartread as sr

PY_SRC = textwrap.dedent('''\
    """Module doc."""
    import os
    from typing import (
        Any,
        Dict,
    )


    # Parse the configuration file.
    def parse_config(path: str) -> dict:
        """Read and parse a config file."""
        with open(path) as fh:
            return {}


    class Loader:
        """Loads things."""

        def __init__(self, root):
            self.root = root

        def load(self):
            return self.root


    def _private():
        pass
    ''')

TS_SRC = textwrap.dedent('''\
    import { readFile } from "node:fs/promises";
    import type { X } from "./x";

    /** A user. */
    export interface User {
      id: string;
    }

    export type Id = string | number;

    enum Color { Red, Green }

    /**
     * Greets someone.
     */
    export function greet(name: string): string {
      return `hi ${name}`;
    }

    export const add = (a: number, b: number): number => {
      return a + b;
    };

    class Service {
      // Runs it.
      async run(x: number): Promise<void> {
        await readFile("x");
      }
    }
    ''')

JS_SRC = textwrap.dedent('''\
    const path = require("path");
    import fs from "fs";

    export function build(dir) {
      return path.join(dir, "out");
    }

    const clean = async (dir) => {
      await fs.promises.rm(dir);
    };

    class Builder {
      run() {
        return build(".");
      }
    }
    ''')

MD_SRC = textwrap.dedent('''\
    ---
    title: Doc
    ---

    # Intro

    Some text.

    ## Setup

    ```bash
    npm install
    ```

    [ref]: https://example.com

    ## Usage

    Text here.

    # Appendix
    ''')


@pytest.fixture(params=["tree-sitter", "lines"])
def backend(request, monkeypatch):
    """Run a test against the grammar backend and against the line extractor."""
    if request.param == "tree-sitter":
        pytest.importorskip("tree_sitter")
    else:
        monkeypatch.setattr(sr, "_backend", lambda language: None)
    return request.param


def _by_name(symbols):
    return {s.name: s for s in symbols}


# ---- language detection ----------------------------------------------------------------------------
def test_detect_language():
    assert sr.detect_language("a/b/c.py") == "python"
    assert sr.detect_language("x.jsx") == "tsx"
    assert sr.detect_language("x.ts") == "typescript"
    assert sr.detect_language("README.md") == "markdown"
    assert sr.detect_language("Makefile") == "unknown"
    assert sr.detect_language("notes.txt") == "unknown"
    assert sr.detect_language("X.PY") == "unknown"  # extensions are case sensitive


def test_split_patterns():
    pats = sr.split_patterns(sr.QUERIES["jsts"])
    assert len(pats) == 9
    assert pats[1].startswith("(lexical_declaration") and pats[1].endswith("@const_func")
    assert sr.split_patterns('(a "(" @x) @y\n(b) @z') == ['(a "(" @x) @y', "(b) @z"]
    assert len(sr.split_patterns(sr.QUERIES["generic"])) == 6


# ---- outlines --------------------------------------------------------------------------------------
def test_python_outline(backend):
    f = sr.parse_file(PY_SRC, "src/config.py")
    assert f.language == "python" and f.total_lines == len(PY_SRC.split("\n"))
    syms = _by_name(f.symbols)
    assert list(syms) == ["parse_config", "Loader", "_private"]
    pc = syms["parse_config"]
    assert (pc.kind, pc.line_start, pc.line_end, pc.exported) == ("function", 9, 12, True)
    assert pc.signature == "def parse_config(path: str) -> dict"
    assert pc.jsdoc == "# Parse the configuration file."
    loader = syms["Loader"]
    assert (loader.kind, loader.line_start, loader.line_end) == ("class", 15, 22)
    assert loader.jsdoc == '"""Loads things."""'
    assert [(c.name, c.kind, c.line_start, c.line_end, c.parent) for c in loader.children] == [
        ("__init__", "method", 18, 19, "Loader"), ("load", "method", 21, 22, "Loader")]
    assert syms["_private"].exported is False
    assert f.imports == ["import os", "from typing import ("]
    assert f.folded_token_estimate > 0

    view = sr.format_folded_view(f)
    assert view.startswith("📁 src/config.py (python, 28 lines)\n\n  📦 Imports: 2 statements\n    import os\n")
    assert "  ƒ parse_config [exported] (L10-13)\n    def parse_config(path: str) -> dict\n" \
           "    💬 # Parse the configuration file." in view
    assert "  ◆ Loader [exported] (L16-23)\n    class Loader\n    💬 Loads things.\n" \
           "    ƒ __init__ (L19-20)\n      def __init__(self, root)" in view
    assert "  ƒ _private (L26-27)" in view


def test_typescript_outline(backend):
    f = sr.parse_file(TS_SRC, "src/user.ts")
    syms = _by_name(f.symbols)
    assert list(syms) == ["User", "Id", "Color", "greet", "add", "Service"]
    assert [(s.kind, s.line_start, s.line_end, s.exported) for s in f.symbols] == [
        ("interface", 4, 6, True), ("type", 8, 8, True), ("enum", 10, 10, False), ("function", 15, 17, True),
        ("function", 19, 21, True), ("class", 23, 28, False)]
    assert syms["greet"].signature == "export function greet(name: string): string"
    assert syms["add"].signature == "export const add = (a: number, b: number): number =>"
    run = syms["Service"].children[0]
    assert (run.name, run.kind, run.line_start, run.line_end, run.jsdoc) == ("run", "method", 25, 27, "// Runs it.")
    assert f.imports == ['import { readFile } from "node:fs/promises";', 'import type { X } from "./x";']
    view = sr.format_folded_view(f)
    assert "  ƒ greet [exported] (L16-18)\n    export function greet(name: string): string\n" \
           "    💬 Greets someone." in view
    assert "  ▣ Color (L11)\n    enum Color" in view


def test_javascript_outline(backend):
    f = sr.parse_file(JS_SRC, "build.js")
    assert f.language == "javascript"
    assert [(s.name, s.kind, s.line_start, s.line_end, s.exported) for s in f.symbols] == [
        ("build", "function", 3, 5, True), ("clean", "function", 7, 9, False), ("Builder", "class", 11, 15, False)]
    assert [(c.name, c.kind) for c in f.symbols[2].children] == [("run", "method")]
    assert f.imports == ['import fs from "fs";']


def test_markdown_outline(backend):
    f = sr.parse_file(MD_SRC, "docs/guide.md")
    kinds = [(s.kind, s.name, s.line_start) for s in f.symbols]
    assert kinds == [("metadata", "anonymous", 0), ("section", "Intro", 4), ("section", "Setup", 8),
                     ("code", "bash", 10), ("reference", "[ref]", 14), ("section", "Usage", 16),
                     ("section", "Appendix", 20)]
    # block ranges include the line break that closes the block, as the grammar reports them
    assert (f.symbols[0].line_end, f.symbols[1].line_end, f.symbols[3].line_end) == (3, 5, 13)
    view = sr.format_folded_view(f)
    assert view.split("\n") == [
        "📄 docs/guide.md (markdown, 22 lines)",
        "  ---frontmatter---".ljust(56) + "L1-4",
        "  # Intro".ljust(56) + "L5",
        "    ## Setup".ljust(56) + "L9",
        "      ```bash".ljust(56) + "L11-14",
        "      ↗ [ref]".ljust(56) + "L15",
        "    ## Usage".ljust(56) + "L17",
        "  # Appendix".ljust(56) + "L21",
    ]


def test_markdown_edge_cases(backend):
    src = "Title\n=====\n\n```\n# not a heading\n```\n\n#hashtag is text\n\n## Closed ##\n\n```py\nunclosed\n"
    f = sr.parse_file(src, "x.md")
    # setext heading text spans rows (so it is anonymous); a closing hash sequence stays in the text
    assert [(s.kind, s.name, s.signature, s.line_start, s.line_end) for s in f.symbols] == [
        ("section", "anonymous", "# anonymous", 0, 2), ("code", "anonymous", "```", 3, 6),
        ("section", "Closed ##", "## Closed ##", 9, 10), ("code", "py", "```py", 11, 13)]


def test_unknown_language_has_no_symbols():
    f = sr.parse_file("just some text\n", "notes.txt")
    assert (f.language, f.symbols, f.imports, f.folded_token_estimate) == ("unknown", [], [], 50)


@pytest.mark.parametrize("name,src,expected", [
    ("a.yaml", "name: app\nserver:\n  host: x\n", [("name", "function", 0, 0), ("server", "function", 1, 3),
                                                     ("host", "function", 2, 2)]),
    ("b.yaml", "a:\n  b: 1\nc: |\n  text\nd: 2\n", [("a", "function", 0, 1), ("b", "function", 1, 1),
                                                    ("c", "function", 2, 3), ("d", "function", 4, 4)]),
    ("a.toml", "[package]\nname = 'x'\n\n[dependencies.serde]\nv = 1\n",
     [("package", "class", 0, 3), ("dependencies.serde", "class", 3, 5)]),
    ("a.sql", "CREATE TABLE users (\n  id INT\n);\nCREATE VIEW v AS SELECT 1;\n",
     [("users", "class", 0, 2), ("v", "class", 3, 3)]),
    ("a.css", ".btn {\n  color: red;\n}\n@keyframes spin {\n  from { top: 0; }\n}\n",
     [(".btn", "function", 0, 2), ("spin", "class", 3, 5)]),
    ("a.hs", "import Data.List\n\narea :: Double -> Double\narea r = r * r\n\nmain = print 1\n",
     [("area", "function", 3, 3)]),
    ("a.go", "package m\n\nfunc Run() {\n}\n\nfunc (s *S) stop() {\n}\n",
     [("Run", "function", 2, 3), ("stop", "method", 5, 6)]),
    ("a.rs", "pub struct P {\n    x: i32,\n}\n\nfn f() {}\n", [("P", "struct", 0, 2), ("f", "function", 4, 4)]),
])
def test_other_languages(backend, name, src, expected):
    f = sr.parse_file(src, name)
    assert [(s.name, s.kind, s.line_start, s.line_end) for s in f.symbols] == expected


def test_exported_rules(backend):
    go = sr.parse_file("package m\n\nfunc Public() {}\n\nfunc private() {}\n", "m.go")
    assert [(s.name, s.exported) for s in go.symbols] == [("Public", True), ("private", False)]
    rs = sr.parse_file("pub fn a() {}\nfn b() {}\n", "l.rs")
    assert [(s.name, s.exported) for s in rs.symbols] == [("a", True), ("b", False)]


@pytest.mark.parametrize("ext", sorted(set(sr.LANG_MAP)))
def test_odd_input_never_raises(backend, ext):
    for src in ("", "\n\n", "{{{{ ((( '''\n\"\"\" `", "\x00\x01 def class fn func }}}} end end", "# " * 500):
        f = sr.parse_file(src, "f" + ext)
        sr.format_folded_view(f)
        assert isinstance(f.symbols, list)


# ---- unfolding -------------------------------------------------------------------------------------
def test_unfold_returns_exactly_the_symbol_source(backend):
    lines = PY_SRC.split("\n")
    out = sr.unfold_symbol(PY_SRC, "src/config.py", "parse_config")
    # the blank lines and comments directly above the symbol come with it
    assert out == "// 📍 src/config.py L7-13\n" + "\n".join(lines[6:13])
    assert out == ("// 📍 src/config.py L7-13\n\n\n# Parse the configuration file.\n"
                   "def parse_config(path: str) -> dict:\n    \"\"\"Read and parse a config file.\"\"\"\n"
                   "    with open(path) as fh:\n        return {}")


def test_unfold_method_and_missing(backend):
    out = sr.unfold_symbol(PY_SRC, "c.py", "load")
    assert out == "// 📍 c.py L21-23\n\n    def load(self):\n        return self.root"
    assert sr.unfold_symbol(PY_SRC, "c.py", "nope") is None


def test_unfold_typescript_includes_doc_comment(backend):
    out = sr.unfold_symbol(TS_SRC, "u.ts", "greet")
    assert out.split("\n")[0] == "// 📍 u.ts L12-18"
    assert out.endswith("/**\n * Greets someone.\n */\nexport function greet(name: string): string {\n"
                        "  return `hi ${name}`;\n}")


def test_unfold_markdown_section(backend):
    out = sr.unfold_symbol(MD_SRC, "g.md", "Setup")
    assert out == "<!-- 📍 g.md L9-15 -->\n## Setup\n\n```bash\nnpm install\n```\n\n[ref]: https://example.com"
    last = sr.unfold_symbol(MD_SRC, "g.md", "Appendix")
    assert last.startswith("<!-- 📍 g.md L21-22 -->\n# Appendix")


# ---- search ----------------------------------------------------------------------------------------
@pytest.fixture
def code_tree(tmp_path):
    root = tmp_path / "repo"
    (root / "src").mkdir(parents=True)
    (root / "lib").mkdir()
    (root / "node_modules" / "pkg").mkdir(parents=True)
    (root / ".hidden").mkdir()
    (root / "src" / "config.py").write_text(PY_SRC, encoding="utf-8")
    (root / "src" / "reader.py").write_text(
        'def load_file(p):\n    """Uses parse_config internally."""\n    return p\n\n\ndef configure():\n    pass\n', encoding="utf-8")
    (root / "lib" / "user.ts").write_text(TS_SRC, encoding="utf-8")
    (root / "node_modules" / "pkg" / "index.js").write_text("function parse_config() {}\n", encoding="utf-8")
    (root / ".hidden" / "x.py").write_text("def parse_config():\n    pass\n", encoding="utf-8")
    (root / "notes.txt").write_text("parse_config\n", encoding="utf-8")
    (root / "big.py").write_text("def parse_config_big():\n    pass\n" + "#" * (sr.MAX_FILE_SIZE + 10), encoding="utf-8")
    (root / "empty.py").write_text("", encoding="utf-8")
    (root / "binary.py").write_bytes(b"def parse_config_bin():\x00\n")
    return root


def test_search_ranks_name_match_first(code_tree):
    res = sr.search_codebase(str(code_tree), "parse_config")
    assert res.total_files_scanned == 3  # config.py, reader.py, user.ts (ignored/hidden/big/empty/binary skipped)
    assert res.matching_symbols[0].symbol_name == "parse_config"
    assert res.matching_symbols[0].file_path == os.path.join("src", "config.py")
    assert res.matching_symbols[0].match_reason == "name match + signature"
    names = [m.symbol_name for m in res.matching_symbols]
    assert "load_file" in names  # doc-comment match ranks after the name match
    assert names.index("parse_config") < names.index("load_file")
    assert all("node_modules" not in m.file_path and ".hidden" not in m.file_path for m in res.matching_symbols)
    assert res.token_estimate == sum(f.folded_token_estimate for f in res.folded_files)
    assert res.total_symbols_found == 14  # 5 + 2 + 7, children included
    reasons = {m.symbol_name: m.match_reason for m in res.matching_symbols}
    assert reasons["load_file"] == "jsdoc match" and reasons["configure"] == "name match"


def test_search_nested_symbol_names_and_reasons(code_tree):
    res = sr.search_codebase(str(code_tree), "load")
    names = {m.symbol_name: m for m in res.matching_symbols}
    assert "Loader.load" in names and names["Loader.load"].kind == "method"
    assert names["Loader.load"].match_reason.startswith("name match")


def test_search_file_pattern_and_max_results(code_tree):
    res = sr.search_codebase(str(code_tree), "a", file_pattern="LIB/")
    assert res.matching_symbols and all(m.file_path.startswith("lib") for m in res.matching_symbols)
    assert res.total_files_scanned == 1
    few = sr.search_codebase(str(code_tree), "a", max_results=2)
    assert len(few.matching_symbols) == 2 and len(few.folded_files) <= 2
    assert {f.file_path for f in few.folded_files} == {m.file_path for m in few.matching_symbols}


def test_format_search_results(code_tree):
    res = sr.search_codebase(str(code_tree), "parse_config", max_results=1)
    text = sr.format_search_results(res, "parse_config")
    lines = text.split("\n")
    assert lines[0] == '🔍 Smart Search: "parse_config"'
    assert lines[1] == f"   Scanned 3 files, found {res.total_symbols_found} symbols"
    assert lines[2] == f"   1 matches across 1 files (~{res.token_estimate} tokens for folded view)"
    assert "── Matching Symbols ──" in lines
    assert f"  function parse_config ({os.path.join('src', 'config.py')}:10)" in lines
    assert "    💬 # Parse the configuration file." in lines
    assert "── Folded File Views ──" in lines
    assert lines[-1] == "  To see full implementation: use smart_unfold with file path and symbol name"
    empty = sr.format_search_results(sr.search_codebase(str(code_tree), "zzzzqqq"), "zzzzqqq")
    assert empty.endswith("   No matching symbols found.")


def test_match_score():
    assert sr.match_score("parse", ["parse"]) == 10
    assert sr.match_score("parseconfig", ["parse", "config"]) == 10
    assert sr.match_score("pxcy", ["pc"]) == 1
    assert sr.match_score("abc", ["xyz"]) == 0


# ---- workspace jail --------------------------------------------------------------------------------
def test_resolve_within_workspace(tmp_path):
    ws = tmp_path / "ws"
    (ws / "src").mkdir(parents=True)
    (ws / "src" / "a.py").write_text("x = 1\n", encoding="utf-8")
    real = os.path.realpath(ws)
    assert sr.resolve_within_workspace("src/a.py", ws) == os.path.join(real, "src", "a.py")
    assert sr.resolve_within_workspace(str(ws / "src" / "a.py"), ws) == os.path.join(real, "src", "a.py")
    assert sr.resolve_within_workspace(".", ws) == real
    assert sr.resolve_within_workspace("  src/missing.py ", ws) == os.path.join(real, "src", "missing.py")


def test_workspace_jail_rejects_escapes(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    outside = tmp_path / "secret.txt"
    outside.write_text("s", encoding="utf-8")
    (ws / "link").symlink_to(outside)
    (ws / "dirlink").symlink_to(tmp_path)
    for bad in ("../secret.txt", str(outside), "link", "dirlink/secret.txt", "src/../../secret.txt", "~",
                "/etc/passwd"):
        with pytest.raises(PermissionError) as err:
            sr.resolve_within_workspace(bad, ws)
        assert str(err.value).startswith(f'Access denied: "{bad}" resolves outside the workspace (')
        assert str(err.value).endswith("MCP file tools can only read files within the current project.")
    (tmp_path / "ws2").mkdir()
    with pytest.raises(PermissionError):  # a sibling sharing the prefix is still outside
        sr.resolve_within_workspace(str(tmp_path / "ws2"), ws)
    for empty in ("", "   ", None, 3):
        with pytest.raises(ValueError, match="file_path is required"):
            sr.resolve_within_workspace(empty, ws)


# ---- agent tools -----------------------------------------------------------------------------------
def test_tools_list():
    assert [t["name"] for t in sr.TOOLS] == ["smart_search", "smart_unfold", "smart_outline"]
    assert sr.TOOLS[0]["input_schema"]["required"] == ["query"]
    assert sr.TOOLS[1]["input_schema"]["required"] == ["file_path", "symbol_name"]
    assert sr.TOOLS[2]["input_schema"]["required"] == ["file_path"]
    assert set(sr.TOOLS[0]["input_schema"]["properties"]) == {"query", "path", "max_results", "file_pattern"}


def test_tool_handlers(code_tree):
    cwd = str(code_tree)
    real = os.path.realpath(cwd)
    outline = sr.tool_smart_outline({"file_path": "src/config.py"}, cwd)
    assert outline.startswith(f"📁 {os.path.join(real, 'src', 'config.py')} (python, 28 lines)")
    assert sr.tool_smart_outline({"file_path": "notes.txt"}, cwd) == \
        "Could not parse notes.txt. File may use an unsupported language or be empty."

    unfolded = sr.tool_smart_unfold({"file_path": "src/config.py", "symbol_name": "load"}, cwd)
    assert unfolded.startswith(f"// 📍 {os.path.join(real, 'src', 'config.py')} L21-23\n")
    missing = sr.tool_smart_unfold({"file_path": "src/config.py", "symbol_name": "nope"}, cwd)
    assert missing == ('Symbol "nope" not found in src/config.py.\n\nAvailable symbols:\n'
                       "  - parse_config (function)\n  - Loader (class)\n  - _private (function)")
    assert sr.tool_smart_unfold({"file_path": "notes.txt", "symbol_name": "x"}, cwd) == \
        "Could not parse notes.txt. File may be unsupported or empty."

    found = sr.tool_smart_search({"query": "parse_config"}, cwd)
    assert found.startswith('🔍 Smart Search: "parse_config"\n   Scanned 3 files')
    scoped = sr.tool_smart_search({"query": "greet", "path": "lib", "max_results": 5}, cwd)
    assert "   Scanned 1 files" in scoped and "function greet (user.ts:16)" in scoped
    patterned = sr.tool_smart_search({"query": "a", "file_pattern": ".ts"}, cwd)
    assert "Scanned 1 files" in patterned


def test_call_tool_reports_errors_as_text(code_tree):
    cwd = str(code_tree)
    ok = sr.call_tool("smart_outline", {"file_path": "src/config.py"}, cwd)
    assert ok["is_error"] is False and ok["text"].startswith("📁 ")
    denied = sr.call_tool("smart_outline", {"file_path": "../outside.py"}, cwd)
    assert denied["is_error"] is True
    assert denied["text"].startswith('Tool execution failed: Access denied: "../outside.py" resolves outside')
    gone = sr.call_tool("smart_unfold", {"file_path": "src/none.py", "symbol_name": "x"}, cwd)
    assert gone["is_error"] is True and gone["text"].startswith("Tool execution failed: ")
    with pytest.raises(KeyError):
        sr.call_tool("nope", {}, cwd)
