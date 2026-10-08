# Dependency layers (FR-026, T031a/T031b)

The folder-dependency picture formerly called "Architecture" is now "Dependency layers".

| Before | After |
|---|---|
| `before.png`: 106 folders in one cycle, ~30 rows over a haze of lines, vendored libraries dominate | `after.png`, `after-expanded.png` |

What changed
- **First-party only.** `vendor/`, `third_party/`, `node_modules/`, the `map.vendored` list in `.cairn/config.toml`,
  `linguist-vendored` in `.gitattributes`, `VENDORED` marker files and NOTICE/THIRD_PARTY entries that call a folder
  vendored or bundled are hidden. The page shows "N vendored folders hidden" with a toggle.
- **One block per large cycle** (5 or more units): "cycle of 80 folders". Click to expand into its members, grouped by path.
- **Budget per layer:** 12 (tokens `budget.nodes_soft`); the rest fold into "+N", which expands in place.
- **Entry points from signals:** system-model containers, `[project.scripts]`, package.json `bin`/`main`,
  `__main__.py`, server modules. Position in the layering plays no part.
- **Evidence on click:** files, symbol and import counts, what the folder uses and is used by.
- **No haze:** only edges between visible blocks; the heaviest 16 until you hover or select a block, then that block's own
  links in the single accent. The number on a line is the import count it stands for.

Data: `Cairn.architecture()` returns `nodes`, `links`, `layers` (`shown`/`rest`), `cycles`, `entries`, `isolated`, `vendored`.
Tests: `tests/system/test_layers.py`.

Setup note: Cairn's own NOTICE says "derived from", not "vendored", so memstore and temporal are hidden through
`[map] vendored = ["src/cairn/engines/memstore", "src/cairn/engines/temporal"]` in `.cairn/config.toml`
(the screenshots were rendered with that list).
