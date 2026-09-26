"""
BM25 lemmatization for consistent keyword matching.

Uses spaCy's lemmatizer for better handling of:
- Verb forms: attending/attends/attended -> attend
- Comparatives/superlatives: older/oldest -> old
- Plurals: memories -> memory
- Avoids over-stemming: organization != organize

Also includes original -ing forms alongside lemmas to handle cases
where spaCy's context-dependent lemmatization produces inconsistent
results (e.g., "meeting" as noun vs verb -> different lemmas).

Without spaCy a deterministic normaliser (lowercase, stop-word removal, suffix stripping,
identifier splitting) is used, so keyword scoring still works offline.
"""

from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)


def lemmatize_for_bm25(text: str) -> str:
    """Lemmatize text for BM25 matching.

    Returns space-joined lemmas for full-text search. Falls back to a
    deterministic normaliser if spaCy is unavailable.
    """
    from cairn.engines.memstore.utils.nlp_models import get_nlp_lemma

    nlp = get_nlp_lemma()
    if nlp is None:
        return normalize_for_bm25(text)

    doc = nlp(text.lower())
    tokens = []

    for token in doc:
        if token.is_punct or token.is_stop:
            continue

        lemma = token.lemma_
        if lemma.isalnum():
            tokens.append(lemma)

        # Also add original if it ends in -ing and differs from lemma.
        # This handles noun/verb ambiguity (meeting/meet, attending/attend).
        if token.text.endswith("ing") and token.text != lemma and token.text.isalnum():
            tokens.append(token.text)

    return " ".join(tokens)


_WORD = re.compile(r"[^\W_]+", re.UNICODE)
_CAMEL = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_STOP = frozenset("""
a about above after again against all am an and any are as at be because been before being below between both
but by can could did do does doing down during each few for from further had has have having he her here hers
herself him himself his how i if in into is it its itself just me more most my myself no nor not now of off on
once only or other our ours ourselves out over own same she should so some such than that the their theirs them
themselves then there these they this those through to too under until up very was we were what when where which
while who whom why will with would you your yours yourself yourselves
""".split())
_SUFFIXES = (("ies", "y"), ("sses", "ss"), ("ing", ""), ("ied", "y"), ("ed", ""), ("es", ""), ("s", ""))


def _light_lemma(word: str) -> str:
    if len(word) <= 3 or word.isdigit():
        return word
    for suffix, repl in _SUFFIXES:
        if word.endswith(suffix) and len(word) - len(suffix) >= 3:
            if suffix == "s" and word.endswith("ss"):
                return word
            stem = word[: -len(suffix)] + repl
            if suffix in ("ing", "ed") and len(stem) > 3 and stem[-1] == stem[-2] and stem[-1] not in "lsz":
                stem = stem[:-1]  # running -> run, stopped -> stop
            return stem
    return word


def normalize_for_bm25(text: str) -> str:
    """spaCy-free lemmatisation: identifiers split, stop words dropped, common suffixes stripped."""
    expanded = _CAMEL.sub(" ", text or "").replace("_", " ")
    tokens = []
    for word in _WORD.findall(expanded.lower()):
        if word in _STOP:
            continue
        lemma = _light_lemma(word)
        tokens.append(lemma)
        if word.endswith("ing") and word != lemma:
            tokens.append(word)
    return " ".join(tokens)
