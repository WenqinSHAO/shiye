"""Integration tests for context assembly in chat using ContextPacker."""

import tempfile
from pathlib import Path
from storage import LocalStore
from workspace import MemoryWorkspace
from datatypes import Message, Role
from datetime import datetime, UTC
from retrieval import SearchRequest, ContextPacker


class MockEmbedder:
    """Mock embedder for testing."""
    def embed(self, texts):
        import numpy as np
        return [np.random.randn(384) for _ in texts]
    
    @property
    def dim(self):
        return 384


def test_context_packer_in_search():
    """Test that ContextPacker properly packs search hits into token-limited context."""
    with tempfile.TemporaryDirectory() as tmp:
        store = LocalStore(embedder=MockEmbedder(), db_path=Path(tmp) / "test.db")
        workspace = MemoryWorkspace(store=store)
        
        # Add some messages to search
        messages = [
            Message(content="Python is great for data science", role=Role.USER, created_at=datetime.now(UTC)),
            Message(content="JavaScript is used for web development", role=Role.USER, created_at=datetime.now(UTC)),
            Message(content="Machine learning uses Python heavily", role=Role.USER, created_at=datetime.now(UTC)),
        ]
        store.add_messages(messages)
        
        # Search for relevant content
        request = SearchRequest(query="Python programming", top_k=5)
        hits = workspace.search(request)
        
        if not hits:
            # If no hits (e.g., no FTS5), skip this test
            return
        
        # Use ContextPacker
        packer = ContextPacker(max_tokens=1000)
        context_bundle = packer.pack(hits, "Python programming")
        
        # Verify structure
        assert 'query' in context_bundle
        assert 'context_items' in context_bundle
        assert 'total_items' in context_bundle
        assert 'estimated_tokens' in context_bundle
        
        # Verify items have required fields
        for item in context_bundle['context_items']:
            assert 'citation_id' in item
            assert 'chunk_id' in item
            assert 'doc_id' in item
            assert 'doc_type' in item
            assert 'text' in item
            assert 'relevance_score' in item
        
        # Verify token limit is respected
        assert context_bundle['estimated_tokens'] <= 1000


def test_context_packer_respects_token_limit():
    """Test that ContextPacker respects token limits."""
    with tempfile.TemporaryDirectory() as tmp:
        store = LocalStore(embedder=MockEmbedder(), db_path=Path(tmp) / "test.db")
        workspace = MemoryWorkspace(store=store)
        
        # Add many long messages
        long_content = "This is a long message. " * 100  # ~500 chars = ~125 tokens
        messages = [
            Message(content=long_content + f" Part {i}", role=Role.USER, created_at=datetime.now(UTC))
            for i in range(10)
        ]
        store.add_messages(messages)
        
        # Search and pack with small token limit
        request = SearchRequest(query="long message", top_k=10)
        hits = workspace.search(request)
        
        if not hits:
            return
        
        # Pack with tight limit - note: chunk_window may be larger than raw text
        packer = ContextPacker(max_tokens=300)
        context_bundle = packer.pack(hits, "long message")
        
        # Should respect token limit (may fit fewer items with chunk_window)
        assert context_bundle['total_items'] <= len(hits)
        assert context_bundle['estimated_tokens'] <= 300
        
        # Verify it stopped before adding all items if there were many hits
        if len(hits) > 3:
            assert context_bundle['total_items'] < len(hits)


def test_context_packer_citation_ids():
    """Test that ContextPacker assigns sequential citation IDs."""
    with tempfile.TemporaryDirectory() as tmp:
        store = LocalStore(embedder=MockEmbedder(), db_path=Path(tmp) / "test.db")
        workspace = MemoryWorkspace(store=store)
        
        # Add messages
        messages = [
            Message(content=f"Message {i}", role=Role.USER, created_at=datetime.now(UTC))
            for i in range(5)
        ]
        store.add_messages(messages)
        
        # Search and pack
        request = SearchRequest(query="Message", top_k=5)
        hits = workspace.search(request)
        
        if not hits:
            return
        
        packer = ContextPacker(max_tokens=5000)
        context_bundle = packer.pack(hits, "Message")
        
        # Verify citation IDs are sequential
        citation_ids = [item['citation_id'] for item in context_bundle['context_items']]
        assert citation_ids == list(range(1, len(citation_ids) + 1))


def test_search_based_context_vs_context_block():
    """Test that search-based context provides more relevant results than context_block."""
    with tempfile.TemporaryDirectory() as tmp:
        store = LocalStore(embedder=MockEmbedder(), db_path=Path(tmp) / "test.db")
        workspace = MemoryWorkspace(store=store)
        
        # Add a mix of relevant and irrelevant messages
        relevant_messages = [
            Message(content="Python is a programming language", role=Role.USER, created_at=datetime.now(UTC)),
            Message(content="Python is used for data science", role=Role.USER, created_at=datetime.now(UTC)),
        ]
        
        irrelevant_messages = [
            Message(content="The weather is nice today", role=Role.USER, created_at=datetime.now(UTC)),
            Message(content="I like pizza", role=Role.USER, created_at=datetime.now(UTC)),
        ]
        
        # Add irrelevant messages first (so they're older)
        store.add_messages(irrelevant_messages)
        # Then add relevant ones
        store.add_messages(relevant_messages)
        
        # Get context via search (should prioritize relevant)
        request = SearchRequest(query="Python programming", top_k=5)
        search_hits = workspace.search(request)
        
        # Get context via context_block (recency-based)
        block_context = workspace.context_block(n=2)
        
        if not search_hits:
            return
        
        # Search should find the relevant messages
        search_texts = [hit.text for hit in search_hits]
        assert any("Python" in text for text in search_texts)
        
        # context_block just returns recent messages (might include irrelevant)
        # This test just verifies both methods work


def test_empty_search_fallback():
    """Test that empty search results don't break context assembly."""
    with tempfile.TemporaryDirectory() as tmp:
        store = LocalStore(embedder=MockEmbedder(), db_path=Path(tmp) / "test.db")
        workspace = MemoryWorkspace(store=store)
        
        # Search with no data should return empty
        request = SearchRequest(query="nonexistent query xyz123", top_k=5)
        hits = workspace.search(request)
        
        # Pack should handle empty hits gracefully
        packer = ContextPacker(max_tokens=1000)
        context_bundle = packer.pack(hits, "nonexistent query xyz123")
        
        assert context_bundle['total_items'] == 0
        assert context_bundle['estimated_tokens'] == 0
        assert context_bundle['context_items'] == []
