// The script of the explainer, with its timings in seconds. index.html draws these as burned-in
// captions and make_captions.mjs writes the same lines to SRT and VTT, so the two cannot disagree.
// Every claim here comes from README.md, docs/mcp.md, docs/architecture.md, docs/models-and-cost.md
// or a real cairn_context / cairn_impact answer about this repository (see README.md beside this file).
window.DURATION = 112;
window.CAPTIONS = [
  [0.6, 4.4, "An agent gets a task: record end lines in graph/extract.py."],
  [4.7, 8.9, "Before it edits, it asks one question. What breaks if I change this?"],
  [10.3, 14.6, "Reading its way to an answer is costly. extract.py alone is 8,739 lines."],
  [14.9, 19.4, "So it makes one call to Cairn's MCP server: cairn_context."],
  [20.2, 23.8, "Cairn has already built five layers from this repo, on this machine."],
  [24.1, 29.2, "Map: a tree-sitter code graph. It finds who calls the code being changed."],
  [29.6, 34.7, "Specs: the requirement and the task that own this file."],
  [35.1, 40.2, "Timeline: git history. Two past fixes here, one for UTF-8 on Windows."],
  [40.6, 45.7, "Memory: the decisions and conventions the team recorded."],
  [46.1, 51.6, "Sessions: an earlier agent found that locations store one line, never a range."],
  [52.3, 57.4, "It all comes back as one pack: ranked, cited and cut to a token budget."],
  [57.8, 62.2, "1,192 tokens, inside a 1,200-token budget. No files copied in."],
  [62.5, 65.8, "Every line names its source, so the agent knows what to verify."],
  [66.3, 70.4, "Now it edits with the blast radius in view."],
  [70.8, 75.2, "cairn_impact confirms it: medium risk, three dependents, two past fixes."],
  [75.6, 79.7, "It writes the range, keeps text I/O in UTF-8, and checks the dependents."],
  [80.3, 84.8, "What it learned goes back with cairn_remember, as one sentence."],
  [85.1, 89.3, "The next session starts with it in the brief. Nothing is learned twice."],
  [89.6, 93.7, "And cairn_session_search finds what any earlier session did."],
  [94.3, 98.8, "None of this needs a model: ASTs, git, spec files and session logs."],
  [99.1, 103.7, "A model only enriches, on Haiku, Sonnet or Opus tiers. It never gates an answer."],
  [104.3, 108.4, "Cairn. Institutional memory for coding agents."],
  [108.7, 111.6, "One command, in any git repository."],
];
