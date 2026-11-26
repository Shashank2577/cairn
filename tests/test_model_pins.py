"""A project that pinned the old default fast model gets the current one; a deliberate choice is never touched."""
from __future__ import annotations

from cairn.modelpins import stale_pins, upgrade_pins
from cairn.project import Project

OLD = '''# header comment
[models]
provider = "claude-code"         # auto
fast = "claude-haiku-4-5-20251001"   # classification, summaries at volume
balanced = "claude-sonnet-5"         # extraction
deep = "claude-opus-5-5"

[deep]
enabled = "auto"
'''


def _project(tmp_path, toml: str) -> Project:
    (tmp_path / ".cairn").mkdir(exist_ok=True)
    (tmp_path / ".cairn" / "config.toml").write_text(toml, encoding="utf-8")
    p = Project(tmp_path)
    p.reload()
    return p


def test_the_old_default_is_reported_with_its_replacement(tmp_path):
    (pin,) = stale_pins(_project(tmp_path, OLD))
    assert pin == {"tier": "fast", "pinned": "claude-haiku-4-5-20251001", "default": "claude-haiku-5-5"}


def test_upgrade_changes_only_that_line_and_keeps_comments(tmp_path):
    p = _project(tmp_path, OLD)
    done = upgrade_pins(p)
    assert [d["tier"] for d in done] == ["fast"]
    text = (tmp_path / ".cairn" / "config.toml").read_text(encoding="utf-8")
    assert 'fast = "claude-haiku-5-5"' in text and "# classification, summaries at volume" in text
    others = lambda t: [ln for ln in t.splitlines() if not ln.startswith("fast")]  # noqa: E731
    assert others(text) == others(OLD)  # nothing else moved
    assert p.cfg("models.fast") == "claude-haiku-5-5" and stale_pins(p) == [] and upgrade_pins(p) == []


def test_a_deliberate_choice_is_left_alone(tmp_path):
    for toml in ('[models]\nfast = "claude-haiku-5-5"\n', '[models]\nfast = "my-local-model"\n', '[models]\nfast = "claude-sonnet-5"\n', ""):
        p = _project(tmp_path, toml)
        assert stale_pins(p) == [] and upgrade_pins(p) == []


def test_a_local_override_is_not_rewritten_in_the_shared_file(tmp_path):
    p = _project(tmp_path, '[models]\nfast = "claude-haiku-5-5"\n')
    p.local_config_path.write_text('[models]\nfast = "claude-haiku-4-5-20251001"\n', encoding="utf-8")
    p.reload()
    assert [x["tier"] for x in stale_pins(p)] == ["fast"]
    upgrade_pins(p)
    assert "claude-haiku-5-5" in p.local_config_path.read_text(encoding="utf-8")


def test_doctor_warns_and_models_upgrade_fixes(cairn):
    from typer.testing import CliRunner

    from cairn.cli import app
    cairn.project.set_cfg("models.fast", "claude-haiku-4-5-20251001")
    runner = CliRunner()
    out = runner.invoke(app, ["doctor"]).output
    assert "Model pins" in out and "pinned" in out and "upgrade" in out  # the table wraps, so match single words
    res = runner.invoke(app, ["models", "--upgrade"])
    assert res.exit_code == 0 and "claude-haiku-5-5" in res.output
    assert "claude-haiku-5-5" in (cairn.project.root / ".cairn" / "config.toml").read_text(encoding="utf-8")
    assert "pinned" not in runner.invoke(app, ["doctor"]).output
