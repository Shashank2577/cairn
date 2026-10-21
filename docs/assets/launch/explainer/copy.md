# Cairn explainer: share copy

**Title:** How Cairn works: one question through five layers of project memory

**Description:** An agent asks "what breaks if I change graph/extract.py?". Cairn sends the question through Map, Specs, Timeline, Memory and Sessions and returns a cited pack of 1,192 tokens, inside a 1,200-token budget. The agent edits with the blast radius in view, and what it learned is saved for the next session. It all runs locally and needs no API key. `uv tool install --python 3.12 cairn-brain`, then `cairn` in any git repo.

**Tweet:** Your coding agent asks "what breaks if I change this?" Cairn answers from five local layers (code map, specs, git history, team memory, past sessions) in one cited pack of about 1,200 tokens instead of a 100k-token file. No API key. `uv tool install --python 3.12 cairn-brain` github.com/Shashank2577/cairn

## Narration

Kokoro, voice af_heart, one call per line (src/narration.js: text, speed, pause before):

1. An agent gets a task: record end lines in graph slash extract dot P Y. _(speed 0.97, pause 0.0 s)_
2. Before it edits, it asks one question... What breaks, if I change this? _(speed 0.93, pause 0.35 s)_
3. Reading its way to an answer is costly. Extract dot P Y alone is eight thousand, seven hundred and thirty-nine lines. _(speed 1.0, pause 0.6 s)_
4. So instead, it makes one call to Cairn's M C P server: cairn context. _(speed 1.0, pause 0.4 s)_
5. Cairn has already built five layers from this repo, right here on this machine. _(speed 1.0, pause 0.6 s)_
6. Map: a tree-sitter code graph. It finds who calls the code being changed. _(speed 1.04, pause 0.3 s)_
7. Specs: the requirement, and the task, that own this file. _(speed 1.04, pause 0.3 s)_
8. Timeline: git history. Two past fixes here, one for U T F eight on Windows. _(speed 1.04, pause 0.3 s)_
9. Memory: the decisions and conventions the team recorded. _(speed 1.04, pause 0.3 s)_
10. Sessions: an earlier agent found that locations store one line... never a range. _(speed 1.04, pause 0.3 s)_
11. It all comes back as one pack: ranked, cited, and cut to a token budget. _(speed 1.0, pause 0.6 s)_
12. One thousand, one hundred and ninety-two tokens... inside a twelve hundred token budget. No files copied in. _(speed 0.97, pause 0.8 s)_
13. Every line names its source, so the agent knows exactly what to verify. _(speed 1.0, pause 0.35 s)_
14. Now it edits, with the blast radius in view. _(speed 1.0, pause 0.6 s)_
15. Cairn impact confirms it: medium risk. Three dependents. Two past fixes. _(speed 0.97, pause 0.8 s)_
16. So it writes the range, keeps text I O in U T F eight, and checks each dependent. _(speed 1.0, pause 0.35 s)_
17. What it learned goes back with cairn remember, in one sentence. _(speed 1.0, pause 0.6 s)_
18. The next session starts with it in the brief. Nothing is learned twice. _(speed 1.0, pause 0.35 s)_
19. And cairn session search finds what any earlier session did. _(speed 1.0, pause 0.35 s)_
20. None of this needs a model. Just syntax trees, git, spec files, and session logs. _(speed 1.0, pause 0.6 s)_
21. A model only enriches, on Haiku, Sonnet, or Opus tiers. It never gates an answer. _(speed 1.0, pause 0.35 s)_
22. Cairn... Institutional memory for coding agents. _(speed 0.93, pause 0.6 s)_
23. One command, in any git repository. _(speed 0.93, pause 0.4 s)_

## Sound

Score synthesised in numpy at 92 BPM (src/music.py): sparse and curious for the question, a pulse that builds through the five layers, a brighter resolved section when the pack and the impact result land, lighter for remembering, a calm outro with a long final chord. Effects (src/sfx.py): whooshes on scene changes and as the question travels, a rising ping per layer, ticks while counters run, a click-and-settle when the pack lands, a chime on the impact result, pops on checks and stones, clicks on the UI screenshots, a soft thud on the install line.
