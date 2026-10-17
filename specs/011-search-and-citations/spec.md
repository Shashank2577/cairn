# Feature Specification: Search that finds text, citations that bound the code, hubs without libraries

**Feature Branch**: `011-search-and-citations`  **Created**: 2026-10-16  **Status**: Implemented

**Input**: The robot-shop documentation experiment (instana/robot-shop at 55292e2199f2fb00a165b1f7d3045fe7f8922038).
Agents searched for `password`, `email`, `cookie`, env vars and ports and got nothing, got hubs and queries
dominated by `Psr\Log` and `org.springframework`, and read whole files because a citation was one line.

## Requirements

- **FR-001** `cairn search` (CLI, MCP `cairn_search`, `Cairn.search`) also finds text: a line of any repository
  file that contains every query word, case-insensitively. Names rank first; text fills the remaining slots.
  An environment variable or a port (`CATALOGUE_HOST`, `8080`) is a literal, so its text hits lead. Kind `text`
  selects only text; any other kind filter keeps text out. No index growth, no model.
- **FR-002** Text search never reads `.env*`, key files, lockfiles, binaries, files over 512 KB or `.cairn/`,
  and a line that assigns a secret-looking name shows the name, not the value.
- **FR-003** A citation can carry the enclosing function's line range (`path:362 (lines 360-374)`): text hits
  and the `cairn system --view` text output. The outermost function or method that contains the line; a class
  only when no function does; nothing when there is no grammar or the line is at module level.
- **FR-004** Library and framework symbols (no source file in the repository, or marked external) are not
  hubs, not area members or names, not query/explain seeds, and a graph walk does not pass through them.

## Success criteria

- **SC-001** On the pinned robot-shop: `search password`, `email`, `8080` return text hits with lines;
  `cookie` returns nothing because the repository has none. `hubs` and `areas` list no `Psr\Log` or
  `org.springframework` symbol. Tests: `tests/test_search_content.py`, `tests/system/test_robot_shop_gaps.py`.

## Limits (documented, not fixed)

Text search scans files on demand: fast on thousands of files, but it is a scan, not an index, and it caps at
20,000 files. Ranges come from tree-sitter grammars for Python, JavaScript/TypeScript, Java, Go, C#, PHP and
shell; other languages get the line only. The JSON system view is unchanged (ranges are added to the text view).
