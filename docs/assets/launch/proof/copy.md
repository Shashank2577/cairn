# Cairn proof video: share copy

**Title:** Cairn: your agent keeps its place and gets smarter every session

**Description:** A 71-second look at what's new in Cairn, filmed on the real UI with its sample data. The Overview page shows how much context Cairn served next to the bytes the tools pulled raw. A resume snapshot under 2 KB gets Claude Code through a compaction. Rejected approaches, error-then-fix pairs, retry loops and user corrections are recorded without a model call. Memories nobody uses go stale and rank lower, and nothing is deleted. Install: `uv tool install --python 3.12 cairn-brain`, then run `cairn` in your repository.

**Tweet:** When Claude Code compacts a long session, Cairn hands back a resume snapshot under 2 KB: the last request, files changed, open tasks and decisions. It also records what not to repeat, with no model call. `uv tool install --python 3.12 cairn-brain`

## Script (on-screen captions, also in cairn-proof.srt / .vtt)

| Time | Scene | Caption |
|---|---|---|
| 0:00 | Title | Your agent keeps its place, and gets smarter every session. |
| 0:05 | 1 of 4, Context served (Overview, sample data) | Cairn's Overview page, with sample data: context served. / Agents read 19× fewer tokens than opening the files behind the answers. / New beside it: bytes pulled raw by tools. 6.5 MB across 1,284 calls. / A measurement only. Nothing is trimmed or blocked. |
| 0:19 | 2 of 4, Long-session survival | Deep into a long session, Claude Code compacts the conversation. / Just before, Cairn's PreCompact hook saves a resume snapshot. / Last request, files changed, open tasks, decisions. Under 2 KB. / Pointers, not content: each line names the cairn_session_* tool to call. / No model call. The agent picks up where it was. |
| 0:36 | 3 of 4, What not to repeat | Cairn also records what not to repeat. / Rejected approaches, error-then-fix pairs, retry loops, user corrections. / Only explicit signals count, and no model is called. / Next session, one search finds them. |
| 0:49 | 4 of 4, Memory that ages (Memory page, sample data) | Memory ages. The Memory page, with sample data. / A memory no agent was told for 90 days is marked Stale and ranks lower. / Conventions and decisions never age. / Nothing is deleted. Being used again restarts the clock. |
| 1:01 | Close | Cairn. Institutional memory for coding agents. Keeps its place. Gets smarter every session. `uv tool install --python 3.12 cairn-brain`, `cd your-repo && cairn` |

## Where every figure comes from

- 19×, 14,079 / 266,450 tokens, 6.5 MB, 1,284 tool calls, per-tool bytes: the UI's sample data (`?mock=1`), Overview page.
- "Stale, never used" and the 90-day rule: the Memory page's sample data and `docs/configuration.md` (`[memory] stale_days = 90`).
- The resume snapshot (722 bytes, 2,048-byte cap), the four event titles and the `git push` search result: real output of
  `resume.build_snapshot`, the recall worker (no model) and `search_text`, run on the fixture session from `tests/engines/test_recall_resume.py`.

## How it was made

Plain code, no video tools besides ffmpeg for encoding: `src/proof.html` draws each frame for a given time (`window.render(t)`),
`src/tools/render.mjs` steps it in headless Chrome at 30 fps and pipes the PNGs to ffmpeg, and `src/tools/music.py` synthesises the music bed.
The UI stills in `src/assets/` were captured from `src/cairn/ui` served statically with `?mock=1`, light theme, 1440 px wide at 2x.
