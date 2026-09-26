#!/usr/bin/env python3
"""Validate, install and activate a custom session-memory mode.

  python3 install_mode.py --mode <draft.json> [--mode-id <id>] [--root <repo>] [--dry-run] [--no-activate]

The draft (usually an inherited ``parent--custom`` override) is merged with its parent and the complete
mode is validated before anything is written. Installing writes the draft to the user modes directory
(``$CAIRN_RECALL_MODES_DIR`` when set, else ``$CAIRN_HOME/modes``) and activates it by setting ``mode`` in
the ``[recall]`` section of ``<repo>/.cairn/config.toml``; the previous mode file and config are backed up
first. Prints a JSON result; exit code 1 with a ``mode-creator:`` message on any validation error.

Run it with the Python environment Cairn is installed in (the interpreter named in the hook commands).
"""
from __future__ import annotations

import json
import os
import re
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path


def fail(message: str) -> None:
    sys.stderr.write(f"mode-creator: {message}\n")
    raise SystemExit(1)


def _import_cairn():
    """Cairn's own modules; when this script runs from inside the package tree the package is put on the path."""
    try:
        from cairn.engines.recall import modes, projects, settings
        return modes, projects, settings
    except ImportError:
        here = Path(__file__).resolve()
        for parent in here.parents:
            if (parent / "cairn" / "__init__.py").is_file():
                sys.path.insert(0, str(parent))
                try:
                    from cairn.engines.recall import modes, projects, settings
                    return modes, projects, settings
                except ImportError:
                    break
    fail("cannot import Cairn; run this script with the Python environment Cairn is installed in")
    raise AssertionError  # unreachable


MODES, PROJECTS, SETTINGS = _import_cairn()
MODE_ID_PATTERN = MODES.MODE_ID
ITEM_ID_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
REQUIRED_PROMPTS = [
    "system_identity", "spatial_awareness", "observer_role", "recording_focus", "skip_guidance", "type_guidance",
    "concept_guidance", "field_guidance", "output_format_header", "format_examples", "footer",
    "xml_title_placeholder", "xml_subtitle_placeholder", "xml_fact_placeholder", "xml_narrative_placeholder",
    "xml_concept_placeholder", "xml_file_placeholder", "xml_summary_request_placeholder",
    "xml_summary_investigated_placeholder", "xml_summary_learned_placeholder", "xml_summary_completed_placeholder",
    "xml_summary_next_steps_placeholder", "xml_summary_notes_placeholder", "header_memory_start",
    "header_memory_continued", "header_summary_checkpoint", "continuation_greeting", "continuation_instruction",
    "summary_instruction", "summary_context_label", "summary_format_instruction", "summary_footer",
]


def parse_args(argv: list[str]) -> dict:
    result: dict = {}
    i = 0
    while i < len(argv):
        token = argv[i]
        if not token.startswith("--"):
            fail(f"unexpected argument: {token}")
        key = token[2:]
        if key in ("dry-run", "no-activate"):
            result[key] = True
            i += 1
            continue
        value = argv[i + 1] if i + 1 < len(argv) else None
        if not value or value.startswith("--"):
            fail(f"missing value for --{key}")
        result[key] = value
        i += 2
    return result


def read_json(path: Path, label: str) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8").lstrip("﻿"))
    except (OSError, ValueError) as exc:
        fail(f"could not parse {label}: {exc}")
        raise AssertionError  # unreachable


def user_modes_dir() -> Path:
    env = os.environ.get("CAIRN_RECALL_MODES_DIR")
    if env:
        return Path(os.path.expanduser(env))
    return Path(os.environ.get("CAIRN_HOME") or Path.home() / ".cairn") / "modes"


def deep_merge(base, override):
    if not isinstance(base, dict):
        return override
    result = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def split_csv(value) -> list[str]:
    return [item.strip() for item in str(value or "").split(",") if item.strip()]


def require_string(value, label: str, allow_empty: bool = False) -> None:
    if not isinstance(value, str) or (not allow_empty and not value.strip()):
        fail(f"{label} must be {'a string' if allow_empty else 'a non-empty string'}")


def validate_items(items, label: str, required_fields: list[str]) -> None:
    if not isinstance(items, list) or not items:
        fail(f"{label} must contain at least one item")
    seen = set()
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            fail(f"{label}[{index}] must be an object")
        for field in required_fields:
            require_string(item.get(field), f"{label}[{index}].{field}")
        if not ITEM_ID_PATTERN.match(item["id"]):
            fail(f"{label}[{index}].id must be lowercase kebab-case")
        if item["id"] in seen:
            fail(f"{label} contains duplicate id: {item['id']}")
        seen.add(item["id"])


def validate_merged_mode(mode) -> None:
    if not isinstance(mode, dict):
        fail("mode must be a JSON object")
    require_string(mode.get("name"), "name")
    require_string(mode.get("description"), "description")
    require_string(mode.get("version"), "version")
    validate_items(mode.get("observation_types"), "observation_types", ["id", "label", "description", "emoji",
                                                                       "work_emoji"])
    validate_items(mode.get("observation_concepts"), "observation_concepts", ["id", "label", "description"])
    prompts = mode.get("prompts")
    if not isinstance(prompts, dict):
        fail("prompts must be an object")
    for prompt in REQUIRED_PROMPTS:
        require_string(prompts.get(prompt), f"prompts.{prompt}", prompt == "format_examples")
    for t in mode["observation_types"]:
        if t["id"] not in prompts["type_guidance"]:
            fail(f"prompts.type_guidance does not mention type: {t['id']}")
    for c in mode["observation_concepts"]:
        if c["id"] not in prompts["concept_guidance"]:
            fail(f"prompts.concept_guidance does not mention concept: {c['id']}")


def atomic_write_json(path: Path, value, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.parent / f".{path.name}.{os.getpid()}.tmp"
    tmp.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.chmod(tmp, mode)
    os.replace(tmp, path)


def timestamp() -> str:
    now = datetime.now(timezone.utc)
    return now.strftime("%Y-%m-%dT%H-%M-%S-") + f"{now.microsecond // 1000:03d}Z"


def backup(path: Path, stamp: str) -> str | None:
    if not path.exists():
        return None
    dest_dir = Path(os.environ.get("CAIRN_HOME") or Path.home() / ".cairn") / "backups"
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"{path.name}.backup-{stamp}"
    shutil.copyfile(path, dest)
    os.chmod(dest, 0o600)
    return str(dest)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    if "mode" not in args:
        fail("usage: install_mode.py --mode <draft.json> [--mode-id <id>] [--root <repo>] [--dry-run] [--no-activate]")
    source = Path(args["mode"]).resolve()
    if not source.exists():
        fail(f"draft mode file not found: {source}")
    mode_id = args.get("mode-id") or source.stem
    if not MODE_ID_PATTERN.match(mode_id):
        fail("mode ID must be lowercase kebab-case, optionally parent--override")

    bundled = Path(MODES.BUILTIN_DIR)
    modes_dir = user_modes_dir()
    destination = modes_dir / f"{mode_id}.json"
    draft = read_json(source, "draft mode")
    if (bundled / f"{mode_id}.json").exists():
        fail(f"mode ID collides with bundled mode: {mode_id}; choose a unique custom ID instead of shadowing "
             "bundled configuration")

    merged = draft
    parts = mode_id.split("--")
    if len(parts) == 2:
        parent_id = parts[0]
        parent = next((c for c in (modes_dir / f"{parent_id}.json", bundled / f"{parent_id}.json") if c.exists()), None)
        if parent is None:
            fail(f"parent mode not found: {parent_id}")
        merged = deep_merge(read_json(parent, f"parent mode {parent_id}"), draft)
    elif len(parts) != 1:
        fail("only one inheritance level is supported")
    validate_merged_mode(merged)

    root_arg = args.get("root")
    root = Path(root_arg).resolve() if root_arg else (PROJECTS.find_store_root(os.getcwd()) or Path.cwd())
    config = root / ".cairn" / "config.toml"

    if args.get("dry-run"):
        print(json.dumps({"ok": True, "dryRun": True, "modeId": mode_id, "sourcePath": str(source),
                          "destinationPath": str(destination), "config": str(config)}, indent=2))
        return 0

    modes_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    stamp = timestamp()
    mode_backup = backup(destination, stamp)
    atomic_write_json(destination, draft)

    config_backup = None
    activated = not args.get("no-activate")
    if activated:
        config_backup = backup(config, stamp)
        SETTINGS.save(root, {"mode": mode_id})
    print(json.dumps({"ok": True, "modeId": mode_id, "modeName": merged["name"], "installedAt": str(destination),
                      "activated": activated, "config": str(config) if activated else None,
                      "backups": {"mode": mode_backup, "config": config_backup}}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
