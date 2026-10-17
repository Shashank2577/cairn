**Lifecycle of a Cairn memory**: A memory is stored active; a newer memory can supersede it, and a person can forget it; superseded and forgotten memories leave search and the brief.

| Element | Type | Technology | Responsibility | Evidence |
|---|---|---|---|---|
| Statement offered | state |  |  | src/cairn/engines/memory.py:229 (extracted) |
| Active | state |  |  | src/cairn/store.py:285 (extracted) |
| Superseded | state |  |  | src/cairn/store.py:273 (extracted) |
| Forgotten | state |  |  | src/cairn/store.py:292 (extracted) |

| # | From | To | What | How | Evidence |
|---|---|---|---|---|---|
| 1 | Statement offered | Active | Stored if not a duplicate |  | src/cairn/engines/memory.py:246 (extracted) |
| 2 | Active | Superseded | A newer memory replaces it |  | src/cairn/engines/memory.py:305 (extracted) |
| 3 | Active | Forgotten | Someone runs forget |  | src/cairn/store.py:294 (extracted) |
