"""Credential masking: each shape has a positive and a look-alike negative. Values are synthetic and built
by concatenation so no secret-looking string sits in the repository."""
from __future__ import annotations

import json

import pytest
from typer.testing import CliRunner

from cairn.cli import app
from cairn.engines.recall.tags import SECRET_PATTERNS, redact, redact_hits, redact_value

A36 = "Ab1" * 12
B32 = "Zy9" * 11
HEX32 = "0123456789abcdef" * 2
JWT_P = "eyJ" + "hbGciOiJIUzI1"

CASES = {  # id: (secret, look-alike that is not one)
    "github-token": ("gh" + "p_" + A36, "gh" + "p_short"),
    "gitlab-token": ("gl" + "pat-" + "a1B2c3D4e5F6g7H8i9J0", "gl" + "pat-tiny"),
    "aws-access-key-id": ("AK" + "IA" + "ABCDEFGHIJKLMNOP", "AK" + "IA" + "abcdefghijklmnop"),
    "jwt": (JWT_P + "." + "eyJ" + "zdWIiOiIxMjM0NTY3" + "." + "sig" + "nature12345", "eyJ" + "onlyone.part"),
    "slack-token": ("xo" + "xb-" + "1234567890-abcdefghij", "xo" + "xb-1"),
    "stripe-key": ("sk" + "_live_" + "a1B2c3D4e5F6g7H8i9", "sk" + "_live_x"),
    "anthropic-key": ("sk" + "-ant-" + "api03-" + "A1b2C3d4E5f6G7h8I9j0", "sk" + "-ant-x"),
    "openai-key": ("sk" + "-" + B32, "sk" + "-learn-more"),
    "npm-token": ("np" + "m_" + "A1b2C3d4E5" * 3 + "A1b2C3", "np" + "m_install_deps"),
    "pypi-token": ("py" + "pi-" + "AgEIcHlwaS5vcmc" + "A1b2C3d4E5f6G7h8I9j0", "py" + "pi-package"),
    "sendgrid-key": ("S" + "G." + "A1b2C3d4E5f6G7h8" + "." + "I9j0K1l2M3n4O5p6", "S" + "G.short"),
    "twilio-key": ("S" + "K" + HEX32, "S" + "K" + "nothex" * 6),
    "google-api-key": ("AI" + "za" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r", "AI" + "za" + "short"),
    "azure-storage-key": ("Account" + "Key=" + "A1b2C3d4E5f6G7h8" * 4 + "==", "Account" + "Key=short"),
    "url-credentials": ("https://" + "alice" + ":" + "hunter2x" + "@example.com/repo", "https://example.com:8080/a@b"),
    "private-key": ("-----BEGIN " + "RSA PRIVATE KEY-----\nMIIabc\n-----END " + "RSA PRIVATE KEY-----",
                    "-----BEGIN " + "PUBLIC KEY-----\nMIIabc\n-----END " + "PUBLIC KEY-----"),
}


def test_every_pattern_has_a_case():
    assert {pid for pid, _, _ in SECRET_PATTERNS} == set(CASES)


@pytest.mark.parametrize("pid", sorted(CASES))
def test_secret_is_masked(pid):
    secret, _ = CASES[pid]
    out = redact(f"before {secret} after")
    assert "***" in out and pid in redact_hits(secret)
    if pid == "url-credentials":
        assert "hunter2x" not in out
    else:
        assert secret[-12:] not in out


@pytest.mark.parametrize("pid", sorted(CASES))
def test_lookalike_is_untouched(pid):
    _, fake = CASES[pid]
    assert pid not in redact_hits(fake)
    assert redact(fake) == fake


def test_url_keeps_host():
    assert redact("git clone " + CASES["url-credentials"][0]) == "git clone https://***@example.com/repo"


def test_key_value_masking_and_format_unchanged():
    assert redact("password=abc123 and --token xyz Bearer abc.def") == "password=*** and --token *** Bearer ***"


def test_redact_value_walks_json():
    tok = CASES["github-token"][0]
    out = redact_value({"a": [tok, {"b": "x " + tok}], "n": 3})
    assert tok not in json.dumps(out) and out["n"] == 3


def test_ingest_masks_prompt_and_tool_io(tmp_path):
    from cairn.engines.recall.ingest import _json_text, session_init
    from cairn.engines.recall.tags import normalize_stored_prompt_text  # noqa: F401
    tok = CASES["github-token"][0]
    assert tok not in _json_text({"tool_input": {"command": f"curl -H {tok}"}, "r": [tok]})
    import inspect
    assert "redact(" in inspect.getsource(session_init)


def test_cli_reports_lines_not_values(tmp_path):
    tok = CASES["github-token"][0]
    f = tmp_path / "sample.txt"
    f.write_text(f"fine line\nexport X={tok}\n", encoding="utf-8")
    res = CliRunner().invoke(app, ["redaction-test", str(f)])
    assert res.exit_code == 0
    assert "line 2" in res.output and "github-token" in res.output and "line 1" not in res.output
    assert tok not in res.output
