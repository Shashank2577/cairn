**Flow of an authenticated MCP call to a Cairn team server**: A client sends a bearer token; the request gate asks the platform service to verify it against the token table and either admits the call with the token's principal or refuses it with 401.

| Element | Type | Technology | Responsibility | Evidence |
|---|---|---|---|---|
| MCP client | external-system |  | An agent configured with a team token | docs/teams.md (extracted) |
| Request gate | component | platform/web.py evaluate() | Authenticates every request | src/cairn/platform/web.py:155 (extracted) |
| Platform service | component | platform/service.py | Owns users and tokens | src/cairn/platform/service.py:1289 (extracted) |
| Team store | data-store | SQLite api_tokens | Hashed tokens and users | src/cairn/platform/service.py:1295 (extracted) |

| # | From | To | What | How | Evidence |
|---|---|---|---|---|---|
| 1 | MCP client | Request gate | Call an MCP tool | POST /mcp/<project>/ · Bearer | src/cairn/platform/web.py:167 (extracted) |
| 2 | Request gate | Platform service | Verify the token | principal_from_bearer() | src/cairn/platform/web.py:168 (extracted) |
| 3 | Platform service | Team store | Look up token by prefix | SELECT api_tokens JOIN users | src/cairn/platform/service.py:1295 (extracted) |
| 4 | Team store | Platform service | Token row and user | row | src/cairn/platform/service.py:1298 (extracted) |
| 5 | Platform service | Request gate | Principal with scopes | Principal | src/cairn/platform/service.py:1306 (extracted) |
| 6 | Request gate | MCP client | Run the tool | 200 · tool result | src/cairn/platform/web.py:184 (extracted) |
| 7 | Request gate | MCP client | Refuse the call | 401 invalid_token | src/cairn/platform/web.py:170 (extracted) |
