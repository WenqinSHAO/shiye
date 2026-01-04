"""Tests for v0.8 migration script."""

import importlib
import os
import sys
import tempfile
from pathlib import Path

import faiss
import pytest
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


def test_faiss_old_vectors_removed():
    """Test that old FAISS vectors are removed during migration."""
    with tempfile.TemporaryDirectory() as tmp:
        store, Message, Role, cfg = make_store(tmp)
        
        # Add initial messages with embeddings
        msgs = [
            Message(content="Old message 1", role=Role.USER),
            Message(content="Old message 2", role=Role.ASSISTANT),
        ]
        old_ids = store.add_messages(msgs)
        
        # Verify initial FAISS state
        idx = faiss.read_index(str(cfg.INDEX_PATH))
        initial_count = idx.ntotal
        assert initial_count == 2, f"Should have 2 initial vectors, got {initial_count}"
        
        # Clear chunk_version to simulate old data
        with store._connect() as conn:
            cur = conn.cursor()
            cur.execute("UPDATE documents SET chunk_version = NULL")
        
        from migrate_v08 import migrate_document
        
        # Get document ID
        with store._connect() as conn:
            cur = conn.cursor()
            cur.execute("SELECT id FROM documents LIMIT 1")
            doc_id = cur.fetchone()['id']
        
        # Migrate the document
        stats = migrate_document(store, doc_id, 'chat', verbose=True, dry_run=False)
        assert stats['success'], f"Migration failed: {stats.get('error')}"
        
        # Verify old vectors were removed and new ones added
        idx = faiss.read_index(str(cfg.INDEX_PATH))
        # Should still be 2 vectors (old removed, new added)
        assert idx.ntotal == 2, f"Expected 2 vectors after migration, got {idx.ntotal}"
        
        # Get new chunk IDs
        with store._connect() as conn:
            cur = conn.cursor()
            cur.execute("SELECT id FROM chunks WHERE deleted = 0 AND document_id = ?", (doc_id,))
            new_ids = [row['id'] for row in cur.fetchall()]
        
        # Verify old IDs are not the same as new IDs
        assert set(old_ids) != set(new_ids), "New chunk IDs should be different from old ones"


def test_roles_preserved():
    """Test that roles are preserved during migration for chat documents."""
    with tempfile.TemporaryDirectory() as tmp:
        store, Message, Role, cfg = make_store(tmp)
        
        # Add chat messages with different roles
        msgs = [
            Message(content="User question", role=Role.USER),
            Message(content="Assistant response", role=Role.ASSISTANT),
            Message(content="User follow-up", role=Role.USER),
        ]
        store.add_messages(msgs)
        
        # Get document ID and original roles
        with store._connect() as conn:
            cur = conn.cursor()
            cur.execute("SELECT id FROM documents LIMIT 1")
            doc_id = cur.fetchone()['id']
            
            cur.execute("SELECT role FROM chunks WHERE document_id = ? AND deleted = 0 ORDER BY seq", (doc_id,))
            original_roles = [row['role'] for row in cur.fetchall()]
        
        # Clear chunk_version to simulate old data
        with store._connect() as conn:
            cur = conn.cursor()
            cur.execute("UPDATE documents SET chunk_version = NULL")
        
        from migrate_v08 import migrate_document
        
        # Migrate
        stats = migrate_document(store, doc_id, 'chat', verbose=True, dry_run=False)
        assert stats['success']
        
        # Check roles are preserved
        with store._connect() as conn:
            cur = conn.cursor()
            cur.execute("SELECT role FROM chunks WHERE document_id = ? AND deleted = 0 ORDER BY seq", (doc_id,))
            new_roles = [row['role'] for row in cur.fetchall()]
        
        assert new_roles == original_roles, f"Roles should be preserved: expected {original_roles}, got {new_roles}"
        assert new_roles == ['user', 'assistant', 'user'], f"Expected specific role sequence, got {new_roles}"


def test_timestamps_preserved():
    """Test that timestamps are preserved during migration."""
    with tempfile.TemporaryDirectory() as tmp:
        store, Message, Role, cfg = make_store(tmp)
        
        from datetime import datetime, UTC, timedelta
        
        # Add messages with specific timestamps
        past_time = datetime.now(UTC) - timedelta(days=7)
        ref_time = datetime.now(UTC) - timedelta(days=6)
        
        msgs = [
            Message(content="Old message", role=Role.USER, created_at=past_time, reference_time=ref_time),
        ]
        store.add_messages(msgs)
        
        # Get document ID and original timestamps
        with store._connect() as conn:
            cur = conn.cursor()
            cur.execute("SELECT id FROM documents LIMIT 1")
            doc_id = cur.fetchone()['id']
            
            cur.execute("SELECT created_at, event_at FROM chunks WHERE document_id = ? AND deleted = 0", (doc_id,))
            row = cur.fetchone()
            original_created = row['created_at']
            original_event = row['event_at']
        
        # Clear chunk_version
        with store._connect() as conn:
            cur = conn.cursor()
            cur.execute("UPDATE documents SET chunk_version = NULL")
        
        from migrate_v08 import migrate_document
        
        # Migrate
        stats = migrate_document(store, doc_id, 'chat', verbose=True, dry_run=False)
        assert stats['success']
        
        # Check timestamps are preserved
        with store._connect() as conn:
            cur = conn.cursor()
            cur.execute("SELECT created_at, event_at FROM chunks WHERE document_id = ? AND deleted = 0", (doc_id,))
            row = cur.fetchone()
            new_created = row['created_at']
            new_event = row['event_at']
        
        assert new_created == original_created, f"created_at should be preserved: expected {original_created}, got {new_created}"
        assert new_event == original_event, f"event_at should be preserved: expected {original_event}, got {new_event}"


def test_migration_aborts_without_embeddings():
    """Test that migration aborts when embeddings are required but not available."""
    with tempfile.TemporaryDirectory() as tmp:
        store, Message, Role, cfg = make_store(tmp)
        
        # Add messages
        msgs = [Message(content="Test message", role=Role.USER)]
        store.add_messages(msgs)
        
        # Get document ID
        with store._connect() as conn:
            cur = conn.cursor()
            cur.execute("SELECT id FROM documents LIMIT 1")
            doc_id = cur.fetchone()['id']
            
            # Count old chunks
            cur.execute("SELECT COUNT(*) as cnt FROM chunks WHERE document_id = ? AND deleted = 0", (doc_id,))
            old_count = cur.fetchone()['cnt']
        
        # Clear chunk_version to simulate old data
        with store._connect() as conn:
            cur = conn.cursor()
            cur.execute("UPDATE documents SET chunk_version = NULL")
        
        # Remove embedder to simulate missing embeddings
        store.embedder = None
        
        from migrate_v08 import migrate_document
        
        # Attempt migration without embedder - should fail
        stats = migrate_document(store, doc_id, 'chat', verbose=True, dry_run=False)
        
        # Migration should have failed
        assert not stats['success'], "Migration should fail without embeddings"
        assert 'Embeddings required but not available' in stats['error']
        
        # Old chunks should still be active (not deleted)
        with store._connect() as conn:
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) as cnt FROM chunks WHERE document_id = ? AND deleted = 0", (doc_id,))
            current_count = cur.fetchone()['cnt']
            assert current_count == old_count, f"Old chunks should remain active, expected {old_count}, got {current_count}"
            
            # Document should NOT be marked as migrated
            cur.execute("SELECT chunk_version FROM documents WHERE id = ?", (doc_id,))
            version = cur.fetchone()['chunk_version']
            assert version is None, "Document should not be marked as migrated when embeddings fail"


def test_faiss_add_failure_restores_db_and_vectors(monkeypatch):
    """FAISS add failure should leave DB/state intact and keep old vectors."""
    with tempfile.TemporaryDirectory() as tmp:
        store, Message, Role, cfg = make_store(tmp)
        
        # Seed data
        msgs = [
            Message(content="Old message 1", role=Role.USER),
            Message(content="Old message 2", role=Role.ASSISTANT),
        ]
        store.add_messages(msgs)
        
        # Simulate legacy data
        with store._connect() as conn:
            cur = conn.cursor()
            cur.execute("UPDATE documents SET chunk_version = NULL")
        
        # Capture baseline counts and metadata
        with store._connect() as conn:
            cur = conn.cursor()
            cur.execute("SELECT id, chunk_strategy, chunk_version FROM documents LIMIT 1")
            row = cur.fetchone()
            doc_id = row['id']
            original_strategy = row['chunk_strategy']
            original_version = row['chunk_version']
            
            cur.execute("SELECT COUNT(*) as cnt FROM chunks WHERE document_id = ? AND deleted = 0", (doc_id,))
            old_active = cur.fetchone()['cnt']
            
            cur.execute("SELECT last_sync_ts FROM vector_index_meta WHERE id = 1")
            meta_row = cur.fetchone()
            prev_sync = meta_row['last_sync_ts'] if meta_row else None
        
        # Force FAISS add to fail
        def fail_add(ids, vectors):
            raise RuntimeError("simulated add failure")
        
        monkeypatch.setattr(store._faiss_index, "add", fail_add)
        
        from migrate_v08 import migrate_document
        
        stats = migrate_document(store, doc_id, 'chat', verbose=True, dry_run=False)
        assert not stats['success']
        assert "simulated add failure" in stats['error']
        
        # Old chunks remain active; new chunks are not left active
        with store._connect() as conn:
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) as cnt FROM chunks WHERE document_id = ? AND deleted = 0", (doc_id,))
            active_after = cur.fetchone()['cnt']
            assert active_after == old_active, f"Expected {old_active} active chunks, got {active_after}"
            
            # Ensure doc chunk flags were restored
            cur.execute("SELECT chunk_strategy, chunk_version FROM documents WHERE id = ?", (doc_id,))
            doc_row = cur.fetchone()
            assert doc_row['chunk_strategy'] == original_strategy
            assert doc_row['chunk_version'] == original_version
            
            # last_sync_ts should not advance on failure
            cur.execute("SELECT last_sync_ts FROM vector_index_meta WHERE id = 1")
            meta_row = cur.fetchone()
            after_sync = meta_row['last_sync_ts'] if meta_row else None
            assert after_sync == prev_sync
        
        # FAISS index should retain old vectors (count unchanged)
        idx = faiss.read_index(str(cfg.INDEX_PATH))
        assert idx.ntotal == old_active


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
    
    test_faiss_old_vectors_removed()
    print("✓ test_faiss_old_vectors_removed")
    
    test_roles_preserved()
    print("✓ test_roles_preserved")
    
    test_timestamps_preserved()
    print("✓ test_timestamps_preserved")
    
    test_migration_aborts_without_embeddings()
    print("✓ test_migration_aborts_without_embeddings")
    
    print("\n✓ All tests passed!")
