"""Isolated SQLite shadow-store foundation for future AIFren memory work.

This package has no runtime wiring and never reads the current JSON archive.
"""

from .store import MemoryV2Store, StoreError
from .retrieval import RetrievalLimits, SemanticRetrievalV2
from .embeddings import EmbeddingLifecycle, MiniLMEmbeddingProvider

__all__ = ("MemoryV2Store", "RetrievalLimits", "SemanticRetrievalV2", "StoreError",
           "EmbeddingLifecycle", "MiniLMEmbeddingProvider")
