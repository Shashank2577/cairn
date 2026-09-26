"""Recall search: semantic (local vectors) with keyword (FTS5) fallback, timelines, per-file history
and recent-session context, rendered as compact indexes for agents (progressive disclosure: search
-> timeline -> get_observations). Mirrors the three search paths of the original design:
filter-only SQL, semantic (vector) search, and keyword search when the vector index is unavailable.
"""
from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Any

from . import sqlsearch
from .fmt import (
    estimate_tokens,
    extract_first_file,
    format_datetime,
    format_time,
    group_by_date,
)
from .modes import load_mode
from .platforms import normalize_platform_source
from .settings import load as load_settings
from .store import Store

log = logging.getLogger("cairn.recall")

RECENCY_WINDOW_MS = 90 * 24 * 60 * 60 * 1000
DEFAULT_LIMIT = 20
CATEGORY_TYPES = ("observations", "sessions", "prompts")


def _split(v: Any) -> Any:
    if isinstance(v, str) and "," in v:
        return [s.strip() for s in v.split(",") if s.strip()]
    return v


def _int(v: Any, default: int) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def normalize_params(args: dict) -> dict:
    n = dict(args or {})
    if n.get("filePath") and not n.get("files"):
        n["files"] = n.pop("filePath")
    if n.get("concept") and not n.get("concepts"):
        n["concepts"] = n.pop("concept")
    for k in ("concepts", "files", "obs_type"):
        if isinstance(n.get(k), str):
            n[k] = [s.strip() for s in n[k].split(",") if s.strip()]
    if isinstance(n.get("type"), str) and "," in n["type"]:
        n["type"] = _split(n["type"])
    start = n.pop("dateStart", None) or n.pop("date_start", None) or n.pop("date_from", None)
    end = n.pop("dateEnd", None) or n.pop("date_end", None) or n.pop("date_to", None)
    if start or end:
        n["date_range"] = {"start": start, "end": end}
    if n.get("isFolder") in ("true", "false"):
        n["isFolder"] = n["isFolder"] == "true"
    src = n.pop("platformSource", None) or n.pop("platform_source", None)
    if isinstance(src, str) and src.strip():
        n["platform_source"] = normalize_platform_source(src)
    if n.get("orderBy"):
        n["order_by"] = n.pop("orderBy")
    return n


def resolve_type_filters(type_: Any, obs_type: Any) -> tuple[Any, Any]:
    """``type`` is a category ('observations'|'sessions'|'prompts') or, otherwise, an observation type."""
    if type_ is None:
        return None, obs_type
    values = type_ if isinstance(type_, list) else [type_]
    if values and all(v in CATEGORY_TYPES for v in values):
        return type_, obs_type
    existing = obs_type if isinstance(obs_type, list) else ([obs_type] if obs_type is not None else [])
    return "observations", list(dict.fromkeys([*existing, *values]))


def _epoch(v: Any) -> int | None:
    return sqlsearch._epoch(v)


class RecallSearch:
    """Search over one repository's recall store."""

    def __init__(self, root: Path | str, store: Store | None = None, *, vectors: Any = "auto"):
        self.root = Path(root)
        self._own = store is None
        self.store = store or Store.open(self.root)
        self.settings = load_settings(self.root)
        self.mode = load_mode(self.settings.get("mode"))
        self._vectors = vectors

    def close(self) -> None:
        if self._own:
            self.store.close()

    def __enter__(self) -> RecallSearch:
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    @property
    def db(self):
        return self.store.db

    def vectors(self):
        if self._vectors == "auto":
            self._vectors = None
            if self.settings.get("vectors"):
                from .vectorsync import VectorSync
                vs = VectorSync(self.root, self.store)
                if vs.available:
                    self._vectors = vs
        return self._vectors or None

    # ---- formatting ------------------------------------------------------------------------------------
    def _read_tokens(self, obs: dict) -> int:
        size = len(obs.get("title") or "") + len(obs.get("subtitle") or "") + len(obs.get("narrative") or "") + \
            len(obs.get("facts") or "")
        return -(-size // 4)

    @staticmethod
    def table_header() -> str:
        return "| ID | Time | T | Title | Read | Work |\n|-----|------|---|-------|------|------|"

    @staticmethod
    def search_table_header() -> str:
        return "| ID | Time | T | Title | Read |\n|----|------|---|-------|------|"

    def observation_index(self, obs: dict) -> str:
        work = int(obs.get("discovery_tokens") or 0)
        wd = f"{self.mode.work_emoji(obs['type'])} {work}" if work > 0 else "-"
        return (f"| #{obs['id']} | {format_time(obs['created_at_epoch'])} | {self.mode.type_icon(obs['type'])} | "
                f"{obs.get('title') or 'Untitled'} | ~{self._read_tokens(obs)} | {wd} |")

    @staticmethod
    def session_index(s: dict) -> str:
        title = s.get("request") or f"Session {(s.get('memory_session_id') or 'unknown')[:8]}"
        return f"| #S{s['id']} | {format_time(s['created_at_epoch'])} | \U0001F3AF | {title} | - | - |"

    def _row(self, kind: str, d: dict, last: str) -> tuple[str, str]:
        t = format_time(d["created_at_epoch"])
        shown = "″" if t == last else t
        if kind == "observation":
            return (f"| #{d['id']} | {shown} | {self.mode.type_icon(d['type'])} | {d.get('title') or 'Untitled'} | "
                    f"~{self._read_tokens(d)} |", t)
        if kind == "session":
            title = d.get("request") or f"Session {(d.get('memory_session_id') or 'unknown')[:8]}"
            return f"| #S{d['id']} | {shown} | \U0001F3AF | {title} | - |", t
        text = d.get("prompt_text") or ""
        title = text if len(text) <= 60 else text[:57] + "..."
        return f"| #P{d['id']} | {shown} | \U0001F4AC | {title} | - |", t

    # ---- semantic helpers --------------------------------------------------------------------------------
    def _semantic(self, query: str, *, doc_types: list[str] | None, project: str | None, platform: str | None,
                  date_range: dict | None, limit: int = 100) -> dict:
        vs = self.vectors()
        if vs is None:
            raise RuntimeError("vector index unavailable")
        start = end = None
        if date_range:
            start, end = _epoch(date_range.get("start")), _epoch(date_range.get("end"))
        else:
            start = int(datetime.now().timestamp() * 1000) - RECENCY_WINDOW_MS
        return vs.query(query, limit, doc_types=doc_types, project=project, platform_source=platform,
                        start_epoch=start, end_epoch=end)

    # ---- search ----------------------------------------------------------------------------------------------
    def search(self, args: dict) -> Any:
        n = normalize_params(args)
        query = n.pop("query", None) or None
        fmt = n.pop("format", None)
        category, obs_type = resolve_type_filters(n.pop("type", None), n.pop("obs_type", None))
        concepts, files = n.pop("concepts", None), n.pop("files", None)
        limit = _int(n.get("limit"), DEFAULT_LIMIT)
        offset = _int(n.get("offset"), 0)
        order_by = n.get("order_by") or "relevance"
        base = {"project": n.get("project"), "platform_source": n.get("platform_source"),
                "date_range": n.get("date_range")}
        want_obs = not category or category == "observations"
        want_ses = not category or category == "sessions"
        want_pr = not category or category == "prompts"
        observations: list[dict] = []
        sessions: list[dict] = []
        prompts_: list[dict] = []
        strategy = "filter_only"
        failure = None
        if not query:
            # Filter-only: each category answers the filters that apply to it; with none at all it is an error.
            answered = 0
            calls = [("obs", want_obs, lambda: sqlsearch.search_observations(
                         self.db, None, limit=limit, offset=offset, order_by=order_by, type=obs_type,
                         concepts=concepts, files=files, **base)),
                     ("ses", want_ses and not (obs_type or concepts or files), lambda: sqlsearch.search_sessions(
                         self.db, None, limit=limit, offset=offset, order_by=order_by, **base)),
                     ("pr", want_pr and not (obs_type or concepts or files), lambda: sqlsearch.search_user_prompts(
                         self.db, None, limit=limit, offset=offset, order_by=order_by, **base))]
            found: dict[str, list] = {"obs": [], "ses": [], "pr": []}
            for key, wanted, call in calls:
                if not wanted:
                    continue
                try:
                    found[key] = call()
                    answered += 1
                except sqlsearch.SearchInputError:
                    continue
            if not answered:
                msg = sqlsearch.MISSING_INPUT
                return {"content": [{"type": "text", "text": msg}], "isError": True} if fmt != "json" else \
                    {"error": msg}
            observations, sessions, prompts_ = found["obs"], found["ses"], found["pr"]
        else:
            used_vectors = False
            if self.vectors() is not None:
                doc_types = [d for d, w in (("observation", want_obs), ("session_summary", want_ses),
                                            ("user_prompt", want_pr)) if w]
                try:
                    res = self._semantic(query, doc_types=doc_types, project=base["project"],
                                         platform=base["platform_source"], date_range=base["date_range"])
                    used_vectors = True
                    strategy = "vectors"
                    obs_ids = [i for i, m in zip(res["ids"], res["metadatas"]) if m["doc_type"] == "observation"]
                    ses_ids = [i for i, m in zip(res["ids"], res["metadatas"]) if m["doc_type"] == "session_summary"]
                    pr_ids = [i for i, m in zip(res["ids"], res["metadatas"]) if m["doc_type"] == "user_prompt"]
                    if obs_ids:
                        observations = self.store.get_observations_by_ids(
                            obs_ids, order_by="relevance", project=base["project"],
                            platform_source=base["platform_source"], type=obs_type, concepts=concepts, files=files)
                    if ses_ids:
                        sessions = self.store.get_session_summaries_by_ids(ses_ids, order_by="date_desc", limit=limit,
                                                                           project=base["project"],
                                                                           platform_source=base["platform_source"])
                    if pr_ids:
                        prompts_ = self.store.get_user_prompts_by_ids(pr_ids, order_by="date_desc", limit=limit,
                                                                      project=base["project"],
                                                                      platform_source=base["platform_source"])
                    if not res["ids"]:
                        used_vectors = False  # nothing indexed (yet) for this scope: try keywords
                except Exception as exc:  # noqa: BLE001 - keyword search is the fallback
                    failure = str(exc)
                    used_vectors = False
                    log.warning("semantic search failed, falling back to keyword search: %s", exc)
            if not used_vectors:
                strategy = "fts"
                observations, sessions, prompts_ = self._keyword(query, want_obs, want_ses, want_pr, limit, offset,
                                                                 order_by, obs_type, concepts, files, base)
        total = len(observations) + len(sessions) + len(prompts_)
        if fmt == "json":
            return {"observations": observations, "sessions": sessions, "prompts": prompts_, "totalResults": total,
                    "query": query or "", "strategy": strategy}
        if total == 0:
            if failure:
                return {"content": [{"type": "text", "text": f"Semantic search failed: {failure}. Falling back to"
                                                             " keyword search; results may be incomplete."}]}
            return {"content": [{"type": "text", "text": f'No results found matching "{query}"'}]}
        combined = [("observation", o) for o in observations] + [("session", s) for s in sessions] + \
            [("prompt", p) for p in prompts_]
        if order_by == "date_desc":
            combined.sort(key=lambda it: -it[1]["created_at_epoch"])
        elif order_by == "date_asc":
            combined.sort(key=lambda it: it[1]["created_at_epoch"])
        combined = combined[:limit or DEFAULT_LIMIT]
        cwd = str(self.root)
        lines = [f'Found {total} result(s) matching "{query}" ({len(observations)} obs, {len(sessions)} sessions,'
                 f" {len(prompts_)} prompts)", ""]
        for day, items in group_by_date(combined, lambda it: it[1]["created_at_epoch"]):
            lines += [f"### {day}", ""]
            by_file: dict[str, list] = {}
            for kind, d in items:
                f = extract_first_file(d.get("files_modified"), cwd, d.get("files_read")) if kind == "observation" \
                    else "General"
                by_file.setdefault(f, []).append((kind, d))
            for f, rows in by_file.items():
                lines += [f"**{f}**", self.search_table_header()]
                last = ""
                for kind, d in rows:
                    row, last = self._row(kind, d, last)
                    lines.append(row)
                lines.append("")
        return {"content": [{"type": "text", "text": "\n".join(lines)}]}

    def _keyword(self, query, want_obs, want_ses, want_pr, limit, offset, order_by, obs_type, concepts, files, base):
        observations, sessions, prompts_ = [], [], []
        try:
            if want_obs:
                observations = sqlsearch.search_observations(self.db, query, limit=limit, offset=offset,
                                                             order_by=order_by, type=obs_type, concepts=concepts,
                                                             files=files, **base) or \
                    sqlsearch.search_observations(self.db, query, limit=limit, offset=offset, order_by=order_by,
                                                  type=obs_type, concepts=concepts, files=files, loose=True, **base)
            if want_ses:
                sessions = sqlsearch.search_sessions(self.db, query, limit=limit, offset=offset, order_by=order_by,
                                                     **base) or \
                    sqlsearch.search_sessions(self.db, query, limit=limit, offset=offset, order_by=order_by,
                                              loose=True, **base)
            if want_pr:
                prompts_ = sqlsearch.search_user_prompts(self.db, query, limit=limit, offset=offset, order_by=order_by,
                                                         **base)
        except Exception as exc:  # noqa: BLE001
            log.error("keyword search failed: %s", exc)
        return observations, sessions, prompts_

    def search_observations(self, args: dict) -> dict:
        n = normalize_params(args)
        query = n.get("query") or ""
        limit = _int(n.get("limit"), DEFAULT_LIMIT)
        results: list[dict] = []
        if self.vectors() is not None and query:
            try:
                res = self._semantic(query, doc_types=["observation"], project=n.get("project"),
                                     platform=n.get("platform_source"), date_range=None)
                results = self.store.get_observations_by_ids(res["ids"], order_by="relevance", limit=limit,
                                                             project=n.get("project"),
                                                             platform_source=n.get("platform_source"))
            except Exception as exc:  # noqa: BLE001
                log.error("semantic observation search failed: %s", exc)
        if not results and query:
            results = sqlsearch.search_observations(self.db, query, limit=limit, project=n.get("project"),
                                                    platform_source=n.get("platform_source")) or \
                sqlsearch.search_observations(self.db, query, limit=limit, project=n.get("project"),
                                              platform_source=n.get("platform_source"), loose=True)
        if not results:
            return {"content": [{"type": "text", "text": f'No observations found matching "{query}"'}]}
        text = f'Found {len(results)} observation(s) matching "{query}"\n\n{self.table_header()}\n' + \
            "\n".join(self.observation_index(o) for o in results)
        return {"content": [{"type": "text", "text": text}]}

    def search_by_file(self, args: dict) -> dict:
        n = normalize_params(args)
        raw = n.get("files") or n.get("file")
        path = raw[0] if isinstance(raw, list) else raw
        if not path:
            return {"content": [{"type": "text", "text": "file path is required"}], "isError": True}
        found = sqlsearch.find_by_file(self.db, path, limit=_int(n.get("limit"), DEFAULT_LIMIT),
                                       is_folder=bool(n.get("isFolder")), project=n.get("project"),
                                       platform_source=n.get("platform_source"), date_range=n.get("date_range"))
        observations, sessions = found["observations"], found["sessions"]
        if self.vectors() is not None and observations:  # rank the file's observations by relevance to the path
            try:
                res = self._semantic(path, doc_types=["observation"], project=n.get("project"),
                                     platform=n.get("platform_source"), date_range={"start": 0})
                order = {i: k for k, i in enumerate(res["ids"])}
                observations.sort(key=lambda o: order.get(o["id"], len(order)))
            except Exception:  # noqa: BLE001 - metadata order stands
                pass
        total = len(observations) + len(sessions)
        if not total:
            return {"content": [{"type": "text", "text": f'No results found for file "{path}"'}]}
        combined = sorted([("observation", o) for o in observations] + [("session", s) for s in sessions],
                          key=lambda it: -it[1]["created_at_epoch"])
        lines = [f'Found {total} result(s) for file "{path}"', ""]
        for day, items in group_by_date(combined, lambda it: it[1]["created_at_epoch"]):
            lines += [f"### {day}", "", self.table_header()]
            lines += [self.observation_index(d) if k == "observation" else self.session_index(d) for k, d in items]
            lines.append("")
        return {"content": [{"type": "text", "text": "\n".join(lines)}]}

    # ---- timeline --------------------------------------------------------------------------------------------
    def timeline(self, args: dict) -> dict:
        n = normalize_params(args)
        anchor, query = n.get("anchor"), n.get("query")
        before = _int(n.get("depth_before"), 10) if n.get("depth_before") is not None else 10
        after = _int(n.get("depth_after"), 10) if n.get("depth_after") is not None else 10
        project, platform = n.get("project"), n.get("platform_source")
        if not anchor and not query:
            return {"content": [{"type": "text", "text": 'Error: Must provide either "anchor" or "query" parameter'}],
                    "isError": True}
        if anchor and query:
            return {"content": [{"type": "text", "text": 'Error: Cannot provide both "anchor" and "query" parameters.'
                                                         " Use one or the other."}], "isError": True}
        anchor_num = anchor if isinstance(anchor, int) and not isinstance(anchor, bool) else (
            int(anchor.strip()) if isinstance(anchor, str) and anchor.strip().isdigit() else None)
        if query:
            results: list[dict] = []
            if self.vectors() is not None:
                try:
                    res = self._semantic(query, doc_types=["observation"], project=project, platform=platform,
                                         date_range=None)
                    results = self.store.get_observations_by_ids(res["ids"][:1], order_by="relevance", limit=1,
                                                                 project=project, platform_source=platform)
                except Exception as exc:  # noqa: BLE001
                    log.error("semantic timeline anchor search failed: %s", exc)
            if not results:
                results = sqlsearch.search_observations(self.db, query, limit=1, project=project,
                                                        platform_source=platform) or \
                    sqlsearch.search_observations(self.db, query, limit=1, project=project, platform_source=platform,
                                                  loose=True)
            if not results:
                return {"content": [{"type": "text", "text": f'No observations found matching "{query}". Try a'
                                                             " different search query."}]}
            top = results[0]
            anchor_id: Any = top["id"]
            anchor_epoch = top["created_at_epoch"]
            data = self.store.get_timeline_around_observation(top["id"], anchor_epoch, before, after, project, platform)
        elif anchor_num is not None:
            hit = self.store.get_observations_by_ids([anchor_num], project=project, platform_source=platform, limit=1)
            if not hit:
                return {"content": [{"type": "text", "text": f"Observation #{anchor_num} not found"}], "isError": True}
            anchor_id, anchor_epoch = anchor_num, hit[0]["created_at_epoch"]
            data = self.store.get_timeline_around_observation(anchor_num, anchor_epoch, before, after, project,
                                                              platform)
        elif isinstance(anchor, str) and (anchor.startswith("S") or anchor.startswith("#S")):
            num = _int(anchor.lstrip("#").lstrip("S"), -1)
            ss = self.store.get_session_summaries_by_ids([num], project=project, platform_source=platform)
            if not ss:
                return {"content": [{"type": "text", "text": f"Session #{num} not found"}], "isError": True}
            anchor_id, anchor_epoch = f"S{num}", ss[0]["created_at_epoch"]
            data = self.store.get_timeline_around_timestamp(anchor_epoch, before, after, project, platform)
        elif isinstance(anchor, str):
            ep = _epoch(anchor)
            if ep is None:
                return {"content": [{"type": "text", "text": f"Invalid timestamp: {anchor}"}], "isError": True}
            anchor_id, anchor_epoch = anchor, ep
            data = self.store.get_timeline_around_timestamp(ep, before, after, project, platform)
        else:
            return {"content": [{"type": "text", "text": "Invalid anchor: must be observation ID (number), session ID"
                                                         ' (e.g., "S123"), or ISO timestamp'}], "isError": True}
        items = [("observation", o, o["created_at_epoch"]) for o in data["observations"]] + \
            [("session", s, s["created_at_epoch"]) for s in data["sessions"]] + \
            [("prompt", p, p["created_at_epoch"]) for p in data["prompts"]]
        items.sort(key=lambda it: it[2])
        items = filter_by_depth(items, anchor_id, anchor_epoch, before, after)
        if not items:
            msg = (f'Found observation matching "{query}", but no timeline context available ({before} records before,'
                   f" {after} records after).") if query else \
                f"No context found around anchor ({before} records before, {after} records after)"
            return {"content": [{"type": "text", "text": msg}]}
        lines = []
        if query:
            a = next((d for k, d, _ in items if k == "observation" and d["id"] == anchor_id), None)
            lines += [f'# Timeline for query: "{query}"',
                      f"**Anchor:** Observation #{anchor_id} - {(a or {}).get('title') or 'Untitled'}"]
        else:
            lines.append(f"# Timeline around anchor: {anchor_id}")
        lines += [f"**Window:** {before} records before -> {after} records after | **Items:** {len(items)}", ""]
        lines += self.render_timeline(items, anchor_id)
        return {"content": [{"type": "text", "text": "\n".join(lines)}]}

    def render_timeline(self, items: list[tuple], anchor_id: Any) -> list[str]:
        cwd = str(self.root)
        lines: list[str] = []
        for day, day_items in group_by_date(items, lambda it: it[2]):
            lines += [f"### {day}", ""]
            current_file, last_time, open_table = None, "", False
            for kind, d, ep in day_items:
                is_anchor = (isinstance(anchor_id, int) and kind == "observation" and d["id"] == anchor_id) or \
                    (isinstance(anchor_id, str) and anchor_id.startswith("S") and kind == "session"
                     and f"S{d['id']}" == anchor_id)
                if kind == "session":
                    if open_table:
                        lines.append("")
                        open_table, current_file, last_time = False, None, ""
                    marker = " <- **ANCHOR**" if is_anchor else ""
                    lines += [f"**\U0001F3AF #S{d['id']}** {d.get('request') or 'Session summary'} "
                              f"({format_datetime(ep)}){marker}", ""]
                elif kind == "prompt":
                    if open_table:
                        lines.append("")
                        open_table, current_file, last_time = False, None, ""
                    text = d.get("prompt_text") or ""
                    lines += [f"**\U0001F4AC User Prompt #{d.get('prompt_number')}** ({format_datetime(ep)})",
                              f"> {text[:100] + '...' if len(text) > 100 else text}", ""]
                else:
                    f = extract_first_file(d.get("files_modified"), cwd, d.get("files_read"))
                    if f != current_file:
                        if open_table:
                            lines.append("")
                        lines += [f"**{f}**", "| ID | Time | T | Title | Tokens |", "|----|------|---|-------|--------|"]
                        current_file, open_table, last_time = f, True, ""
                    t = format_time(ep)
                    shown = t if t != last_time else '"'
                    last_time = t
                    marker = " <- **ANCHOR**" if is_anchor else ""
                    lines.append(f"| #{d['id']} | {shown} | {self.mode.type_icon(d['type'])} | "
                                 f"{d.get('title') or 'Untitled'}{marker} | ~{estimate_tokens(d.get('narrative'))} |")
            if open_table:
                lines.append("")
        return lines

    def timeline_by_query(self, args: dict) -> dict:
        n = normalize_params(args)
        if n.get("mode", "auto") != "interactive":
            return self.timeline(args)
        query, limit = n.get("query") or "", _int(n.get("limit"), 5)
        results = sqlsearch.search_observations(self.db, query, limit=limit, project=n.get("project"),
                                                platform_source=n.get("platform_source"), loose=True) if query else []
        if self.vectors() is not None and query:
            try:
                res = self._semantic(query, doc_types=["observation"], project=n.get("project"),
                                     platform=n.get("platform_source"), date_range=None)
                results = self.store.get_observations_by_ids(res["ids"], order_by="relevance", limit=limit) or results
            except Exception:  # noqa: BLE001
                pass
        if not results:
            return {"content": [{"type": "text", "text": f'No observations found matching "{query}". Try a different'
                                                         " search query."}]}
        lines = ["# Timeline Anchor Search Results", "", f'Found {len(results)} observation(s) matching "{query}"', "",
                 "To get timeline context around any of these observations, use the timeline tool with the"
                 " observation ID as the anchor.", "", f"**Top {len(results)} matches:**", ""]
        for i, o in enumerate(results, 1):
            lines.append(f"{i}. **{'[' + o['type'] + '] ' if o.get('type') else ''}{o.get('title') or 'Observation #' + str(o['id'])}**")
            lines += [f"   - ID: {o['id']}", f"   - Date: {format_datetime(o['created_at_epoch'])}"]
            if o.get("subtitle"):
                lines.append(f"   - {o['subtitle']}")
            lines.append("")
        return {"content": [{"type": "text", "text": "\n".join(lines)}]}

    # ---- recent context ---------------------------------------------------------------------------------------
    def recent_context(self, args: dict) -> dict:
        from .projects import project_context
        n = normalize_params(args)
        project = n.get("project") or project_context(str(self.root)).primary
        limit = max(1, _int(n.get("limit"), 3))
        platform = n.get("platform_source")
        sessions = self.store.get_recent_sessions_with_status(project, limit, platform)
        if not sessions:
            return {"content": [{"type": "text", "text": f'# Recent Session Context\n\nNo previous sessions found for'
                                                         f' project "{project}".'}]}
        lines = ["# Recent Session Context", "", f"Showing last {len(sessions)} session(s) for **{project}**:", ""]
        for s in sessions:
            if not s.get("memory_session_id"):
                continue
            lines += ["---", ""]
            if s.get("has_summary"):
                summ = self.store.get_summary_for_session(s["memory_session_id"], platform)
                if summ:
                    label = f" (Prompt #{summ['prompt_number']})" if summ.get("prompt_number") else ""
                    lines += [f"**Summary{label}**", ""]
                    for key, lab in (("request", "Request"), ("completed", "Completed"), ("learned", "Learned"),
                                     ("next_steps", "Next Steps")):
                        if summ.get(key):
                            lines.append(f"**{lab}:** {summ[key]}")
                    for key, lab in (("files_read", "Files Read"), ("files_edited", "Files Edited")):
                        from .store import parse_list
                        vals = parse_list(summ.get(key))
                        if vals:
                            lines.append(f"**{lab}:** {', '.join(vals)}")
                    lines.append(f"**Date:** {format_datetime(summ['created_at_epoch'])}")
            elif s.get("status") == "active":
                lines += ["**In Progress**", ""]
                if s.get("user_prompt"):
                    lines.append(f"**Request:** {s['user_prompt']}")
                obs = self.store.get_observations_for_session(s["memory_session_id"], platform)
                if obs:
                    lines += ["", f"**Observations ({len(obs)}):**"] + [f"- {o['title']}" for o in obs]
                else:
                    lines += ["", "*No observations yet*"]
                lines += ["", "**Status:** Active - summary pending", f"**Date:** {format_datetime(s['started_at_epoch'])}"]
            else:
                lines += [f"**{str(s.get('status')).capitalize()}**", ""]
                if s.get("user_prompt"):
                    lines.append(f"**Request:** {s['user_prompt']}")
                lines += ["", f"**Status:** {s.get('status')} - no summary available",
                          f"**Date:** {format_datetime(s['started_at_epoch'])}"]
            lines.append("")
        return {"content": [{"type": "text", "text": "\n".join(lines)}]}

    def semantic_context(self, q: str, project: str | None = None, limit: int = 5,
                         platform_source: str | None = None) -> dict:
        """Relevant past observations for a prompt (used by the prompt hook when semantic injection is on)."""
        limit = min(max(_int(limit, 5), 1), 20)
        if not q or len(q) < 20:
            return {"context": "", "count": 0}
        res = self.search({"query": q, "type": "observations", "project": project, "limit": limit, "format": "json",
                           **({"platformSource": platform_source} if platform_source else {})})
        obs = (res or {}).get("observations") or []
        if not obs:
            return {"context": "", "count": 0}
        lines = ["## Relevant Past Work (semantic match)\n"]
        for o in obs[:limit]:
            lines.append(f"### {o.get('title') or 'Observation'} ({(o.get('created_at') or '')[:10]})")
            if o.get("narrative"):
                lines.append(o["narrative"])
            lines.append("")
        return {"context": "\n".join(lines), "count": len(obs)}

    def semantic_ids(self, query: str, limit: int = 100) -> list[int]:
        """Observation ids nearest to ``query`` (for corpus building); keyword ids when vectors are off."""
        if self.vectors() is not None:
            try:
                return self._semantic(query, doc_types=["observation"], project=None, platform=None,
                                      date_range={"start": 0}, limit=limit)["ids"]
            except Exception:  # noqa: BLE001
                pass
        return [o["id"] for o in sqlsearch.search_observations(self.db, query, limit=limit, loose=True)]


def filter_by_depth(items: list[tuple], anchor_id: Any, anchor_epoch: int, before: int, after: int) -> list[tuple]:
    if not items:
        return items
    if isinstance(anchor_id, int):
        idx = next((i for i, (k, d, _) in enumerate(items) if k == "observation" and d["id"] == anchor_id), -1)
    elif isinstance(anchor_id, str) and anchor_id.startswith("S"):
        num = _int(anchor_id[1:], -1)
        idx = next((i for i, (k, d, _) in enumerate(items) if k == "session" and d["id"] == num), -1)
    else:
        idx = next((i for i, it in enumerate(items) if it[2] >= anchor_epoch), len(items) - 1)
    if idx == -1:
        return items
    return items[max(0, idx - before): min(len(items), idx + after + 1)]
