"""Folder context files: CLAUDE.md timelines, AGENTS.md / markdown blocks, the Cursor rules file and the
claude-md generate / clean commands."""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from cairn.engines.recall import folders
from cairn.engines.recall.settings import load as load_settings
from cairn.engines.recall.store import Store

OPEN, CLOSE = folders.CONTEXT_TAG_OPEN, folders.CONTEXT_TAG_CLOSE


@pytest.fixture
def repo(tmp_path, monkeypatch):
    monkeypatch.setenv("CAIRN_HOME", str(tmp_path / "home"))
    root = tmp_path / "repo"
    (root / "src").mkdir(parents=True)
    (root / "src" / "app.py").write_text("print('hi')\n")
    return root


def obs(title: str, *, read=(), modified=(), type_="discovery") -> dict:
    return {"title": title, "narrative": f"{title}: what happened", "type": type_, "facts": ["a fact"],
            "concepts": ["how-it-works"], "files_read": list(read), "files_modified": list(modified)}


def seed(root: Path, observations: list[dict], project: str = "repo", session: str = "content-1",
         summary: dict | None = None) -> None:
    store = Store.open(root)
    try:
        sid = store.create_sdk_session(session, project)
        memory_id = store.ensure_memory_session_id(sid)
        store.store_observations(memory_id, project, observations, summary=summary)
    finally:
        store.close()


def enabled(root: Path, **overrides) -> dict:
    return {**load_settings(root), "folder_context": True, **overrides}


def block_of(text: str) -> str:
    return text[text.index(OPEN):text.index(CLOSE) + len(CLOSE)]


# ---- automatic folder timelines ----------------------------------------------------------------------------
def test_update_writes_block_listing_the_observation(repo):
    seed(repo, [obs("Parse config at startup", modified=["src/app.py"])])
    written = folders.update_folder_claude_md_files(repo, ["src/app.py"], "repo", settings=enabled(repo))
    target = repo / "src" / "CLAUDE.md"
    assert written == [target]
    text = target.read_text()
    assert text.startswith(f"{OPEN}\n# Recent Activity\n\n### ")
    assert text.endswith(CLOSE)
    assert "| ID | Time | T | Title | Read |" in text
    assert "| #1 |" in text and "Parse config at startup" in text and "| ○ |" in text
    assert all(ord(ch) <= 0xFFFF for ch in text)


def test_update_accepts_absolute_paths_and_an_open_store(repo):
    seed(repo, [obs("Absolute path work", read=[str(repo / "src" / "app.py")])])
    store = Store.open(repo, readonly=True)
    try:
        written = folders.update_folder_claude_md_files(repo, [str(repo / "src" / "app.py")], "repo",
                                                        settings=enabled(repo), store=store)
    finally:
        store.close()
    assert written == [repo / "src" / "CLAUDE.md"]
    assert "Absolute path work" in written[0].read_text()


def test_update_preserves_user_text_and_is_idempotent(repo):
    seed(repo, [obs("Refactor loader", modified=["src/app.py"], type_="refactor")])
    target = repo / "src" / "CLAUDE.md"
    target.write_text(f"# Notes\n\nKeep me.\n{OPEN}\nstale timeline\n{CLOSE}\nAfter the block.\n")
    folders.update_folder_claude_md_files(repo, ["src/app.py"], "repo", settings=enabled(repo))
    first = target.read_text()
    assert first.startswith(f"# Notes\n\nKeep me.\n{OPEN}\n# Recent Activity")
    assert first.endswith(f"{CLOSE}\nAfter the block.\n")
    assert "stale timeline" not in first and "Refactor loader" in first and "| ↻ |" in first
    folders.update_folder_claude_md_files(repo, ["src/app.py"], "repo", settings=enabled(repo))
    assert target.read_text() == first
    assert first.count(OPEN) == 1


def test_update_appends_block_to_a_file_without_one(repo):
    seed(repo, [obs("Add route", modified=["src/app.py"])])
    target = repo / "src" / "CLAUDE.md"
    target.write_text("User written docs\n")
    folders.update_folder_claude_md_files(repo, ["src/app.py"], "repo", settings=enabled(repo))
    text = target.read_text()
    assert text.startswith(f"User written docs\n\n\n{OPEN}\n# Recent Activity")
    assert text.endswith(CLOSE)


def test_update_is_gated_on_folder_context(repo):
    seed(repo, [obs("Gate check", modified=["src/app.py"])])
    assert folders.update_folder_claude_md_files(repo, ["src/app.py"], "repo",
                                                 settings={**load_settings(repo), "folder_context": False}) == []
    assert not (repo / "src" / "CLAUDE.md").exists()


def test_update_uses_local_md_when_configured(repo):
    seed(repo, [obs("Local file", modified=["src/app.py"])])
    written = folders.update_folder_claude_md_files(repo, ["src/app.py"], "repo",
                                                    settings=enabled(repo, folder_use_local_md=True))
    assert written == [repo / "src" / "CLAUDE.local.md"]
    assert not (repo / "src" / "CLAUDE.md").exists()


@pytest.mark.parametrize("exclude", [json.dumps(["src"]), json.dumps(["src/"]), "['src']", "ABS"])
def test_update_honours_folder_md_exclude(repo, exclude):
    (repo / "src" / "deep").mkdir()
    seed(repo, [obs("Excluded work", modified=["src/app.py", "src/deep/x.py"])])
    value = json.dumps([str(repo / "src")]) if exclude == "ABS" else exclude
    written = folders.update_folder_claude_md_files(repo, ["src/app.py", "src/deep/x.py"], "repo",
                                                    settings=enabled(repo, folder_md_exclude=value))
    assert written == []
    assert not (repo / "src" / "CLAUDE.md").exists() and not (repo / "src" / "deep" / "CLAUDE.md").exists()


def test_update_skips_unsafe_invalid_and_active_folders(repo, tmp_path):
    for d in ("node_modules/pkg", "build", "lib", "nested", "src/src", "my dir"):
        (repo / d).mkdir(parents=True, exist_ok=True)
    (repo / "nested" / ".git").mkdir()
    paths = ["README.md", "node_modules/pkg/index.js", "build/out.js", "nested/mod.py", "src/src/dup.py",
             "my dir/a.py", "lib/#1", "~/notes.md", "https://example.com/a.py", str(tmp_path / "elsewhere.py"),
             "../outside/x.py", "", "lib/CLAUDE.md", "lib/code.py", "src/app.py"]
    seed(repo, [obs("Everything", modified=[p for p in paths if p])])
    written = folders.update_folder_claude_md_files(repo, paths, "repo", settings=enabled(repo))
    assert written == [repo / "src" / "CLAUDE.md"]
    for d in ("", "node_modules/pkg", "build", "lib", "nested", "src/src", "my dir"):
        assert not (repo / d / "CLAUDE.md").exists(), d
    assert not (tmp_path / "CLAUDE.md").exists()


def test_update_lists_direct_children_of_the_folder_only(repo):
    (repo / "src" / "sub").mkdir()
    (repo / "lib" / "src").mkdir(parents=True)
    seed(repo, [obs("Top level edit", modified=["src/app.py"]),
                obs("Nested edit", modified=["src/sub/deep.py"]),
                obs("Lookalike edit", modified=["lib/src/other.py"])])
    folders.update_folder_claude_md_files(repo, ["src/app.py", "src/sub/deep.py"], "repo", settings=enabled(repo))
    top = (repo / "src" / "CLAUDE.md").read_text()
    assert "Top level edit" in top and "Nested edit" not in top and "Lookalike edit" not in top
    sub = (repo / "src" / "sub" / "CLAUDE.md").read_text()
    assert "Nested edit" in sub and "Top level edit" not in sub


def test_update_filters_by_project_and_lists_session_summaries(repo):
    seed(repo, [obs("Mine", modified=["src/app.py"])], summary={"request": "Fix the login flow",
                                                             "completed": "done"})
    seed(repo, [obs("Someone else's", modified=["src/app.py"])], project="other", session="content-2")
    folders.update_folder_claude_md_files(repo, ["src/app.py"], "repo", settings=enabled(repo))
    text = (repo / "src" / "CLAUDE.md").read_text()
    assert "Mine" in text and "Someone else's" not in text
    assert "#S1" in text and "Fix the login flow" in text and "| ◎ |" in text


def test_no_activity_never_creates_and_empties_an_existing_block(repo):
    (repo / "lib").mkdir()
    seed(repo, [obs("Only src", modified=["src/app.py"])])
    assert folders.update_folder_claude_md_files(repo, ["lib/x.py"], "repo", settings=enabled(repo)) == []
    assert not (repo / "lib" / "CLAUDE.md").exists()
    target = repo / "lib" / "CLAUDE.md"
    target.write_text(f"Keep\n\n{OPEN}\nold rows\n{CLOSE}\n")
    assert folders.update_folder_claude_md_files(repo, ["lib/x.py"], "repo", settings=enabled(repo)) == [target]
    assert target.read_text() == f"Keep\n\n{OPEN}\n\n{CLOSE}\n"


def test_skeleton_denylist_leaves_empty_folders_alone(repo):
    (repo / "lib").mkdir()
    seed(repo, [obs("Only src", modified=["src/app.py"])])
    target = repo / "lib" / "CLAUDE.md"
    original = f"Keep\n\n{OPEN}\nold rows\n{CLOSE}\n"
    target.write_text(original)
    written = folders.update_folder_claude_md_files(
        repo, ["lib/x.py", "src/app.py"], "repo", settings=enabled(repo, folder_md_skeleton_denylist='["lib"]'))
    assert written == [repo / "src" / "CLAUDE.md"]
    assert target.read_text() == original


def test_update_without_a_store_is_a_no_op(repo):
    assert folders.update_folder_claude_md_files(repo, ["src/app.py"], "repo", settings=enabled(repo)) == []


# ---- block helpers -------------------------------------------------------------------------------------------
def test_replace_tagged_content():
    assert folders.replace_tagged_content("", "New") == f"{OPEN}\nNew\n{CLOSE}"
    assert (folders.replace_tagged_content(f"before\n{OPEN}\nold\n{CLOSE}\nafter", "new")
            == f"before\n{OPEN}\nnew\n{CLOSE}\nafter")
    assert folders.replace_tagged_content("docs", "gen") == f"docs\n\n{OPEN}\ngen\n{CLOSE}"
    assert (folders.replace_tagged_content(f"x\n{OPEN}\nhalf", "n")
            == f"x\n{OPEN}\nhalf\n\n{OPEN}\nn\n{CLOSE}")
    assert (folders.replace_tagged_content(f"x\n{CLOSE}\ny", "n") == f"x\n{CLOSE}\ny\n\n{OPEN}\nn\n{CLOSE}")


def test_to_bmp_safe():
    assert folders.to_bmp_safe("\U0001F534 bug \U0001F3AF \U0001F600 ok\ud800") == "● bug ◎ • ok"
    assert folders.to_bmp_safe("") == ""
    assert folders.to_bmp_safe("plain ✓") == "plain ✓"


def test_format_timeline_for_claude_md():
    assert folders.format_timeline_for_claude_md("") == ""
    assert folders.format_timeline_for_claude_md("no table rows here") == ""
    text = ("### Sep 25, 2026\n\n| ID | Time | T | Title | Read | Work |\n|-----|------|---|-------|------|------|\n"
            "| #124 | 4:30 PM | \U0001F535 | Second | ~150 | - |\n| #123 | ″ | \U0001F535 | First | ~100 | - |\n"
            "| #S7 | 9:05 AM | \U0001F3AF | Session work | - | - |\n")
    out = folders.format_timeline_for_claude_md(text)
    assert out.splitlines()[:6] == ["# Recent Activity", "", "### Sep 25, 2026", "",
                                    "| ID | Time | T | Title | Read |", "|----|------|---|-------|------|"]
    assert "| #124 | 4:30 PM | \U0001F535 | Second | ~150 |" in out
    assert '| #123 | " | \U0001F535 | First | ~100 |' in out
    assert "| #S7 | 9:05 AM | \U0001F3AF | Session work | - |" in out


def test_write_claude_md_to_folder_guards(tmp_path):
    missing = tmp_path / "deep" / "nested"
    assert folders.write_claude_md_to_folder(missing, "x", "CLAUDE.md") is None
    assert not (tmp_path / "deep").exists()
    git_dir = tmp_path / "proj" / ".git" / "refs"
    git_dir.mkdir(parents=True)
    assert folders.write_claude_md_to_folder(git_dir, "x", "CLAUDE.md") is None
    assert not (git_dir / "CLAUDE.md").exists()
    ok = tmp_path / "src" / "git-utils"
    ok.mkdir(parents=True)
    out = folders.write_claude_md_to_folder(ok, "hello \U0001F7E3", "CLAUDE.md")
    assert out == ok / "CLAUDE.md" and out.read_text() == f"{OPEN}\nhello ◆\n{CLOSE}"
    assert not (ok / "CLAUDE.md.tmp").exists()


def test_write_agents_md_replaces_only_its_block(tmp_path):
    path = tmp_path / "sub" / "AGENTS.md"
    folders.write_agents_md(path, "first context")
    assert path.read_text() == f"{OPEN}\n# Memory Context\n\nfirst context\n{CLOSE}"
    path.write_text(f"# Agents\n\nHouse rules.\n\n{path.read_text()}\n\nFooter.\n")
    folders.write_agents_md(str(path), "second context")
    text = path.read_text()
    assert text == f"# Agents\n\nHouse rules.\n\n{OPEN}\n# Memory Context\n\nsecond context\n{CLOSE}\n\nFooter.\n"
    folders.write_agents_md("", "ignored")
    git_file = tmp_path / ".git" / "AGENTS.md"
    folders.write_agents_md(git_file, "x")
    assert not git_file.exists()


def test_inject_context_into_markdown_file(tmp_path):
    fresh = tmp_path / "rules" / "context.md"
    folders.inject_context_into_markdown_file(fresh, "ctx \U0001F534", "# Header")
    assert fresh.read_text() == f"# Header\n\n{OPEN}\nctx ●\n{CLOSE}\n"
    plain = tmp_path / "plain.md"
    folders.inject_context_into_markdown_file(plain, "one")
    assert plain.read_text() == f"{OPEN}\none\n{CLOSE}\n"
    plain.write_text("User notes\n\n\n")
    folders.inject_context_into_markdown_file(plain, "two")
    assert plain.read_text() == f"User notes\n\n{OPEN}\ntwo\n{CLOSE}\n"
    folders.inject_context_into_markdown_file(plain, "three")
    assert plain.read_text() == f"User notes\n\n{OPEN}\nthree\n{CLOSE}\n"


# ---- Cursor rules file ----------------------------------------------------------------------------------------
def test_cursor_context_setup_update_and_removal(repo, tmp_path):
    seed(repo, [obs("Wire the cache", modified=["src/app.py"])])
    assert folders.update_cursor_context_for_project(repo, "repo") is False  # not set up for Cursor
    assert not folders.cursor_context_path(repo).exists()

    assert folders.setup_cursor_project_context(repo, "repo") is True
    rules = folders.cursor_context_path(repo)
    assert rules == repo / ".cursor" / "rules" / "cairn-context.mdc"
    text = rules.read_text()
    assert text.startswith('---\nalwaysApply: true\ndescription: "Cairn context from past sessions (auto-updated)"\n'
                           "---\n\n# Memory Context from Past Sessions\n")
    assert "Wire the cache" in text and text.endswith("for more detailed queries.*\n")
    assert all(ord(ch) <= 0xFFFF for ch in text)
    registry = json.loads((tmp_path / "home" / "cursor-projects.json").read_text())
    assert registry["repo"]["workspacePath"] == str(repo) and registry["repo"]["installedAt"]

    seed(repo, [obs("Evict stale entries", modified=["src/app.py"])], session="content-9")
    assert folders.update_cursor_context_for_project(repo, "repo") is True
    assert "Evict stale entries" in rules.read_text()
    assert folders.update_cursor_context_for_project(None, "repo") is True  # store found from the workspace

    assert folders.remove_cursor_project_context(repo, "repo") == [str(rules)]
    assert not rules.exists() and folders.read_cursor_registry() == {}


def test_cursor_setup_writes_a_placeholder_without_memory(tmp_path, monkeypatch):
    monkeypatch.setenv("CAIRN_HOME", str(tmp_path / "home"))
    workspace = tmp_path / "fresh"
    workspace.mkdir()
    assert folders.setup_cursor_project_context(workspace, "fresh") is False
    assert folders.cursor_context_path(workspace).read_text() == folders.CURSOR_PLACEHOLDER
    assert "fresh" in folders.read_cursor_registry()


def test_cursor_registry_and_mcp_config(tmp_path):
    reg = tmp_path / "reg.json"
    assert folders.read_cursor_registry(reg) == {}
    reg.write_text("{broken")
    assert folders.read_cursor_registry(reg) == {}
    folders.register_cursor_project("a", "/ws/a", reg)
    folders.register_cursor_project("b", "/ws/b", reg)
    folders.unregister_cursor_project("a", reg)
    assert list(folders.read_cursor_registry(reg)) == ["b"]
    mcp = tmp_path / ".cursor" / "mcp.json"
    mcp.parent.mkdir()
    mcp.write_text(json.dumps({"mcpServers": {"other": {"command": "x"}}}))
    folders.configure_cursor_mcp(mcp, "cairn", ["mcp"])
    assert json.loads(mcp.read_text())["mcpServers"] == {"other": {"command": "x"},
                                                          "cairn": {"command": "cairn", "args": ["mcp"]}}


# ---- cairn recall claude-md generate | clean ----------------------------------------------------------------------
def test_generate_dry_run_then_write(repo, capsys):
    (repo / "src" / "util.py").write_text("")
    seed(repo, [obs("Split helpers", modified=["src/app.py"], type_="feature"),
                obs("Read utilities", read=["src/util.py"])])
    assert folders.generate_claude_md(repo, dry_run=True) == 0
    assert "would write src/CLAUDE.md (2 observations)" in capsys.readouterr().out
    assert not (repo / "src" / "CLAUDE.md").exists()

    assert folders.generate_claude_md(repo) == 0
    assert "wrote src/CLAUDE.md (2 observations)" in capsys.readouterr().out
    text = (repo / "src" / "CLAUDE.md").read_text()
    assert text.startswith(f"{OPEN}\n# Recent Activity\n\n{folders.GENERATED_NOTE}\n\n### ")
    assert "**app.py**" in text and "**util.py**" in text and "| ◆ | Split helpers |" in text
    assert not (repo / ".cairn" / "CLAUDE.md").exists()


def test_generate_without_store_or_folders(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("CAIRN_HOME", str(tmp_path / "home"))
    empty = tmp_path / "empty"
    empty.mkdir()
    assert folders.generate_claude_md(empty) == 0
    assert "No folders found" in capsys.readouterr().out
    (empty / "pkg").mkdir()
    assert folders.generate_claude_md(empty) == 0
    assert "No session store found" in capsys.readouterr().out


@pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")
def test_tracked_folders_uses_git(tmp_path):
    root = tmp_path / "g"
    (root / "a" / "b").mkdir(parents=True)
    (root / "a" / "b" / "f.py").write_text("")
    (root / "untracked").mkdir()
    (root / "untracked" / "u.py").write_text("")
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "add", "a/b/f.py"], cwd=root, check=True)
    assert folders.tracked_folders(root) == {root / "a", root / "a" / "b"}


def test_clean_removes_only_blocks_and_deletes_emptied_files(repo, capsys):
    only_block = repo / "src" / "CLAUDE.md"
    only_block.write_text(f"{OPEN}\n# Recent Activity\n{CLOSE}\n")
    (repo / "docs").mkdir()
    mixed = repo / "docs" / "CLAUDE.md"
    mixed.write_text(f"# Docs\n\n{OPEN}\nrows\n{CLOSE}\n\nMore notes\n")
    untouched = repo / "CLAUDE.md"
    untouched.write_text("# Root instructions\n")
    (repo / "node_modules").mkdir()
    ignored = repo / "node_modules" / "CLAUDE.md"
    ignored.write_text(f"{OPEN}\nx\n{CLOSE}")

    assert folders.clean_claude_md(repo, dry_run=True) == 0
    assert "would delete (empty) src/CLAUDE.md" in capsys.readouterr().out
    assert only_block.exists() and OPEN in mixed.read_text()

    assert folders.clean_claude_md(repo) == 0
    out = capsys.readouterr().out
    assert "Done: 1 deleted, 1 cleaned, 0 errors" in out
    assert not only_block.exists()
    assert mixed.read_text() == "# Docs\n\n\n\nMore notes"
    assert untouched.read_text() == "# Root instructions\n"
    assert OPEN in ignored.read_text()
    assert folders.clean_claude_md(repo) == 0
    assert "No context files" in capsys.readouterr().out
