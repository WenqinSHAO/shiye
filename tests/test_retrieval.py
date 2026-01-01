"""Tests for enhanced retrieval functionality."""

import tempfile
from pathlib import Path
import pytest

from storage import LocalStore
from embeddings import EmbeddingProvider
from retrieval import (
    SearchRequest, 
    Candidate, 
    RecencyBooster, 
    TypeBooster, 
    ExactMatchBooster, 
    Deduplicator,
    ContextPacker
)
from datatypes import Message, Role
from datetime import datetime, UTC, timedelta


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


def test_schema_migration(temp_store):
    """Test that schema migration creates all required columns and tables."""
    with temp_store._connect() as conn:
        # Check chunks table has new columns
        cursor = conn.execute('PRAGMA table_info(chunks)')
        columns = [row[1] for row in cursor.fetchall()]
        
        assert 'char_start' in columns
        assert 'char_end' in columns
        assert 'embedding_model' in columns
        assert 'chunk_window' in columns
        
        # Check FTS5 table exists
        cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='chunks_fts'")
        fts_table = cursor.fetchone()
        assert fts_table is not None


def test_fts5_population(temp_store):
    """Test that FTS5 table is populated on chunk insert."""
    # Add a message
    msg = Message(
        content="This is a test message about kubernetes",
        role=Role.USER
    )
    
    temp_store.add_messages([msg], document_meta={'doc_type': 'note', 'title': 'Test Note'})
    
    # Check FTS5 table has the content
    with temp_store._connect() as conn:
        cursor = conn.execute("SELECT text FROM chunks_fts WHERE chunks_fts MATCH 'kubernetes'")
        row = cursor.fetchone()
        assert row is not None
        assert 'kubernetes' in row[0]


def test_sparse_retrieval(temp_store):
    """Test FTS5 sparse retrieval."""
    # Add test messages
    messages = [
        Message(content="Docker container orchestration with Kubernetes", role=Role.USER),
        Message(content="Python data science and machine learning", role=Role.USER),
        Message(content="Kubernetes pod networking and services", role=Role.USER),
    ]
    
    temp_store.add_messages(messages, document_meta={'doc_type': 'note', 'title': 'Tech Notes'})
    
    # Search for kubernetes
    request = SearchRequest(query="kubernetes", top_k=10)
    results = temp_store._sparse_retrieval(request)
    
    assert len(results) > 0
    assert all(c.channel == 'sparse' for c in results)
    # Should return chunks with kubernetes
    assert any('kubernetes' in c.text_preview.lower() for c in results)


def test_rrf_fusion():
    """Test reciprocal rank fusion."""
    # Create mock candidates from different retrievers
    dense = [
        Candidate(chunk_id=1, score=0.9, channel='dense'),
        Candidate(chunk_id=2, score=0.8, channel='dense'),
        Candidate(chunk_id=3, score=0.7, channel='dense'),
    ]
    
    sparse = [
        Candidate(chunk_id=2, score=0.85, channel='sparse'),
        Candidate(chunk_id=4, score=0.75, channel='sparse'),
        Candidate(chunk_id=1, score=0.70, channel='sparse'),
    ]
    
    with tempfile.TemporaryDirectory() as tmpdir:
        store = LocalStore(db_path=Path(tmpdir) / 'test.db', data_dir=Path(tmpdir))
        fused = store._fuse_rrf([dense, sparse], k=60)
    
    # Candidate 1 and 2 appear in both, should rank highest
    assert len(fused) > 0
    assert fused[0].chunk_id in [1, 2]
    assert all(c.channel == 'fused' for c in fused)


def test_recency_booster():
    """Test recency boosting."""
    now = datetime.now(UTC)
    
    candidates = [
        Candidate(chunk_id=1, score=1.0, channel='fused', timestamp=now - timedelta(days=1)),
        Candidate(chunk_id=2, score=1.0, channel='fused', timestamp=now - timedelta(days=60)),
        Candidate(chunk_id=3, score=1.0, channel='fused', timestamp=now - timedelta(days=15)),
    ]
    
    request = SearchRequest(query="test", enable_time_boost=True)
    booster = RecencyBooster(decay_days=30, boost_factor=0.2)
    
    result = booster.process(request, candidates)
    
    # Most recent (chunk_id=1) should be boosted most
    assert result[0].chunk_id == 1


def test_type_booster():
    """Test document type boosting."""
    candidates = [
        Candidate(chunk_id=1, score=1.0, channel='fused', doc_type='chat'),
        Candidate(chunk_id=2, score=1.0, channel='fused', doc_type='note'),
        Candidate(chunk_id=3, score=1.0, channel='fused', doc_type='rss_daily_summary'),
    ]
    
    request = SearchRequest(query="test")
    booster = TypeBooster()
    
    result = booster.process(request, candidates)
    
    # Note should be boosted most (1.2x)
    assert result[0].chunk_id == 2


def test_exact_match_booster():
    """Test exact phrase match boosting."""
    candidates = [
        Candidate(chunk_id=1, score=1.0, channel='fused', text_preview="Docker containers and orchestration"),
        Candidate(chunk_id=2, score=1.0, channel='fused', text_preview="Test kubernetes deployment"),
        Candidate(chunk_id=3, score=1.0, channel='fused', text_preview="Python programming"),
    ]
    
    request = SearchRequest(query="kubernetes", enable_exact_boost=True)
    booster = ExactMatchBooster(boost_factor=1.5)
    
    result = booster.process(request, candidates)
    
    # Chunk 2 has exact match, should be boosted
    assert result[0].chunk_id == 2


def test_deduplicator():
    """Test deduplication by document."""
    candidates = [
        Candidate(chunk_id=1, score=1.0, channel='fused', doc_id=1),
        Candidate(chunk_id=2, score=0.9, channel='fused', doc_id=1),
        Candidate(chunk_id=3, score=0.8, channel='fused', doc_id=2),
        Candidate(chunk_id=4, score=0.7, channel='fused', doc_id=2),
    ]
    
    request = SearchRequest(query="test")
    dedup = Deduplicator(mode='by_doc')
    
    result = dedup.process(request, candidates)
    
    # Should keep only best chunk per document
    assert len(result) == 2
    assert result[0].chunk_id == 1  # Best from doc 1
    assert result[1].chunk_id == 3  # Best from doc 2


def test_context_packer():
    """Test context packing with token limits."""
    from retrieval import SearchHit
    
    hits = [
        SearchHit(
            chunk_id=i,
            doc_id=i,
            doc_type='note',
            doc_title=f'Note {i}',
            doc_source=None,
            text='x' * 1000,  # 1000 chars ~ 250 tokens
            char_start=0,
            char_end=1000,
            chunk_window=None,
            created_at=datetime.now(UTC),
            event_at=None,
            ingested_at=None,
            scores={'final': 1.0},
            rank=i,
            tags=[],
            focus_hint=None
        )
        for i in range(100)
    ]
    
    packer = ContextPacker(max_tokens=2000)
    result = packer.pack(hits, "test query")
    
    # Should fit about 8 items (2000 tokens / 250 tokens per item)
    assert result['total_items'] <= 10
    assert result['estimated_tokens'] <= 2000


def test_search_with_filters(temp_store):
    """Test search with document type filter."""
    # Add messages of different types
    temp_store.add_messages(
        [Message(content="Kubernetes deployment guide", role=Role.USER)],
        document_meta={'doc_type': 'note', 'title': 'Note 1'}
    )
    
    temp_store.add_messages(
        [Message(content="Kubernetes in production", role=Role.USER)],
        document_meta={'doc_type': 'web_page', 'title': 'Article 1'}
    )
    
    # Search with type filter
    request = SearchRequest(
        query="kubernetes",
        filters={'doc_type': 'note'},
        top_k=10
    )
    
    results = temp_store.search(request)
    
    # Should only return notes
    assert len(results) > 0
    assert all(c.doc_type == 'note' for c in results if c.doc_type)


def test_full_search_pipeline(temp_store):
    """Test end-to-end search pipeline."""
    # Add test data
    messages = [
        Message(content="Kubernetes orchestration and container management", role=Role.USER),
        Message(content="Docker containerization best practices", role=Role.USER),
        Message(content="Python machine learning frameworks", role=Role.USER),
    ]
    
    temp_store.add_messages(messages, document_meta={'doc_type': 'note', 'title': 'Tech Notes'})
    
    # Execute search
    request = SearchRequest(query="kubernetes container", top_k=5)
    results = temp_store.search(request)
    
    # Should return results
    assert len(results) > 0
    
    # Results should be candidates with scores
    assert all(hasattr(c, 'score') for c in results)
    assert all(hasattr(c, 'chunk_id') for c in results)
    
    # Top result should be relevant
    assert any('kubernetes' in c.text_preview.lower() or 'container' in c.text_preview.lower() 
               for c in results[:3] if c.text_preview)
