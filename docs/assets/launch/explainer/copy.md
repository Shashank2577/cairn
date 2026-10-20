# Cairn explainer: share copy

**Title:** How Cairn works: one question through five layers of project memory

**Description:** An agent asks "what breaks if I change graph/extract.py?". Cairn sends the question through Map, Specs, Timeline, Memory and Sessions and returns a cited pack of 1,192 tokens, inside a 1,200-token budget. The agent edits with the blast radius in view, and what it learned is saved for the next session. It all runs locally and needs no API key. `uv tool install --python 3.12 cairn-brain`, then `cairn` in any git repo.

**Tweet:** Your coding agent asks "what breaks if I change this?" Cairn answers from five local layers (code map, specs, git history, team memory, past sessions) in one cited pack of about 1,200 tokens instead of a 100k-token file. No API key. `uv tool install --python 3.12 cairn-brain` github.com/Shashank2577/cairn

## Narration

macOS `say`, voice Samantha at 175 words per minute, one line per caption (src/narration.js):

1. An agent gets a task: record end lines in graph slash extract dot P Y.
2. Before it edits, it asks one question. What breaks if I change this?
3. Reading its way to an answer is costly. Extract dot P Y alone is 8,739 lines.
4. So it makes one call to Cairn's M C P server: cairn context.
5. Cairn has already built five layers from this repo, on this machine.
6. Map: a tree-sitter code graph. It finds who calls the code being changed.
7. Specs: the requirement and the task that own this file.
8. Timeline: git history. Two past fixes here, one for U T F 8 on Windows.
9. Memory: the decisions and conventions the team recorded.
10. Sessions: an earlier agent found that locations store one line, never a range.
11. It all comes back as one pack: ranked, cited, and cut to a token budget.
12. 1,192 tokens, inside a 1,200 token budget. No files copied in.
13. Every line names its source, so the agent knows what to verify.
14. Now it edits, with the blast radius in view.
15. Cairn impact confirms it: medium risk, three dependents, two past fixes.
16. It writes the range, keeps text I O in U T F 8, and checks the dependents.
17. What it learned goes back with cairn remember, as one sentence.
18. The next session starts with it in the brief. Nothing is learned twice.
19. And cairn session search finds what any earlier session did.
20. None of this needs a model: A S Ts, git, spec files, and session logs.
21. A model only enriches, on Haiku, Sonnet, or Opus tiers. It never gates an answer.
22. Cairn. Institutional memory for coding agents.
23. One command, in any git repository.
