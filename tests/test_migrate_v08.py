"""Tests for v0.8 migration script."""

import importlib
import json
import os
import sys
import tempfile
from datetime import datetime, UTC
from pathlib import Path

import faiss
import pytest
import numpy as np

# Add scripts directory to path
sys.path.insert(0, str(Path(__file__).parent.parent / 'scripts'))

from migrate_v08 import (
    MIGRATABLE_WHERE_CLAUSE,
    expected_strategy_for_doc_type,
    should_migrate_doc,
)
from chunking import count_tokens
from chunking_utils import get_embedding_max_tokens, normalize_chunk_strategy


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
    assert normalize_chunk_strategy('HeaderAwareChunker') == 'header-aware'
    assert normalize_chunk_strategy('SentenceWindowChunker') == 'sentence-window'
    assert normalize_chunk_strategy('FixedTokenChunker') == 'fixed-token'
    assert normalize_chunk_strategy('MessageChunker') == 'per-message'


def test_selection_when_strategy_missing():
    """Documents with chunk_strategy NULL should still be selected even if chunk_version=1."""
    with tempfile.TemporaryDirectory() as tmp:
        store, Message, Role, cfg = make_store(tmp)
        
        store.add_messages([Message(content="Hello", role=Role.USER)])
        
        with store._connect() as conn:
            cur = conn.cursor()
            cur.execute("UPDATE documents SET chunk_strategy = NULL WHERE id = (SELECT id FROM documents WHERE status IS NULL LIMIT 1)")
            cur.execute(f"SELECT COUNT(*) as cnt FROM documents WHERE {MIGRATABLE_WHERE_CLAUSE}")
            row = cur.fetchone()
            assert row['cnt'] == 1, "Legacy document should be picked up for migration"


def test_selection_when_strategy_mismatched():
    """Documents with mismatched strategy vs doc_type should be migrated even when chunk_version=1."""
    with tempfile.TemporaryDirectory() as tmp:
        store, Message, Role, cfg = make_store(tmp)
        
        store.add_messages([Message(content="Hello mismatch", role=Role.USER)])
        
        with store._connect() as conn:
            cur = conn.cursor()
            cur.execute("SELECT id, doc_type FROM documents WHERE status IS NULL LIMIT 1")
            row = cur.fetchone()
            doc_id = row['id']
            doc_type = row['doc_type']
            cur.execute(
                "UPDATE documents SET chunk_strategy = 'fixed-token', chunk_version = 1 WHERE id = ?",
                (doc_id,),
            )
            cur.execute(f"SELECT COUNT(*) as cnt FROM documents WHERE {MIGRATABLE_WHERE_CLAUSE}")
            assert cur.fetchone()['cnt'] == 1, "Mismatch should satisfy migratable clause"
        
        with store._connect() as conn:
            cur = conn.cursor()
            cur.execute("SELECT * FROM documents WHERE id = ?", (doc_id,))
            assert should_migrate_doc(cur.fetchone())
        
        from migrate_v08 import migrate_document
        stats = migrate_document(store, doc_id, doc_type, verbose=True, dry_run=False)
        assert stats['success']
        
        with store._connect() as conn:
            cur = conn.cursor()
            cur.execute("SELECT chunk_strategy FROM documents WHERE id = ?", (doc_id,))
            strategy = cur.fetchone()['chunk_strategy']
            assert strategy == expected_strategy_for_doc_type(doc_type)


def test_migration_skips_empty_documents():
    """Documents without chunks or raw_content should be skipped, not failed."""
    with tempfile.TemporaryDirectory() as tmp:
        store, Message, Role, cfg = make_store(tmp)
        
        with store._connect() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO documents (source, uri, doc_type, created_at, ingested_at, title, chunk_strategy, chunk_version)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                ("test", "empty://doc", "web_page", "2025-01-01T00:00:00Z", "2025-01-01T00:00:00Z", "Empty", None, None)
            )
            doc_id = cur.lastrowid
        
        from migrate_v08 import migrate_document
        stats = migrate_document(store, doc_id, 'web_page', verbose=True, dry_run=False)
        assert stats['success']
        assert stats.get('skipped')
        assert stats['new_chunk_count'] == 0


def test_force_migration_runs_even_when_up_to_date():
    """Force flag should migrate even when strategy/version look current."""
    with tempfile.TemporaryDirectory() as tmp:
        store, Message, Role, cfg = make_store(tmp)
        
        doc_meta = {
            "source": "test",
            "uri": "test://doc",
            "doc_type": "note",
            "title": "Up-to-date",
        }
        result = store.add_document_chunked("Hello world", doc_meta)
        doc_id = result["document_id"]
        
        from migrate_v08 import migrate_document
        stats = migrate_document(store, doc_id, 'note', verbose=True, dry_run=False)
        assert stats['success']
        
        # Mark as up-to-date
        with store._connect() as conn:
            cur = conn.cursor()
            cur.execute("UPDATE documents SET chunk_version = 1, chunk_strategy = 'header-aware' WHERE id = ?", (doc_id,))
        
        # Run again to ensure force-like behavior works when invoked directly
        stats2 = migrate_document(store, doc_id, 'note', verbose=True, dry_run=False)
        assert stats2['success']

def test_chunks_respect_embedding_max_length():
    """Oversized chunks should be split to the embedder's max token length."""
    with tempfile.TemporaryDirectory() as tmp:
        store, Message, Role, cfg = make_store(tmp)
        
        long_text = " ".join(f"token{i}" for i in range(1200))
        doc_meta = {
            "source": "test",
            "uri": "rss://long",
            "doc_type": "rss_daily_summary",
            "title": "Long RSS",
        }
        result = store.add_document_chunked(long_text, doc_meta)
        doc_id = result["document_id"]
        
        with store._connect() as conn:
            cur = conn.cursor()
            cur.execute("UPDATE documents SET chunk_version = NULL WHERE id = ?", (doc_id,))
        
        from migrate_v08 import migrate_document
        
        stats = migrate_document(store, doc_id, 'rss_daily_summary', verbose=True, dry_run=False)
        assert stats['success']
        
        max_tokens = get_embedding_max_tokens(store.embedder)
        with store._connect() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT text FROM chunks WHERE document_id = ? AND deleted = 0 ORDER BY seq",
                (doc_id,)
            )
            rows = cur.fetchall()
        
        assert len(rows) > 1, "Oversized chunk should be split to respect embedder limit"
        for row in rows:
            assert count_tokens(row['text']) <= max_tokens


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
        
        # Manually create a document with multi-message raw_content (simulating old format)
        # This is how old chat documents were stored before per-message document creation
        raw_content = [
            {'content': 'Hello there', 'role': 'user', 'created_at': datetime.now(UTC).isoformat()},
            {'content': 'Hi! How can I help?', 'role': 'assistant', 'created_at': datetime.now(UTC).isoformat()},
            {'content': 'Tell me about Python', 'role': 'user', 'created_at': datetime.now(UTC).isoformat()},
        ]
        
        with store._connect() as conn:
            cur = conn.cursor()
            cur.execute('''
                INSERT INTO documents (doc_type, source, uri, title, raw_content, chunk_strategy, chunk_version, created_at, ingested_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                'chat',
                'test',
                'test://chat',
                'Test Chat',
                json.dumps(raw_content),
                None,  # Old format without strategy
                None,  # Old format without version
                datetime.now(UTC).isoformat(),
                datetime.now(UTC).isoformat()
            ))
            doc_id = cur.lastrowid
        
        from migrate_v08 import migrate_document, get_document_content
        
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
        
        # Create a document with raw_content
        raw_content = [{'content': 'Test message', 'role': 'user', 'created_at': datetime.now(UTC).isoformat()}]
        
        with store._connect() as conn:
            cur = conn.cursor()
            cur.execute('''
                INSERT INTO documents (doc_type, source, uri, title, raw_content, chunk_strategy, chunk_version, created_at, ingested_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                'chat',
                'test',
                'test://chat',
                'Test Chat',
                json.dumps(raw_content),
                None,
                None,
                datetime.now(UTC).isoformat(),
                datetime.now(UTC).isoformat()
            ))
            doc_id = cur.lastrowid
        
        from migrate_v08 import migrate_document
        
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
        
        # Manually create a document with multi-message raw_content (simulating old format)
        raw_content = [
            {'content': 'User question', 'role': 'user', 'created_at': datetime.now(UTC).isoformat()},
            {'content': 'Assistant response', 'role': 'assistant', 'created_at': datetime.now(UTC).isoformat()},
            {'content': 'User follow-up', 'role': 'user', 'created_at': datetime.now(UTC).isoformat()},
        ]
        
        with store._connect() as conn:
            cur = conn.cursor()
            cur.execute('''
                INSERT INTO documents (doc_type, source, uri, title, raw_content, chunk_strategy, chunk_version, created_at, ingested_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                'chat',
                'test',
                'test://chat',
                'Test Chat',
                json.dumps(raw_content),
                None,
                None,
                datetime.now(UTC).isoformat(),
                datetime.now(UTC).isoformat()
            ))
            doc_id = cur.lastrowid
        
        from migrate_v08 import migrate_document
        
        # Migrate
        stats = migrate_document(store, doc_id, 'chat', verbose=True, dry_run=False)
        assert stats['success']
        
        # Check roles are preserved
        with store._connect() as conn:
            cur = conn.cursor()
            cur.execute("SELECT role FROM chunks WHERE document_id = ? AND deleted = 0 ORDER BY seq", (doc_id,))
            new_roles = [row['role'] for row in cur.fetchall()]
        
        assert new_roles == ['user', 'assistant', 'user'], f"Expected specific role sequence, got {new_roles}"


def test_raw_content_and_chunk_metadata_preserved():
    """Migration should prefer raw_content and keep per-chunk metadata."""
    with tempfile.TemporaryDirectory() as tmp:
        store, Message, Role, cfg = make_store(tmp)
        
        # Seed chat with metadata that differs from stored chunks
        store.add_messages([Message(content="Stale chunk", role=Role.USER, metadata={"topic": "stale"})])
        
        raw_created = "2024-01-02T03:04:05+00:00"
        raw_event = "2024-01-03T03:04:05+00:00"
        raw_payload = [{
            "content": "Raw restored content",
            "role": "assistant",
            "created_at": raw_created,
            "event_at": raw_event,
            "tags": {"topic": "raw", "focus_hint": "raw_hint"},
            "focus_hint": "raw_hint",
        }]
        
        with store._connect() as conn:
            cur = conn.cursor()
            cur.execute("SELECT id FROM documents LIMIT 1")
            doc_id = cur.fetchone()['id']
            cur.execute(
                "UPDATE documents SET chunk_strategy = NULL, raw_content = ?, chunk_version = 1 WHERE id = ?",
                (json.dumps(raw_payload), doc_id),
            )
            cur.execute(
                "UPDATE chunks SET text = 'stale chunk', tags = ?, focus_hint = NULL WHERE document_id = ?",
                (json.dumps({"topic": "stale"}), doc_id),
            )
        
        from migrate_v08 import migrate_document
        
        stats = migrate_document(store, doc_id, 'chat', verbose=True, dry_run=False)
        assert stats['success']
        
        with store._connect() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT text, role, created_at, event_at, tags, focus_hint FROM chunks WHERE document_id = ? AND deleted = 0",
                (doc_id,),
            )
            row = cur.fetchone()
            assert row['text'] == "Raw restored content"
            assert row['role'] == "assistant"
            assert row['created_at'] == raw_created
            assert row['event_at'] == raw_event
            tags = json.loads(row['tags'])
            assert tags.get("topic") == "raw"
            assert row['focus_hint'] == "raw_hint"
            
            cur.execute("SELECT raw_content FROM documents WHERE id = ?", (doc_id,))
            stored_raw = json.loads(cur.fetchone()['raw_content'])
            assert stored_raw[0]['content'] == "Raw restored content"


def test_migration_handles_invalid_chunk_tags():
    """Migration should tolerate non-JSON chunk tags when rebuilding raw_content."""
    with tempfile.TemporaryDirectory() as tmp:
        store, Message, Role, cfg = make_store(tmp)

        store.add_messages([Message(content="Bad tags", role=Role.USER)])

        with store._connect() as conn:
            cur = conn.cursor()
            cur.execute("SELECT DISTINCT document_id FROM chunks LIMIT 1")
            doc_id = cur.fetchone()['document_id']
            cur.execute(
                "UPDATE documents SET chunk_strategy = NULL, raw_content = NULL, chunk_version = NULL WHERE id = ?",
                (doc_id,),
            )
            cur.execute(
                "UPDATE chunks SET tags = ? WHERE document_id = ?",
                ("not-json", doc_id),
            )

        from migrate_v08 import migrate_document

        stats = migrate_document(store, doc_id, 'chat', verbose=True, dry_run=False)
        assert stats['success']

        with store._connect() as conn:
            cur = conn.cursor()
            cur.execute("SELECT raw_content FROM documents WHERE id = ?", (doc_id,))
            stored_raw = json.loads(cur.fetchone()['raw_content'])
            assert stored_raw[0]['tags'] == {}


def test_timestamps_preserved():
    """Test that timestamps are preserved during migration."""
    with tempfile.TemporaryDirectory() as tmp:
        store, Message, Role, cfg = make_store(tmp)
        
        from datetime import datetime, UTC, timedelta
        
        # Create a document with specific timestamps in raw_content
        past_time = datetime.now(UTC) - timedelta(days=7)
        ref_time = datetime.now(UTC) - timedelta(days=6)
        
        raw_content = [
            {
                'content': 'Old message',
                'role': 'user',
                'created_at': past_time.isoformat(),
                'event_at': ref_time.isoformat()
            }
        ]
        
        with store._connect() as conn:
            cur = conn.cursor()
            cur.execute('''
                INSERT INTO documents (doc_type, source, uri, title, raw_content, chunk_strategy, chunk_version, created_at, ingested_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                'chat',
                'test',
                'test://chat',
                'Test Chat',
                json.dumps(raw_content),
                None,
                None,
                past_time.isoformat(),
                datetime.now(UTC).isoformat()
            ))
            doc_id = cur.lastrowid
        
        from migrate_v08 import migrate_document
        
        # Migrate
        stats = migrate_document(store, doc_id, 'chat', verbose=True, dry_run=False)
        assert stats['success']
        
        # Check timestamps are preserved
        with store._connect() as conn:
            cur = conn.cursor()
            cur.execute("SELECT created_at, event_at FROM chunks WHERE document_id = ? AND deleted = 0", (doc_id,))
            row = cur.fetchone()
        
        assert row['created_at'] == past_time.isoformat(), f"created_at should be preserved"
        assert row['event_at'] == ref_time.isoformat(), f"event_at should be preserved"


def test_missing_timestamps_use_document_times():
    """Missing chunk timestamps should fall back to document timestamps, not now()."""
    with tempfile.TemporaryDirectory() as tmp:
        store, Message, Role, cfg = make_store(tmp)
        
        from datetime import datetime, UTC, timedelta
        doc_created = (datetime.now(UTC) - timedelta(days=30)).isoformat()
        
        # Create document without timestamps in raw_content
        raw_content = [{'content': 'Needs timestamps', 'role': 'user'}]
        
        with store._connect() as conn:
            cur = conn.cursor()
            cur.execute('''
                INSERT INTO documents (doc_type, source, uri, title, raw_content, chunk_strategy, chunk_version, created_at, event_at, ingested_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                'chat',
                'test',
                'test://chat',
                'Test Chat',
                json.dumps(raw_content),
                None,
                None,
                doc_created,
                None,  # No event_at
                datetime.now(UTC).isoformat()
            ))
            doc_id = cur.lastrowid
        
        from migrate_v08 import migrate_document
        stats = migrate_document(store, doc_id, 'chat', verbose=True, dry_run=False)
        assert stats['success']
        
        with store._connect() as conn:
            cur = conn.cursor()
            cur.execute("SELECT created_at, event_at FROM chunks WHERE document_id = ? AND deleted = 0", (doc_id,))
            row = cur.fetchone()
            assert row['created_at'] == doc_created
            assert row['event_at'] is None


def test_migration_aborts_without_embeddings():
    """Test that migration aborts when embeddings are required but not available."""
    with tempfile.TemporaryDirectory() as tmp:
        store, Message, Role, cfg = make_store(tmp)
        
        # Create a document with actual content
        raw_content = [{'content': 'Test message', 'role': 'user', 'created_at': datetime.now(UTC).isoformat()}]
        
        with store._connect() as conn:
            cur = conn.cursor()
            cur.execute('''
                INSERT INTO documents (doc_type, source, uri, title, raw_content, chunk_strategy, chunk_version, created_at, ingested_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                'chat',
                'test',
                'test://chat',
                'Test Chat',
                json.dumps(raw_content),
                None,
                None,
                datetime.now(UTC).isoformat(),
                datetime.now(UTC).isoformat()
            ))
            doc_id = cur.lastrowid
        
        # Remove embedder to simulate missing embeddings
        store.embedder = None
        
        from migrate_v08 import migrate_document
        
        # Attempt migration without embedder - should fail
        stats = migrate_document(store, doc_id, 'chat', verbose=True, dry_run=False)
        
        # Migration should have failed
        assert not stats['success'], "Migration should fail without embeddings"
        assert 'Embeddings required but not available' in stats['error']
        
        # Document should NOT be marked as migrated
        with store._connect() as conn:
            cur = conn.cursor()
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
            # Get a document with actual chunks (not the empty default doc)
            cur.execute("SELECT id, chunk_strategy, chunk_version FROM documents WHERE id > 1 LIMIT 1")
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
        # Should have 2 vectors (one from each document that was added)
        assert idx.ntotal == 2


if __name__ == '__main__':
    # Run tests
    test_normalize_chunk_strategy()
    print("✓ test_normalize_chunk_strategy")
    test_selection_when_strategy_missing()
    print("✓ test_selection_when_strategy_missing")
    
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
    test_missing_timestamps_use_document_times()
    print("✓ test_missing_timestamps_use_document_times")
    test_raw_content_and_chunk_metadata_preserved()
    print("✓ test_raw_content_and_chunk_metadata_preserved")
    
    test_migration_aborts_without_embeddings()
    print("✓ test_migration_aborts_without_embeddings")
    
    print("\n✓ All tests passed!")
