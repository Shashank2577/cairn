"""Scripted stand-in for ``cairn.router.Router`` used by the memory engine tests.

It recognises each prompt the engine sends (fact extraction, ADD/UPDATE/DELETE/NONE reconciliation,
additive extraction, procedural summaries, relevance scoring, answering) and replies the way a model
would, from simple deterministic rules. No network, no model.

Reconciliation rules, per new fact against the old memories shown in the prompt:
  same text (case/space-insensitive)              -> NONE on that memory
  "no longer <old text>"                          -> DELETE that memory
  same first two words as an old memory           -> UPDATE it to the new fact
  "<a> + <b>"                                     -> two ADDs (a compound statement split in two)
  otherwise                                       -> ADD
"""
from __future__ import annotations

import ast
import json
import re


def _norm(text: str) -> str:
    return " ".join(str(text).lower().split())


def _blocks(prompt: str) -> list[str]:
    return re.findall(r"```\s*\n(.*?)\n\s*```", prompt, re.S)


class FakeRouter:
    def __init__(self, available: bool = True, *, bad_json_first: int = 0):
        self.available = available
        self.calls: list[dict] = []
        self.bad_json_first = bad_json_first
        self.project = None
        self.brain = None

    def deep_enabled(self) -> bool:
        return self.available

    def tasks(self) -> list[str]:
        return [c["task"] for c in self.calls]

    def complete(self, task, prompt, *, system="", cached_context="", max_tokens=1200, budget=None, tier=None):
        if not self.available:
            raise RuntimeError("no model key configured")
        self.calls.append({"task": task, "system": system, "prompt": prompt, "tier": tier, "max_tokens": max_tokens})
        if self.bad_json_first > 0 and "JSON" in system:
            self.bad_json_first -= 1
            return "Sure! Here is what I found (not json)"
        if "relevance scoring assistant" in system:
            q = set(re.findall(r"\w+", prompt.split("Document:")[0].lower()))
            d = set(re.findall(r"\w+", prompt.split("Document:")[-1].lower()))
            return f"{len(q & d) / max(1, len(q)):.2f}"
        if "memory summarization system" in system:
            return "## Summary of the agent's execution history\n" + prompt.strip()[-300:]
        if "Memory Extractor" in system:
            return self._additive(prompt)
        if "smart memory manager" in prompt:
            return self._reconcile(prompt)
        if '"facts"' in system or "'facts'" in system:
            return self._facts(prompt)
        if "You can call these tools" in system:
            return json.dumps({"content": "", "tool_calls": [{"name": "lookup", "arguments": {"q": prompt[:20]}}]})
        if task == "memory_chat":
            return "ANSWER from memories: " + prompt
        if "INSTRUCTIONS:" in prompt:
            return "INSTRUCTIONS: remember build tooling choices\nTEST_MESSAGE: We use pnpm."
        return "ok"

    # ---- scripted replies ------------------------------------------------------------------------
    @staticmethod
    def _facts(prompt: str) -> str:
        text = prompt.split("Input:\n", 1)[-1]
        facts = []
        for line in text.splitlines():
            if line.startswith("user: "):
                fact = line[len("user: "):].strip()
                if fact and "nothing to remember" not in fact:
                    facts.append(fact)
        return json.dumps({"facts": facts})

    @staticmethod
    def _reconcile(prompt: str) -> str:
        blocks = _blocks(prompt)
        facts = ast.literal_eval(blocks[-1].strip())
        old = ast.literal_eval(blocks[0].strip()) if len(blocks) >= 2 else []
        out, next_id = [], len(old)
        for fact in facts:
            if " + " in fact:  # the model splits a compound statement into separate memories
                for part in fact.split(" + "):
                    out.append({"id": str(next_id), "text": part.strip(), "event": "ADD"})
                    next_id += 1
                continue
            f = _norm(fact)
            decided = None
            for mem in old:
                t = _norm(mem["text"])
                if f == t:
                    decided = {"id": mem["id"], "text": mem["text"], "event": "NONE"}
                elif f.startswith("no longer ") and f[len("no longer "):] == t:
                    decided = {"id": mem["id"], "text": mem["text"], "event": "DELETE"}
                elif f.split()[:2] == t.split()[:2]:
                    decided = {"id": mem["id"], "text": fact, "event": "UPDATE", "old_memory": mem["text"]}
                if decided:
                    break
            if decided is None:
                decided = {"id": str(next_id), "text": fact, "event": "ADD"}
                next_id += 1
            out.append(decided)
        return "```json\n" + json.dumps({"memory": out}) + "\n```"

    @staticmethod
    def _additive(prompt: str) -> str:
        new = prompt.split("## New Messages\n", 1)[1].split("\n\n## Observation Date", 1)[0]
        existing = prompt.split("## Existing Memories\n", 1)[1].split("\n\n## New Messages", 1)[0]
        linked = [m["id"] for m in json.loads(existing or "[]")][:1]
        mems = []
        for line in new.splitlines():
            if line.startswith("user: "):
                mems.append({"id": str(len(mems)), "text": line[6:].strip(), "attributed_to": "user",
                             "linked_memory_ids": linked})
        return json.dumps({"memory": mems})
