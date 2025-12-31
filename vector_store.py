import json
from pathlib import Path
from typing import Iterable, List, Optional, Sequence, Tuple

import numpy as np

from config import INDEX_PATH

try:
    import faiss
except ImportError as e:  # noqa: F841
    faiss = None


class FaissIndex:
    """Simple FAISS index persisted to disk."""

    def __init__(self, dim: int, index_path: Path = INDEX_PATH, metric: str = "ip"):
        if not faiss:
            raise RuntimeError("faiss-cpu not installed; cannot initialize FaissIndex.")
        self.dim = dim
        self.index_path = index_path
        self.metric = metric
        self.index = self._load_or_create()

    def _load_or_create(self):
        if self.index_path.exists():
            idx = faiss.read_index(str(self.index_path))
            if idx.d != self.dim:
                raise ValueError(
                    f"FAISS index dim mismatch: {idx.d} (index) vs {self.dim} (config)"
                )
            return idx
        base = faiss.IndexFlatIP(self.dim) if self.metric == "ip" else faiss.IndexFlatL2(self.dim)
        return faiss.IndexIDMap2(base)

    def persist(self) -> None:
        self.index_path.parent.mkdir(parents=True, exist_ok=True)
        faiss.write_index(self.index, str(self.index_path))

    def add(self, ids: Sequence[int], vectors: np.ndarray) -> None:
        id_array = np.asarray(list(ids)).astype("int64")
        self.index.add_with_ids(vectors, id_array)
        self.persist()

    def search(self, vector: np.ndarray, top_k: int = 5) -> Tuple[List[int], List[float]]:
        if vector.ndim == 1:
            vector = vector.reshape(1, -1)
        scores, ids = self.index.search(vector, top_k)
        found_ids = [int(i) for i in ids[0] if i != -1]
        found_scores = [float(s) for s in scores[0][: len(found_ids)]]
        return found_ids, found_scores

    def rebuild(self, ids: Sequence[int], vectors: np.ndarray) -> None:
        """Clear and rebuild index (use for bulk refresh)."""
        base = faiss.IndexFlatIP(self.dim) if self.metric == "ip" else faiss.IndexFlatL2(self.dim)
        self.index = faiss.IndexIDMap2(base)
        if len(ids):
            self.add(ids, vectors)
        else:
            self.persist()
