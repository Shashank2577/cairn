---
name: cairn-how-it-works
description: Explain how Cairn's session memory captures observations, when memory injection kicks in, and where data lives. Use when the user asks "how does Cairn's session memory work?" or "what is this thing doing?".
---

# How Cairn's session memory works

## What it does

Every Read, Edit, and Bash that your coding agent makes turns into a compressed observation. Observations get summarized at session end. Relevant ones get auto-injected into future prompts so the next session starts with context from the last one — no re-explaining the codebase, no re-discovering decisions.

## When it kicks in

Memory injection starts on your second session in a project.

The first session in a fresh project seeds memory; subsequent sessions receive auto-injected context for relevant past work. Run `/learn-codebase` if you want to front-load the entire repo into memory in a single pass (~5 minutes, optional).

## Where data lives

Everything stays on this machine: each repository's session memory lives in `<repo>/.cairn/sessions.db` (observations, summaries, prompts and the search index), its settings in the `[recall]` section of `<repo>/.cairn/config.toml`, and user-level files (custom modes, logs) under `$CAIRN_HOME` (default `~/.cairn`).

Nothing leaves your machine except calls to whichever model provider you configured for compression (the Anthropic API, an OpenAI-compatible endpoint such as OpenRouter or Ollama, or the signed-in Claude Code CLI). Delete `.cairn/sessions.db` to forget a repository's session memory; `cairn uninstall` removes the agent integrations.
