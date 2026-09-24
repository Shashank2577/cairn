---
description: "Record clarified decisions as team memory"
---

# Save clarifications as decisions

## Steps

Read the `## Clarifications` section of the current `spec.md`. For each answered question that is a
durable decision (not feature-local trivia), run:

```bash
cairn remember "<decision as one sentence, naming the code or area>" --kind decision
```

Skip anything already stored (`cairn recall "<topic>"` first). Report what was saved.
