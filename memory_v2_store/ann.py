"""Derived local HNSW index for scalable Memory V2 semantic candidates.

SQLite remains authoritative.  The index stores only vector labels and claim
IDs, is rebuilt from ``claim_embeddings``, and every hit is later checked by
the normal character/lifecycle SQL path.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import struct
from typing import Any


class AnnUnavailable(RuntimeError):
    pass


class HnswClaimIndex:
    VERSION = 1
    _CACHE: dict[str, tuple[object, dict[str, int], int, int]] = {}
    _CACHE_COUNTS: dict[str, int] = {}
    _REVERSE_CACHE: dict[str, dict[int, str]] = {}

    def __init__(
        self,
        store,
        character_id: str,
        provider: Any,
        *,
        include_historical_evidence: bool = False,
    ) -> None:
        self.store, self.character_id, self.provider = store, str(character_id), provider
        self.include_historical_evidence = bool(include_historical_evidence)
        digest = hashlib.sha256(
            f"{character_id}|{provider.provider}|{provider.model}|"
            f"{provider.preprocessing_fingerprint}|historical="
            f"{int(self.include_historical_evidence)}".encode()
        ).hexdigest()[:20]
        self.directory = None if store.path == ":memory:" else Path(store.path).resolve().parent / "ann_index"
        self.index_path = self.directory / f"{digest}.hnsw" if self.directory else None
        self.meta_path = self.directory / f"{digest}.json" if self.directory else None
        self.cache_key = f"{store.path}|{id(store) if store.path == ':memory:' else ''}|{digest}"

    def _library(self):
        try:
            import hnswlib  # type: ignore
            import numpy as np
            return hnswlib, np
        except Exception as error:
            raise AnnUnavailable(type(error).__name__) from error

    def _rows(self):
        return self.store.iter_ann_embedding_rows(
            self.character_id, self.provider,
            include_historical_evidence=self.include_historical_evidence,
        )

    def _count(self):
        return self.store.ann_embedding_count(
            self.character_id, self.provider,
            include_historical_evidence=self.include_historical_evidence,
        )

    def _vector(self, blob):
        values = struct.unpack(f"<{self.provider.dimensions}f", blob)
        if len(values) != self.provider.dimensions:
            raise AnnUnavailable("invalid_vector")
        return values

    def _load(self):
        if self.index_path is None or not self.index_path.exists() or not self.meta_path.exists():
            return None
        try:
            hnswlib, _ = self._library()
            meta = json.loads(self.meta_path.read_text(encoding="utf-8"))
            if meta.get("version") != self.VERSION or meta.get("dimensions") != self.provider.dimensions:
                return None
            index = hnswlib.Index(space="cosine", dim=self.provider.dimensions)
            index.load_index(str(self.index_path), max_elements=max(1, int(meta.get("max_elements", 1))))
            loaded = index, {str(key): value for key, value in meta.get("labels", {}).items()}, int(meta.get("next_label", 0)), int(meta.get("max_elements", 1))
            self._CACHE[self.cache_key] = loaded
            return loaded
        except Exception:
            return None

    def _save(self, index, labels, next_label, max_elements):
        if self.directory is None:
            return
        self.directory.mkdir(parents=True, exist_ok=True)
        temporary_index = self.index_path.with_name(self.index_path.name + ".tmp")
        temporary_meta = self.meta_path.with_name(self.meta_path.name + ".tmp")
        index.save_index(str(temporary_index))
        temporary_meta.write_text(json.dumps({
            "version": self.VERSION, "dimensions": self.provider.dimensions,
            "labels": labels, "next_label": next_label, "max_elements": max_elements,
        }, sort_keys=True), encoding="utf-8")
        temporary_index.replace(self.index_path)
        temporary_meta.replace(self.meta_path)
        self._CACHE[self.cache_key] = (index, labels, next_label, max_elements)

    def ensure(self):
        hnswlib, np = self._library()
        count = self._count()
        cached = self._CACHE.get(self.cache_key)
        if cached is not None and self._CACHE_COUNTS.get(self.cache_key) == count and (
            self.directory is None or (self.index_path.exists() and self.meta_path.exists())
        ):
            cached[0].set_ef(4096)
            return cached[0], cached[1]
        rows = self._rows()
        loaded = self._load()
        if loaded is None:
            max_elements = max(16, count * 2)
            index = hnswlib.Index(space="cosine", dim=self.provider.dimensions)
            index.init_index(max_elements=max_elements, ef_construction=160, M=16)
            labels, next_label = {}, 0
        else:
            index, labels, next_label, max_elements = loaded
        additions = 0
        batch: list[tuple[str, tuple[float, ...]]] = []
        for row in rows:
            if row["claim_id"] in labels:
                continue
            batch.append((row["claim_id"], self._vector(row["vector_blob"])))
            if len(batch) < 256:
                continue
            next_label, max_elements = self._append(index, np, labels, batch, next_label, max_elements)
            additions += len(batch)
            batch.clear()
        if batch:
            next_label, max_elements = self._append(index, np, labels, batch, next_label, max_elements)
            additions += len(batch)
        if additions:
            self._save(index, labels, next_label, max_elements)
        else:
            self._CACHE[self.cache_key] = (index, labels, next_label, max_elements)
        self._CACHE_COUNTS[self.cache_key] = count
        self._REVERSE_CACHE[self.cache_key] = {label: claim_id for claim_id, label in labels.items()}
        # MiniLM's close paraphrase neighbours can be separated by thousands
        # of generic long-history fillers.  A still-bounded larger ef keeps
        # ANN recall high without returning to a corpus scan.
        index.set_ef(4096)
        return index, labels

    @staticmethod
    def _append(index, np, labels, batch, next_label, max_elements):
        needed = next_label + len(batch)
        if needed > max_elements:
            max_elements = max(needed, max_elements * 2)
            index.resize_index(max_elements)
        index.add_items(np.asarray([item[1] for item in batch], dtype=np.float32),
                        np.asarray(range(next_label, needed), dtype=np.int64))
        for label, (claim_id, _) in zip(range(next_label, needed), batch):
            labels[claim_id] = label
        return needed, max_elements

    def query(
        self,
        vector: list[float],
        limit: int,
        *,
        ef: int = 4096,
        candidate_multiplier: int = 16,
        truth_scope_id: str | None = None,
        include_historical_evidence: bool = False,
    ) -> list[tuple[str, float]]:
        if limit < 1:
            return []
        _, np = self._library()
        index, labels = self.ensure()
        if not labels:
            return []
        reverse = self._REVERSE_CACHE.setdefault(self.cache_key, {label: claim_id for claim_id, label in labels.items()})
        # The default remains the measured deep-recall configuration.  The
        # non-authoritative dual-read path may explicitly choose a smaller,
        # still bounded normal search before escalating on weak results.
        index.set_ef(max(1, int(ef)))
        count = min(len(labels), max(limit * max(1, int(candidate_multiplier)), limit))
        found, distances = index.knn_query(np.asarray([vector], dtype=np.float32), k=count)
        candidates = [
            (reverse[int(label)], max(-1.0, min(1.0, 1.0 - float(distance))))
            for label, distance in zip(found[0], distances[0]) if int(label) in reverse
        ]
        allowed = self.store.retrieval_scope_claim_ids(
            self.character_id,
            (claim_id for claim_id, _score in candidates),
            truth_scope_id=truth_scope_id,
            include_historical_evidence=include_historical_evidence,
        )
        return [item for item in candidates if item[0] in allowed]

    def status(self) -> dict[str, int | bool]:
        return {"available": True, "indexed_labels": len(self._load()[1]) if self._load() else 0,
                "embedding_rows": self._count()}
