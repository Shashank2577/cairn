"""Word-overlap measures used to check a model's memory decisions against the facts they are about."""
from __future__ import annotations

import re


def _norm(text: str) -> str:
    return " ".join((text or "").split())


def _words(text: str) -> set[str]:
    return set(re.findall(r"\w+", (text or "").lower()))


def _contained(part: str, whole: str) -> float:
    """Share of ``part``'s words that appear in ``whole``."""
    wp = _words(part)
    return len(wp & _words(whole)) / len(wp) if wp else 0.0


_FUNCTION_WORDS = frozenset("""
a an the and or but if then else of in on at to for from by with without as is are was were be been being
it its this that these those we you they he she i me my our your their them us not no nor do does did done
has have had will would should shall can could must may might so than too very all any each every only also
into onto over about after before when where which who whom what how there here via per just
""".split())


def _content_words(text: str) -> set[str]:
    words = _words(text)
    return (words - _FUNCTION_WORDS) or words


def _overlap(a: str, b: str) -> float:
    """Shared words over the shorter text's words: high when one statement is about the other.
    Function words ("the", "is", "with", ...) do not count, so two unrelated sentences with the same
    grammar do not look related."""
    wa, wb = _content_words(a), _content_words(b)
    return len(wa & wb) / min(len(wa), len(wb)) if wa and wb else 0.0


def _jaccard(a: str, b: str) -> float:
    wa, wb = _words(a), _words(b)
    return len(wa & wb) / len(wa | wb) if wa and wb else 0.0


def same_fact(a: str, b: str) -> bool:
    """Whether two memory texts state the same thing (same words, give or take a few)."""
    return bool(a and b) and (_norm(a).lower() == _norm(b).lower() or _jaccard(a, b) >= 0.6)


# Thresholds for accepting a model's decision about an existing memory
CARRIES_FACT = 0.5   # an updated/added text must keep at least this share of the new fact's words
ABOUT_MEMORY = 0.3   # the new fact must share at least this much (over the shorter text) with the memory it changes
