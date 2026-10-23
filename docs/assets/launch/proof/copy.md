# Cairn proof video: share copy

**Title:** Cairn: your agent keeps its place and gets smarter every session

**Description:** A 90-second narrated look at what's new in Cairn, filmed on the real UI with its sample data. The Overview page shows how much context Cairn served next to the bytes the tools pulled raw. A resume snapshot under 2 KB gets Claude Code through a compaction. Rejected approaches, error-then-fix pairs, retry loops and user corrections are recorded without a model call. Memories nobody uses go stale and rank lower, and nothing is deleted. Install: `uv tool install --python 3.12 cairn-brain`, then run `cairn` in your repository.

**Tweet:** When Claude Code compacts a long session, Cairn hands back a resume snapshot under 2 KB: the last request, files changed, open tasks and decisions. It also records what not to repeat, with no model call. `uv tool install --python 3.12 cairn-brain`

## Narration (Kokoro, voice af_heart; the captions show the same lines in written form)

Speed 0.93 on the opening and closing lines, 0.97 on the scene openers, 1.04 on the list of event kinds, 1.0 elsewhere.
Silence of 0.3 to 0.8 s before each line, longest before the big figures and the install line.

| Start | Scene | Spoken line |
|---|---|---|
| 0:00.6 | Title | Your agent keeps its place... and gets smarter, every session. |
| 0:06.0 | 1 of 4, Context served (Overview, sample data) | This is Cairn's Overview page, with sample data. First: context served. |
| 0:11.0 |  | Agents read nineteen times fewer tokens... than opening the files behind the answers. |
| 0:16.1 |  | New, right beside it: bytes pulled raw by tools. Six point five megabytes, across twelve hundred and eighty-four calls. |
| 0:23.7 |  | It's a measurement only. Nothing is trimmed, or blocked. |
| 0:27.8 | 2 of 4, Long-session survival | Deep into a long session... Claude Code compacts the conversation. |
| 0:31.7 |  | Just before it does, Cairn's pre-compact hook saves a resume snapshot. |
| 0:36.2 |  | The last request, the files changed, open tasks, decisions... all in under two kilobytes. |
| 0:41.9 |  | Pointers, not content. Each line names the cairn session tool to call. |
| 0:46.3 |  | No model call. The agent picks up right where it was. |
| 0:50.4 | 3 of 4, What not to repeat | Cairn also records... what not to repeat. |
| 0:53.2 |  | Rejected approaches. Errors, and their fixes. Retry loops. And user corrections. |
| 0:58.5 |  | Only explicit signals count, and no model is called. |
| 1:01.9 |  | Next session... one search finds them. |
| 1:05.1 | 4 of 4, Memory that ages (Memory page, sample data) | Memory ages, too. Here's the Memory page, with sample data. |
| 1:09.4 |  | A memory no agent was told for ninety days is marked stale... and ranks lower. |
| 1:14.1 |  | Conventions and decisions never age. |
| 1:16.6 |  | Nothing is deleted. Using it again restarts the clock. |
| 1:21.4 | Close | Cairn. Install it once, then run cairn in your repository. |

The close also shows on screen: "Institutional memory for coding agents. Keeps its place. Gets smarter every session."
`uv tool install --python 3.12 cairn-brain`, `cd your-repo && cairn`.

## Music and sound, scene by scene

| Scene | Score (100 BPM, synthesised) | Sound effects (synced to the frame) |
|---|---|---|
| Title | Pad swell on C; a plucked arpeggio steps in under the title | Three soft pops as the cairn stones stack |
| Context served | Confident: C, G/B, Am, F with bass on the beat, 8th arpeggio, shaker, soft kick | Click as the Overview opens, pop on the Sample data badge, tick count-up and chime on the 19× line and on the bytes line |
| Long-session survival | Hush: dark pad and long bass; the bed dips while the conversation compacts and opens again with the snapshot | Click as the terminal opens, a cold "freeze" whoosh on the compaction, a pop on the PreCompact chip, a rising "unfold" when the snapshot returns, quiet ticks as the byte meter climbs, chime at 722 bytes |
| What not to repeat | Driving: Am, F, C, G with 8th bass, 16th arpeggio and shaker, kick on every beat | Four distinct blips as the four event cards land (falling "no", rising "fixed", three taps, a bent note), pop on the pill, typing ticks and a pop on the search result |
| Memory that ages | Calm and warm: Fmaj7, Cmaj7/E, Dm7, Cmaj7; half-note bass, slow arpeggio, no drums | Click as the Memory page opens, pop on the Sample data badge, a dimming "fade" tone on the Stale badge, a soft pop on the conventions |
| Close | A build (rising arpeggio, opening filter) into one clean C chord, then a two-second tail | Soft pop on the wordmark, a low thud as the install command lands |

The score sits 12 dB under the voice between lines and ducks a further 8 dB while a line is spoken; effects peak about
6 dB under the voice between lines and 12 dB under it while a line is spoken. Master: light limiter, -16 LUFS integrated,
true peak -2.2 dBFS.

## Where every figure comes from

- 19×, 14,079 / 266,450 tokens, 6.5 MB, 1,284 tool calls, per-tool bytes: the UI's sample data (`?mock=1`), Overview page.
- "Stale, never used" and the 90-day rule: the Memory page's sample data and `docs/configuration.md` (`[memory] stale_days = 90`).
- The resume snapshot (722 bytes, 2,048-byte cap), the four event titles and the `git push` search result: real output of
  `resume.build_snapshot`, the recall worker (no model) and `search_text`, run on the fixture session from `tests/engines/test_recall_resume.py`.

## How it was made

Plain code. Kokoro (local) speaks the narration; ffmpeg does the limiter, loudness and encoding. The audio scripts run on the
Python environment that has Kokoro, numpy and scipy (`~/cairn-video-audio/.venv/bin/python`).

1. `tools/voice.py` speaks each phrase, checks each clip's length and peak (and re-renders one that is short or clips), levels
   every line to the same RMS, and lays the scenes out so every line ends inside its scene. Writes `timing.js` and `assets/timing.json`,
   including each scene's clock warp so its visual beats land on the spoken lines.
2. `tools/score.py` writes the score and `tools/sfx.py` the effects (cue sheet in `assets/sfx.json`), both timed from the warp.
3. `tools/mix.py` mixes, ducks and masters to `assets/mix.m4a` and prints per-scene levels.
4. `node tools/render.mjs video ../cairn-proof.mp4` (with `PUPPETEER_CORE` pointing at a puppeteer-core install) steps
   `proof.html` in headless Chrome at 30 fps, pipes the frames to ffmpeg with the mix, and writes the .srt and .vtt from the same captions.

The UI stills in `src/assets/` were captured from `src/cairn/ui` served statically with `?mock=1`, light theme, 1440 px wide at 2x.
