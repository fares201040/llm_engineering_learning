"""Backend-neutral retrieval façade."""

from .answer import (  # noqa: F401
    Result,
    fetch_context,
    fetch_exact_chroma,
    fetch_exact_postgres,
    fetch_semantic_chroma,
    fetch_semantic_postgres,
)

__all__ = [name for name in globals() if not name.startswith("_")]
