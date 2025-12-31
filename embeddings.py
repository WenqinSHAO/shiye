import warnings
from pathlib import Path
from typing import Iterable, Optional

import numpy as np

from config import MODEL_CACHE, MODEL_NAME, VECTOR_DIM_ENV


class EmbeddingProvider:
    """Thin wrapper around a sentence-transformers model for local embeddings."""

    def __init__(self, model_name: str = MODEL_NAME, cache_dir: Path = MODEL_CACHE):
        self.model_name = model_name
        self.cache_dir = cache_dir
        self.model = None
        self.dim = None

    def load(self) -> None:
        if self.model:
            return
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError:
            raise RuntimeError(
                "sentence-transformers is not installed; install requirements and retry."
            )
        cache_dir = Path(self.cache_dir)
        cache_dir.mkdir(parents=True, exist_ok=True)
        # sentence-transformers >=3 uses cache_folder; older versions accept cache_folder as well
        self.model = SentenceTransformer(self.model_name, cache_folder=str(cache_dir))
        self.dim = self.model.get_sentence_embedding_dimension()
        # allow manual override for swapping models without reload
        if VECTOR_DIM_ENV:
            try:
                self.dim = int(VECTOR_DIM_ENV)
            except ValueError:
                warnings.warn("SHIYE_VECTOR_DIM is set but not an int; ignoring.")

    def embed(self, texts: Iterable[str]) -> np.ndarray:
        if not self.model:
            self.load()
        vectors = self.model.encode(
            list(texts),
            convert_to_numpy=True,
            normalize_embeddings=True,
            show_progress_bar=False,
        )
        return vectors.astype("float32")
