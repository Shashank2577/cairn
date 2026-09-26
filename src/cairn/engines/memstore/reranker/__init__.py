"""
Reranker implementations for memory search.

Imported lazily: the model-backed rerankers pull in heavy optional libraries.
"""

from .base import BaseReranker

__all__ = ["BaseReranker", "HuggingFaceReranker", "LLMReranker", "SentenceTransformerReranker"]

_LAZY = {
    "HuggingFaceReranker": ".huggingface_reranker",
    "LLMReranker": ".llm_reranker",
    "SentenceTransformerReranker": ".sentence_transformer_reranker",
}


def __getattr__(name):
    if name in _LAZY:
        import importlib

        return getattr(importlib.import_module(_LAZY[name], __name__), name)
    raise AttributeError(name)
