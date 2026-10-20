# Cairn go-to-market film: share copy

**Title:** Cairn: institutional memory for coding agents

**Description:** Coding agents re-read the same files, retry fixes your team reverted and lose their place after compaction. Cairn gives them a map, specs, timeline, memory and session history, and answers impact, why and context questions with cited, token-budgeted packs. One command in any git repo, local, no API key. On Cairn's own repository, one impact answer took 391 tokens instead of 144,228 across 33 files.

**Tweet:** Your agents start every session from zero. Cairn remembers for them: cited impact/why/context answers inside a token budget. One answer on Cairn's own repo: 391 tokens vs 144,228. uv tool install --python 3.12 cairn-brain && cairn github.com/Shashank2577/cairn

## Narration script

Voiced with macOS `say -v Samantha -r 175`, one line per caption (`src/narration.py`):

1. Your coding agents are capable.
2. But every session starts from zero.
3. They re-read the same files.
4. They retry the fix your team reverted.
5. After compaction, they lose their place.
6. And you pay for every re-read, in tokens.
7. Cairn is institutional memory for coding agents.
8. Five layers: map, specs, timeline, memory, and sessions.
9. Before an edit, an agent asks Cairn: impact, why, context.
10. It gets a cited answer, inside a token budget.
11. On Cairn's own repo, one answer took three hundred ninety-one tokens,
12. instead of a hundred forty-four thousand, two hundred twenty-eight tokens, across thirty-three files.
13. After compaction, the next session picks up where it left off.
14. One command, in any git repo. Local. No API key.
15. Works with Claude Code, Codex, Cursor, Gemini, Copilot, and OpenCode.
16. Cairn. Memory your agents can cite.

---

Source of the numbers: `docs/onboarding.md` ("Reading the savings numbers"), measured on Cairn's own repository for `src/cairn/router.py`.
Film: `cairn-gtm.mp4` (68.2 s, 1920x1080, 30 fps), voiceover over a synthesized music bed, mixed to about -16 LUFS. Captions: `cairn-gtm.srt`, `cairn-gtm.vtt` (also burned in).
Rebuild: `cd src && python3 narration.py && node render.mjs` (needs macOS `say`, Google Chrome, ffmpeg and numpy; `narration.py` also regenerates the music and writes `timing.json`).
