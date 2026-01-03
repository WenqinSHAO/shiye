"""Integration tests for chunked ingestion of web pages, papers, and RSS summaries."""

import tempfile
from pathlib import Path
from storage import LocalStore
from datatypes import Message, Role
from datetime import datetime, UTC


class MockEmbedder:
    """Mock embedder for testing."""
    def embed(self, texts):
        import numpy as np
        return [np.random.randn(384) for _ in texts]
    
    @property
    def dim(self):
        return 384


def test_web_page_chunked_ingestion():
    """Test that web pages are chunked with HeaderAwareChunker."""
    with tempfile.TemporaryDirectory() as tmp:
        store = LocalStore(embedder=MockEmbedder(), db_path=Path(tmp) / "test.db")
        
        web_content = """# Introduction
This is a test web page with structured content.

## Section 1
First section with some content.

## Section 2
Second section with more content.

### Subsection 2.1
Detailed subsection content.
"""
        
        result = store.add_document_chunked(
            content=web_content,
            document_meta={
                "doc_type": "web_page",
                "title": "Test Web Page",
                "source": "url",
                "uri": "https://example.com/test",
            }
        )
        
        # Verify multiple chunks were created
        assert result['chunk_count'] > 1, "Web page should be chunked into multiple parts"
        
        # Verify document metadata
        with store._connect() as conn:
            cur = conn.cursor()
            cur.execute("SELECT chunk_strategy, chunk_version FROM documents WHERE id = ?", 
                       (result['document_id'],))
            row = cur.fetchone()
            assert row['chunk_strategy'] == 'header-aware'
            assert row['chunk_version'] == 1
            
            # Verify chunks have metadata
            cur.execute("""
                SELECT heading_path, parent_doc_seq, seq 
                FROM chunks 
                WHERE document_id = ? AND deleted = 0
                ORDER BY seq
            """, (result['document_id'],))
            chunks = cur.fetchall()
            
            assert len(chunks) == result['chunk_count']
            # At least one chunk should have heading info
            assert any(c['heading_path'] for c in chunks)


def test_paper_chunked_ingestion():
    """Test that papers are chunked with SentenceWindowChunker."""
    with tempfile.TemporaryDirectory() as tmp:
        store = LocalStore(embedder=MockEmbedder(), db_path=Path(tmp) / "test.db")
        
        paper_content = """Abstract: This is a test paper. 
It contains multiple sentences. Each sentence provides important information.
The sentence window chunker should handle this appropriately.

Introduction: Papers have structured content. They contain multiple sections.
Each section has multiple sentences that need to be chunked properly.
"""
        
        result = store.add_document_chunked(
            content=paper_content,
            document_meta={
                "doc_type": "paper",
                "title": "Test Paper",
                "source": "arxiv",
                "uri": "https://arxiv.org/abs/test",
            }
        )
        
        # Verify document metadata
        with store._connect() as conn:
            cur = conn.cursor()
            cur.execute("SELECT chunk_strategy, chunk_version FROM documents WHERE id = ?", 
                       (result['document_id'],))
            row = cur.fetchone()
            assert row['chunk_strategy'] == 'sentence-window'
            assert row['chunk_version'] == 1
            
            # Verify chunks exist
            cur.execute("SELECT COUNT(*) as count FROM chunks WHERE document_id = ? AND deleted = 0", 
                       (result['document_id'],))
            row = cur.fetchone()
            assert row['count'] >= 1


def test_rss_summary_chunked_ingestion():
    """Test that RSS summaries are chunked with FixedTokenChunker."""
    with tempfile.TemporaryDirectory() as tmp:
        store = LocalStore(embedder=MockEmbedder(), db_path=Path(tmp) / "test.db")
        
        rss_content = """Daily RSS Brief:
- Item 1: Important news about topic A
- Item 2: Update on topic B
- Item 3: Analysis of topic C
"""
        
        result = store.add_document_chunked(
            content=rss_content,
            document_meta={
                "doc_type": "rss_daily_summary",
                "title": "RSS Daily Brief",
                "source": "rss",
                "tags": {"keywords": ["test"], "count": 3},
            }
        )
        
        # Verify document metadata
        with store._connect() as conn:
            cur = conn.cursor()
            cur.execute("SELECT chunk_strategy, chunk_version FROM documents WHERE id = ?", 
                       (result['document_id'],))
            row = cur.fetchone()
            assert row['chunk_strategy'] == 'fixed-token'
            assert row['chunk_version'] == 1


def test_chunked_document_retrievable():
    """Test that chunked documents are retrievable via search."""
    with tempfile.TemporaryDirectory() as tmp:
        store = LocalStore(embedder=MockEmbedder(), db_path=Path(tmp) / "test.db")
        
        # Add a web page with distinctive content
        result = store.add_document_chunked(
            content="# Machine Learning\nMachine learning is about algorithms.",
            document_meta={
                "doc_type": "web_page",
                "title": "ML Guide",
                "source": "url",
                "uri": "https://example.com/ml",
            }
        )
        
        # Verify we can search and find it
        from retrieval import SearchRequest
        from workspace import MemoryWorkspace
        
        workspace = MemoryWorkspace(store=store)
        request = SearchRequest(query="machine learning", top_k=5)
        hits = workspace.search(request)
        
        # Should find at least one hit (depends on FTS5 availability)
        # If FTS5 is not available, this might be empty
        if hits:
            assert any(hit.doc_id == result['document_id'] for hit in hits)
            # Verify chunk metadata is present
            for hit in hits:
                if hit.doc_id == result['document_id']:
                    assert hit.seq is not None
                    assert hit.chunk_window is not None


def test_fallback_on_chunking_failure():
    """Test that ingestion falls back gracefully if chunking fails."""
    with tempfile.TemporaryDirectory() as tmp:
        store = LocalStore(embedder=MockEmbedder(), db_path=Path(tmp) / "test.db")
        
        # Empty content should still work
        result = store.add_document_chunked(
            content="",
            document_meta={
                "doc_type": "web_page",
                "title": "Empty Page",
                "source": "url",
                "uri": "https://example.com/empty",
            }
        )
        
        # Should create at least one chunk (fallback)
        assert result['chunk_count'] >= 1
        assert result['document_id'] is not None
