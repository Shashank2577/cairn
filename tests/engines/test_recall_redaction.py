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
    "basic-auth": ("Authorization: Ba" + "sic dXNlcjpwYXNzd29yZDEyMw==", "Authorization: Basic"),
    "huggingface-token": ("hf" + "_" + "A1b2C3d4E5" * 3 + "A1b2", "hf" + "_model_name"),
    "aws-secret-key-bare": ("wJalrXUtnFEMI/K7MDENG/bPxRfiCY" + "EXAMPLEKEY", "0123456789abcdef" * 2 + "01234567"),
    "slack-webhook": ("https://hooks." + "slack.com/services/T01ABCDEF/B01ABCDEF/A1b2C3d4E5f6G7h8I9j0", "https://hooks." + "slack.com/docs"),
    "aws-secret-key": ("aws_secret_" + "access_key = " + "A1b2C3d4E5f6G7h8I9j0" * 2, "aws_secret_" + "access_key = short"),
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


# ---- write paths: every place captured or derived text is stored or sent --------------------------------------
import subprocess  # noqa: E402

from cairn.engines.recall import hooks  # noqa: E402
from cairn.engines.recall.redaction_scan import COLUMNS, scan_store  # noqa: E402
from cairn.engines.recall.schema import store_path  # noqa: E402
from cairn.engines.recall.store import Store  # noqa: E402

TOK = CASES["github-token"][0]
KEY = CASES["aws-access-key-id"][0]


@pytest.fixture()
def repo(tmp_path, monkeypatch):
    monkeypatch.setenv("CAIRN_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("CAIRN_RECALL_NO_SPAWN", "1")
    for var in ("CAIRN_INTERNAL", "CLAUDE_PROJECT_DIR"):
        monkeypatch.delenv(var, raising=False)
    root = tmp_path / "shop"
    (root / ".cairn").mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    return root


def _everything(root) -> str:
    with Store.open(root) as st:
        out = []
        for table, cols in COLUMNS.items():
            out += [str(tuple(r)) for r in st.db.execute(f"SELECT {', '.join(cols)} FROM {table}")]
    return "\n".join(out)


def test_hooks_prompt_tool_io_and_summary_are_masked_everywhere(repo):
    def cap(event, **p):
        return hooks.run(event, {"session_id": "s1", "cwd": str(repo), **p}, "claude-code")
    (repo / ".cairn" / "config.toml").write_text('[team]\nserver = "https://cairn.example.com"\nproject = "p"\n',
                                                 encoding="utf-8")
    cap("session-init", prompt=f"use {TOK} please")
    cap("observation", tool_name="Bash", tool_use_id="t1", tool_input={"command": f"curl -u {KEY}"},
        tool_response={"out": [f"https://u:{B32}@host.example/x"]})
    cap("summarize", last_assistant_message=f"I used {TOK}")
    dump = _everything(repo)
    assert dump and TOK not in dump and KEY not in dump and B32 not in dump  # prompts, queue, tool_uses, outbox
    assert scan_store(store_path(repo)) == {}


def test_store_masks_model_written_memory_and_imports_but_keeps_prose(repo):
    with Store.open(repo) as st:
        st.import_sdk_session({"content_session_id": "c", "memory_session_id": "m1", "project": "p"})
        st.import_sdk_session({"content_session_id": "c2", "memory_session_id": "m2", "project": "p"})
        st.store_observations("m1", "p", [{"title": f"Found {TOK}", "narrative": "the token: refresh logic",
                                           "facts": [f"key {KEY}"]}],
                              {"request": f"r {TOK}", "learned": "password: rotation policy"})
        st.import_user_prompt({"content_session_id": "c", "prompt_number": 1, "prompt_text": f"x {TOK}"})
        st.import_observation({"memory_session_id": "m2", "title": f"t {KEY}", "narrative": "n", "created_at_epoch": 1})
        st.import_session_summary({"memory_session_id": "m2", "request": f"q {TOK}"})
        sid = st.db.execute("SELECT id FROM sdk_sessions WHERE memory_session_id='m1'").fetchone()[0]
        st.save_conversation(sid, [{"role": "user", "content": f"see {TOK}"}])
        st.enqueue(sid, "c", "summarize", last_assistant_message=f"a {TOK}", last_user_message=f"u {KEY}")
    dump = _everything(repo)
    assert TOK not in dump and KEY not in dump
    assert "the token: refresh logic" in dump and "password: rotation policy" in dump  # prose is not mangled


def test_deterministic_fallback_summary_is_masked():
    from cairn.engines.recall import fallback
    from cairn.engines.recall.modes import load_mode
    assert "redact" in fallback.__dict__ and fallback.redact(f"git push {TOK}") == "git push ***"


def test_ledger_holds_no_text():
    from cairn import store as brain
    ddl = brain.SCHEMA[brain.SCHEMA.index("CREATE TABLE IF NOT EXISTS ledger("):]
    ddl = ddl[:ddl.index(");")]
    text_cols = [ln for ln in ddl.split(",") if "TEXT" in ln]
    assert [ln.strip().split()[0] for ln in text_cols] == ["task", "tier", "model"]  # labels only


def test_scan_counts_per_shape_read_only(repo):
    with Store.open(repo) as st:  # create the schema
        st.db.execute("INSERT INTO user_prompts(content_session_id, prompt_number, prompt_text, created_at,"
                      " created_at_epoch) VALUES('c', 1, ?, 'x', 1)", (f"raw {TOK} and {KEY}",))
        st.db.execute("INSERT INTO user_prompts(content_session_id, prompt_number, prompt_text, created_at,"
                      " created_at_epoch) VALUES('c', 2, 'clean', 'x', 2)")
    before = _everything(repo)
    counts = scan_store(store_path(repo))
    assert counts == {"github-token": {"user_prompts": 1}, "aws-access-key-id": {"user_prompts": 1}}
    assert _everything(repo) == before  # nothing rewritten
    res = CliRunner().invoke(app, ["redaction-scan", "--json"], env={"PWD": str(repo)})
    assert TOK not in res.output and KEY not in res.output


# ---- review findings ---------------------------------------------------------------------------------------------
B64 = "dXNlcjpwYXNzd29yZDEyMw=="


def test_json_quoted_and_nested_secrets_are_masked():
    assert "hunter2" not in json.dumps(redact_value({"password": "hunter2"}))
    nested = redact_value({"env": {"API_KEY": "abc12345", "PATH": "/usr/bin"}})
    assert nested["env"]["API_KEY"] == "***" and nested["env"]["PATH"] == "/usr/bin"
    assert "hunter2" not in redact('{"password": "hunter2", "n": 1}')
    assert "abc12345" not in redact('{"env":{"API_KEY":"abc12345"}}')
    assert "abc12345" not in redact("{'api_key': 'abc12345'}")


def test_basic_auth_header_is_masked():
    out = redact("curl -H 'Authorization: Basic " + B64 + "' x")
    assert B64 not in out and "Authorization: Basic ***" in out
    assert B64 not in json.dumps(redact_value({"headers": {"Authorization": "Basic " + B64}}))
    assert "basic-auth" in redact_hits("Authorization: Basic " + B64)
    assert redact("Basic authentication is supported") == "Basic authentication is supported"


@pytest.mark.parametrize("text", [
    "max_tokens=1024", "token = foo.split()", "token: bucket algorithm", "Fix the password: validation bug",
    "tokens = tokenizer.encode(x)", "--max-tokens 1024", "password_field: str", "if token == None:",
    "the secret: how it works", "token=self.get_token()", "api_key=os.environ", "token=$TOKEN"])
def test_prose_and_code_are_not_mangled(text):
    assert redact(text) == text


@pytest.mark.parametrize("text,gone", [
    ("password=hunter2", "hunter2"), ("API_KEY=abc12345", "abc12345"), ("--token abcDEF123456", "abcDEF123456"),
    ("password: hunter2xyz9", "hunter2xyz9"), ('token = "s3cr3tvalue"', "s3cr3tvalue")])
def test_real_key_values_still_masked(text, gone):
    assert gone not in redact(text)


def test_url_userinfo_with_at_sign_in_password():
    out = redact("postgres://user:p@ss@host.example/db")
    assert out == "postgres://***@host.example/db"


def test_new_shapes():
    hook = "https://hooks." + "slack.com/services/" + "T01ABCDEF/B01ABCDEF/" + "A1b2C3d4E5f6G7h8I9j0K1l2"
    hf = "hf" + "_" + "A1b2C3d4E5" * 3 + "A1b2"
    aws = "aws_secret_" + "access_key = " + "A1b2C3d4E5f6G7h8I9j0" * 2
    for text, pid in ((hook, "slack-webhook"), (hf, "huggingface-token"), (aws, "aws-secret-key")):
        assert pid in redact_hits(text), pid
        assert redact(text).count("A1b2C3d4") == 0
    assert redact("see hf_model_name and hooks.slack.com docs") == "see hf_model_name and hooks.slack.com docs"


def test_twilio_ignores_plain_hex_ids():
    for ident in ("0123456789abcdef" * 4, "0123456789abcdef" * 2, "AC" + "0123456789ABCDEF" * 2,
                  "commit " + "a" * 40, "SKU" + "0123456789abcdef" * 2):
        assert "twilio-key" not in redact_hits(ident), ident


def test_store_masks_update_observation_and_session_fields(repo):
    with Store.open(repo) as st:
        sid = st.create_sdk_session("c1", "p", f"hello {TOK}", f"title {KEY}")
        st.create_sdk_session("c1", "p", "", f"later {TOK}")  # existing row path
        st.db.execute("UPDATE sdk_sessions SET custom_title=NULL WHERE id=?", (sid,))
        st.create_sdk_session("c1", "p", "", f"again {TOK}")  # UPDATE custom_title path
        st.import_sdk_session({"content_session_id": "cx", "memory_session_id": "m1", "project": "p"})
        ids = st.store_observations("m1", "p", [{"title": "t", "narrative": "n"}])["observation_ids"]
        st.update_observation(ids[0], {"title": f"a {TOK}", "narrative": f"b {KEY}", "text": f"c {TOK}",
                                       "facts": [f"d {KEY}"], "subtitle": f"e {TOK}"})
        st.update_observation(ids[0], {"facts": f"x {TOK}"})
    assert TOK not in _everything(repo) and KEY not in _everything(repo)
    with Store.open(repo) as st:
        assert TOK not in str([tuple(r) for r in st.db.execute("SELECT custom_title, user_prompt FROM sdk_sessions")])


def test_more_review_cases():
    for text in ("the token: expired error", "secret_key_length=32", "SECRET_KEY_LENGTH = 32",
                 "src/" + "a1B2c3D4e5" * 4 + "/file.py", "camelCaseIdentifierWithoutDigitsAtAllXYZabcdefg"):
        assert redact(text) == text, text
    assert "hunter2" not in redact("{'PASSWORD': 'hunter2', 'Nested': {'Api_Key': 'abc12345'}}")
    assert "hunter2" not in json.dumps(redact_value({"Config": [{"PASSWORD": "hunter2"}]}))
    bare = "wJalrXUtnFEMI/K7MDENG/bPxRfiCY" + "EXAMPLEKEY"
    assert redact(f"key is {bare}.") == "key is ***."


def test_redact_is_fast_on_large_text():
    import time
    big = ("line with token: words and https://host:80/a@b plus key=value\n" * 40_000) + "x" * 200_000
    t = time.perf_counter()
    redact(big)
    redact_value({"out": big})
    assert time.perf_counter() - t < 5
