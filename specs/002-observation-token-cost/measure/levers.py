"""Experimental prompt levers, applied to a replay run only (no change to src/). Each patch is a function taking the
loaded mode; ``trailer_once`` also rewrites the per-event prompt so the closing instructions appear once per call."""
from __future__ import annotations

from cairn.engines.recall import prompts

TRAILER_MARK = "If a <parameters> or <outcome> block above contains"


def terse(mode) -> None:
    """A shorter observer brief and a hard size limit on each observation (target: fewer input and output tokens)."""
    p = mode.config["prompts"]  # Mode.prompts returns a copy
    p["observer_role"] = ("You watch a DIFFERENT coding session and record durable technical signal about it. You are a silent, "
                          "one-way recorder: never contact the session or its agents; output only the XML below.")
    p["recording_focus"] = ("WHAT TO RECORD\nWhat changed, what was learned, what failed, decisions made. Be specific: file, function, "
                            "value, error text. Use past-tense verbs (implemented, fixed, discovered, confirmed). Do not describe "
                            "the act of observing.")
    p["skip_guidance"] = ("WHEN TO SKIP\nRoutine operations with no finding: empty status checks, plain listings, clean installs, "
                          "repeats of something already recorded, empty searches. Then return <skip_summary reason=\"noise\" /> only.")
    p["field_guidance"] = ("facts: at most 3, each ONE self-contained statement under 20 words with specifics (no pronouns).\n"
                           "narrative: at most 2 sentences. files: full paths from the project root.")
    p["footer"] = ("You summarize a DIFFERENT session, not this one. Output only <observation> blocks or <skip_summary />. "
                   "When several tool uses in this batch belong to one piece of work, write ONE observation for them.")
    p["type_guidance"] = p["type_guidance"][:300]
    p["concept_guidance"] = p["concept_guidance"][:250]


def nocompress() -> None:
    """Oversized tool fields are cut head/tail with an elision marker instead of being condensed by an extra model call."""
    from cairn.engines.recall.observer import ObserverLLM
    ObserverLLM.compress = lambda self, text, budget: None


def apply(names: list[str], mode) -> set[str]:
    flags = set()
    for n in names:
        if n == "terse":
            terse(mode)
        elif n == "trailer_once":
            flags.add(n)
        elif n == "nocompress":
            nocompress()
        else:
            raise SystemExit(f"unknown patch {n}")
    return flags


def split_trailer(block: str) -> tuple[str, str]:
    head, sep, tail = block.partition("\n\n" + TRAILER_MARK)
    return head, (TRAILER_MARK + tail) if sep else ""


def install_trailer_once() -> None:
    """build_observation_prompt returns only the event; the closing instructions are appended once per model call."""
    original = prompts.build_observation_prompt

    def build(*a, **k):
        return split_trailer(original(*a, **k))[0]
    prompts.build_observation_prompt = build
    prompts._TRAILER = split_trailer(original("T", "{}", "{}", 0))[1]
