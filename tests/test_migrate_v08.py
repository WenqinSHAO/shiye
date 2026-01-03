"""Tests for v0.8 migration script."""

import importlib
import os
import sys
import tempfile
from pathlib import Path

import numpy as np

# Add scripts directory to path
sys.path.insert(0, str(Path(__file__).parent.parent / 'scripts'))


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
    """Create a test store with fake embedder."""
    os.environ["SHIYE_DATA_DIR"] = tmp_dir
    for name in ("config", "vector_store", "embeddings", "storage", "chunking"):
        if name in sys.modules:
            importlib.reload(sys.modules[name])
        else:
            importlib.import_module(name)
    import config as cfg
    import storage
    from datatypes import Message, Role

    store = storage.LocalStore(
        db_path=cfg.DB_PATH,
        data_dir=cfg.DATA_DIR,
        embedder=FakeEmbedder(),
    )
    return store, Message, Role, cfg


def test_normalize_chunk_strategy():
    """Test chunk strategy normalization."""
    from migrate_v08 import normalize_chunk_strategy
    
    assert normalize_chunk_strategy('HeaderAwareChunker') == 'header-aware'
    assert normalize_chunk_strategy('SentenceWindowChunker') == 'sentence-window'
    assert normalize_chunk_strategy('FixedTokenChunker') == 'fixed-token'
    assert normalize_chunk_strategy('MessageChunker') == 'per-message'


def test_faiss_parameter_order():
    """Test that FAISS add is called with correct parameter order (ids, vectors)."""
    with tempfile.TemporaryDirectory() as tmp:
        store, Message, Role, cfg = make_store(tmp)
        
        # Add initial messages to create old-style chunks
        msgs = [
            Message(content="First message", role=Role.USER),
            Message(content="Second message", role=Role.USER),
        ]
        chunk_ids = store.add_messages(msgs)
        
        # Clear chunk_version to simulate old data
        with store._connect() as conn:
            cur = conn.cursor()
            cur.execute("UPDATE documents SET chunk_version = NULL")
        
        # Import migration module
        from migrate_v08 import migrate_document
        
        # Get document ID
        with store._connect() as conn:
            cur = conn.cursor()
            cur.execute("SELECT id, doc_type FROM documents LIMIT 1")
            row = cur.fetchone()
            doc_id = row['id']
            doc_type = row['doc_type'] or 'chat'
        
        # Migrate the document
        stats = migrate_document(store, doc_id, doc_type, verbose=True, dry_run=False)
        
        # Check migration succeeded
        assert stats['success'], f"Migration failed: {stats.get('error')}"
        
        # Verify FAISS index was updated (should have new embeddings)
        import faiss
        idx = faiss.read_index(str(cfg.INDEX_PATH))
        assert idx.ntotal > 0, "FAISS index should have embeddings"
        
        # Verify embedding_id was set
        with store._connect() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT COUNT(*) as cnt FROM chunks WHERE deleted = 0 AND embedding_id IS NOT NULL"
            )
            count = cur.fetchone()['cnt']
            assert count > 0, "All active chunks should have embedding_id set"


def test_chat_document_chunking():
    """Test that chat documents are chunked with message list, not string."""
    with tempfile.TemporaryDirectory() as tmp:
        store, Message, Role, cfg = make_store(tmp)
        
        # Add chat messages
        msgs = [
            Message(content="Hello there", role=Role.USER),
            Message(content="Hi! How can I help?", role=Role.ASSISTANT),
            Message(content="Tell me about Python", role=Role.USER),
        ]
        store.add_messages(msgs)
        
        # Clear chunk_version to simulate old data
        with store._connect() as conn:
            cur = conn.cursor()
            cur.execute("UPDATE documents SET chunk_version = NULL")
        
        from migrate_v08 import migrate_document, get_document_content
        
        # Get document
        with store._connect() as conn:
            cur = conn.cursor()
            cur.execute("SELECT id FROM documents LIMIT 1")
            doc_id = cur.fetchone()['id']
        
        # Get content (should be list for chat)
        content = get_document_content(store, doc_id, 'chat')
        assert isinstance(content, list), "Chat content should be a list"
        assert len(content) == 3, "Should have 3 messages"
        
        # Migrate
        stats = migrate_document(store, doc_id, 'chat', verbose=True, dry_run=False)
        assert stats['success'], f"Migration failed: {stats.get('error')}"
        
        # Check new chunks exist (should be 3, not character-by-character)
        with store._connect() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT COUNT(*) as cnt FROM chunks WHERE document_id = ? AND deleted = 0",
                (doc_id,)
            )
            new_count = cur.fetchone()['cnt']
            # Should have 3 chunks (one per message), not 60+ (one per character)
            assert new_count == 3, f"Expected 3 chunks, got {new_count}"


def test_chunk_strategy_normalization():
    """Test that chunk_strategy is normalized correctly."""
    with tempfile.TemporaryDirectory() as tmp:
        store, Message, Role, cfg = make_store(tmp)
        
        # Add document
        doc_meta = {
            "source": "test",
            "uri": "test://doc",
            "doc_type": "note",
            "title": "Test Note"
        }
        result = store.add_document_chunked("# Header\n\nSome content here.", doc_meta)
        doc_id = result['document_id']
        
        # Clear chunk_version to simulate old data
        with store._connect() as conn:
            cur = conn.cursor()
            cur.execute("UPDATE documents SET chunk_version = NULL, chunk_strategy = 'HeaderAwareChunker' WHERE id = ?", (doc_id,))
        
        from migrate_v08 import migrate_document
        
        # Migrate
        stats = migrate_document(store, doc_id, 'note', verbose=True, dry_run=False)
        assert stats['success']
        
        # Check normalized strategy
        with store._connect() as conn:
            cur = conn.cursor()
            cur.execute("SELECT chunk_strategy FROM documents WHERE id = ?", (doc_id,))
            strategy = cur.fetchone()['chunk_strategy']
            assert strategy == 'header-aware', f"Expected 'header-aware', got '{strategy}'"


def test_embedding_id_and_metadata():
    """Test that embedding_id is set and index metadata is written."""
    with tempfile.TemporaryDirectory() as tmp:
        store, Message, Role, cfg = make_store(tmp)
        
        # Add messages
        msgs = [Message(content="Test message", role=Role.USER)]
        store.add_messages(msgs)
        
        # Clear embedding_id and chunk_version
        with store._connect() as conn:
            cur = conn.cursor()
            cur.execute("UPDATE chunks SET embedding_id = NULL")
            cur.execute("UPDATE documents SET chunk_version = NULL")
        
        from migrate_v08 import migrate_document
        
        with store._connect() as conn:
            cur = conn.cursor()
            cur.execute("SELECT id FROM documents LIMIT 1")
            doc_id = cur.fetchone()['id']
        
        # Migrate
        stats = migrate_document(store, doc_id, 'chat', verbose=True, dry_run=False)
        assert stats['success']
        
        # Verify embedding_id is set
        with store._connect() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT embedding_id FROM chunks WHERE document_id = ? AND deleted = 0",
                (doc_id,)
            )
            rows = cur.fetchall()
            assert len(rows) > 0
            for row in rows:
                assert row['embedding_id'] is not None, "embedding_id should be set"
            
            # Verify index metadata was written
            cur.execute("SELECT last_sync_ts FROM vector_index_meta WHERE id = 1")
            meta = cur.fetchone()
            assert meta is not None, "Index metadata should exist"
            assert meta['last_sync_ts'] is not None, "last_sync_ts should be set"


def test_dry_run_mode():
    """Test that dry-run mode doesn't modify database."""
    with tempfile.TemporaryDirectory() as tmp:
        store, Message, Role, cfg = make_store(tmp)
        
        # Add messages
        msgs = [Message(content="Test", role=Role.USER)]
        store.add_messages(msgs)
        
        # Get initial state
        with store._connect() as conn:
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) as cnt FROM chunks WHERE deleted = 0")
            initial_count = cur.fetchone()['cnt']
            cur.execute("UPDATE documents SET chunk_version = NULL")
        
        from migrate_v08 import migrate_document
        
        with store._connect() as conn:
            cur = conn.cursor()
            cur.execute("SELECT id FROM documents LIMIT 1")
            doc_id = cur.fetchone()['id']
        
        # Dry run migration
        stats = migrate_document(store, doc_id, 'chat', verbose=True, dry_run=True)
        assert stats['success']
        
        # Verify no changes
        with store._connect() as conn:
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) as cnt FROM chunks WHERE deleted = 0")
            final_count = cur.fetchone()['cnt']
            assert final_count == initial_count, "Dry run should not modify database"
            
            cur.execute("SELECT chunk_version FROM documents WHERE id = ?", (doc_id,))
            version = cur.fetchone()['chunk_version']
            assert version is None, "Dry run should not update chunk_version"


if __name__ == '__main__':
    # Run tests
    test_normalize_chunk_strategy()
    print("✓ test_normalize_chunk_strategy")
    
    test_faiss_parameter_order()
    print("✓ test_faiss_parameter_order")
    
    test_chat_document_chunking()
    print("✓ test_chat_document_chunking")
    
    test_chunk_strategy_normalization()
    print("✓ test_chunk_strategy_normalization")
    
    test_embedding_id_and_metadata()
    print("✓ test_embedding_id_and_metadata")
    
    test_dry_run_mode()
    print("✓ test_dry_run_mode")
    
    print("\n✓ All tests passed!")
