"""Integration tests for chunked ingestion and retrieval."""

import tempfile
from pathlib import Path
import pytest

from storage import LocalStore
from embeddings import EmbeddingProvider
from datatypes import Message, Role
from datetime import datetime, UTC


@pytest.fixture
def temp_store():
    """Create a temporary store for testing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / 'test.db'
        data_dir = Path(tmpdir)
        
        # Create store with embedder
        embedder = EmbeddingProvider()
        store = LocalStore(db_path=db_path, data_dir=data_dir, embedder=embedder)
        
        yield store


def test_save_note_chunked_creates_multiple_chunks(temp_store):
    """Test that save_note_chunked creates multiple chunks for long notes."""
    # Create a long note with headers
    content = """# Introduction
This is the introduction section with some content that should be in its own chunk.

## Background
The background provides important context for understanding the problem domain.
This section contains additional details that help set the stage.

## Related Work
Previous research has explored various approaches to this problem.
Many studies have contributed to our understanding of the field.

# Methodology
This section describes our approach in detail with comprehensive explanations.

## Data Collection
We collected data from multiple sources over an extended period.
The data collection process was carefully designed and executed.

## Analysis
Statistical analysis was performed using standard methods and tools.
Results were validated through multiple independent checks.
"""
    
    # Save note with chunking
    result = temp_store.save_note_chunked(content, title="Test Note", use_chunking=True)
    
    assert result is not None
    assert result['id'] is not None
    note_id = result['id']
    
    # Verify document was created with chunk_strategy
    with temp_store._connect() as conn:
        cur = conn.cursor()
        cur.execute("SELECT chunk_strategy, chunk_version FROM documents WHERE id = ?", (note_id,))
        doc_row = cur.fetchone()
        assert doc_row is not None
        assert doc_row['chunk_strategy'] == 'header-aware'
        assert doc_row['chunk_version'] == 1
        
        # Verify multiple chunks were created
        cur.execute("SELECT COUNT(*) as count FROM chunks WHERE document_id = ? AND deleted = 0", (note_id,))
        count_row = cur.fetchone()
        chunk_count = count_row['count']
        
        # Should have multiple chunks (at least 3-4 for this content)
        assert chunk_count >= 3, f"Expected multiple chunks, got {chunk_count}"
        
        # Verify chunks have heading_path metadata
        cur.execute("SELECT heading_path, seq, char_start, char_end FROM chunks WHERE document_id = ? AND deleted = 0 ORDER BY seq", (note_id,))
        chunks = cur.fetchall()
        
        # Check first chunk has heading path
        assert chunks[0]['heading_path'] is not None
        assert 'Introduction' in chunks[0]['heading_path']
        
        # Check sequential ordering
        for i, chunk in enumerate(chunks):
            assert chunk['seq'] == i
            assert chunk['char_start'] >= 0
            assert chunk['char_end'] > chunk['char_start']


def test_save_note_single_chunk_for_short_notes(temp_store):
    """Test that short notes are still stored as single chunks."""
    content = "This is a short note without much content."
    
    result = temp_store.save_note_chunked(content, title="Short Note", use_chunking=True)
    
    assert result is not None
    note_id = result['id']
    
    # Verify only one chunk was created (falls back to save_note)
    with temp_store._connect() as conn:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) as count FROM chunks WHERE document_id = ? AND deleted = 0", (note_id,))
        count_row = cur.fetchone()
        assert count_row['count'] == 1


def test_add_messages_populates_chunk_strategy(temp_store):
    """Test that add_messages populates chunk_strategy for chat documents."""
    messages = [
        Message(content="Hello, how are you?", role=Role.USER),
        Message(content="I'm doing well, thank you!", role=Role.ASSISTANT),
        Message(content="What's the weather like?", role=Role.USER),
    ]
    
    doc_meta = {
        "doc_type": "chat",
        "title": "Test Chat",
        "source": "test"
    }
    
    chunk_ids = temp_store.add_messages(messages, document_meta=doc_meta)
    
    assert len(chunk_ids) == 3
    
    # Get document ID from first chunk
    with temp_store._connect() as conn:
        cur = conn.cursor()
        cur.execute("SELECT document_id FROM chunks WHERE id = ?", (chunk_ids[0],))
        doc_id = cur.fetchone()['document_id']
        
        # Verify chunk_strategy was set
        cur.execute("SELECT chunk_strategy, chunk_version FROM documents WHERE id = ?", (doc_id,))
        doc_row = cur.fetchone()
        assert doc_row['chunk_strategy'] == 'per-message'
        assert doc_row['chunk_version'] == 1
        
        # Verify chunks have parent_doc_seq set
        cur.execute("SELECT parent_doc_seq FROM chunks WHERE document_id = ? ORDER BY seq", (doc_id,))
        seqs = [row['parent_doc_seq'] for row in cur.fetchall()]
        assert seqs == [0, 1, 2]


def test_save_note_regular_populates_chunk_strategy(temp_store):
    """Test that regular save_note also populates chunk_strategy."""
    content = "Regular note content"
    
    result = temp_store.save_note(content, title="Regular Note")
    
    assert result is not None
    note_id = result['id']
    
    with temp_store._connect() as conn:
        cur = conn.cursor()
        cur.execute("SELECT chunk_strategy FROM documents WHERE id = ?", (note_id,))
        doc_row = cur.fetchone()
        assert doc_row['chunk_strategy'] == 'single-chunk'


def test_chunked_note_retrieval(temp_store):
    """Test that chunked notes can be retrieved and searched."""
    content = """# Machine Learning Basics
Machine learning is a subset of artificial intelligence.

## Supervised Learning
Supervised learning uses labeled data for training models.

## Unsupervised Learning  
Unsupervised learning finds patterns in unlabeled data.
"""
    
    result = temp_store.save_note_chunked(content, title="ML Notes", use_chunking=True)
    note_id = result['id']
    
    # Verify we can get the note back
    note = temp_store.get_note(note_id)
    assert note is not None
    assert note['id'] == note_id
    
    # Verify chunks are accessible
    with temp_store._connect() as conn:
        cur = conn.cursor()
        cur.execute("SELECT text, heading_path FROM chunks WHERE document_id = ? AND deleted = 0 ORDER BY seq", (note_id,))
        chunks = cur.fetchall()
        
        # Should have multiple chunks
        assert len(chunks) > 1
        
        # Verify heading paths are preserved
        heading_paths = [c['heading_path'] for c in chunks if c['heading_path']]
        assert len(heading_paths) > 0
        assert any('Supervised Learning' in h for h in heading_paths)
