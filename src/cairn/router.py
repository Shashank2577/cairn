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

import os
from dataclasses import dataclass
from typing import Any

from .project import Project
from .store import Brain

TIERS = ("fast", "balanced", "deep", "frontier")

TASK_TIER: dict[str, str] = {
    "classify": "fast", "summarize": "fast", "label": "fast", "brief": "fast",
    "extract": "balanced", "episode": "balanced", "memory": "balanced", "link": "balanced",
    "why": "deep", "impact": "deep", "drift": "deep", "ask": "deep",
    "review": "frontier",
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


class Router:
    def __init__(self, project: Project, brain: Brain | None = None):
        self.project = project
        self.brain = brain
        self.provider = str(project.cfg("models.provider", "anthropic"))
        self.base_url = project.cfg("models.base_url")
        self._client: Any = None

    # ---- availability ----------------------------------------------------------------------------
    @property
    def api_key(self) -> str | None:
        if os.environ.get("CAIRN_API_KEY"):
            return os.environ["CAIRN_API_KEY"]
        if self.provider == "anthropic":
            return os.environ.get("ANTHROPIC_API_KEY")
        return os.environ.get("OPENAI_API_KEY") or ("local" if self.base_url else None)

    @property
    def available(self) -> bool:
        return bool(self.api_key)

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
        tier = TASK_TIER.get(task, "balanced")
        idx = TIERS.index(tier)
        while input_tokens > COMFORT_WINDOW[TIERS[idx]] and idx < len(TIERS) - 2:  # never auto-escalate to frontier
            idx += 1
        return TIERS[idx]

    # ---- calls -----------------------------------------------------------------------------------
    def complete(self, task: str, prompt: str, *, system: str = "", cached_context: str = "",
                 max_tokens: int = 1200, budget: Budget | None = None, tier: str | None = None) -> str:
        """Run one completion. ``cached_context`` is sent as a prompt-cached block (stable prefix)."""
        if not self.available:
            raise RuntimeError("no model key configured")
        est = estimate_tokens(system + cached_context + prompt) + max_tokens
        tier = tier or self.tier_for(task, est)
        model = self.model(tier)
        if budget:
            budget.check(est)
        usage = {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0}
        ok = False
        try:
            if self.provider == "anthropic":
                text, usage = self._anthropic(model, system, cached_context, prompt, max_tokens)
            else:
                text, usage = self._openai(model, system, cached_context, prompt, max_tokens)
            ok = True
            return text
        finally:
            if budget:
                budget.charge(usage["input"] + usage["output"] or est)
            if self.brain:
                self.brain.log_call(task, tier, model, usage, ok)

    def _anthropic(self, model: str, system: str, cached: str, prompt: str, max_tokens: int):
        import anthropic

        if self._client is None:
            kwargs = {"api_key": self.api_key}
            if self.base_url:
                kwargs["base_url"] = self.base_url
            self._client = anthropic.Anthropic(**kwargs)
        sys_blocks: list[dict] = []
        if system:
            sys_blocks.append({"type": "text", "text": system})
        if cached:
            sys_blocks.append({"type": "text", "text": cached, "cache_control": {"type": "ephemeral"}})
        resp = self._client.messages.create(model=model, max_tokens=max_tokens, system=sys_blocks or anthropic.NOT_GIVEN,
                                            messages=[{"role": "user", "content": prompt}])
        text = "".join(getattr(b, "text", "") for b in resp.content)
        u = resp.usage
        return text, {"input": u.input_tokens or 0, "output": u.output_tokens or 0,
                      "cache_read": getattr(u, "cache_read_input_tokens", 0) or 0,
                      "cache_write": getattr(u, "cache_creation_input_tokens", 0) or 0}

    def _openai(self, model: str, system: str, cached: str, prompt: str, max_tokens: int):
        import httpx

        base = (self.base_url or "https://api.openai.com/v1").rstrip("/")
        messages = []
        if system or cached:
            messages.append({"role": "system", "content": "\n\n".join(x for x in (system, cached) if x)})
        messages.append({"role": "user", "content": prompt})
        r = httpx.post(f"{base}/chat/completions", timeout=120,
                       headers={"Authorization": f"Bearer {self.api_key}"},
                       json={"model": model, "messages": messages, "max_tokens": max_tokens})
        r.raise_for_status()
        data = r.json()
        u = data.get("usage", {})
        return data["choices"][0]["message"]["content"], {
            "input": u.get("prompt_tokens", 0), "output": u.get("completion_tokens", 0),
            "cache_read": (u.get("prompt_tokens_details") or {}).get("cached_tokens", 0), "cache_write": 0}
