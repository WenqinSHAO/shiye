# Enhanced Retrieval Design for Shiye

**Purpose**: Design guideline for implementing Phase 2 (v0.7) hybrid retrieval system.  
**Status**: Planning document - not yet implemented.  
**Target**: Provide detailed specifications for coding agent to implement enhanced search.

---

## 1) Current System Reality and Target Outcomes

### What Shiye Already Has (v0.5)

**Storage Architecture**:
- Local-first under `~/.shiye/`
  - `shiye.db`: SQLite with documents + chunks tables
  - `shiye.faiss`: FAISS IndexIDMap2 (ID-mapped, returns chunk IDs directly)
  - `models/`: Cached embedding models

**Data Model** ([storage.py](storage.py)):
- **documents** table: `id`, `source`, `uri`, `doc_type`, `created_at`, `event_at`, `ingested_at`, `title`, `tags`, `hash`, `status`
- **chunks** table: `id`, `document_id`, `seq`, `text`, `role`, `token_count`, `embedding_id`, `created_at`, `event_at`, `tags`, `focus_hint`, `deleted`
- Document types: `chat`, `note`, `web_page`, `paper`, `rss_daily_summary`
- Three-timestamp system: `created_at` (storage time), `event_at` (content time), `ingested_at` (import time)

**Module Architecture**:
- `workspace.py`: High-level memory operations (add, recall, context_block)
- `storage.py`: LocalStore class - SQLite + FAISS persistence, chunk management
- `vector_store.py`: FaissIndex class - IndexIDMap2 wrapper (add/search/rebuild)
- `embeddings.py`: EmbeddingProvider - sentence-transformers with L2 normalization (dim=384)
- `orchestrator.py`: DSPy-based LLM coordination
- `web.py`: FastAPI with chat, /note, /add, /rss, /summarize commands

**Current Retrieval** ([workspace.py](workspace.py), [storage.py](storage.py)):
- `LocalStore.recall(query)`: Basic semantic search via FAISS (top_k=5)
- `LocalStore.context_block(n)`: Fetch recent chunks
- Soft deletion: `deleted` flag in chunks table
- Already using IndexIDMap2 (no row drift, stable chunk_id mapping)

### Target: What Enhanced Retrieval Should Deliver

1. **Hybrid retrieval**: Dense (FAISS) + Sparse (SQLite FTS5 BM25) with metadata filters
2. **Fusion**: Combine retriever results with Reciprocal Rank Fusion (RRF)
3. **Reranking**: Pluggable cross-encoder (FlashRank or BGE) for top candidates
4. **Multi-cue scoring**: Recency boosts, type preferences, exact-match detection
5. **Traceable outputs**: Chunk IDs, doc IDs, timestamps, scores per stage, source refs
6. **Citation-aware context**: Pack results with precise offsets for LLM context

### Gap Analysis

| Feature | Status | Required for v0.7 |
|---------|--------|-------------------|
| Stable chunk IDs | ✅ Implemented | - |
| Soft deletion | ✅ Implemented | - |
| ID-mapped FAISS | ✅ IndexIDMap2 | - |
| Sparse (FTS5) retrieval | ❌ Missing | **Create chunks_fts table** |
| Hybrid fusion (RRF) | ❌ Missing | **Implement RRF in storage.py** |
| Reranking | ❌ Missing | **Add Reranker interface + FlashRank** |
| Multi-cue scoring | ❌ Missing | **Post-processor pipeline** |
| Chunk offsets (char_start/end) | ❌ Missing | **Schema migration + ingestion update** |
| Embedding model versioning | ❌ Missing | **Add embedding_model column** |
| Search UI | ❌ Missing | **Add /find command in web.py** |

---

## 2) Data Model Enhancements

### Current Schema (Strengths)

**Already good:**
- ✅ Stable chunk IDs: `chunks.id` used as FAISS `embedding_id`
- ✅ Soft deletion: `deleted` flag for incremental cleanup
- ✅ Three timestamps: `created_at`, `event_at`, `ingested_at` for temporal filtering
- ✅ Document types and tags for metadata filtering

**Gaps for citation and retrieval quality:**
- ⚠️ No `char_start`/`char_end`: Can't show "where in document" this chunk came from
- ⚠️ No `embedding_model`: Can't track when chunks need re-embedding
- ⚠️ No FTS5 table: No sparse/keyword search capability

### Schema Migration (Phase 2.1 - First Step)

**SQL migration script** (run once):

```sql
-- 1. Add columns to chunks table
ALTER TABLE chunks ADD COLUMN char_start INTEGER DEFAULT 0;
ALTER TABLE chunks ADD COLUMN char_end INTEGER DEFAULT -1;
ALTER TABLE chunks ADD COLUMN embedding_model TEXT DEFAULT 'sentence-transformers/all-MiniLM-L6-v2';
ALTER TABLE chunks ADD COLUMN chunk_window TEXT;  -- optional display context

-- 2. Create FTS5 virtual table for sparse search
CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
    chunk_id UNINDEXED,  -- link back to chunks.id
    text,                 -- searchable content
    doc_type UNINDEXED,  -- for filtering
    tokenize='porter unicode61'
);

-- 3. Populate FTS5 from existing chunks
INSERT INTO chunks_fts(chunk_id, text, doc_type)
SELECT 
    c.id, 
    c.text, 
    d.doc_type
FROM chunks c
JOIN documents d ON c.document_id = d.id
WHERE c.deleted = 0;

-- 4. Create trigger to keep FTS5 in sync
CREATE TRIGGER IF NOT EXISTS chunks_fts_insert
AFTER INSERT ON chunks
BEGIN
    INSERT INTO chunks_fts(chunk_id, text, doc_type)
    SELECT NEW.id, NEW.text, d.doc_type
    FROM documents d WHERE d.id = NEW.document_id;
END;

CREATE TRIGGER IF NOT EXISTS chunks_fts_delete
AFTER UPDATE OF deleted ON chunks
WHEN NEW.deleted = 1
BEGIN
    DELETE FROM chunks_fts WHERE chunk_id = NEW.id;
END;
```

**Implementation location**: Add as `storage.py: _migrate_schema_v2()`, call from `__init__` if needed.

### Chunking Strategy Per Document Type

Update ingestion logic to store `char_start`/`char_end`:

| `doc_type` | Current Chunking | Enhanced Strategy (v0.7) |
|------------|------------------|--------------------------|
| `chat` | Per message | Store conversation windows (±2 turns) for context |
| `note` | Paragraph-based | Markdown header-aware + sliding 512-token windows |
| `web_page` | Main content extraction | HTML structure-aware, track original DOM offsets |
| `paper` | Abstract + sections | Section boundaries with paper metadata |
| `rss_daily_summary` | Full summary | Per-item chunks with feed source |

**Key change**: When calling `storage.add_messages()`, include `char_start` and `char_end` in chunk metadata to enable precise citation recovery.

---

## 3) Retrieval Pipeline Architecture

### Design Philosophy

- **Entry point**: `workspace.py` exposes `search(request)` method for orchestrator
- **Storage layer**: `storage.py` implements retrieval primitives
- **Vector ops**: `vector_store.py` handles FAISS operations
- **Orchestrator**: `orchestrator.py` assembles context for LLM

**Pipeline stages** (each produces traceable artifacts for debug UI):

```
Query → Parse → Multi-Retrieval → Fusion → Rerank → Post-Process → Context Pack
```

### Stage A: Query Parsing

**Input dataclass** (add to `datatypes.py`):

```python
@dataclass
class SearchRequest:
    query: str
    filters: Dict[str, Any] = field(default_factory=dict)
    # Filter keys: doc_type, tags, time_field, before, after
    top_k: int = 20
    enable_rerank: bool = True
    enable_time_boost: bool = True
    enable_exact_boost: bool = True
```

**Parse structured syntax:**
- `type:note` → `filters['doc_type'] = 'note'`
- `tag:project` → `filters['tags'] = 'project'`
- `before:2025-10-01` → `filters['before'] = '2025-10-01'`
- `after:2024-12-01` → `filters['after'] = '2024-12-01'`
- `time:event` → `filters['time_field'] = 'event_at'` (default: `created_at`)

**Implementation**: Add `storage.py: _parse_search_query(query_str)` helper.

### Stage B: Multi-Retriever Candidate Generation

**Implement in `storage.py`:**

```python
@dataclass
class Candidate:
    chunk_id: int
    score: float
    channel: str  # 'dense', 'sparse', 'exact', 'fused', 'rerank'
    doc_id: Optional[int] = None
    doc_type: Optional[str] = None
    timestamp: Optional[datetime] = None
    text_preview: Optional[str] = None

def search_hybrid(self, request: SearchRequest) -> List[List[Candidate]]:
    """Run multiple retrievers and return channel-separated results."""
    
    # Retriever 1: Dense FAISS search
    dense_candidates = self._dense_retrieval(request)
    
    # Retriever 2: Sparse FTS5 search
    sparse_candidates = self._sparse_retrieval(request)
    
    # Retriever 3: Exact match (optional)
    exact_candidates = self._exact_match_retrieval(request)
    
    return [dense_candidates, sparse_candidates, exact_candidates]
```

**Dense retrieval** (enhance existing `recall()`):

```python
def _dense_retrieval(self, request: SearchRequest) -> List[Candidate]:
    """FAISS semantic search with metadata post-filtering."""
    # 1. Embed query
    query_vec = self.embedder.embed([request.query])[0]
    
    # 2. FAISS over-retrieval (top 500 to account for filtering)
    chunk_ids, scores = self._faiss_index.search(query_vec, top_k=500)
    
    # 3. Fetch metadata and apply filters
    with self._connect() as conn:
        placeholders = ','.join('?' * len(chunk_ids))
        sql = f"""
        SELECT c.id, c.document_id, c.text, c.created_at, c.event_at, d.doc_type
        FROM chunks c
        JOIN documents d ON c.document_id = d.id
        WHERE c.id IN ({placeholders})
          AND c.deleted = 0
        """
        
        params = list(chunk_ids)
        
        # Apply filters
        if request.filters.get('doc_type'):
            sql += " AND d.doc_type = ?"
            params.append(request.filters['doc_type'])
        
        if request.filters.get('before'):
            time_field = request.filters.get('time_field', 'created_at')
            sql += f" AND c.{time_field} < ?"
            params.append(request.filters['before'])
        
        # ... similar for 'after', 'tags'
        
        rows = conn.execute(sql, params).fetchall()
    
    # 4. Build candidates with original FAISS scores
    results = []
    score_map = dict(zip(chunk_ids, scores))
    for row in rows:
        results.append(Candidate(
            chunk_id=row['id'],
            score=score_map[row['id']],
            channel='dense',
            doc_id=row['document_id'],
            doc_type=row['doc_type'],
            timestamp=ensure_utc(row.get('event_at') or row['created_at']),
            text_preview=row['text'][:200]
        ))
    
    return sorted(results, key=lambda x: x.score, reverse=True)[:request.top_k * 2]
```

**Sparse retrieval** (new, requires FTS5):

```python
def _sparse_retrieval(self, request: SearchRequest) -> List[Candidate]:
    """SQLite FTS5 BM25 search."""
    with self._connect() as conn:
        # FTS5 full-text search
        sql = """
        SELECT 
            f.chunk_id,
            f.text,
            bm25(f) as score,
            c.document_id,
            c.created_at,
            c.event_at,
            f.doc_type
        FROM chunks_fts f
        JOIN chunks c ON f.chunk_id = c.id
        WHERE chunks_fts MATCH ?
          AND c.deleted = 0
        """
        
        params = [request.query]
        
        # Apply filters
        if request.filters.get('doc_type'):
            sql += " AND f.doc_type = ?"
            params.append(request.filters['doc_type'])
        
        sql += " ORDER BY bm25(f) LIMIT ?"
        params.append(request.top_k * 2)
        
        rows = conn.execute(sql, params).fetchall()
    
    # BM25 scores are negative (lower is better), normalize to positive
    results = []
    for row in rows:
        normalized_score = 1.0 / (1.0 + abs(row['score']))  # convert to 0-1 range
        results.append(Candidate(
            chunk_id=row['chunk_id'],
            score=normalized_score,
            channel='sparse',
            doc_id=row['document_id'],
            doc_type=row['doc_type'],
            timestamp=ensure_utc(row.get('event_at') or row['created_at']),
            text_preview=row['text'][:200]
        ))
    
    return results
```

**Exact match** (regex/substring):

```python
def _exact_match_retrieval(self, request: SearchRequest) -> List[Candidate]:
    """Find exact phrase matches within filtered candidates."""
    # Only run on subset from dense/sparse to avoid full table scan
    # Return with boosted score
    pass  # Implement as optional enhancement
```

### Stage C: Fusion with Reciprocal Rank Fusion (RRF)

**Why RRF**: Robust to score-scale differences (FAISS: 0-1, BM25: negative arbitrary), no hyperparameters to tune.

```python
def _fuse_rrf(self, retriever_results: List[List[Candidate]], k: int = 60) -> List[Candidate]:
    """Reciprocal Rank Fusion: combine rankings from multiple retrievers.
    
    RRF score = sum over all retrievers of: 1 / (k + rank)
    where k=60 is standard constant, rank is 1-indexed position.
    """
    from collections import defaultdict
    
    rrf_scores = defaultdict(float)
    chunk_map = {}  # chunk_id -> best Candidate object
    
    for channel_results in retriever_results:
        for rank, candidate in enumerate(channel_results, start=1):
            rrf_scores[candidate.chunk_id] += 1.0 / (k + rank)
            
            # Keep candidate with most metadata
            if candidate.chunk_id not in chunk_map:
                chunk_map[candidate.chunk_id] = candidate
    
    # Build fused candidates
    fused = []
    for chunk_id, rrf_score in sorted(rrf_scores.items(), key=lambda x: x[1], reverse=True):
        candidate = chunk_map[chunk_id]
        candidate.score = rrf_score
        candidate.channel = 'fused'
        fused.append(candidate)
    
    return fused
```

**Reference**: Simon Willison's [hybrid search with RRF](https://simonwillison.net/2024/Oct/4/hybrid-full-text-search-and-vector-search-with-sqlite/).

### Stage D: Reranking with Cross-Encoder

**Interface design** (add to new `retrieval.py` module):

```python
from typing import Protocol

class Reranker(Protocol):
    """Protocol for pluggable rerankers."""
    def rerank(self, query: str, candidates: List[Candidate]) -> List[Candidate]:
        """Rerank candidates based on query relevance."""
        ...
```

**FlashRank implementation** (recommended for v0.7):

```python
class FlashRankReranker:
    """Lightweight CPU-friendly reranker using FlashRank.
    
    Install: pip install flashrank
    """
    def __init__(self, model: str = 'ms-marco-MiniLM-L-12-v2', cache_dir: Path = None):
        try:
            from flashrank import Ranker
            self.ranker = Ranker(model_name=model, cache_dir=str(cache_dir or DATA_DIR / 'models'))
        except ImportError:
            raise RuntimeError("FlashRank not installed: pip install flashrank")
    
    def rerank(self, query: str, candidates: List[Candidate], store: LocalStore) -> List[Candidate]:
        """Rerank top candidates with cross-encoder scoring."""
        if not candidates:
            return candidates
        
        # Fetch full text for top N candidates (reranking is expensive)
        top_n = min(50, len(candidates))
        passages = []
        for c in candidates[:top_n]:
            chunk = store.get_chunk(c.chunk_id)  # need to implement get_chunk()
            passages.append({
                "id": c.chunk_id,
                "text": chunk.text
            })
        
        # Run reranker
        reranked = self.ranker.rerank(query, passages)
        
        # Update candidates with rerank scores
        result = []
        for item in reranked:
            # Find original candidate
            orig = next(c for c in candidates if c.chunk_id == item['id'])
            orig.score = item['score']
            orig.channel = 'rerank'
            result.append(orig)
        
        # Append remaining candidates (not reranked) at the end
        reranked_ids = {item['id'] for item in reranked}
        result.extend([c for c in candidates[top_n:] if c.chunk_id not in reranked_ids])
        
        return result
```

**Alternative: BGE Reranker** (stronger but heavier):

```python
class BGEReranker:
    """BGE cross-encoder reranker.
    
    Install: pip install sentence-transformers
    Model: BAAI/bge-reranker-v2-m3
    """
    def __init__(self, model: str = 'BAAI/bge-reranker-v2-m3'):
        from sentence_transformers import CrossEncoder
        self.model = CrossEncoder(model, max_length=512)
    
    def rerank(self, query: str, candidates: List[Candidate], store: LocalStore) -> List[Candidate]:
        # Similar to FlashRank but using CrossEncoder.predict()
        pass
```

**Configuration** (add to `config.py`):

```python
SHIYE_RERANKER = os.getenv("SHIYE_RERANKER", "flashrank")  # 'flashrank', 'bge', 'none'
```

### Stage E: Post-Processing (Multi-Cue Scoring)

**Composable post-processors:**

```python
class PostProcessor(Protocol):
    """Protocol for result post-processing."""
    def process(self, request: SearchRequest, candidates: List[Candidate]) -> List[Candidate]:
        ...
```

**Recency booster:**

```python
class RecencyBooster:
    """Boost recently created/occurred content."""
    def __init__(self, decay_days: int = 30, boost_factor: float = 0.2):
        self.decay_days = decay_days
        self.boost_factor = boost_factor
    
    def process(self, request: SearchRequest, candidates: List[Candidate]) -> List[Candidate]:
        if not request.enable_time_boost:
            return candidates
        
        now = datetime.now(UTC)
        for c in candidates:
            if c.timestamp:
                age_days = (now - c.timestamp).days
                # Linear decay: 1.0 at age=0, 0.0 at age=decay_days
                recency_factor = max(0, 1.0 - age_days / self.decay_days)
                c.score *= (1.0 + self.boost_factor * recency_factor)
        
        return sorted(candidates, key=lambda x: x.score, reverse=True)
```

**Type/tag booster:**

```python
class TypeBooster:
    """Prefer certain document types."""
    DEFAULT_BOOSTS = {
        'note': 1.2,
        'web_page': 1.1,
        'chat': 1.0,
        'paper': 1.15,
        'rss_daily_summary': 0.9
    }
    
    def __init__(self, boosts: Dict[str, float] = None):
        self.boosts = boosts or self.DEFAULT_BOOSTS
    
    def process(self, request: SearchRequest, candidates: List[Candidate]) -> List[Candidate]:
        for c in candidates:
            if c.doc_type in self.boosts:
                c.score *= self.boosts[c.doc_type]
        return sorted(candidates, key=lambda x: x.score, reverse=True)
```

**Exact match booster:**

```python
class ExactMatchBooster:
    """Boost candidates with exact phrase matches."""
    def __init__(self, boost_factor: float = 1.5):
        self.boost_factor = boost_factor
    
    def process(self, request: SearchRequest, candidates: List[Candidate]) -> List[Candidate]:
        if not request.enable_exact_boost:
            return candidates
        
        query_lower = request.query.lower()
        for c in candidates:
            if c.text_preview and query_lower in c.text_preview.lower():
                c.score *= self.boost_factor
        
        return sorted(candidates, key=lambda x: x.score, reverse=True)
```

**Deduplicator:**

```python
class Deduplicator:
    """Remove duplicate chunks, keep best per document."""
    def __init__(self, mode: str = 'by_doc'):
        self.mode = mode  # 'by_doc', 'by_text'
    
    def process(self, request: SearchRequest, candidates: List[Candidate]) -> List[Candidate]:
        if self.mode == 'by_doc':
            # Keep only best chunk per document
            seen_docs = set()
            result = []
            for c in candidates:
                if c.doc_id not in seen_docs:
                    seen_docs.add(c.doc_id)
                    result.append(c)
            return result
        elif self.mode == 'by_text':
            # Remove near-duplicate text (simple hash-based)
            seen_hashes = set()
            result = []
            for c in candidates:
                text_hash = hash(c.text_preview[:100]) if c.text_preview else 0
                if text_hash not in seen_hashes:
                    seen_hashes.add(text_hash)
                    result.append(c)
            return result
        return candidates
```

**Pipeline composition** (in `storage.py: search()`):

```python
def search(self, request: SearchRequest) -> List[Candidate]:
    """Full search pipeline with hybrid retrieval, fusion, rerank, and post-processing."""
    
    # Stage B: Multi-retrieval
    retriever_results = self.search_hybrid(request)
    
    # Stage C: Fusion
    fused = self._fuse_rrf(retriever_results)
    
    # Stage D: Reranking
    if request.enable_rerank and self.reranker:
        fused = self.reranker.rerank(request.query, fused, self)
    
    # Stage E: Post-processing
    post_processors = [
        RecencyBooster(),
        TypeBooster(),
        ExactMatchBooster(),
        Deduplicator(mode='by_doc')
    ]
    
    result = fused
    for processor in post_processors:
        result = processor.process(request, result)
    
    return result[:request.top_k]
```

---

## 4) Context Assembly for LLM

### SearchHit Output Format

**Goal**: Provide traceable, citeable context to orchestrator.

```python
@dataclass
class SearchHit:
    """Final search result with full context and provenance."""
    chunk_id: int
    doc_id: int
    doc_type: str
    doc_title: Optional[str]
    doc_source: Optional[str]  # URL, file path, etc.
    
    # Text and position
    text: str
    char_start: int
    char_end: int
    chunk_window: Optional[str]  # surrounding context for display
    
    # Timestamps
    created_at: datetime
    event_at: Optional[datetime]
    ingested_at: Optional[datetime]
    
    # Retrieval provenance
    scores: Dict[str, float]  # {'dense': 0.8, 'sparse': 0.6, 'fused': 0.85, 'rerank': 0.92, 'final': 0.95}
    rank: int
    
    # Metadata
    tags: List[str]
    focus_hint: Optional[str]
```

### Context Packer

```python
class ContextPacker:
    """Pack search hits into token-budget-aware context for LLM."""
    
    def __init__(self, max_tokens: int = 8000, tokenizer_name: str = 'gpt-4'):
        self.max_tokens = max_tokens
        # Use simple approximation: 1 token ~= 4 chars for English
        self.chars_per_token = 4
    
    def pack(self, hits: List[SearchHit], query: str) -> Dict[str, Any]:
        """Pack hits into context bundle with citation metadata."""
        context_items = []
        total_tokens = 0
        
        for rank, hit in enumerate(hits, start=1):
            # Estimate tokens
            text_tokens = len(hit.text) // self.chars_per_token
            
            if total_tokens + text_tokens > self.max_tokens:
                break
            
            context_items.append({
                'citation_id': rank,
                'chunk_id': hit.chunk_id,
                'doc_id': hit.doc_id,
                'doc_type': hit.doc_type,
                'source': hit.doc_source,
                'title': hit.doc_title,
                'text': hit.text,
                'event_at': hit.event_at.isoformat() if hit.event_at else None,
                'created_at': hit.created_at.isoformat(),
                'relevance_score': hit.scores.get('final', 0.0)
            })
            
            total_tokens += text_tokens
        
        return {
            'query': query,
            'context_items': context_items,
            'total_items': len(context_items),
            'estimated_tokens': total_tokens
        }
```

**Usage in orchestrator.py:**

```python
# In orchestrator's context assembly
search_results = workspace.search(SearchRequest(query=user_query, top_k=20))
hits = [SearchHit(...) for candidate in search_results]  # Convert Candidate → SearchHit
context_bundle = ContextPacker(max_tokens=6000).pack(hits, user_query)

# Format for LLM prompt
context_text = "\n\n".join([
    f"[{item['citation_id']}] {item['doc_type']} from {item['created_at'][:10]}:\n{item['text']}"
    for item in context_bundle['context_items']
])
```

---

## 5) Module Structure and Integration Points

### New Module: `retrieval.py`

Create dedicated module for retrieval abstractions:

```python
# retrieval.py
"""Enhanced retrieval interfaces and implementations."""

from dataclasses import dataclass
from typing import Protocol, List, Dict, Any

@dataclass
class SearchRequest:
    """Search query with filters and options."""
    query: str
    filters: Dict[str, Any] = field(default_factory=dict)
    top_k: int = 20
    enable_rerank: bool = True
    enable_time_boost: bool = True
    enable_exact_boost: bool = True

@dataclass
class Candidate:
    """Intermediate retrieval candidate."""
    chunk_id: int
    score: float
    channel: str
    doc_id: Optional[int] = None
    doc_type: Optional[str] = None
    timestamp: Optional[datetime] = None
    text_preview: Optional[str] = None

@dataclass
class SearchHit:
    """Final search result with full provenance."""
    # ... as defined above

class Reranker(Protocol):
    def rerank(self, query: str, candidates: List[Candidate], store) -> List[Candidate]:
        ...

class PostProcessor(Protocol):
    def process(self, request: SearchRequest, candidates: List[Candidate]) -> List[Candidate]:
        ...

class FlashRankReranker:
    # ... implementation

class RecencyBooster:
    # ... implementation

# etc.
```

### Integration with Existing Modules

**workspace.py** - Add search entry point:

```python
def search(self, request: SearchRequest) -> List[SearchHit]:
    """Enhanced semantic search with hybrid retrieval."""
    if not self.store:
        return []
    
    candidates = self.store.search(request)
    
    # Convert Candidate → SearchHit with full metadata
    hits = []
    for rank, candidate in enumerate(candidates, start=1):
        chunk = self.store.get_chunk(candidate.chunk_id)
        doc = self.store.get_document(candidate.doc_id)
        
        hit = SearchHit(
            chunk_id=chunk.id,
            doc_id=doc.id,
            doc_type=doc.doc_type,
            doc_title=doc.title,
            doc_source=doc.uri or doc.source,
            text=chunk.text,
            char_start=chunk.char_start,
            char_end=chunk.char_end,
            chunk_window=chunk.chunk_window,
            created_at=chunk.created_at,
            event_at=chunk.event_at,
            ingested_at=doc.ingested_at,
            scores={'final': candidate.score},
            rank=rank,
            tags=json.loads(chunk.tags) if chunk.tags else [],
            focus_hint=chunk.focus_hint
        )
        hits.append(hit)
    
    return hits
```

**storage.py** - Implement search primitives:

```python
class LocalStore:
    def __init__(self, ..., reranker: Optional[Reranker] = None):
        # ... existing init
        self.reranker = reranker
        self._migrate_schema_v2()  # Add FTS5 and new columns
    
    def _migrate_schema_v2(self):
        """Apply schema migrations for v0.7 retrieval enhancements."""
        with self._connect() as conn:
            # Check if already migrated
            cursor = conn.execute("PRAGMA table_info(chunks)")
            columns = [row[1] for row in cursor.fetchall()]
            
            if 'char_start' not in columns:
                # Run migration SQL (from section 2)
                pass
    
    def search(self, request: SearchRequest) -> List[Candidate]:
        """Main search method - full pipeline."""
        # Implementation from Stage E above
        pass
    
    def _dense_retrieval(self, request: SearchRequest) -> List[Candidate]:
        # Implementation from Stage B
        pass
    
    def _sparse_retrieval(self, request: SearchRequest) -> List[Candidate]:
        # Implementation from Stage B
        pass
    
    def get_chunk(self, chunk_id: int) -> StoredChunk:
        """Fetch single chunk by ID."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM chunks WHERE id = ? AND deleted = 0",
                (chunk_id,)
            ).fetchone()
            if not row:
                raise ValueError(f"Chunk {chunk_id} not found")
            return StoredChunk(
                id=row['id'],
                document_id=row['document_id'],
                text=row['text'],
                # ... map all fields
            )
    
    def get_document(self, doc_id: int) -> Dict:
        """Fetch document metadata."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM documents WHERE id = ?",
                (doc_id,)
            ).fetchone()
            return dict(row) if row else {}
```

**web.py** - Add /find command:

```python
@app.post("/chat")
async def chat_endpoint(message: ChatMessage):
    # ... existing chat handling
    
    # Detect /find command
    if message.content.strip().startswith('/find'):
        query = message.content[5:].strip()
        return await handle_search(query)
    
    # ... rest of chat logic

async def handle_search(query: str):
    """Handle explicit search command."""
    from retrieval import SearchRequest
    
    # Parse query for filters (simple implementation)
    # e.g., "/find type:note kubernetes" → filters={'doc_type': 'note'}, query='kubernetes'
    
    request = SearchRequest(query=query, top_k=20)
    hits = workspace.search(request)
    
    # Format results for UI
    results_html = "<div class='search-results'>"
    for hit in hits:
        results_html += f"""
        <div class='search-hit' data-chunk-id='{hit.chunk_id}'>
            <div class='hit-header'>
                <span class='hit-type'>{hit.doc_type}</span>
                <span class='hit-score'>{hit.scores.get('final', 0):.3f}</span>
                <span class='hit-date'>{hit.event_at or hit.created_at}</span>
            </div>
            <div class='hit-title'>{hit.doc_title or 'Untitled'}</div>
            <div class='hit-text'>{hit.text[:300]}...</div>
            <div class='hit-source'><a href='{hit.doc_source}'>{hit.doc_source}</a></div>
        </div>
        """
    results_html += "</div>"
    
    return {
        "role": "assistant",
        "content": results_html,
        "timestamp": datetime.now(UTC).isoformat()
    }
```

---

## 6) Dependencies and Configuration

### New Dependencies (add to requirements.txt)

```txt
# Existing dependencies remain unchanged
# Add for Phase 2:
flashrank>=0.2.0  # Lightweight reranker (recommended)
# OR
# sentence-transformers>=2.2.0  # If using BGE reranker
```

### Configuration (config.py)

```python
# Add retrieval settings
SHIYE_RERANKER = os.getenv("SHIYE_RERANKER", "flashrank")  # 'flashrank', 'bge', 'none'
SHIYE_RERANK_TOP_K = int(os.getenv("SHIYE_RERANK_TOP_K", "50"))
SHIYE_SEARCH_TOP_K = int(os.getenv("SHIYE_SEARCH_TOP_K", "20"))
SHIYE_RRF_K = int(os.getenv("SHIYE_RRF_K", "60"))  # RRF constant
SHIYE_RECENCY_DECAY_DAYS = int(os.getenv("SHIYE_RECENCY_DECAY_DAYS", "30"))
```

---

## 7) Testing and Evaluation

### Unit Tests (tests/test_retrieval.py)

```python
def test_fts5_sparse_search():
    """Test FTS5 keyword search."""
    store = LocalStore(...)
    request = SearchRequest(query="machine learning")
    results = store._sparse_retrieval(request)
    assert len(results) > 0
    assert all(c.channel == 'sparse' for c in results)

def test_rrf_fusion():
    """Test reciprocal rank fusion."""
    dense = [Candidate(1, 0.9, 'dense'), Candidate(2, 0.8, 'dense')]
    sparse = [Candidate(2, 0.7, 'sparse'), Candidate(3, 0.6, 'sparse')]
    fused = store._fuse_rrf([dense, sparse])
    # Candidate 2 should rank highest (appears in both)
    assert fused[0].chunk_id == 2

def test_reranking():
    """Test FlashRank reranker."""
    reranker = FlashRankReranker()
    candidates = [Candidate(1, 0.5, 'fused'), Candidate(2, 0.4, 'fused')]
    reranked = reranker.rerank("test query", candidates, store)
    assert all(c.channel == 'rerank' for c in reranked)

def test_schema_migration():
    """Test v2 schema migration."""
    store = LocalStore(...)
    with store._connect() as conn:
        cursor = conn.execute("PRAGMA table_info(chunks)")
        columns = [row[1] for row in cursor.fetchall()]
        assert 'char_start' in columns
        assert 'char_end' in columns
        assert 'embedding_model' in columns
```

### Integration Tests

```python
def test_full_search_pipeline():
    """Test end-to-end search with all stages."""
    workspace = MemoryWorkspace()
    
    # Add test documents
    workspace.add_with_document(
        messages=[Message(role=Role.USER, content="Kubernetes pod networking")],
        document_meta={'doc_type': 'note', 'title': 'K8s Notes'}
    )
    
    # Search
    request = SearchRequest(query="kubernetes networking", top_k=5)
    hits = workspace.search(request)
    
    assert len(hits) > 0
    assert hits[0].doc_type == 'note'
    assert 'kubernetes' in hits[0].text.lower()
```

### Evaluation Metrics

Create `eval/golden_queries.json`:

```json
[
  {
    "query": "recent notes about kubernetes",
    "expected_doc_ids": [123, 456],
    "expected_types": ["note"],
    "time_constraint": "last_30_days"
  }
]
```

Run evaluation:

```python
def evaluate_retrieval(golden_queries):
    """Compute Recall@K, MRR, nDCG."""
    metrics = {'recall@5': [], 'recall@10': [], 'mrr': []}
    
    for item in golden_queries:
        request = SearchRequest(query=item['query'], top_k=10)
        hits = workspace.search(request)
        hit_doc_ids = [h.doc_id for h in hits]
        
        # Recall@K
        recall_5 = len(set(hit_doc_ids[:5]) & set(item['expected_doc_ids'])) / len(item['expected_doc_ids'])
        metrics['recall@5'].append(recall_5)
        
        # MRR
        for rank, doc_id in enumerate(hit_doc_ids, 1):
            if doc_id in item['expected_doc_ids']:
                metrics['mrr'].append(1.0 / rank)
                break
    
    return {k: sum(v)/len(v) for k, v in metrics.items()}
```

---

## 8) Implementation Roadmap for Coding Agent

### Phase 2.1: Foundation (Schema + FTS5)

**Tasks:**
1. **Schema migration** (storage.py)
   - [ ] Add `char_start`, `char_end`, `embedding_model`, `chunk_window` columns
   - [ ] Create `chunks_fts` FTS5 table
   - [ ] Add triggers to keep FTS5 in sync
   - [ ] Implement `_migrate_schema_v2()` method

2. **Update ingestion** (storage.py, handlers.py)
   - [ ] Modify `add_messages()` to store `char_start`/`char_end`
   - [ ] Populate FTS5 table on chunk insert
   - [ ] Store current `SHIYE_EMBED_MODEL` in `embedding_model` column

3. **Basic sparse search** (storage.py)
   - [ ] Implement `_sparse_retrieval(request)` using FTS5
   - [ ] Test FTS5 search returns correct chunks

### Phase 2.2: Hybrid Retrieval + Fusion

**Tasks:**
4. **Dataclasses** (new retrieval.py)
   - [ ] Create `SearchRequest`, `Candidate`, `SearchHit` dataclasses
   - [ ] Move to shared module for import by storage/workspace

5. **Dense retrieval refactor** (storage.py)
   - [ ] Refactor existing `recall()` into `_dense_retrieval(request)`
   - [ ] Add metadata filtering after FAISS over-retrieval
   - [ ] Return `List[Candidate]` instead of `Message`

6. **Multi-retriever** (storage.py)
   - [ ] Implement `search_hybrid(request)` → returns [dense_results, sparse_results]
   - [ ] Test both retrievers return candidates

7. **RRF fusion** (storage.py)
   - [ ] Implement `_fuse_rrf(retriever_results)` method
   - [ ] Unit test RRF with mock candidates
   - [ ] Integration test: verify fused ranking changes vs. single retriever

### Phase 2.3: Reranking

**Tasks:**
8. **Reranker interface** (retrieval.py)
   - [ ] Define `Reranker` Protocol
   - [ ] Implement `FlashRankReranker`
   - [ ] Add config: `SHIYE_RERANKER` env var

9. **Integration** (storage.py)
   - [ ] Add `reranker: Optional[Reranker]` to LocalStore.__init__
   - [ ] Call `reranker.rerank()` in search pipeline
   - [ ] Add dependency: `flashrank` to requirements.txt

10. **Testing**
    - [ ] Unit test FlashRankReranker with sample candidates
    - [ ] Verify reranked order differs from fused order

### Phase 2.4: Post-Processing

**Tasks:**
11. **Post-processors** (retrieval.py)
    - [ ] Implement `RecencyBooster`
    - [ ] Implement `TypeBooster`
    - [ ] Implement `ExactMatchBooster`
    - [ ] Implement `Deduplicator`

12. **Pipeline integration** (storage.py)
    - [ ] Add post-processor chain in `search()` method
    - [ ] Make processors configurable via SearchRequest options

13. **Testing**
    - [ ] Test each post-processor independently
    - [ ] Test full pipeline with all processors

### Phase 2.5: Context Assembly & UI

**Tasks:**
14. **Context packer** (retrieval.py)
    - [ ] Implement `ContextPacker.pack()` method
    - [ ] Test token budget enforcement

15. **Workspace integration** (workspace.py)
    - [ ] Add `search(request)` method
    - [ ] Convert `Candidate` → `SearchHit` with full metadata
    - [ ] Add helper: `get_chunk()`, `get_document()` in storage.py

16. **Web UI** (web.py)
    - [ ] Add `/find <query>` command handler
    - [ ] Format search results as HTML
    - [ ] Add CSS for `.search-results`, `.search-hit` classes
    - [ ] Display scores, timestamps, sources

17. **Orchestrator integration** (orchestrator.py)
    - [ ] Use `workspace.search()` instead of `recall()` for context
    - [ ] Integrate `ContextPacker` for token-aware assembly
    - [ ] Include citation_id in prompts for traceable responses

### Phase 2.6: Testing & Documentation

**Tasks:**
18. **Unit tests** (tests/test_retrieval.py)
    - [ ] Test all new methods: sparse search, fusion, reranking, post-processing
    - [ ] Test schema migration idempotency

19. **Integration tests** (tests/test_storage.py)
    - [ ] End-to-end search pipeline test
    - [ ] Test filters: doc_type, time ranges, tags
    - [ ] Test /find command in web UI

20. **Evaluation** (eval/retrieval_eval.py)
    - [ ] Create golden query set (20-50 queries)
    - [ ] Implement Recall@K, MRR computation
    - [ ] Run baseline evaluation and document results

21. **Documentation**
    - [ ] Update README.md with /find command usage
    - [ ] Update TODO.md: mark Phase 2 tasks complete
    - [ ] Add retrieval_eval.md with evaluation results
    - [ ] Code comments for all new methods

---

## 9) Optional Enhancements (Defer to v0.9+)

### Query Expansion (RAG-Fusion)

```python
class QueryExpander:
    """Generate multiple query variations for better recall."""
    def expand(self, query: str) -> List[str]:
        # Use LLM to generate 3-5 related queries
        # Run retrieval for each, then fuse with RRF
        pass
```

### Embedded Vector Search in SQLite (Future Backend)

Consider `sqlite-vec` for single-file backend:
- Eliminates FAISS dependency
- Better pre-filtering support (WHERE clauses before vector search)
- Trade-off: Slower than FAISS for large datasets (>100k vectors)

**Migration path**: Design `VectorIndex` interface so backends can be swapped without changing storage.py API.

### Late Interaction (ColBERT)

For maximum retrieval quality:
- RAGatouille library provides ColBERT interface
- Requires more storage (multi-vector per chunk)
- Defer until hitting quality ceiling with cross-encoder reranking

---

## 10) Reference Links

**Core Technologies:**
- [SQLite FTS5 Documentation](https://www.chiark.greenend.org.uk/doc/sqlite3/fts5.html) - Full-text search
- [FAISS Guidelines](https://github.com/facebookresearch/faiss/wiki/Guidelines-to-choose-an-index) - Choosing FAISS index types
- [FlashRank](https://github.com/PrithivirajDamodaran/FlashRank) - Lightweight reranker
- [BGE Models](https://bge-model.com/tutorial/5_Reranking/5.2.html) - Reranker documentation

**Hybrid Search Patterns:**
- [Simon Willison: Hybrid Search with SQLite](https://simonwillison.net/2024/Oct/4/hybrid-full-text-search-and-vector-search-with-sqlite/) - RRF implementation
- [SQLite Hybrid Search Example](https://github.com/liamca/sqlite-hybrid-search) - Reference implementation

**Research:**
- [RAG-Fusion Paper](https://arxiv.org/abs/2402.03367) - Query expansion technique

---

## Summary

This document provides **implementation-ready specifications** for Phase 2 (v0.7) retrieval enhancements:

✅ **Aligned with Shiye's architecture**: Uses existing LocalStore, FaissIndex, MemoryWorkspace patterns  
✅ **Schema-defined**: SQL migrations, column additions, FTS5 setup  
✅ **Code-ready**: Function signatures, dataclasses, integration points specified  
✅ **Testable**: Unit test, integration test, and evaluation strategies outlined  
✅ **Incremental**: Broken into 6 sub-phases (2.1-2.6) for step-by-step implementation  

**Coding agent should**:
1. Start with Phase 2.1 (schema migration)
2. Progress through phases 2.2-2.6 sequentially
3. Run tests after each phase
4. Update TODO.md to track progress

**Key design decisions**:
- RRF for fusion (no tuning required)
- FlashRank for reranking (lightweight, CPU-friendly)
- FTS5 for sparse search (built into SQLite, no new dependencies)
- Composable post-processors for flexibility
- Citation-aware context assembly for LLM transparency
