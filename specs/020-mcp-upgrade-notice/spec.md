# 020 — A running MCP server says when Cairn was upgraded under it

## Problem
Agents start `cairn mcp` once per session and keep it for the session's life. On 2026-11-20, about 15 such
processes started between 25 October and 15 November were still running across this laptop's sessions, so fixes
installed on 16 November never reached them. In GEMs, context packs were still recorded twice on 17 November by a
server started on 15 November.

## Why not restart in place
The server runs the MCP SDK's stdio loop. Re-executing it would lose the client's `initialize` handshake and any
requests already read from stdin, Windows has no real exec, and during a reinstall the interpreter can be missing
for a few seconds.

## Change
- `_install_stamp()`: time and size of the package's `RECORD` file, which every install rewrites (the version
  number stayed 0.3.1 across this week's reinstalls).
- Each stdio tool result is checked against the stamp taken at start; the first result after a change ends with
  one note asking the agent to tell the user the session needs a restart. Said once per server. Nothing is said when
  the stamp cannot be read (a source checkout, or mid-install).

## Not covered
Servers already running when this ships are the old code and cannot say anything; those sessions need a restart.
