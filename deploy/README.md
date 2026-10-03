# Deploying the Cairn team server

A minimal self-hosted deployment: one container, one volume. The full hosting
story is in [docs/multi-repo.md](../docs/multi-repo.md); team-mode mechanics are
in [docs/teams.md](../docs/teams.md).

1. `cd deploy`
2. `CAIRN_PUBLIC_URL=https://cairn.example.com docker compose up -d --build`
   (or run without a URL first and add it before inviting people)
3. Create the first owner — this account is a server admin — and note the
   one-time password it prints: `docker compose exec cairn cairn team init
   --email you@example.com --name "Your Name" --team "EdgePlus"`
4. Sign in at your `CAIRN_PUBLIC_URL`, change the password, add each repository
   on the Team → Projects page (or `docker compose exec cairn cairn project add
   https://github.com/edgeplus/api.git --team edgeplus --branch main`) and run
   the first sync from the page.
5. Invite engineers: `docker compose exec cairn cairn team invite
   alice@example.com --role member --team edgeplus`.
6. Each engineer, in their checkout: `cairn agents connect --server
   https://cairn.example.com --project <project id>` (the id is in the page
   URL), create a token on the page (or `cairn token issue`) and export
   `CAIRN_TOKEN=<token>`.
7. Point git forges at each project's webhook (`cairn project webhook <ref>`
   prints the URL and secret) so pushes re-sync automatically.

The server listens on `127.0.0.1:4747` on the host. Put a TLS proxy in front of
it and set `CAIRN_PUBLIC_URL` to the external address — it pins the allowed
Host header and origin and builds the invite and webhook links. With Caddy that
is the whole config:

```
cairn.example.com {
    reverse_proxy 127.0.0.1:4747
}
```

Caddy flushes the server's event streams (live updates, streamed explanations)
unbuffered; with nginx set `proxy_buffering off;` — the full proxy checklist,
including `trust_proxy`, is in [docs/teams.md](../docs/teams.md#deploying-behind-a-reverse-proxy).

Back up the `cairn-state` volume: it holds `platform.db` (users, teams, tokens,
audit log), `secret.key` (back it up together with `platform.db` — it signs CSRF
tokens and derives webhook secrets), `server.toml`, and the cloned repositories
whose `.cairn/` folders are the team's shared brain. A nightly
`docker run --rm -v cairn-state:/data -v $PWD/backups:/backup alpine tar
czf /backup/cairn-$(date +%F).tgz -C /data .` is enough at this scale; everything
else is rebuildable with `cairn sync`.
