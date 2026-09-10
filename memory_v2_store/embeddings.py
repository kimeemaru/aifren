"""Replaceable, local embedding lifecycle for the isolated Memory V2 store.

Vectors are derived/local and safe to delete and rebuild.  They may support
non-authoritative shadow retrieval diagnostics, never V1 prompt retrieval.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import platform
from typing import Protocol, Sequence

from benchmarks.memory_v2.models import EmbeddingIdentity

from .store import MemoryV2Store


def content_sha256(text: str) -> str:
    return hashlib.sha256(str(text).encode("utf-8")).hexdigest()


class EmbeddingProvider(Protocol):
    """Small provider boundary; models remain independently replaceable."""

    provider: str
    model: str
    model_version: str
    dimensions: int
    normalized: bool
    dtype: str
    preprocessing_fingerprint: str
    device: str

    def embed(self, texts: Sequence[str]) -> list[list[float]]: ...

    def identity_for(self, text: str) -> EmbeddingIdentity: ...


@dataclass
class MiniLMEmbeddingProvider:
    """Lazy adapter for AIFren's existing local all-MiniLM-L6-v2 model."""

    provider: str = "sentence-transformers"
    model: str = "all-MiniLM-L6-v2"
    model_version: str = "local"
    dimensions: int = 384
    normalized: bool = True
    dtype: str = "float32"
    preprocessing_fingerprint: str = "utf8-verbatim-v1"
    device: str = "cpu"
    _model: object | None = None

    def _load(self):
        if self._model is None:
            # Reuse the existing local-only model policy without importing the
            # production Memory class or touching V1 persistence.
            from memory.embeddings import EmbeddingModel

            model = EmbeddingModel()
            self._model = model
            self.dimensions = int(model.model.get_embedding_dimension())
            self.device = str(getattr(model.model, "device", "cpu"))
        return self._model

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        model = self._load()
        vectors = model.model.encode(list(texts), normalize_embeddings=True, convert_to_numpy=True)
        return [[float(value) for value in vector] for vector in vectors]

    def identity_for(self, text: str) -> EmbeddingIdentity:
        return EmbeddingIdentity(self.provider, self.model, self.dimensions,
                                 self.preprocessing_fingerprint, content_sha256(text))


class EmbeddingLifecycle:
    """Explicit derived-index maintenance used only by V2 diagnostics."""

    def __init__(self, store: MemoryV2Store, provider: EmbeddingProvider, *, include_legacy_unverified: bool = False):
        self.store = store
        self.provider = provider
        self.include_legacy_unverified = include_legacy_unverified

    def health(self) -> dict[str, int]:
        return self.store.embedding_health(self.provider, include_legacy_unverified=self.include_legacy_unverified)

    def mark_incompatible_stale(self) -> int:
        return self.store.mark_incompatible_embeddings_stale(self.provider)

    def rebuild_all(self) -> dict[str, int]:
        return self._rebuild(stale_only=False)

    def rebuild_stale_or_missing(self) -> dict[str, int]:
        return self._rebuild(stale_only=True)

    def rebuild_claims(self, claim_ids: Sequence[str], *, stale_only: bool = True,
                       character_id: str | None = None, report_health: bool = True) -> dict[str, int]:
        """Refresh only changed claims so normal dual-read never scans V2."""
        return self._rebuild(stale_only=stale_only, claim_ids=claim_ids,
                             character_id=character_id, report_health=report_health)

    def _rebuild(self, *, stale_only: bool, claim_ids: Sequence[str] | None = None,
                 character_id: str | None = None, report_health: bool = True) -> dict[str, int]:
        if claim_ids is None:
            self.mark_incompatible_stale()
        rows = self.store.embedding_source_claims(
            include_legacy_unverified=self.include_legacy_unverified,
            claim_ids=claim_ids,
            character_id=character_id,
        )
        selected = []
        for row in rows:
            if not stale_only or not self.store.embedding_is_current(row, self.provider):
                selected.append(row)
        if not selected:
            return {"embedded": 0, "failed": 0, **(self.health() if report_health else {})}
        try:
            vectors = self.provider.embed([row["content"] for row in selected])
            if len(vectors) != len(selected):
                raise ValueError("provider returned a different number of vectors")
            for row, vector in zip(selected, vectors):
                self.store.store_embedding(row, self.provider, vector)
            return {"embedded": len(selected), "failed": 0, **(self.health() if report_health else {})}
        except Exception as error:
            # Preserve any existing current vector; record the retryable state
            # only for claims that did not have usable derived data.
            for row in selected:
                self.store.store_embedding_failure(row, self.provider, str(error))
            return {"embedded": 0, "failed": len(selected), **(self.health() if report_health else {})}
