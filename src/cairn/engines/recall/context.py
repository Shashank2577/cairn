"""Session-start context: a compact timeline of recent observations and session summaries, with
token economics (tokens to read the index vs. the work tokens that produced it). Standard library
only; runs inside the SessionStart hook.

Agents receive a plain index (``for_human=False``); terminals get the colored rendering. The block is
fitted to the hook delivery limit by dropping whole items, most expensive first.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from . import health, schema
from .fmt import (
    compact_time,
    extract_first_file,
    format_date,
    format_datetime,
    format_time,
    header_datetime,
    parse_json_array,
    thousands,
)
from .modes import Mode, load_mode
from .platforms import normalize_platform_source
from .projects import project_context
from .settings import csv
from .settings import load as load_settings
from .tags import SYSTEM_REMINDER_RE

CONTEXT_OUTPUT_LIMIT = 10_000
SUMMARY_LOOKAHEAD = 1
CHARS_PER_TOKEN = 4

C = {"reset": "\x1b[0m", "bright": "\x1b[1m", "dim": "\x1b[2m", "cyan": "\x1b[36m", "green": "\x1b[32m",
     "yellow": "\x1b[33m", "blue": "\x1b[34m", "magenta": "\x1b[35m", "gray": "\x1b[90m", "red": "\x1b[31m"}


@dataclass
class ContextConfig:
    total_observation_count: int
    full_observation_count: int
    session_count: int
    show_read_tokens: bool
    show_work_tokens: bool
    show_savings_amount: bool
    show_savings_percent: bool
    observation_types: list[str]
    observation_concepts: list[str]
    full_observation_field: str
    show_last_summary: bool
    show_last_message: bool


def _narrow(configured: str, mode_ids: list[str]) -> list[str]:
    """Settings narrow the mode's list, never widen it; ids a mode does not know are ignored."""
    ids = csv(configured)
    if not ids:
        return list(mode_ids)
    valid = [i for i in ids if i in mode_ids]
    return valid or list(mode_ids)


def load_config(settings: dict, mode: Mode) -> ContextConfig:
    return ContextConfig(
        total_observation_count=int(settings["context_observations"]),
        full_observation_count=int(settings["context_full_count"]),
        session_count=int(settings["context_session_count"]),
        show_read_tokens=bool(settings["context_show_read_tokens"]),
        show_work_tokens=bool(settings["context_show_work_tokens"]),
        show_savings_amount=bool(settings["context_show_savings_amount"]),
        show_savings_percent=bool(settings["context_show_savings_percent"]),
        observation_types=_narrow(settings["context_observation_types"], mode.type_ids()),
        observation_concepts=_narrow(settings["context_observation_concepts"], mode.concept_ids()),
        full_observation_field=str(settings["context_full_field"] or "narrative"),
        show_last_summary=bool(settings["context_show_last_summary"]),
        show_last_message=bool(settings["context_show_last_message"]),
    )


# ---- token economics ------------------------------------------------------------------------------------
def observation_tokens(obs: dict) -> int:
    """Tokens to read one observation: title, subtitle, narrative and the facts as stored."""
    facts = obs.get("facts")
    size = (len(obs.get("title") or "") + len(obs.get("subtitle") or "") + len(obs.get("narrative") or "")
            + len(json.dumps(facts if facts else [], ensure_ascii=False)))
    return -(-size // CHARS_PER_TOKEN)


def token_economics(observations: list[dict]) -> dict:
    read = sum(observation_tokens(o) for o in observations)
    work = sum(int(o.get("discovery_tokens") or 0) for o in observations)
    savings = work - read
    return {"totalObservations": len(observations), "totalReadTokens": read, "totalDiscoveryTokens": work,
            "savings": savings, "savingsPercent": round(savings / work * 100) if work > 0 else 0}


def _show_economics(cfg: ContextConfig) -> bool:
    return cfg.show_read_tokens or cfg.show_work_tokens or cfg.show_savings_amount or cfg.show_savings_percent


# ---- queries -----------------------------------------------------------------------------------------------
_OBS_SELECT = ("o.id, o.memory_session_id, COALESCE(s.platform_source, 'claude') AS platform_source,"
               " s.content_session_id, o.type, o.title, o.subtitle, o.narrative, o.facts, o.concepts, o.files_read,"
               " o.files_modified, o.discovery_tokens, o.created_at, o.created_at_epoch, o.project")


def query_observations(db: sqlite3.Connection, projects: list[str], cfg: ContextConfig,
                       platform_source: str | None = None, limit: int | None = None) -> list[dict]:
    projects = [p for p in projects if p.strip()]
    types, concepts = cfg.observation_types, cfg.observation_concepts
    if not types or not concepts:
        return []
    proj = ""
    args: list[Any] = [platform_source, platform_source]
    if projects:
        marks = ",".join("?" * len(projects))
        proj = f"AND (o.project IN ({marks}) OR o.merged_into_project IN ({marks}))"
        args += projects + projects
    args += types + concepts + [limit if limit is not None else cfg.total_observation_count]
    rows = db.execute(
        f"SELECT {_OBS_SELECT} FROM observations o LEFT JOIN sdk_sessions s ON o.memory_session_id = s.memory_session_id"
        f" WHERE (? IS NULL OR s.platform_source = ?) {proj} AND o.type IN ({','.join('?' * len(types))})"
        f" AND EXISTS (SELECT 1 FROM json_each(o.concepts) WHERE value IN ({','.join('?' * len(concepts))}))"
        " ORDER BY o.created_at_epoch DESC LIMIT ?", args).fetchall()
    return [dict(r) for r in rows]


def count_observations(db: sqlite3.Connection, projects: list[str], platform_source: str | None = None) -> int:
    if not projects:
        return 0
    marks = ",".join("?" * len(projects))
    return int(db.execute(
        "SELECT COUNT(*) FROM observations o LEFT JOIN sdk_sessions s ON o.memory_session_id = s.memory_session_id"
        f" WHERE (o.project IN ({marks}) OR o.merged_into_project IN ({marks})) AND (? IS NULL OR s.platform_source = ?)",
        (*projects, *projects, platform_source, platform_source)).fetchone()[0])


def query_summaries(db: sqlite3.Connection, projects: list[str], cfg: ContextConfig,
                    platform_source: str | None = None) -> list[dict]:
    if not projects:
        return []
    marks = ",".join("?" * len(projects))
    rows = db.execute(
        "SELECT ss.id, ss.memory_session_id, COALESCE(s.platform_source, 'claude') AS platform_source, ss.request,"
        " ss.investigated, ss.learned, ss.completed, ss.next_steps, ss.created_at, ss.created_at_epoch, ss.project"
        " FROM session_summaries ss LEFT JOIN sdk_sessions s ON ss.memory_session_id = s.memory_session_id"
        f" WHERE (ss.project IN ({marks}) OR ss.merged_into_project IN ({marks})) AND (? IS NULL OR s.platform_source = ?)"
        " ORDER BY ss.created_at_epoch DESC LIMIT ?",
        (*projects, *projects, platform_source, platform_source, cfg.session_count + SUMMARY_LOOKAHEAD)).fetchall()
    return [dict(r) for r in rows]


# ---- prior assistant message ---------------------------------------------------------------------------------
def cwd_to_dashed(cwd: str) -> str:
    return re.sub(r"[/.]", "-", cwd)


def claude_config_dir() -> Path:
    return Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude")


def _last_assistant_text(lines: list[str]) -> str:
    for line in reversed(lines):
        if '"type":"assistant"' not in line.replace(" ", ""):
            continue
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        content = (entry.get("message") or {}).get("content")
        if entry.get("type") == "assistant" and isinstance(content, list):
            text = "".join(b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text")
            text = SYSTEM_REMINDER_RE.sub("", text).strip()
            if text:
                return text
    return ""


def prior_session_message(observations: list[dict], cfg: ContextConfig, current_session_id: str | None,
                          cwd: str) -> str:
    if not cfg.show_last_message or not observations:
        return ""
    prior = next((o for o in observations if o.get("content_session_id") != current_session_id
                  and o.get("memory_session_id") != current_session_id), None)
    if not prior:
        return ""
    sid = prior.get("content_session_id") or prior.get("memory_session_id")
    path = claude_config_dir() / "projects" / cwd_to_dashed(cwd) / f"{sid}.jsonl"
    try:
        content = path.read_text(errors="replace", encoding="utf-8").strip()
    except OSError:
        return ""
    return _last_assistant_text([ln for ln in content.split("\n") if ln.strip()]) if content else ""


# ---- timeline assembly ---------------------------------------------------------------------------------------
def prepare_summaries(display: list[dict], all_: list[dict]) -> list[dict]:
    newest = all_[0]["id"] if all_ else None
    out = []
    for i, s in enumerate(display):
        older = None if i == 0 else (all_[i + 1] if i + 1 < len(all_) else None)
        out.append({**s, "displayEpoch": older["created_at_epoch"] if older else s["created_at_epoch"],
                    "displayTime": older["created_at"] if older else s["created_at"],
                    "shouldShowLink": s["id"] != newest})
    return out


def build_timeline(observations: list[dict], summaries: list[dict]) -> list[tuple[str, dict]]:
    items = [("observation", o) for o in observations] + [("summary", s) for s in summaries]
    items.sort(key=lambda it: it[1]["created_at_epoch"] if it[0] == "observation" else it[1]["displayEpoch"])
    return items


def _group_by_day(timeline: list[tuple[str, dict]]) -> list[tuple[str, list]]:
    groups: dict[str, list] = {}
    order: dict[str, int] = {}
    for kind, d in timeline:
        key = format_date(d["created_at_epoch"] if kind == "observation" else d["displayEpoch"])
        groups.setdefault(key, []).append((kind, d))
        order.setdefault(key, d["created_at_epoch"] if kind == "observation" else d["displayEpoch"])
    return sorted(groups.items(), key=lambda kv: order[kv[0]])


def _detail(obs: dict, cfg: ContextConfig) -> str | None:
    if cfg.full_observation_field == "narrative":
        return obs.get("narrative")
    facts = parse_json_array(obs.get("facts"))
    return "\n".join(str(f) for f in facts) if facts else None


def _render_agent(project: str, mode: Mode, observations: list[dict], summaries: list[dict], cfg: ContextConfig,
                  cwd: str, session_id: str | None) -> str:
    econ = token_economics(observations)
    out = [f"# [{project}] recent context, {header_datetime()}", f"Mode: {mode.label()}", ""]
    legend = " ".join(f"{t.get('emoji', '')}{t['id']}" for t in mode.observation_types)
    out += [f"Legend: \U0001F3AFsession {legend}", "Format: ID TIME TYPE TITLE",
            "Fetch details: get_observations([IDs]) | Search: the recall search tool", ""]
    if _show_economics(cfg):
        parts = [f"{econ['totalObservations']} obs ({thousands(econ['totalReadTokens'])}t read)",
                 f"{thousands(econ['totalDiscoveryTokens'])}t work"]
        if econ["totalDiscoveryTokens"] > 0 and (cfg.show_savings_amount or cfg.show_savings_percent):
            parts.append(f"{econ['savingsPercent']}% savings" if cfg.show_savings_percent
                         else f"{thousands(econ['savings'])}t saved")
        out += [f"Stats: {' | '.join(parts)}", ""]
    display = summaries[: cfg.session_count]
    timeline = build_timeline(observations, prepare_summaries(display, summaries))
    full_ids = {o["id"] for o in observations[: cfg.full_observation_count]}
    for day, items in _group_by_day(timeline):
        out.append(f"### {day}")
        last_time = ""
        for kind, d in items:
            if kind == "summary":
                out.append(f"S{d['id']} {d.get('request') or 'Session started'} ({format_datetime(d['displayTime'])})")
                continue
            t = format_time(d["created_at_epoch"])
            shown = t if t != last_time else ""
            last_time = t
            time_disp = compact_time(shown) if shown else '"'
            icon = mode.type_icon(d["type"])
            title = d.get("title") or "Untitled"
            if d["id"] in full_ids:
                out.append(f"**{d['id']}** {time_disp} {icon} **{title}**")
                det = _detail(d, cfg)
                if det:
                    out.append(det)
                tok = []
                if cfg.show_read_tokens:
                    tok.append(f"~{observation_tokens(d)}t")
                if cfg.show_work_tokens:
                    w = int(d.get("discovery_tokens") or 0)
                    tok.append(f"{mode.work_emoji(d['type'])} {thousands(w)}" if w > 0 else "-")
                if tok:
                    out.append(" ".join(tok))
                out.append("")
            else:
                out.append(f"{d['id']} {time_disp} {icon} {title}")
    newest_sum = summaries[0] if summaries else None
    newest_obs = observations[0] if observations else None
    if _should_show_summary(cfg, newest_sum, newest_obs):
        for label, key in (("Investigated", "investigated"), ("Learned", "learned"), ("Completed", "completed"),
                           ("Next Steps", "next_steps")):
            if newest_sum.get(key):
                out += [f"**{label}**: {newest_sum[key]}", ""]
    prior = prior_session_message(observations, cfg, session_id, cwd)
    if prior:
        out += ["", "---", "", "**Previously**", "", f"A: {prior}", ""]
    if _show_economics(cfg) and econ["totalDiscoveryTokens"] > 0 and econ["savings"] > 0:
        out += ["", f"Access {round(econ['totalDiscoveryTokens'] / 1000)}k tokens of past work via "
                    "get_observations([IDs]) or the recall search tool."]
    return "\n".join(out).rstrip()


def _render_human(project: str, mode: Mode, observations: list[dict], summaries: list[dict], cfg: ContextConfig,
                  cwd: str, session_id: str | None) -> str:
    econ = token_economics(observations)
    out = ["", f"{C['bright']}{C['cyan']}[{project}] recent context, {header_datetime()}{C['reset']}",
           f"{C['dim']}Mode: {mode.label()}{C['reset']}", f"{C['gray']}{'─' * 60}{C['reset']}", ""]
    legend = " | ".join(f"{t.get('emoji', '')} {t['id']}" for t in mode.observation_types)
    out += [f"{C['dim']}Legend: session-request | {legend}{C['reset']}", ""]
    out += [f"{C['bright']}Column Key{C['reset']}",
            f"{C['dim']}  Read: Tokens to read this observation (cost to learn it now){C['reset']}",
            f"{C['dim']}  Work: Tokens spent on work that produced this record ( research, building, deciding){C['reset']}",
            ""]
    out += [f"{C['dim']}Context Index: This semantic index (titles, types, files, tokens) is usually sufficient to"
            f" understand past work.{C['reset']}", "",
            f"{C['dim']}When you need implementation details, rationale, or debugging context:{C['reset']}",
            f"{C['dim']}  - Fetch by ID: get_observations([IDs]) for observations visible in this index{C['reset']}",
            f"{C['dim']}  - Search history: use the recall search tool for past decisions, bugs, and deeper research"
            f"{C['reset']}",
            f"{C['dim']}  - Trust this index over re-reading code for past decisions and learnings{C['reset']}", ""]
    if _show_economics(cfg):
        out.append(f"{C['bright']}{C['cyan']}Context Economics{C['reset']}")
        out.append(f"{C['dim']}  Loading: {econ['totalObservations']} observations "
                   f"({thousands(econ['totalReadTokens'])} tokens to read){C['reset']}")
        out.append(f"{C['dim']}  Work investment: {thousands(econ['totalDiscoveryTokens'])} tokens spent on research,"
                   f" building, and decisions{C['reset']}")
        if econ["totalDiscoveryTokens"] > 0 and (cfg.show_savings_amount or cfg.show_savings_percent):
            line = "  Your savings: "
            if cfg.show_savings_amount and cfg.show_savings_percent:
                line += f"{thousands(econ['savings'])} tokens ({econ['savingsPercent']}% reduction from reuse)"
            elif cfg.show_savings_amount:
                line += f"{thousands(econ['savings'])} tokens"
            else:
                line += f"{econ['savingsPercent']}% reduction from reuse"
            out.append(f"{C['green']}{line}{C['reset']}")
        out.append("")
    display = summaries[: cfg.session_count]
    timeline = build_timeline(observations, prepare_summaries(display, summaries))
    full_ids = {o["id"] for o in observations[: cfg.full_observation_count]}
    for day, items in _group_by_day(timeline):
        out += [f"{C['bright']}{C['cyan']}{day}{C['reset']}", ""]
        current_file, last_time = None, ""
        for kind, d in items:
            if kind == "summary":
                current_file, last_time = None, ""
                out += [f"{C['yellow']}#S{d['id']}{C['reset']} {d.get('request') or 'Session started'} "
                        f"({format_datetime(d['displayTime'])})", ""]
                continue
            f = extract_first_file(d.get("files_modified"), cwd, d.get("files_read"))
            t = format_time(d["created_at_epoch"])
            show = t != last_time
            last_time = t
            if f != current_file:
                out.append(f"{C['dim']}{f}{C['reset']}")
                current_file = f
            icon = mode.type_icon(d["type"])
            title = d.get("title") or "Untitled"
            read = observation_tokens(d)
            work = int(d.get("discovery_tokens") or 0)
            time_part = f"{C['dim']}{t}{C['reset']}" if show else " " * len(t)
            read_part = f"{C['dim']}(~{read}t){C['reset']}" if cfg.show_read_tokens and read > 0 else ""
            work_part = (f"{C['dim']}({mode.work_emoji(d['type'])} {thousands(work)}t){C['reset']}"
                         if cfg.show_work_tokens and work > 0 else "")
            if d["id"] in full_ids:
                out.append(f"  {C['dim']}#{d['id']}{C['reset']}  {time_part}  {icon}  {C['bright']}{title}{C['reset']}")
                det = _detail(d, cfg)
                if det:
                    out.append(f"    {C['dim']}{det}{C['reset']}")
                if read_part or work_part:
                    out.append(f"    {read_part} {work_part}")
                out.append("")
            else:
                out.append(f"  {C['dim']}#{d['id']}{C['reset']}  {time_part}  {icon}  {title} {read_part} {work_part}")
        out.append("")
    newest_sum = summaries[0] if summaries else None
    newest_obs = observations[0] if observations else None
    if _should_show_summary(cfg, newest_sum, newest_obs):
        for label, key, color in (("Investigated", "investigated", "blue"), ("Learned", "learned", "yellow"),
                                  ("Completed", "completed", "green"), ("Next Steps", "next_steps", "magenta")):
            if newest_sum.get(key):
                out += [f"{C[color]}{label}:{C['reset']} {newest_sum[key]}", ""]
    prior = prior_session_message(observations, cfg, session_id, cwd)
    if prior:
        out += ["", "---", "", f"{C['bright']}{C['magenta']}Previously{C['reset']}", "",
                f"{C['dim']}A: {prior}{C['reset']}", ""]
    if _show_economics(cfg) and econ["totalDiscoveryTokens"] > 0 and econ["savings"] > 0:
        out += ["", f"{C['dim']}Access {round(econ['totalDiscoveryTokens'] / 1000)}k tokens of past research & decisions"
                    f" for just {thousands(econ['totalReadTokens'])}t. Use the recall tools to fetch memories by ID."
                    f"{C['reset']}"]
    return "\n".join(out).rstrip()


def _should_show_summary(cfg: ContextConfig, summary: dict | None, newest_obs: dict | None) -> bool:
    if not cfg.show_last_summary or not summary:
        return False
    if not any(summary.get(k) for k in ("investigated", "learned", "completed", "next_steps")):
        return False
    if newest_obs and summary["created_at_epoch"] <= newest_obs["created_at_epoch"]:
        return False
    return True


def empty_state(project: str, mode: Mode, for_human: bool) -> str:
    if for_human:
        return (f"\n{C['bright']}{C['cyan']}[{project}] recent context, {header_datetime()}{C['reset']}\n"
                f"{C['dim']}Mode: {mode.label()}{C['reset']}\n{C['gray']}{'─' * 60}{C['reset']}\n\n"
                f"{C['dim']}No previous sessions found for this project yet.{C['reset']}\n")
    return f"# [{project}] recent context, {header_datetime()}\nMode: {mode.label()}\n\nNo previous sessions found."


# ---- budget fitting -----------------------------------------------------------------------------------------
def _reduce(cfg: ContextConfig, count: int) -> tuple[ContextConfig, int] | None:
    if cfg.full_observation_count > 0:
        return replace(cfg, full_observation_count=0), count
    if cfg.show_last_summary:
        return replace(cfg, show_last_summary=False), count
    if cfg.session_count > 0:
        return replace(cfg, session_count=cfg.session_count // 2), count
    if count > 1:
        return cfg, count // 2
    return None


def fit_to_budget(observations: list[dict], cfg: ContextConfig,
                  render: Callable[[list[dict], ContextConfig], str], limit: float = CONTEXT_OUTPUT_LIMIT) -> dict:
    count = len(observations)
    text = render(observations, cfg)
    reductions = 0
    while len(text) > limit:
        nxt = _reduce(cfg, count)
        if nxt is None:
            return {"text": text, "config": cfg, "observationCount": count, "reductions": reductions,
                    "overBudget": True}
        cfg, count = nxt
        text = render(observations[:count], cfg)
        reductions += 1
    return {"text": text, "config": cfg, "observationCount": count, "reductions": reductions, "overBudget": False}


def _paint_red(text: str) -> str:
    return "\n".join(f"{C['red']}{ln}{C['reset']}" if ln.strip() else ln for ln in text.split("\n"))


def _with_warning(warning: str, text: str) -> str:
    if not warning:
        return text
    return f"{text}\n\n{warning}" if text else warning


def inject_stats(observations: list[dict], summaries: list[dict], full: bool) -> dict:
    econ = token_economics(observations)
    buckets = {"bugfix": 0, "discovery": 0, "decision": 0, "refactor": 0, "other": 0}
    sessions, oldest = set(), None
    for o in observations:
        buckets[o["type"] if o["type"] in buckets and o["type"] != "other" else "other"] += 1
        sessions.add(o.get("memory_session_id"))
        oldest = o["created_at_epoch"] if oldest is None else min(oldest, o["created_at_epoch"])
    return {"observation_count": len(observations), "session_count": len(sessions),
            "timeline_depth_days": max(0, (schema.now_ms() - oldest) // 86_400_000) if oldest else 0,
            "has_session_summary": bool(summaries), **{f"obs_type_{k}": v for k, v in buckets.items()},
            "tokens_injected": econ["totalReadTokens"], "tokens_saved_vs_naive": econ["savings"],
            "search_strategy": "full" if full else "timeline"}


def generate_context_with_stats(root: Path | str | None, *, cwd: str | None = None, projects: list[str] | None = None,
                                platform_source: str | None = None, full: bool = False, for_human: bool = False,
                                session_id: str | None = None, settings: dict | None = None,
                                limit: int = CONTEXT_OUTPUT_LIMIT) -> tuple[str, dict | None]:
    settings = settings or load_settings(root)
    mode = load_mode(settings.get("mode"))
    cfg = load_config(settings, mode)
    cwd = cwd or (str(root) if root else os.getcwd())
    ctx = project_context(cwd)
    projects = [p for p in (projects or ctx.all_projects) if p]
    project = projects[-1] if projects else ctx.primary
    if full:
        cfg.total_observation_count = 999_999
        cfg.session_count = 999_999
    src = normalize_platform_source(platform_source) if platform_source else None
    try:
        db = schema.connect(schema.store_path(root), readonly=True) if root else None
    except (FileNotFoundError, sqlite3.Error):
        db = None
    if db is None:
        return _with_warning("", empty_state(project, mode, for_human)) if root else "", None
    try:
        from .store import Store
        warn = health.warning_text(Store(db))
        warning = _paint_red(warn) if (for_human and warn) else warn
        query_projects = projects if len(projects) > 1 else [project]
        observations = query_observations(db, query_projects, cfg, src)
        summaries = query_summaries(db, query_projects, cfg, src)
        if not observations and not summaries:
            return _with_warning(warning, empty_state(project, mode, for_human)), None
        renderer = _render_human if for_human else _render_agent
        fitted = fit_to_budget(observations, cfg,
                               lambda items, c: _with_warning(warning, renderer(project, mode, items, summaries, c,
                                                                                cwd, session_id)),
                               float("inf") if full else limit)
        stats = inject_stats(observations[: fitted["observationCount"]],
                             summaries[: fitted["config"].session_count], full)
        return fitted["text"], stats
    finally:
        db.close()


def generate_context(root: Path | str | None, **kwargs) -> str:
    return generate_context_with_stats(root, **kwargs)[0]


WELCOME_HINT = """# Cairn session memory

This project has no session memory yet. The current session will seed it; later sessions receive
auto-injected context for relevant past work.

Memory injection starts on your second session in a project. It builds passively as work happens.

Live activity: {viewer_url}

This message disappears once the first observation lands.
"""


def viewer_url(root: Path | str | None) -> str:
    port = 4747
    if root:
        try:
            import tomllib
            port = int(tomllib.loads((Path(root) / ".cairn" / "config.toml").read_text(encoding="utf-8"))
                       .get("server", {}).get("port", port))
        except (OSError, ValueError, TypeError, Exception):  # noqa: BLE001 - config issues never block context
            pass
    return f"http://localhost:{port}"


def inject_context(root: Path | str | None, projects: list[str], *, platform_source: str | None = None,
                   for_human: bool = False, full: bool = False, settings: dict | None = None,
                   limit: int = CONTEXT_OUTPUT_LIMIT, session_id: str | None = None) -> str:
    """What SessionStart injects: the welcome hint for a project with no memory yet, else the timeline."""
    settings = settings or load_settings(root)
    src = normalize_platform_source(platform_source) if platform_source else None
    if settings.get("welcome_hint") and not full and root:
        try:
            db = schema.connect(schema.store_path(root), readonly=True)
        except (FileNotFoundError, sqlite3.Error):
            db = None
        try:
            has = count_observations(db, projects, src) > 0 if db else False
            warn = ""
            if db and not has:
                from .store import Store
                warn = health.warning_text(Store(db))
        finally:
            if db:
                db.close()
        if not has:
            hint = WELCOME_HINT.format(viewer_url=viewer_url(root))
            return _with_warning(_paint_red(warn) if (for_human and warn) else warn, hint)
    return generate_context(root, cwd=str(root) if root else None, projects=projects, platform_source=platform_source,
                            full=full, for_human=for_human, settings=settings, limit=limit, session_id=session_id)
