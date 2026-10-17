# Deep mode and limits, in plain words

## What "deep" is

Everything core in Cairn (the map, impact, why, history, specs, session capture) works with no model at all.
**Deep** is the optional part where a model reads new commits, sessions and documents and writes facts and
narrated answers. It is a separate switch so nothing depends on it.

Deep is **on** when a model backend is usable, and **off** when it is not. Cairn always says which, and why:

```text
cairn doctor
  ●  Deep   on · using the Claude Code CLI at /opt/homebrew/bin/claude
  ○  Deep   off · the Claude Code CLI was not found on PATH or in the usual install folders (...)
               fix: install or sign in to Claude Code (`claude`), or set ANTHROPIC_API_KEY ...
```

The same line appears on the UI overview and in Project settings, so the three places never disagree.

### Why deep used to turn itself off

With the Claude Code sign-in as the backend, Cairn needs to find the `claude` program. A server started by a hook,
an editor or a service manager often has a short search path that does not include Homebrew or `~/.local/bin`, so
deep looked off although `claude` was installed. Cairn now also looks in `/opt/homebrew/bin`, `/usr/local/bin`,
`~/.local/bin` and `~/.claude/local`, and shows the one it used.

To turn deep off on purpose, set `[deep] enabled = false` in `.cairn/config.toml`; doctor then says it was turned
off by configuration.

## The limits (what people call "budget")

Four different limits used to share one word. They now have plain names, the same everywhere
(`cairn doctor`, Project settings, the overview API):

| Limit | Config key | Default | What it caps | At the limit |
|---|---|---|---|---|
| Context size | `context.budget` | 1,800 tokens | The most Cairn context an agent gets in one answer. | Lower-ranked evidence is left out; the best of each section is kept first. |
| Deep sync allowance | `deep.budget_tokens` | 150,000 tokens | The most model tokens one deep sync may spend. | The deep sync stops, keeps what it finished, continues next sync. Deterministic features are unaffected. |
| Deep sync time limit | `deep.wall_seconds` | 300 s | The longest a deep sync may run. | It stops and later syncs fill in the rest. |
| Memory learning time limit | `memory.wall_seconds` | 300 s | The longest a sync spends learning decisions and gotchas from the repository. | It stops; the remaining items are learned next sync. |

"Used" next to each limit means: for context size, the typical answer so far; for the deep allowance, new model
tokens spent by every job in the last 24 hours (a day's total, not one sync, so it can be larger than the
per-sync allowance).

## A server that cannot be trusted

One local server serves every repository on the machine. It records where it runs from. If that code is deleted
(for example, a removed git worktree), or its version differs from your installed `cairn`, then `cairn status`,
`cairn doctor` and the UI say so and give one fix:

```text
cairn up      # replaces the server; every repository on this machine is interrupted for a few seconds
```

`cairn up` and `cairn ui` replace such a server automatically and tell you they did.
