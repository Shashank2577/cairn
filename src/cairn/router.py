"""Model router: the right model for each job, with prompt caching, budgets and a cost ledger.

Tiers
  fast      high-volume classification, labels, summaries        (default: Haiku)
  balanced  extraction, fact building, linking tie-breaks         (default: Sonnet)
  deep      judgment & synthesis: why, impact, drift, ask         (default: Opus)
  frontier  opt-in whole-system architecture reviews              (default: Fable)

Every call is attributed in the ledger (``cairn models --ledger``). Deterministic features never
depend on this module; it only enriches.
"""
from __future__ import annotations

import json
import logging
import os
import queue
import shutil
import sqlite3
import subprocess
import tempfile
import threading
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

from .project import Project
from .store import Brain

log = logging.getLogger("cairn.router")

TIERS = ("fast", "balanced", "deep", "frontier")

TASK_TIER: dict[str, str] = {
    "classify": "fast", "summarize": "fast", "label": "fast", "brief": "fast",
    "extract": "balanced", "episode": "balanced", "memory": "balanced", "link": "balanced",
    "why": "deep", "impact": "deep", "drift": "deep", "ask": "deep",
    "review": "frontier",
    # temporal fact graph
    "temporal.extract": "balanced", "temporal.dedupe": "balanced", "temporal.resolve": "fast",
    "temporal.attributes": "fast", "temporal.timestamps": "fast", "temporal.summarize": "fast",
    "temporal.community": "fast", "temporal.saga": "fast", "temporal.rerank": "fast",
    # memory
    "memory_rerank": "fast", "memory_chat": "balanced", "memory_procedural": "balanced", "memory_instructions": "fast",
    # code & document graph
    "graph-extract": "balanced", "graph-label": "fast", "graph-dedup": "fast", "graph-triage": "deep",
    # agent session memory
    "recall_observe": "fast", "recall_summarize": "fast", "recall_compress": "fast", "recall_corpus": "balanced",
}
# Input size (tokens) above which a tier is escalated one step for quality.
COMFORT_WINDOW = {"fast": 24_000, "balanced": 80_000, "deep": 180_000, "frontier": 400_000}


class BudgetExceeded(RuntimeError):
    pass


@dataclass
class Budget:
    total: int
    used: int = 0

    @property
    def remaining(self) -> int:
        return max(0, self.total - self.used)

    def check(self, estimate: int) -> None:
        if estimate > self.remaining:
            raise BudgetExceeded(f"needs ~{estimate} tokens, {self.remaining} left of {self.total}")

    def charge(self, n: int) -> None:
        self.used += n


def estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)


PROVIDERS = ("anthropic", "openai", "claude-code")
NO_USAGE = {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0}


def resolve_provider(configured: str, base_url: str | None = None) -> str | None:
    """`auto` picks, in order: an Anthropic key, an OpenAI-compatible key or endpoint, then the
    signed-in Claude Code CLI (the user's subscription, no key needed). None when nothing is usable."""
    if configured and configured != "auto":
        return configured
    if os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("CAIRN_API_KEY"):
        return "anthropic"
    if os.environ.get("OPENAI_API_KEY") or base_url:
        return "openai"
    if shutil.which("claude") and not os.environ.get("CAIRN_NO_CLI_MODELS"):
        return "claude-code"
    return None


class Router:
    def __init__(self, project: Project, brain: Brain | None = None):
        self.project = project
        self.brain = brain
        self.base_url = project.cfg("models.base_url")
        self.provider = resolve_provider(str(project.cfg("models.provider", "auto")), self.base_url)
        self._client: Any = None

    # ---- availability ----------------------------------------------------------------------------
    @property
    def api_key(self) -> str | None:
        if os.environ.get("CAIRN_API_KEY"):
            return os.environ["CAIRN_API_KEY"]
        if self.provider == "anthropic":
            return os.environ.get("ANTHROPIC_API_KEY")
        if self.provider == "claude-code":
            return "subscription" if shutil.which("claude") else None
        return os.environ.get("OPENAI_API_KEY") or ("local" if self.base_url else None)

    @property
    def available(self) -> bool:
        return self.provider is not None and bool(self.api_key)

    def deep_enabled(self) -> bool:
        flag = self.project.cfg("deep.enabled", "auto")
        if flag is False or str(flag).lower() in ("false", "off", "no"):
            return False
        return self.available

    def model(self, tier: str) -> str:
        return str(self.project.cfg(f"models.{tier}", "")) or {
            "fast": "claude-haiku-4-5-20251001", "balanced": "claude-sonnet-5",
            "deep": "claude-opus-5-5", "frontier": "claude-fable-5-1"}[tier]

    def table(self) -> list[dict]:
        rows = []
        for tier in TIERS:
            tasks = sorted(t for t, v in TASK_TIER.items() if v == tier)
            rows.append({"tier": tier, "model": self.model(tier), "tasks": tasks})
        return rows

    def tier_for(self, task: str, input_tokens: int = 0) -> str:
        """The task's tier. A big input moves a judgement task up one step for quality, never to frontier.
        Bulk work on the fast tier never moves: its callers bound their prompts, and paying a stronger model
        on every background call is how a plan's quota disappears."""
        tier = TASK_TIER.get(task, "balanced")
        idx = TIERS.index(tier)
        if tier != "fast" and input_tokens > COMFORT_WINDOW[tier] and idx < len(TIERS) - 2:
            idx += 1
        return TIERS[idx]

    # ---- calls -----------------------------------------------------------------------------------
    def _plan(self, task: str, prompt: str, system: str, cached_context: str, max_tokens: int,
              budget: Budget | None, tier: str | None) -> tuple[int, str, str]:
        """(estimated tokens, tier, model) for a call, after the availability and budget checks."""
        if not self.available:
            raise RuntimeError("no model key configured")
        est = estimate_tokens(system + cached_context + prompt) + max_tokens
        tier = tier or self.tier_for(task, est)
        if budget:
            budget.check(est)
        return est, tier, self.model(tier)

    def _settle(self, task: str, tier: str, model: str, usage: dict, ok: bool, est: int,
                budget: Budget | None) -> None:
        if budget:
            budget.charge(usage["input"] + usage["output"] or est)
        if self.brain:
            try:  # a busy ledger must never throw away an answer the model already gave
                self.brain.log_call(task, tier, model, usage, ok)
            except sqlite3.Error as exc:
                log.warning("model call not recorded in the ledger: %s", exc)

    def complete(self, task: str, prompt: str, *, system: str = "", cached_context: str = "",
                 max_tokens: int = 1200, budget: Budget | None = None, tier: str | None = None) -> str:
        """Run one completion. ``cached_context`` is sent as a prompt-cached block (stable prefix)."""
        est, tier, model = self._plan(task, prompt, system, cached_context, max_tokens, budget, tier)
        usage = dict(NO_USAGE)
        ok = False
        try:
            if self.provider == "anthropic":
                text, usage = self._anthropic(model, system, cached_context, prompt, max_tokens)
            elif self.provider == "claude-code":
                text, usage = self._claude_code(model, system, cached_context, prompt, max_tokens)
            else:
                text, usage = self._openai(model, system, cached_context, prompt, max_tokens)
            ok = True
            return text
        finally:
            self._settle(task, tier, model, usage, ok, est, budget)

    def stream(self, task: str, prompt: str, *, system: str = "", cached_context: str = "",
               max_tokens: int = 1200, budget: Budget | None = None, tier: str | None = None,
               cancel: threading.Event | None = None) -> Iterator[str]:
        """Like ``complete``, but yields the answer as the model writes it. Setting ``cancel`` (from any thread)
        or closing the iterator stops the model call within a moment. The ledger records every call, a stopped
        one with an estimate of what it used when the provider never got to report."""
        est, tier, model = self._plan(task, prompt, system, cached_context, max_tokens, budget, tier)
        usage = dict(NO_USAGE)
        provider = {"anthropic": self._anthropic_stream, "claude-code": self._claude_code_stream}.get(
            self.provider or "", self._openai_stream)
        chunks = provider(model, system, cached_context, prompt, max_tokens, usage, cancel)
        ok, written = False, 0
        try:
            for chunk in chunks:
                written += len(chunk)
                yield chunk
            ok = True  # finished, or stopped by the reader: neither is a failure
        except GeneratorExit:
            ok = True
            raise
        finally:
            chunks.close()
            if not any(usage.values()):
                usage.update(input=estimate_tokens(system + cached_context + prompt), output=written // 4)
            self._settle(task, tier, model, usage, ok, est, budget)

    # anthropic
    def _anthropic_client(self):
        import anthropic

        if self._client is None:
            kwargs = {"api_key": self.api_key}
            if self.base_url:
                kwargs["base_url"] = self.base_url
            self._client = anthropic.Anthropic(**kwargs)
        return self._client

    @staticmethod
    def _anthropic_system(system: str, cached: str) -> list[dict]:
        blocks: list[dict] = []
        if system:
            blocks.append({"type": "text", "text": system})
        if cached:
            blocks.append({"type": "text", "text": cached, "cache_control": {"type": "ephemeral"}})
        return blocks

    @staticmethod
    def _anthropic_usage(u: Any) -> dict:
        return {"input": u.input_tokens or 0, "output": u.output_tokens or 0,
                "cache_read": getattr(u, "cache_read_input_tokens", 0) or 0,
                "cache_write": getattr(u, "cache_creation_input_tokens", 0) or 0}

    def _anthropic(self, model: str, system: str, cached: str, prompt: str, max_tokens: int):
        import anthropic

        resp = self._anthropic_client().messages.create(
            model=model, max_tokens=max_tokens, system=self._anthropic_system(system, cached) or anthropic.NOT_GIVEN,
            messages=[{"role": "user", "content": prompt}])
        return "".join(getattr(b, "text", "") for b in resp.content), self._anthropic_usage(resp.usage)

    def _anthropic_stream(self, model: str, system: str, cached: str, prompt: str, max_tokens: int, usage: dict,
                          cancel: threading.Event | None = None):
        import anthropic

        with self._anthropic_client().messages.stream(
                model=model, max_tokens=max_tokens, system=self._anthropic_system(system, cached) or anthropic.NOT_GIVEN,
                messages=[{"role": "user", "content": prompt}]) as s:
            for text in s.text_stream:
                if cancel is not None and cancel.is_set():
                    return  # leaving the block closes the connection
                yield text
            usage.update(self._anthropic_usage(s.get_final_message().usage))

    # claude-code: one isolated, tool-less turn through the signed-in Claude Code CLI (the user's plan). It runs
    # from an empty temp folder with no user/project settings, MCP servers or tools, so the user's own hooks and
    # plugins (including Cairn's capture) never fire for Cairn's model calls. The CLI manages output length.
    @staticmethod
    def _claude_code_args(model: str, system: str, cached: str, output: str) -> list[str]:
        args = ["claude", "-p", "--model", model, "--output-format", output, "--setting-sources", "local",
                "--strict-mcp-config", "--tools", "", "--no-session-persistence"]
        if output == "stream-json":
            args += ["--verbose", "--include-partial-messages"]
        if system or cached:
            args += ["--system-prompt", "\n\n".join(x for x in (system, cached) if x)]
        return args

    @staticmethod
    def _claude_code_result(data: dict) -> dict:
        """Usage of a finished CLI turn; raises when the turn failed (quota, sign-in, model)."""
        if data.get("is_error"):
            raise RuntimeError(f"model call failed: {str(data.get('result'))[:200]}")
        u = data.get("usage") or {}
        return {"input": u.get("input_tokens", 0), "output": u.get("output_tokens", 0),
                "cache_read": u.get("cache_read_input_tokens", 0), "cache_write": u.get("cache_creation_input_tokens", 0)}

    def _claude_code(self, model: str, system: str, cached: str, prompt: str, max_tokens: int):
        del max_tokens
        with tempfile.TemporaryDirectory(prefix="cairn-model-") as cwd:
            res = subprocess.run(self._claude_code_args(model, system, cached, "json"), input=prompt,
                                 capture_output=True, text=True, timeout=600, cwd=cwd,
                                 env={**os.environ, "CAIRN_INTERNAL": "1"}, check=False)
        try:
            data = json.loads(res.stdout)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"model call failed: {(res.stderr or res.stdout).strip()[:200]}") from exc
        return str(data.get("result") or ""), self._claude_code_result(data)

    def _claude_code_stream(self, model: str, system: str, cached: str, prompt: str, max_tokens: int, usage: dict,
                            cancel: threading.Event | None = None):
        del max_tokens
        with tempfile.TemporaryDirectory(prefix="cairn-model-") as cwd, \
                open(os.path.join(cwd, "stderr.log"), "w+") as err:  # a file: a full stderr pipe can't stall the stream
            proc = subprocess.Popen(self._claude_code_args(model, system, cached, "stream-json"), cwd=cwd,
                                    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=err, text=True,
                                    env={**os.environ, "CAIRN_INTERNAL": "1"})
            lines: queue.Queue = queue.Queue()  # read on a side thread, so a stop is noticed between lines

            def pump() -> None:
                for raw in proc.stdout:
                    lines.put(raw)
                lines.put(None)
            threading.Thread(target=pump, daemon=True, name="cairn-model-stream").start()
            result = None
            try:
                proc.stdin.write(prompt)
                proc.stdin.close()
                while True:
                    try:
                        line = lines.get(timeout=0.2)
                    except queue.Empty:
                        line = ""
                    if cancel is not None and cancel.is_set():
                        return  # the reader stopped it: the process is killed below
                    if line is None:
                        break
                    try:
                        event = json.loads(line)
                    except ValueError:
                        continue
                    if event.get("type") == "stream_event":
                        delta = (event.get("event") or {}).get("delta") or {}
                        if delta.get("type") == "text_delta" and delta.get("text"):
                            yield delta["text"]
                    elif event.get("type") == "result" or "is_error" in event:
                        result = event
                proc.wait(timeout=30)
            finally:
                if proc.poll() is None:  # the reader went away (or we failed): stop the model call
                    proc.kill()
                    proc.wait()
            if result is None:
                err.seek(0)
                raise RuntimeError(f"model call failed: {err.read().strip()[:200] or f'exit {proc.returncode}'}")
            usage.update(self._claude_code_result(result))

    # openai-compatible
    def _openai_request(self, model: str, system: str, cached: str, prompt: str, max_tokens: int) -> tuple[str, dict]:
        messages = []
        if system or cached:
            messages.append({"role": "system", "content": "\n\n".join(x for x in (system, cached) if x)})
        messages.append({"role": "user", "content": prompt})
        base = (self.base_url or "https://api.openai.com/v1").rstrip("/")
        return f"{base}/chat/completions", {"model": model, "messages": messages, "max_tokens": max_tokens}

    @staticmethod
    def _openai_usage(u: dict) -> dict:
        return {"input": u.get("prompt_tokens", 0), "output": u.get("completion_tokens", 0),
                "cache_read": (u.get("prompt_tokens_details") or {}).get("cached_tokens", 0), "cache_write": 0}

    def _openai(self, model: str, system: str, cached: str, prompt: str, max_tokens: int):
        import httpx

        url, body = self._openai_request(model, system, cached, prompt, max_tokens)
        r = httpx.post(url, timeout=120, headers={"Authorization": f"Bearer {self.api_key}"}, json=body)
        r.raise_for_status()
        data = r.json()
        return data["choices"][0]["message"]["content"], self._openai_usage(data.get("usage", {}))

    def _openai_stream(self, model: str, system: str, cached: str, prompt: str, max_tokens: int, usage: dict,
                       cancel: threading.Event | None = None):
        import httpx

        url, body = self._openai_request(model, system, cached, prompt, max_tokens)
        body["stream"] = True
        if not self.base_url:  # not every compatible server accepts stream_options
            body["stream_options"] = {"include_usage": True}
        with httpx.stream("POST", url, timeout=120, headers={"Authorization": f"Bearer {self.api_key}"},
                          json=body) as r:
            r.raise_for_status()
            for line in r.iter_lines():
                if cancel is not None and cancel.is_set():
                    return
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                chunk = json.loads(data)
                for choice in chunk.get("choices") or []:
                    if (choice.get("delta") or {}).get("content"):
                        yield choice["delta"]["content"]
                if chunk.get("usage"):
                    usage.update(self._openai_usage(chunk["usage"]))
