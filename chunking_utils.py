"""Shared helpers for chunking and embedding utilities."""

from __future__ import annotations

from typing import Optional

from chunking import _get_tokenizer


def normalize_chunk_strategy(chunker_class_name: str) -> str:
    """Normalize chunk strategy name to match storage conventions.

    Args:
        chunker_class_name: Class name like 'HeaderAwareChunker', 'MessageChunker', etc.

    Returns:
        Normalized strategy name: 'header-aware', 'sentence-window', 'fixed-token', or 'per-message'
    """
    strategy = chunker_class_name.replace("Chunker", "").lower()
    if "headeraware" in strategy:
        return "header-aware"
    if "sentencewindow" in strategy:
        return "sentence-window"
    if "fixedtoken" in strategy:
        return "fixed-token"
    if "message" in strategy:
        return "per-message"
    return strategy


def get_embedding_max_tokens(
    embedder,
    *,
    default: Optional[int] = 512,
    use_chunking_tokenizer: bool = True,
) -> Optional[int]:
    """Infer the maximum token length supported by the embedder/model."""
    max_tokens = None

    model = getattr(embedder, "model", None) if embedder else None
    if model:
        try:
            if hasattr(model, "get_max_seq_length"):
                max_tokens = model.get_max_seq_length()
        except Exception:
            max_tokens = None
        if max_tokens is None:
            max_tokens = getattr(model, "max_seq_length", None)
        if not max_tokens:
            try:
                tok = getattr(model, "tokenizer", None)
                max_tokens = getattr(tok, "model_max_length", None) or getattr(
                    tok, "max_len_single_sentence", None
                )
            except Exception:
                max_tokens = None

    if use_chunking_tokenizer and not max_tokens:
        tok = _get_tokenizer()
        if tok not in (None, "fallback"):
            max_tokens = getattr(tok, "model_max_length", None) or getattr(
                tok, "max_len_single_sentence", None
            )

    try:
        max_tokens = int(max_tokens) if max_tokens else None
    except Exception:
        max_tokens = None

    if max_tokens and max_tokens > 100000:
        max_tokens = None

    if not max_tokens or max_tokens <= 0:
        return default

    return max_tokens
