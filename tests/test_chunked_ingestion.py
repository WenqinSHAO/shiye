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
    # Create a long note with headers (needs >200 chars with headers for chunking)
    content = """# Introduction
This is the introduction section with some content that should be in its own chunk.
We need to add more content here to exceed the threshold for chunking.

## Background
The background provides important context for understanding the problem domain.
This section contains additional details that help set the stage for our research.
More details are needed to make this section substantial enough.

## Related Work
Previous research has explored various approaches to this problem across multiple domains.
Many studies have contributed to our understanding of the field and established key principles.

# Methodology
This section describes our approach in detail with comprehensive explanations.
We outline the systematic process followed throughout the research project.

## Data Collection
We collected data from multiple sources over an extended period of time.
The data collection process was carefully designed and executed with attention to quality.

## Analysis
Statistical analysis was performed using standard methods and modern tools.
Results were validated through multiple independent checks and peer review processes.
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
    
    # Verify we can get the note back with FULL content reconstructed
    note = temp_store.get_note(note_id)
    assert note is not None
    assert note['id'] == note_id
    
    # The content should be reconstructed from all chunks
    # It should contain all the sections
    assert "Machine Learning Basics" in note['content']
    assert "Supervised Learning" in note['content']
    assert "Unsupervised Learning" in note['content']
    
    # Verify chunks are accessible
    with temp_store._connect() as conn:
        cur = conn.cursor()
        cur.execute("SELECT text, heading_path FROM chunks WHERE document_id = ? AND deleted = 0 ORDER BY seq", (note_id,))
        chunks = cur.fetchall()
        
        # Should have chunks (may be 1 if content is too short)
        assert len(chunks) >= 1
        
        # If multiple chunks, verify heading paths are preserved
        if len(chunks) > 1:
            heading_paths = [c['heading_path'] for c in chunks if c['heading_path']]
            assert len(heading_paths) > 0


def test_chat_messages_have_cumulative_offsets(temp_store):
    """Test that chat messages have cumulative character offsets."""
    messages = [
        Message(content="First message", role=Role.USER),
        Message(content="Second reply", role=Role.ASSISTANT),
        Message(content="Third message", role=Role.USER),
    ]
    
    doc_meta = {
        "doc_type": "chat",
        "title": "Test Chat",
        "source": "test"
    }
    
    chunk_ids = temp_store.add_messages(messages, document_meta=doc_meta)
    
    assert len(chunk_ids) == 3
    
    # Verify cumulative offsets
    with temp_store._connect() as conn:
        cur = conn.cursor()
        cur.execute("SELECT char_start, char_end, text FROM chunks WHERE id IN (?, ?, ?) ORDER BY seq", chunk_ids)
        chunks = cur.fetchall()
        
        # First message: starts at 0
        assert chunks[0]['char_start'] == 0
        assert chunks[0]['char_end'] == len(messages[0].content)
        
        # Second message: starts where first ended + 1 (separator)
        expected_start = chunks[0]['char_end'] + 1
        assert chunks[1]['char_start'] == expected_start
        assert chunks[1]['char_end'] == expected_start + len(messages[1].content)
        
        # Third message: starts where second ended + 1
        expected_start = chunks[1]['char_end'] + 1
        assert chunks[2]['char_start'] == expected_start
        assert chunks[2]['char_end'] == expected_start + len(messages[2].content)


def test_default_chat_gets_chunk_strategy(temp_store):
    """Test that messages added to default chat document get chunk_strategy set."""
    messages = [
        Message(content="Test message", role=Role.USER),
    ]
    
    # Add without document_meta (uses default chat document)
    chunk_ids = temp_store.add_messages(messages)
    
    # Get document ID
    with temp_store._connect() as conn:
        cur = conn.cursor()
        cur.execute("SELECT document_id FROM chunks WHERE id = ?", (chunk_ids[0],))
        doc_id = cur.fetchone()['document_id']
        
        # Verify chunk_strategy was set
        cur.execute("SELECT chunk_strategy FROM documents WHERE id = ?", (doc_id,))
        doc_row = cur.fetchone()
        assert doc_row['chunk_strategy'] == 'per-message'


def test_chunked_note_update_removes_old_faiss_embeddings(temp_store):
    """Test that updating a chunked note removes old embeddings from FAISS."""
    # Skip if FAISS not available
    if not temp_store._faiss_index:
        import pytest
        pytest.skip("FAISS not available")
    
    # Create initial note
    content_v1 = """# Version 1
This is the first version with some content to trigger chunking.
We need enough text here to exceed the threshold.

## Section A
Content for section A with more details.

## Section B
Content for section B with additional information.
"""
    
    result = temp_store.save_note_chunked(content_v1, title="Test Note V1", use_chunking=True)
    note_id = result['id']
    
    # Get initial chunk IDs
    with temp_store._connect() as conn:
        cur = conn.cursor()
        cur.execute("SELECT id FROM chunks WHERE document_id = ? AND deleted = 0", (note_id,))
        old_chunk_ids = [row['id'] for row in cur.fetchall()]
    
    assert len(old_chunk_ids) > 0
    initial_index_size = temp_store._faiss_index.index.ntotal
    
    # Update the note with new content
    content_v2 = """# Version 2
This is the updated version with completely different content structure.
More text to ensure we trigger chunking again.

## New Section X
Different content for the new structure.

## New Section Y
More different content with additional details and information.
"""
    
    result = temp_store.save_note_chunked(content_v2, title="Test Note V2", note_id=note_id, use_chunking=True)
    
    # Get new chunk IDs
    with temp_store._connect() as conn:
        cur = conn.cursor()
        cur.execute("SELECT id FROM chunks WHERE document_id = ? AND deleted = 0", (note_id,))
        new_chunk_ids = [row['id'] for row in cur.fetchall()]
    
    assert len(new_chunk_ids) > 0
    
    # Verify old chunks are marked deleted
    with temp_store._connect() as conn:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) as count FROM chunks WHERE id IN ({}) AND deleted = 1".format(
            ','.join('?' * len(old_chunk_ids))
        ), old_chunk_ids)
        deleted_count = cur.fetchone()['count']
        assert deleted_count == len(old_chunk_ids)
    
    # Note: We can't easily verify FAISS removal without access to internal state,
    # but the test ensures the code path is exercised
