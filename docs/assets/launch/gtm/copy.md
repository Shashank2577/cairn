# Cairn go-to-market film: share copy

**Title:** Cairn: institutional memory for coding agents

**Description:** Coding agents re-read the same files, retry fixes your team reverted and lose their place after compaction. Cairn gives them a map, specs, timeline, memory and session history, and answers impact, why and context questions with cited, token-budgeted packs. One command in any git repo, local, no API key. On Cairn's own repository, one impact answer took 391 tokens instead of 144,228 across 33 files.

**Tweet:** Your agents start every session from zero. Cairn remembers for them: cited impact/why/context answers inside a token budget. One answer on Cairn's own repo: 391 tokens vs 144,228. uv tool install --python 3.12 cairn-brain && cairn github.com/Shashank2577/cairn

## Narration script

Voiced with Kokoro (`af_heart`), one phrase per caption (`src/narration.py`). Speed in brackets; the
pause is the silence before the line.

1. Your coding agents are capable. (0.92)
2. But every session... starts from zero. (0.92, 0.45 s)
3. They re-read the same files. (1.05, 0.2 s)
4. They retry the fix your team already reverted. (1.05, 0.2 s)
5. After compaction, they lose their place. (1.05, 0.2 s)
6. And every re-read? You pay for it... in tokens. (1.0, 0.25 s)
7. This is Cairn: institutional memory, for coding agents. (1.0, 0.55 s)
8. Five layers: map, specs, timeline, memory, and sessions. (1.0, 0.35 s)
9. Before an edit, your agent asks Cairn: impact, why, context. (1.0, 0.35 s)
10. And it gets a cited answer, inside a token budget. (1.0, 0.3 s)
11. On Cairn's own repo, one answer took... three hundred and ninety-one tokens. (0.96, 0.55 s)
12. Not a hundred and forty-four thousand, two hundred and twenty-eight tokens, across thirty-three files. (1.0, 0.35 s)
13. Three hundred and sixty-nine times fewer. (0.92, 0.6 s)
14. And after compaction, the next session picks up... where it left off. (1.0, 0.45 s)
15. One command, in any git repo. Local. No API key. (1.0, 0.4 s)
16. Works with Claude Code, Codex, Cursor, Gemini, Copilot, and OpenCode. (1.0, 0.3 s)
17. Cairn. Memory your agents can cite. (0.92, 0.6 s)

## Sound

- **Problem (0-15 s):** A minor pad, a soft bass note per bar and a sparse two-note motif. A low pulse and a shaker grow as the problems pile up. Pops mark terminal lines, a two-tone error blip the reverted edit, a falling sweep the compaction, and small clinks the re-read files.
- **Cairn appears:** a whoosh on the paper wipe, then the score opens on a C major downbeat. A low impact lands on the title, with taps as the stones settle.
- **Layers, ask, answer:** C G Am F with bass and a plucked arpeggio; kick and hi-hat enter with the five layers. Pops mark the layer stones, chips, answer rows and budget, and ticks follow the typed tool call.
- **Proof:** the drums drop out for the bar before the number. A short riser leads into a bright chime as 369x appears.
- **Resume:** a soft airy release with a warm dyad as "Back where it left off" lands.
- **Install:** the score builds with four-on-the-floor kick, sixteenth shaker, driving bass and a doubled arpeggio. Typing ticks follow the commands, and pops mark checks, facts and agent pills.
- **End card:** a low impact on the logo and a held Cadd9 with a rising arpeggio and a reverb tail.
- **Mix:** music is ducked 8 dB and effects 4 dB under the voice, with effects about 7 dB below it. Mastered to about -16 LUFS with true peak under -1.5 dBFS.

---

Source of the numbers: `docs/onboarding.md` ("Reading the savings numbers"), measured on Cairn's own repository for `src/cairn/router.py`.
Film: `cairn-gtm.mp4` (66.2 s, 1920x1080, 30 fps). Captions: `cairn-gtm.srt`, `cairn-gtm.vtt` (also burned in).
Rebuild: `cd src && <python with kokoro> narration.py && node render.mjs` (needs Kokoro, Google Chrome, ffmpeg and numpy; `narration.py` writes `timing.json` and runs `music.py` and `sfx.py`).
