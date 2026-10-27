// The spoken narration: one line per caption in captions.js, in the same order and with the same
// facts, written for the ear. Each entry is [text, speed, pause before it in seconds].
// Tool names are spoken without underscores; numbers and file names are written out as said.
window.NARRATION = [
  ["An agent gets a task: record end lines in graph slash extract dot P Y.", 0.97, 0.0],
  ["Before it edits, it asks one question... What breaks, if I change this?", 0.93, 0.35],
  ["Reading its way to an answer is costly. Extract dot P Y alone is eight thousand, seven hundred and thirty-nine lines.", 1.0, 0.6],
  ["So instead, it makes one call to Cairn's M C P server: cairn context.", 1.0, 0.4],
  ["Cairn has already built five layers from this repo, right here on this machine.", 1.0, 0.6],
  ["Map: a tree-sitter code graph. It finds who calls the code being changed.", 1.04, 0.3],
  ["Specs: the requirement, and the task, that own this file.", 1.04, 0.3],
  ["Timeline: git history. Two past fixes here, one for U T F eight on Windows.", 1.04, 0.3],
  ["Memory: the decisions and conventions the team recorded.", 1.04, 0.3],
  ["Sessions: an earlier agent found that locations store one line... never a range.", 1.04, 0.3],
  ["It all comes back as one pack: ranked, cited, and cut to a token budget.", 1.0, 0.6],
  ["One thousand, one hundred and ninety-two tokens... inside a twelve hundred token budget. No files copied in.", 0.97, 0.8],
  ["Every line names its source, so the agent knows exactly what to verify.", 1.0, 0.35],
  ["Now it edits, with the blast radius in view.", 1.0, 0.6],
  ["Cairn impact confirms it: medium risk. Three dependents. Two past fixes.", 0.97, 0.8],
  ["So it writes the range, keeps text I O in U T F eight, and checks each dependent.", 1.0, 0.35],
  ["What it learned goes back with cairn remember, in one sentence.", 1.0, 0.6],
  ["The next session starts with it in the brief. Nothing is learned twice.", 1.0, 0.35],
  ["And cairn session search finds what any earlier session did.", 1.0, 0.35],
  ["None of this needs a model. Just syntax trees, git, spec files, and session logs.", 1.0, 0.6],
  ["A model only enriches, on Haiku, Sonnet, or Opus tiers. It never gates an answer.", 1.0, 0.35],
  ["Cairn... Institutional memory for coding agents.", 0.93, 0.6],
  ["One command, in any git repository.", 0.93, 0.4],
];
