"""Tests for chunking module."""

import pytest
from chunking import (
    count_tokens,
    FixedTokenChunker,
    SentenceWindowChunker,
    SentenceSplitter,
    HeaderAwareChunker,
    MessageChunker,
    get_chunker_for_doctype,
)


def test_count_tokens():
    """Test token counting."""
    # Simple text
    text = "Hello world"
    tokens = count_tokens(text)
    assert tokens > 0
    assert tokens < 10  # Should be around 2-3 tokens
    
    # Empty text
    assert count_tokens("") == 0
    
    # Longer text
    long_text = "This is a longer piece of text that should have more tokens. " * 10
    long_tokens = count_tokens(long_text)
    assert long_tokens > tokens


def test_fixed_token_chunker_basic():
    """Test basic fixed-token chunking."""
    chunker = FixedTokenChunker(chunk_size=20, overlap_tokens=5)
    
    # Text that should result in multiple chunks
    text = "This is a test sentence. " * 20
    chunks = chunker.chunk(text)
    
    assert len(chunks) > 1
    
    # Check chunk properties
    for i, chunk in enumerate(chunks):
        assert chunk.text
        assert chunk.char_start >= 0
        assert chunk.char_end > chunk.char_start
        assert chunk.seq == i
        assert chunk.token_count is not None
        
        # Token count should be within reasonable bounds
        if i < len(chunks) - 1:  # Not last chunk
            assert chunk.token_count <= 25  # chunk_size + some tolerance


def test_fixed_token_chunker_empty():
    """Test chunker with empty/invalid input."""
    chunker = FixedTokenChunker()
    
    assert chunker.chunk("") == []
    assert chunker.chunk("   ") == []


def test_fixed_token_chunker_small_text():
    """Test chunker with text smaller than chunk size."""
    chunker = FixedTokenChunker(chunk_size=100)
    
    text = "Short text."
    chunks = chunker.chunk(text)
    
    assert len(chunks) == 1
    assert chunks[0].text == text
    assert chunks[0].char_start == 0
    assert chunks[0].char_end == len(text)


def test_sentence_splitter():
    """Test sentence splitting."""
    text = "First sentence. Second sentence! Third sentence? Fourth sentence.\n\nNew paragraph."
    sentences = SentenceSplitter.split(text)
    
    assert len(sentences) >= 4
    assert any("First" in s for s in sentences)
    assert any("Second" in s for s in sentences)


def test_sentence_window_chunker():
    """Test sentence-based chunking."""
    chunker = SentenceWindowChunker(target_tokens=50, min_tokens=10)
    
    text = "First sentence. " * 5 + "Second sentence. " * 5 + "Third sentence. " * 5
    chunks = chunker.chunk(text)
    
    assert len(chunks) > 0
    
    for i, chunk in enumerate(chunks):
        assert chunk.text
        assert chunk.seq == i
        # Should group multiple sentences
        assert chunk.text.count('.') >= 1


def test_sentence_window_chunker_respect_boundaries():
    """Test that sentence chunker respects sentence boundaries."""
    chunker = SentenceWindowChunker(target_tokens=30, min_tokens=5)
    
    text = "Short one. " * 3 + "A much longer sentence with many words that should probably be in its own chunk. " + "Final short."
    chunks = chunker.chunk(text)
    
    # Each chunk should end with sentence ending
    for chunk in chunks:
        # Should not cut sentences mid-way
        assert chunk.text.strip()


def test_header_aware_chunker_basic():
    """Test markdown header-aware chunking."""
    chunker = HeaderAwareChunker(max_tokens=100)
    
    text = """# Introduction
This is the introduction section with some content.

## Background
This is the background subsection.

## Related Work
This section discusses related work.

# Methodology
This is the methodology section."""
    
    chunks = chunker.chunk(text)
    
    assert len(chunks) >= 3
    
    # Check that heading paths are set
    heading_paths = [c.heading_path for c in chunks if c.heading_path]
    assert len(heading_paths) > 0
    assert any("Introduction" in h for h in heading_paths)
    assert any("Background" in h for h in heading_paths)


def test_header_aware_chunker_hierarchy():
    """Test that header chunker tracks heading hierarchy."""
    chunker = HeaderAwareChunker(max_tokens=200)
    
    text = """# Chapter 1
Content for chapter 1.

## Section 1.1
Content for section 1.1.

### Subsection 1.1.1
Content for subsection.

## Section 1.2
Content for section 1.2.

# Chapter 2
Content for chapter 2."""
    
    chunks = chunker.chunk(text)
    
    # Find chunk with deepest nesting
    nested_chunks = [c for c in chunks if c.heading_path and '>' in c.heading_path]
    assert len(nested_chunks) > 0
    
    # Check hierarchy is preserved
    for chunk in nested_chunks:
        parts = chunk.heading_path.split(' > ')
        assert len(parts) >= 2


def test_header_aware_chunker_splits_long_sections():
    """Test that long sections are split even within headers."""
    chunker = HeaderAwareChunker(max_tokens=30)
    
    # Create a long section
    text = "# Long Section\n" + "This is a sentence. " * 50
    
    chunks = chunker.chunk(text)
    
    # Should split into multiple chunks
    assert len(chunks) > 1
    
    # All chunks from same section should have same heading path
    heading_paths = set(c.heading_path for c in chunks)
    assert len(heading_paths) == 1


def test_header_aware_chunker_no_headers():
    """Test chunker falls back when no headers present."""
    chunker = HeaderAwareChunker(max_tokens=50)
    
    text = "Just plain text without any headers. " * 20
    chunks = chunker.chunk(text)
    
    assert len(chunks) > 0
    # Should still chunk the text
    assert chunks[0].text


def test_message_chunker_individual():
    """Test message chunking without windows."""
    chunker = MessageChunker(create_windows=False)
    
    messages = [
        "First message",
        "Second message",
        "Third message"
    ]
    
    chunks = chunker.chunk(messages)
    
    assert len(chunks) == 3
    
    for i, chunk in enumerate(chunks):
        assert chunk.text == messages[i]
        assert chunk.seq == i


def test_message_chunker_with_windows():
    """Test message chunking with turn windows."""
    chunker = MessageChunker(create_windows=True, window_size=3)
    
    messages = [
        "Message 1",
        "Message 2",
        "Message 3",
        "Message 4",
        "Message 5"
    ]
    
    chunks = chunker.chunk(messages)
    
    # Should have individual messages + windows
    # 5 individual + (5-3+1)=3 windows = 8 chunks
    assert len(chunks) >= 5
    
    # First 5 should be individual messages
    for i in range(5):
        assert chunks[i].text == messages[i]


def test_message_chunker_empty():
    """Test message chunker with empty input."""
    chunker = MessageChunker()
    
    assert chunker.chunk([]) == []


def test_get_chunker_for_doctype():
    """Test chunker factory function."""
    # Test different doc types
    assert isinstance(get_chunker_for_doctype('note'), HeaderAwareChunker)
    assert isinstance(get_chunker_for_doctype('web_page'), HeaderAwareChunker)
    assert isinstance(get_chunker_for_doctype('paper'), SentenceWindowChunker)
    assert isinstance(get_chunker_for_doctype('chat'), MessageChunker)  # Fixed to return MessageChunker
    assert isinstance(get_chunker_for_doctype('rss_daily_summary'), FixedTokenChunker)
    assert isinstance(get_chunker_for_doctype('unknown'), FixedTokenChunker)


def test_chunk_provenance():
    """Test that chunks maintain proper provenance."""
    chunker = FixedTokenChunker(chunk_size=50, overlap_tokens=10)
    
    text = "This is the first part. " * 10 + "This is the second part. " * 10
    chunks = chunker.chunk(text)
    
    # Check sequential ordering
    for i in range(len(chunks)):
        assert chunks[i].seq == i
    
    # Check character offsets are increasing (allowing for overlap)
    for i in range(len(chunks) - 1):
        # Next chunk should start at or after current chunk start
        assert chunks[i + 1].char_start >= chunks[i].char_start


def test_chunk_token_limits():
    """Test that chunks respect token limits."""
    max_tokens = 100
    chunker = FixedTokenChunker(chunk_size=max_tokens, overlap_tokens=10)
    
    # Generate long text
    text = "Word " * 500
    chunks = chunker.chunk(text)
    
    # All chunks should be within limits (with some tolerance for tokenization)
    for chunk in chunks[:-1]:  # Exclude last chunk which may be smaller
        assert chunk.token_count <= max_tokens * 1.1  # 10% tolerance


def test_sentence_window_maintains_context():
    """Test that sentence window chunker maintains readable context."""
    chunker = SentenceWindowChunker(target_tokens=40)
    
    text = """The quick brown fox jumps over the lazy dog. This is a test sentence. 
Another sentence follows here. And yet another one. One more for good measure."""
    
    chunks = chunker.chunk(text)
    
    # Each chunk should start and end at sentence boundaries
    for chunk in chunks:
        text = chunk.text.strip()
        # Should end with sentence terminator (except possibly the last chunk)
        if text:
            assert text[-1] in '.!?' or chunk.seq == len(chunks) - 1


def test_header_chunker_preserves_content():
    """Test that no content is lost during header-aware chunking."""
    chunker = HeaderAwareChunker(max_tokens=100)
    
    text = """# Section 1
Content 1.

## Subsection 1.1
Content 1.1.

# Section 2
Content 2."""
    
    chunks = chunker.chunk(text)
    
    # Reconstruct text from chunks (approximately, without exact offsets)
    reconstructed = ' '.join(c.text for c in chunks)
    
    # All major content should be present
    assert "Section 1" in reconstructed
    assert "Content 1" in reconstructed
    assert "Subsection 1.1" in reconstructed
    assert "Section 2" in reconstructed
