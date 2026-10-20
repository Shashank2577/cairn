# Cairn proof video: share copy

**Title:** Cairn: your agent keeps its place and gets smarter every session

**Description:** A 90-second narrated look at what's new in Cairn, filmed on the real UI with its sample data. The Overview page shows how much context Cairn served next to the bytes the tools pulled raw. A resume snapshot under 2 KB gets Claude Code through a compaction. Rejected approaches, error-then-fix pairs, retry loops and user corrections are recorded without a model call. Memories nobody uses go stale and rank lower, and nothing is deleted. Install: `uv tool install --python 3.12 cairn-brain`, then run `cairn` in your repository.

**Tweet:** When Claude Code compacts a long session, Cairn hands back a resume snapshot under 2 KB: the last request, files changed, open tasks and decisions. It also records what not to repeat, with no model call. `uv tool install --python 3.12 cairn-brain`

## Narration (spoken by macOS `say -v Samantha -r 175`; the captions show the same lines in written form)

| Start | Scene | Spoken line |
|---|---|---|
| 0:00.6 | Title | Your agent keeps its place, and gets smarter every session. |
| 0:06.0 | 1 of 4, Context served (Overview, sample data) | Cairn's Overview page, with sample data. First, context served. |
| 0:10.9 | | Agents read nineteen times fewer tokens than opening the files behind the answers. |
| 0:15.7 | | New beside it: bytes pulled raw by tools. Six point five megabytes, across twelve hundred and eighty-four calls. |
| 0:23.0 | | It's a measurement only. Nothing is trimmed or blocked. |
| 0:27.0 | 2 of 4, Long-session survival | Deep into a long session, Claude Code compacts the conversation. |
| 0:31.2 | | Just before that, Cairn's pre-compact hook saves a resume snapshot. |
| 0:35.7 | | The last request, files changed, open tasks and decisions, in under two kilobytes. |
| 0:41.7 | | Pointers, not content. Each line names the cairn session tool to call. |
| 0:46.4 | | No model call. The agent picks up where it was. |
| 0:50.5 | 3 of 4, What not to repeat | Cairn also records what not to repeat. |
| 0:53.0 | | Rejected approaches, errors and their fixes, retry loops, and user corrections. |
| 0:58.4 | | Only explicit signals count, and no model is called. |
| 1:02.0 | | Next session, one search finds them. |
| 1:05.6 | 4 of 4, Memory that ages (Memory page, sample data) | Memory ages, too. Here is the Memory page, with sample data. |
| 1:10.2 | | A memory no agent was told for ninety days is marked stale, and ranks lower. |
| 1:14.9 | | Conventions and decisions never age. |
| 1:17.3 | | Nothing is deleted. Being used again restarts the clock. |
| 1:21.6 | Close | Cairn. Install it once, then run cairn in your repository. |

The close also shows on screen: "Institutional memory for coding agents. Keeps its place. Gets smarter every session."
`uv tool install --python 3.12 cairn-brain`, `cd your-repo && cairn`.

## Where every figure comes from

- 19×, 14,079 / 266,450 tokens, 6.5 MB, 1,284 tool calls, per-tool bytes: the UI's sample data (`?mock=1`), Overview page.
- "Stale, never used" and the 90-day rule: the Memory page's sample data and `docs/configuration.md` (`[memory] stale_days = 90`).
- The resume snapshot (722 bytes, 2,048-byte cap), the four event titles and the `git push` search result: real output of
  `resume.build_snapshot`, the recall worker (no model) and `search_text`, run on the fixture session from `tests/engines/test_recall_resume.py`.

## How it was made

Plain code; the only outside tools are macOS `say` for the voice and ffmpeg for audio conversion, mixing loudness and encoding.

1. `python3 src/tools/voice.py` speaks each line, measures it, and lays the scenes out so every line ends inside its scene with
   0.3 s between lines (writes `src/timing.js` and `src/assets/timing.json`).
2. `python3 src/tools/music.py src/assets/music.wav` synthesises the music bed, with its lifts on the scene cuts from the timing.
3. `python3 src/tools/mix.py` puts the bed 14 dB under the voice between lines and 22 dB under it while a line is spoken, then
   normalises the mix to -16 LUFS (`src/assets/mix.m4a`).
4. `node src/tools/render.mjs video cairn-proof.mp4` (with `PUPPETEER_CORE` pointing at a puppeteer-core install) steps
   `src/proof.html` in headless Chrome at 30 fps, pipes the frames to ffmpeg with the mix, and writes the .srt and .vtt from the same captions.
   Each scene's choreography is warped so its beats land on the spoken lines.

The UI stills in `src/assets/` were captured from `src/cairn/ui` served statically with `?mock=1`, light theme, 1440 px wide at 2x.
