import importlib
import os
import sqlite3
import sys
import tempfile

import faiss
import numpy as np


# Helpers ---------------------------------------------------------------------


class FakeEmbedder:
    """Deterministic, cheap embedder for tests."""

    def __init__(self, dim: int = 4):
        self.dim = dim
        self.loaded = False

    def load(self):
        self.loaded = True

    def embed(self, texts):
        # simple one-hot-ish embeddings for predictability
        vecs = []
        for i, _ in enumerate(texts):
            v = np.zeros(self.dim, dtype="float32")
            v[min(i, self.dim - 1)] = 1.0
            vecs.append(v)
        return np.vstack(vecs)


def make_store(tmp_dir: str):
    # ensure modules pick up the temp data dir
    os.environ["SHIYE_DATA_DIR"] = tmp_dir
    for name in ("config", "vector_store", "embeddings", "storage"):
        if name in sys.modules:
            importlib.reload(sys.modules[name])
        else:
            importlib.import_module(name)
    import config as cfg
    import storage  # reloaded above
    from datatypes import Message, Role

    store = storage.LocalStore(
        db_path=cfg.DB_PATH,
        data_dir=cfg.DATA_DIR,
        embedder=FakeEmbedder(),
    )
    return store, Message, Role, cfg


# Tests -----------------------------------------------------------------------


def test_ingest_and_recall_and_index_sync():
    with tempfile.TemporaryDirectory() as tmp:
        store, Message, Role, cfg = make_store(tmp)

        msgs = [
            Message(content="hello world", role=Role.USER),
            Message(content="ripe bananas taste good", role=Role.USER),
        ]
        ids = store.add_messages(msgs)
        assert len(ids) == 2

        # DB counts
        conn = sqlite3.connect(cfg.DB_PATH)
        n_chunks = conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
        assert n_chunks == 2
        conn.close()

        # FAISS count
        idx = faiss.read_index(str(cfg.INDEX_PATH))
        assert idx.ntotal == 2

        # Recall by keyword (substring fallback)
        hit = store.recall("bananas")
        assert hit is not None
        assert hit.content in {"ripe bananas taste good", "hello world"}


def test_clear_resets_chunks_and_index():
    with tempfile.TemporaryDirectory() as tmp:
        store, Message, Role, cfg = make_store(tmp)
        store.add_messages([Message(content="one", role=Role.USER)])

        # precondition
        idx = faiss.read_index(str(cfg.INDEX_PATH))
        assert idx.ntotal == 1

        store.clear()

        conn = sqlite3.connect(cfg.DB_PATH)
        n_chunks = conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
        conn.close()
        assert n_chunks == 0

        idx = faiss.read_index(str(cfg.INDEX_PATH))
        assert idx.ntotal == 0
