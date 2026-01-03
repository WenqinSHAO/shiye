# Chunking Strategies for Enhanced Retrieval (v0.8)

This document describes the token-aware chunking strategies implemented in Shiye v0.8 for improved retrieval quality and context preservation.

## Overview

Chunking is the process of splitting documents into smaller, semantically meaningful pieces that:
- Fit within the embedding model's token limits (~256 tokens for `all-MiniLM-L6-v2`)
- Preserve document structure (headers, sections, paragraphs)
- Enable precise citations with character offsets and metadata
- Support efficient retrieval without near-duplicate embeddings

## Chunking Strategies by Document Type

### 1. Notes (Markdown)
**Strategy**: Header-Aware Chunking

Notes use `HeaderAwareChunker` which:
- Splits by markdown headers (`#`, `##`, `###`, etc.)
- Preserves heading hierarchy (e.g., "Introduction > Background > Related Work")
- Respects token limits (300 tokens max per chunk)
- Splits long sections further if needed

**Configuration**:
```python
HeaderAwareChunker(
    max_tokens=300,           # Maximum tokens per chunk
    min_section_tokens=50     # Minimum tokens for standalone section
)
```

**Example**:
```markdown
# Introduction
This is the introduction...

## Background
The background provides...

## Related Work
Previous research...
```

Results in 3 chunks with heading paths:
- Chunk 0: "Introduction"
- Chunk 1: "Introduction > Background"
- Chunk 2: "Introduction > Related Work"

### 2. Web Pages
**Strategy**: Header-Aware Chunking (HTML/Markdown hybrid)

Web pages use the same `HeaderAwareChunker` with:
- HTML heading detection (h1, h2, h3, etc.)
- Boilerplate removal (navigation, footers)
- URL and title preservation in metadata

### 3. Papers (Academic/PDF)
**Strategy**: Sentence Window Chunking

Papers use `SentenceWindowChunker` which:
- Groups complete sentences to target ~260 tokens
- Never breaks sentences mid-way
- Preserves page numbers
- Maintains section markers when available

**Configuration**:
```python
SentenceWindowChunker(
    target_tokens=260,    # Target tokens per chunk
    min_tokens=100        # Minimum before starting new chunk
)
```

**Why sentence boundaries?**
- Academic writing uses complete sentences as semantic units
- Preserves citations and references
- Better for technical content

### 4. Chat Messages
**Strategy**: Per-Message Chunking

Chat uses `MessageChunker` which:
- Keeps each message as an individual chunk
- Optionally creates turn windows (3-7 messages) for context
- No overlap between messages
- Preserves conversation flow

**Configuration**:
```python
MessageChunker(
    create_windows=False,   # Whether to create multi-turn windows
    window_size=5           # Messages per window if enabled
)
```

### 5. RSS Daily Summaries
**Strategy**: Single Chunk

RSS summaries are typically stored as single chunks since they're already concise summaries.

## Key Design Decisions

### Token Counting
- Uses the embedding model's tokenizer (`all-MiniLM-L6-v2` by default)
- Falls back to character-based approximation (1 token ≈ 4 chars) if tokenizer unavailable
- All chunks measured before indexing

### Overlap Strategy
- **Minimal overlap** (0-30 tokens) during chunking
- **Neighbor expansion** at context-assembly time instead
- This keeps the index efficient without duplicate embeddings

### Why minimal overlap?
1. Reduces index size (fewer near-duplicate embeddings)
2. Enables efficient neighbor expansion later
3. Preserves semantic coherence via structure-aware splitting
4. Better for hybrid search (BM25 + dense retrieval)

## Metadata Tracking

Each chunk stores:
- `char_start`, `char_end`: Character offsets in original document
- `seq`: Sequence number within document
- `heading_path`: Full heading hierarchy (e.g., "Chapter 1 > Section 1.1")
- `page_number`: Page number for papers/PDFs
- `token_count`: Actual measured tokens
- `embedding_model`: Model used for embedding

## Context Assembly

At retrieval time, chunks can be expanded with neighbors:

```python
from context_assembly import expand_chunks_with_neighbors

expanded = expand_chunks_with_neighbors(
    store=store,
    chunk_ids=[retrieved_chunk_ids],
    neighbor_range=1,           # ±1 chunk
    max_expansion_chars=2000    # Maximum context size
)
```

Benefits:
- Reconstruct coherent context around matches
- See full paragraphs/sections, not just fragments
- Navigate from chunk → context → document

## Migration for Existing Data

Existing chunks (pre-v0.8) will:
- Continue to work with the new system
- Have default values for new fields (heading_path=None, etc.)
- Can be re-chunked selectively by document type

To re-chunk existing notes:
```python
# TODO: Background rechunking job implementation
# Will allow selective re-processing of documents with new chunking strategies
```

## Configuration Options

Chunking behavior can be customized via environment variables:

```bash
# Enable/disable chunking for new documents
SHIYE_USE_CHUNKING=true

# Chunk size for fixed-token chunker
SHIYE_CHUNK_SIZE=256

# Overlap tokens for fixed-token chunker
SHIYE_CHUNK_OVERLAP=20

# Neighbor expansion range for context assembly
SHIYE_NEIGHBOR_RANGE=1
```

## Performance Considerations

### Chunking Performance
- Header-aware: O(n) where n = document length
- Token-aware: O(n × t) where t = tokenization cost
- Sentence-window: O(n × s) where s = sentence detection

All chunkers are fast enough for real-time ingestion.

### Index Size
With minimal overlap:
- 10,000 documents × 5 chunks avg = 50,000 chunks
- Each embedding: 384 dimensions × 4 bytes = 1.5KB
- Total FAISS index: ~75MB
- Plus SQLite FTS5 for sparse search

### Retrieval Performance
- Dense search: O(log n) with FAISS
- Sparse search: O(1) with FTS5
- Neighbor expansion: O(k) where k = neighbor_range
- No significant overhead from chunking

## Debugging

### UI Features
1. **Chunk metadata in search results**
   - Heading path badge (e.g., "Introduction > Background")
   - Page number (e.g., "p.3")
   - Chunk sequence (e.g., "chunk #5")

2. **Debug panel enhancements**
   - Show chunk provenance
   - Display character offsets
   - Link back to source document

### Command Line
Enable chunking debug output:
```bash
SHIYE_DEBUG_RETRIEVAL=true python main.py
```

## Examples

### Example 1: Chunking a Note

```python
from chunking import HeaderAwareChunker

content = """# My Research Notes
Some introduction...

## Key Findings
Finding 1...
Finding 2...

## Future Work
Next steps...
"""

chunker = HeaderAwareChunker(max_tokens=300)
chunks = chunker.chunk(content)

for chunk in chunks:
    print(f"Chunk {chunk.seq}: {chunk.heading_path}")
    print(f"  Position: {chunk.char_start}-{chunk.char_end}")
    print(f"  Tokens: {chunk.token_count}")
```

### Example 2: Expanding with Context

```python
from context_assembly import expand_chunks_with_neighbors, format_chunk_location

# After retrieving chunk_id=123 from search
expanded = expand_chunks_with_neighbors(store, [123], neighbor_range=1)

for exp_chunk in expanded:
    print(f"Core: {exp_chunk.core_text[:100]}")
    print(f"Expanded: {exp_chunk.expanded_text[:200]}")
    print(f"Neighbors: {exp_chunk.neighbor_seq_before} before, {exp_chunk.neighbor_seq_after} after")
```

## Future Enhancements

Planned for future versions:
1. **Semantic chunking**: Use embedding similarity to find natural boundaries
2. **Multi-modal chunking**: Handle images, tables, code blocks specially
3. **Adaptive chunking**: Adjust strategy based on document characteristics
4. **Chunk quality metrics**: Measure coherence, completeness, overlap
5. **Smart re-chunking**: Detect when document changes require re-chunking

## References

- [Sentence Transformers Documentation](https://www.sbert.net/)
- [FAISS Documentation](https://github.com/facebookresearch/faiss)
- [SQLite FTS5](https://www.sqlite.org/fts5.html)
- Chunking best practices from RAG research papers
