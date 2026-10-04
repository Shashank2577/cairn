"""``cairn timeline …`` subcommands over the temporal graph (mount with
``app.add_typer(timeline_app, name="timeline")``)."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

import typer

from .service import ModelUnavailableError, TemporalService, run_sync

timeline_app = typer.Typer(help="Timeline facts: the temporal fact graph (what was true, and when).",
                           no_args_is_help=True)


def _context() -> tuple[Any, Any, Any]:
    """(project, brain, router) for the repository in the current directory."""
    from cairn.core import Cairn

    c = Cairn.here()
    return c.project, c.brain, c.router


def _service() -> TemporalService:
    project, brain, router = _context()
    return TemporalService(project, router, brain)


def _out(data: Any, as_json: bool, lines: list[str] | None = None) -> None:
    if as_json or lines is None:
        typer.echo(json.dumps(data, indent=2, default=str))
    else:
        typer.echo('\n'.join(lines) if lines else '(nothing)')


def _window(f: dict) -> str:
    start = (f.get('valid_at') or '?')[:10]
    end = (f.get('invalid_at') or '')[:10]
    return f'[{start} → {end or "now"}]'


def _call(coro: Any) -> Any:
    try:
        return run_sync(coro)
    except ModelUnavailableError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(2) from None


@timeline_app.command('add')
def add(content: str = typer.Argument(..., help="Episode text, or @path to read a file"),
        name: str = typer.Option('', help="Episode name (default: derived from the content)"),
        source: str = typer.Option('text', help="text | json | message"),
        at: Optional[str] = typer.Option(None, help="When it happened (ISO-8601; default now)"),
        group: Optional[str] = typer.Option(None, help="Namespace (default: this project)"),
        saga: Optional[str] = typer.Option(None, help="Chain the episode into a named saga"),
        description: str = typer.Option('', help="Source description"),
        as_json: bool = typer.Option(False, '--json')):
    """Add an episode; its entities and facts are extracted and reconciled with the graph."""
    body = Path(content[1:]).read_text(encoding="utf-8") if content.startswith('@') else content
    res = _call(_service().add_episode(name or body[:40].strip(), body, source=source, reference_time=at,
                                       group_id=group, saga=saga, source_description=description))
    _out(res, as_json, [f"+ {f['fact']} {_window(f)}" for f in res['facts'] if f['current']]
         + [f"- {f['fact']} {_window(f)}" for f in res['invalidated']])


@timeline_app.command('search')
def search(query: str,
           limit: int = typer.Option(10, help="Max facts"),
           current: bool = typer.Option(False, help="Only facts that are still true"),
           at: Optional[str] = typer.Option(None, help="Facts that were true at this time (ISO-8601)"),
           types: Optional[str] = typer.Option(None, help="Comma-separated fact types"),
           group: Optional[str] = typer.Option(None),
           as_json: bool = typer.Option(False, '--json')):
    """Search facts (hybrid BM25 + vector + graph) with their validity windows."""
    svc = _service()
    if at:
        facts = _call(svc.facts_at(at, query, group_ids=group, max_facts=limit))
    else:
        facts = _call(svc.search_facts(query, group_ids=group, max_facts=limit, current_only=current,
                                       edge_types=types.split(',') if types else None))
    _out(facts, as_json, [f"{'•' if f['current'] else '×'} {f['fact']} {_window(f)}  {f['uuid'][:8]}"
                          for f in facts])


@timeline_app.command('entities')
def entities(query: str, limit: int = typer.Option(10), type: Optional[str] = typer.Option(None, '--type'),
             group: Optional[str] = typer.Option(None), as_json: bool = typer.Option(False, '--json')):
    """Search entities, optionally by type."""
    nodes = _call(_service().search_nodes(query, group_ids=group, max_nodes=limit,
                                          entity_types=[type] if type else None))
    _out(nodes, as_json, [f"{n['name']} ({', '.join(lb for lb in n['labels'] if lb != 'Entity') or 'Entity'})"
                          f" — {n['summary'][:80]}" for n in nodes])


@timeline_app.command('show')
def show(uuid: str, as_json: bool = typer.Option(False, '--json')):
    """One fact or entity (with all its facts) by uuid."""
    from .errors import EdgeNotFoundError, NodeNotFoundError

    svc = _service()
    try:
        data = _call(svc.get_entity_edge(uuid))
        lines = [f"{data['fact']} {_window(data)}", f"type: {data['name']}", f"episodes: {len(data['episodes'])}"]
    except EdgeNotFoundError:
        try:
            data = _call(svc.get_node(uuid))
        except NodeNotFoundError:
            typer.echo(f'nothing with uuid {uuid}', err=True)
            raise typer.Exit(1) from None
        lines = [f"{data['name']}: {data['summary']}"] + [f"  {f['fact']} {_window(f)}" for f in data['facts']]
    _out(data, as_json, lines)


@timeline_app.command('episodes')
def episodes(last: int = typer.Option(10, help="How many"), group: Optional[str] = typer.Option(None),
             as_json: bool = typer.Option(False, '--json')):
    """The most recent episodes."""
    eps = _call(_service().get_episodes(group_ids=group, last_n=last))
    _out(eps, as_json, [f"{(e['valid_at'] or '')[:10]}  {e['name']}  ({e['source']})  {e['uuid'][:8]}" for e in eps])


@timeline_app.command('communities')
def communities(build: bool = typer.Option(False, help="Rebuild communities first (uses the model)"),
                group: Optional[str] = typer.Option(None), as_json: bool = typer.Option(False, '--json')):
    """Clusters of related entities with summaries."""
    svc = _service()
    rows = (_call(svc.build_communities(group))['communities'] if build
            else _call(svc.list_communities(group_id=group)))
    _out(rows, as_json, [f"{c['name']}: {c['summary'][:100]}" for c in rows])


@timeline_app.command('saga')
def saga(name: Optional[str] = typer.Argument(None), summarize: bool = typer.Option(False),
         group: Optional[str] = typer.Option(None), as_json: bool = typer.Option(False, '--json')):
    """List sagas, or show / refresh one saga's running summary."""
    svc = _service()
    if name and summarize:
        data = _call(svc.summarize_saga(name, group_id=group))
        _out(data, as_json, [data['summary'] or '(empty)'])
        return
    rows = _call(svc.list_sagas(group_id=group))
    if name:
        rows = [s for s in rows if s['name'] == name]
    _out(rows, as_json, [f"{s['name']}: {s['summary'][:100] or '(not summarized)'}" for s in rows])


@timeline_app.command('delete')
def delete(uuid: str, episode: bool = typer.Option(False, help="The uuid is an episode (cascades)")):
    """Delete a fact, or an episode and what only it created."""
    svc = _service()
    res = _call(svc.delete_episode(uuid) if episode else svc.delete_entity_edge(uuid))
    typer.echo(res['message'])


@timeline_app.command('clear')
def clear(group: Optional[str] = typer.Option(None), everything: bool = typer.Option(False, '--all'),
          yes: bool = typer.Option(False, '--yes', help="Do not ask for confirmation")):
    """Clear this project's timeline graph (or every group with --all)."""
    if not yes:
        typer.confirm('Delete the timeline graph data?', abort=True)
    typer.echo(_call(_service().clear(group, everything=everything))['message'])


@timeline_app.command('status')
def status(as_json: bool = typer.Option(False, '--json')):
    """Store health and counts."""
    st = _call(_service().status())
    c = st.get('counts', {})
    _out(st, as_json, [f"{st['status']} · {st['backend']} · group {st['group_id']} · model: "
                       f"{'yes' if st['model'] else 'no'}",
                       f"{c.get('episodes', 0)} episodes, {c.get('entities', 0)} entities, "
                       f"{c.get('facts', 0)} facts ({c.get('current_facts', 0)} current), "
                       f"{c.get('communities', 0)} communities, {c.get('sagas', 0)} sagas"]
         + ([st['message']] if st.get('message') else []))


@timeline_app.command('ingest')
def ingest(budget: int = typer.Option(150_000, help="Token ceiling for this run")):
    """Build timeline facts from repository activity now (what `cairn sync --deep` runs)."""
    from cairn.engines.chronicle import Chronicle
    from cairn.router import Budget

    project, brain, router = _context()
    res = run_sync(Chronicle(project, brain, router).ingest(Budget(budget), on_progress=typer.echo))
    typer.echo(json.dumps(res))


@timeline_app.command('resync')
def resync():
    """Rebuild the read-model mirror of facts from the graph."""
    from cairn.engines.chronicle import Chronicle

    project, brain, router = _context()
    typer.echo(f'{Chronicle(project, brain, router).resync_mirror()} facts mirrored')


@timeline_app.command('reembed')
def reembed(group: Optional[str] = typer.Option(None)):
    """Recompute every stored vector with the current local embedder."""
    typer.echo(json.dumps(_call(_service().reembed(group))))
