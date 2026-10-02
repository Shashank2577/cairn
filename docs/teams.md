# Teams

The same server works for one developer and for a team. Moving to a shared server means changing
configuration, not installing anything else ([ADR-0007](adr/0007-one-server-many-projects.md)).
Platform state (users, teams, projects, tokens, sessions and the audit log) lives in
`$CAIRN_HOME/platform.db`. Each project's data stays in its own `.cairn/` folder.

## Local mode and team mode

| | Local mode (default) | Team mode |
|---|---|---|
| Start | `cairn ui`, `cairn up` or `cairn serve` | `cairn serve --team`, or `mode = "team"` in `server.toml` |
| Binds to | Loopback only | Any address |
| Sign-in | None. Every request acts as the local owner. | A session cookie (sign in on the page) or a bearer token |
| Projects | Every repository you ran `cairn ui` or `cairn up` in, in your "Personal" team | Projects registered on the server, by local path or by git URL |
| Protections | Loopback Host header only; state changes from other browser origins are refused | Also: roles, CSRF tokens for cookie requests, login throttling |

In local mode a bearer token, if you send one, narrows what a request can do.

## Set up a team server

1. **Create the first owner** on the server machine. This user becomes a server admin. If the
   machine already has a solo owner, `team init` adopts that account and its projects.

   ```sh
   cairn team init --email you@example.com --name "Your Name" --team "Acme"
   ```

   This prints a one-time password, which must be changed at first sign-in. To type your own
   instead, pass `--set-password`. Passwords need at least 10 characters.

2. **Configure the server** in `$CAIRN_HOME/server.toml`:

   ```toml
   [server]
   mode = "team"
   host = "127.0.0.1"                       # the reverse proxy runs on the same machine
   port = 4747
   public_url = "https://cairn.example.com"
   trust_proxy = true
   ```

   Every key is listed in [configuration](configuration.md#server-settings-cairn_homeservertoml).

3. **Run it** under your process manager:

   ```sh
   cairn serve --team
   ```

4. **Add projects:**

   ```sh
   cairn project add https://github.com/acme/api.git --team acme --branch main   # cloned under $CAIRN_HOME/repos
   cairn project add /srv/checkouts/web --team acme                             # a folder on the server
   ```

   A project added from the terminal is cloned (or registered) but not synced yet: open it on the
   page and choose "Run the first sync". Admins can also add projects from the page (Team →
   Projects), which clones the repository and runs the first sync in the background.

5. **Invite people:** `cairn team invite alice@example.com --role member --team acme`, or use Team →
   Members. The link (`<public_url>/?invite=<token>`) is shown once and expires after
   `invite_days` (7 by default). A new user picks a name and password when accepting. A signed-in
   user just joins.

## Roles

Roles are ranked owner > admin > member > viewer. Each action names the lowest role that may do it
(`src/cairn/platform/rbac.py`):

| Action | What it covers | viewer | member | admin | owner |
|---|---|:-:|:-:|:-:|:-:|
| `project.read` | Map, specs, timeline, memory, sessions, search, context packs, MCP read tools | ✓ | ✓ | ✓ | ✓ |
| `project.write` | Memories, spec task status from the page, MCP write tools | | ✓ | ✓ | ✓ |
| `project.sync` | Start a sync or refresh from the remote | | ✓ | ✓ | ✓ |
| `project.capture` | Agents sending captured sessions to a team server | | ✓ | ✓ | ✓ |
| `project.admin` | Project settings, rename, delete, webhook, per-project roles, and adding projects to the team | | | ✓ | ✓ |
| `team.read` | See the team and its members | ✓ | ✓ | ✓ | ✓ |
| `team.members` | Invite, remove and change roles | | | ✓ | ✓ |
| `team.tokens` | List and revoke other members' tokens | | | ✓ | ✓ |
| `team.audit` | Read the team's audit log | | | ✓ | ✓ |
| `team.admin` | Rename the team and change its settings | | | | ✓ |
| `team.delete` | Delete the team | | | | ✓ |

- Admins manage people at or below their own role. Only owners can make or change owners, and a
  team always keeps at least one owner.
- **Per-project overrides** replace a member's team role for one project: `none` (hides the
  project), `viewer`, `member` or `admin`. Team owners are never overridden. You can't set an
  override above your own role. Manage them in Team → Projects, or with
  `PUT /api/projects/<id>/members/<user id>`.
- **Server admins** (the first owner that `cairn team init` creates, and the local owner) manage accounts
  (`cairn user …`, or Server users in the account menu), register folders that are already on the server, read the whole
  audit log and, when `team_creation = "admins"`, create teams.
- A project the caller can't see answers 404. An action the role doesn't allow answers 403.

## API tokens

Tokens are for agents, CI and scripts. Create one on the page (account menu → API tokens, or Team →
API tokens), with `POST /api/tokens`, or on the server with `cairn token issue`:

```sh
cairn token issue --name "alice laptop" --user alice@example.com --team acme --project api --scope agent
```

- A token acts as its user within **one team**, and optionally **one project**. It can never do more
  than the user's role allows. Scopes only narrow it further.
- **Scopes** are actions or presets: `read` (`project.read`, `team.read`), `agent` (the default:
  read, write, capture and `team.read`), `ci` (`project.read`, `project.sync`) and `all` (whatever
  the role allows).
- The value looks like `cairn_<8-character prefix>_<40 characters>` and is shown once. Only its
  SHA-256 digest is stored. Send it as `Authorization: Bearer <token>`.
- Tokens expire after `token_days` (90 by default). `--expires-days 0` means never.
  `cairn token revoke <id|prefix|token>` revokes one at once. Removing a member revokes their
  tokens for that team, and disabling an account stops all of its tokens.
- A token can't be used to change passwords, manage sign-in sessions, issue other tokens, create
  teams or accept invitations.

## Agents on a team server

Each developer, in their checkout of the project:

```sh
cairn agents connect --server https://cairn.example.com --project <project id>
export CAIRN_TOKEN=cairn_…        # a token with the agent scope, for this project
```

The project id (`p_…`) is in the page's URL (`#/p/<id>/…`), and `cairn project list --json` shows
it too. `agents connect` points `.mcp.json` (and the Cursor, Gemini and VS Code configs when they
exist) at `https://cairn.example.com/mcp/<project id>/` with the header
`Authorization: Bearer ${CAIRN_TOKEN}`. The token never lands on disk, so the configs can be
committed. A user whose role can't write gets the read-only tool set. See [MCP tools](mcp.md#http-endpoint).

## Session capture from other machines

Agents on developers' machines can send their captured sessions to the team server, so the whole
team's session memory lives with the project.

- **Connect:** `cairn agents connect --server URL --project ID` also writes `[team] server`,
  `[team] project` and `[team] token_env` (the `--env-var` name, `CAIRN_TOKEN` by default) into the
  repository's `.cairn/config.toml`.
- **Outbox:** from then on, every hook event the local store accepts is also queued in an outbox.
  Before it's queued it's cleaned: private blocks are removed, secrets are masked and long fields
  are trimmed. Local capture works as before.
- **Push:** the `Stop` and `SessionEnd` hooks push the outbox in the background. You can push by
  hand with `cairn sessions push`, and check what's waiting with `cairn sessions push --status`. The
  token comes from the variable named by `[team] token_env` (default `CAIRN_TOKEN`), the same one the
  agent configs reference. Sends are
  idempotent. After a failure, pushes back off and retry from the last acknowledged event.
- **Server:** `POST /api/p/<project id>/sessions/events` takes up to 500 events and 16 MB per
  request and needs `project.capture` (member and above; tokens with the `agent` scope have it). Other roles
  get 403. A malformed batch gets 400, and one that's too large gets 413. Events go through the same
  privacy, redaction and skip rules as local capture, under the server's own `[recall]` settings.
  Each session belongs to the member whose token pushed it. Its id is prefixed with their user id,
  so one member can't write into another's session, and the Sessions page shows whose machine it
  came from. The server's worker then turns them into observations.

Events captured before `[team]` was set aren't sent. Model work happens once, on the server:
`cairn agents connect` sets `[recall] worker_model = false`, so the developer's own worker still
writes local records (the session-start context keeps working offline) but derives them without a
model. Set it back to `true` if you want both.

## Git projects and webhooks

A project registered from a git URL is cloned under `repos_dir` (`$CAIRN_HOME/repos/<project id>`).
The server follows one branch: the one you give with `--branch`, or the remote default otherwise.

- **Supported URLs:** `https://`, `ssh://` and `user@host:path`. The server refuses URLs with
  embedded credentials. Use the server's git credential helper or an SSH deploy key instead.
  `http://` and `git://` remotes need `allow_insecure_git`. `file://` URLs and server paths need
  `allow_local_git` when added over HTTP; the terminal commands may always use them. Git runs
  without a shell, prompts or repository hooks.
- **Refresh:** the Refresh button in Team → Projects, or `POST /api/projects/<id>/refresh`, fetches
  the branch, resets the clone to match it exactly and re-syncs. `cairn project refresh <ref>`
  only fetches and resets; sync afterwards from the page. Local folders are never pulled.
- **Webhooks:** `cairn project webhook <ref>` (or "Set up webhook" in Team → Projects) prints the payload
  URL, `<public_url>/api/projects/<id>/hooks/git`, and a secret, shown once. Configure the forge to
  send push events as `application/json`:
  - GitHub, Gitea and Bitbucket: use the secret as the webhook secret. Deliveries are checked with
    HMAC-SHA256 (`X-Hub-Signature-256`, `X-Gitea-Signature`).
  - GitLab: use it as the secret token (`X-Gitlab-Token`).

  A push to the followed branch triggers a refresh and a sync. Pings, other events, other branches
  and branch deletions are acknowledged and ignored. Bodies over 10 MB are refused. Rotating the
  secret invalidates the old one. The secret itself is never stored, only its digest; for signed
  deliveries (GitHub, Gitea, Bitbucket) it's re-derived from the server key when one arrives.

## Audit log

Every platform change is recorded: server bootstrap, user creation, disabling and enabling, admin
changes, sign-ins (including failed ones) and sign-outs, team creation, updates and deletion,
invitations, joins, role changes and removals, project registration, updates, deletion and role
overrides, webhook rotations and deliveries, and token issue and revocation. Each entry has the
actor, target, team, project, time and client address. Changes made with the terminal commands are
recorded too, with no actor.

Read it on the page (Team → Audit log), with `GET /api/audit?team=<id>` (`team.audit`), or with
`?project=<id>` (`project.admin`). Only server admins can read the whole log.

## Deploying behind a reverse proxy

Run `cairn serve --team` on loopback and let the proxy terminate TLS:

- Set `public_url` to the external address. It pins the allowed Host header and browser origin,
  and it builds invite and webhook links. Without `public_url` or `allowed_hosts`, team mode accepts
  any Host header and logs a warning.
- Set `trust_proxy = true` so the server reads the client address and scheme from
  `X-Forwarded-For` and `X-Forwarded-Proto`. Only do this when the proxy is the only way in.
- Forward the original `Host` header.
- The page's live updates are a server-sent event stream (`/api/p/<id>/stream`), and model
  explanations stream from `/api/p/<id>/narrate`. Turn off response buffering for both, for example
  `proxy_buffering off;` in nginx.
- Session cookies are `HttpOnly` and `SameSite=Lax`. Over HTTPS they're `Secure`, with the `__Host-`
  prefix. Cookie-authenticated requests that change state must send the `X-CSRF-Token` header the
  sign-in response returns. The page does this for you.
- The server key signs CSRF tokens and derives webhook secrets. It's `$CAIRN_HOME/secret.key`,
  created with mode 0600, or you can supply it in `CAIRN_SECRET_KEY`. Back it up with
  `platform.db`. If the key is lost, rotate the webhook secrets of projects fed by GitHub, Gitea or
  Bitbucket.

Example nginx location:

```nginx
location / {
    proxy_pass http://127.0.0.1:4747;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-For $remote_addr;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_buffering off;
}
```

## Shared stores

By default each project's stores are embedded files in its `.cairn/` folder on the server. Teams
running many projects, or several servers, can point the fact graph and memory at shared servers.
Put the settings in the project's committed `.cairn/config.toml`:

```toml
[temporal]
url = "bolt://graph.internal:7687"   # the backend (neo4j here) is worked out from the URL
user = "cairn"                    # password from CAIRN_TEMPORAL_PASSWORD

[memory]
vector_store = { provider = "qdrant", config = { url = "http://vectors.internal:6333" } }
```

Install the client libraries with extras: `cairn-brain[temporal-neo4j]`,
`cairn-brain[temporal-falkordb]`, and `cairn-brain[memory-server]` (Qdrant, pgvector and Chroma).
Extras install the same way:
`uv tool install --python 3.12 'cairn-brain[temporal-neo4j]'`.
Neptune (`neptune-db://…`) is supported too, but its AWS client libraries aren't part of any extra,
so you install them yourself.
Facts and memories are always mirrored into each project's read model, so the page and agents keep
answering even while a shared store is down ([ADR-0003](adr/0003-embedded-temporal-store.md)).
