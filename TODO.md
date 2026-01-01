# Shiye Planning and Roadmap

## Project Vision

**Value Promise**: Personal "off-brain" memory plus on-demand micro-automation, grounded in your own data.

**Target Personas**:
- Power researcher (fast ingest/recall)
- Workflow hacker (ad-hoc tools)
- Inbox triager (cleanup + reminders)

## Current Status (v0)

### ✅ Completed Features

#### Core Infrastructure
- [x] Web-based UI with FastAPI (terminal UI deprecated)
- [x] Multi-round chat with DSPy backend
- [x] Local SQLite + FAISS storage under `~/.shiye`
- [x] Sentence transformer embeddings (all-MiniLM-L6-v2)
- [x] Configurable via environment variables

#### Data Model
- [x] Unified document schema (type, timestamps, tags, source)
- [x] Multiple document types: chat, note, web_page, paper, rss_daily_summary
- [x] Timestamp system: created_at, event_at, ingested_at (UTC ISO format)
- [x] Soft deletion for chunks with FAISS sync

#### Ingest Capabilities
- [x] Web URL fetching via `/add fetch <url>`
- [x] URL reference storage via `/add refs <urls>`
- [x] ArXiv paper metadata extraction
- [x] RSS feed aggregation with daily digest
- [x] Note-taking mode with markdown support
- [x] Image handling in notes

#### UI Features
- [x] Chat interface with history
- [x] 3-panel note editor (`/note` command)
- [x] Markdown rendering with math support (MathJax)
- [x] Image uploads and display
- [x] Command system: /note, /add, /rss, /summarize, /clear

#### Storage & Retrieval
- [x] SQLite schema for documents, chunks, events
- [x] FAISS vector index for semantic search
- [x] Basic retrieval with context assembly
- [x] Time-aware chunking and timestamps

### 🚧 Known Limitations

1. **No active semantic search UI**: Semantic recall exists in backend but not exposed as first-class UI action
2. **Limited error handling**: Backend LLM failures not always gracefully surfaced to UI
3. **No draft recovery**: Unsaved notes can be lost on browser issues
4. **Single LLM provider**: Only Deepseek currently supported
5. **No multi-device sync**: Local-only storage
6. **Limited ingest formats**: Only URLs and text; no PDFs, EPUBs, emails yet
7. **No tool execution**: Code generation/execution not implemented
8. **No proactive reminders**: No timeline-based notifications

## Next Steps (Prioritized)

### Phase 1: Polish Current Features (v0.5)

**Goal**: Make existing features production-ready

1. **UI Resilience**
   - [x] Surface backend errors in web UI (LLM failures, storage issues)
   - [x] Add draft recovery for notes (localStorage backup)
   - [x] Improve loading states and error messages
   - [x] Add toast notifications for background operations

2. **Note Improvements**
   - [x] Implement autosave with conflict resolution
   - [x] Autosave when going back to chat mode

### Phase 2: Enhanced Retrieval (v0.7) ✅ COMPLETED

**Goal**: Hybrid retrieval with multi-cue scoring and traceable results  
**Reference**: See [Retrieval.md](Retrieval.md) for implementation details and code entry points  
**Status**: Production-ready for testing and user feedback

#### Implementation Summary

The enhanced retrieval system is implemented across these modules:

- **`retrieval.py`** (new): Core abstractions - SearchRequest, Candidate, SearchHit, Reranker protocol, post-processors
- **`storage.py`**: LocalStore search pipeline - _dense_retrieval(), _sparse_retrieval(), search(), schema migration
- **`workspace.py`**: High-level search() entry point - converts Candidates to SearchHits with full metadata
- **`web.py`**: /find command UI - parses filters, formats HTML results
- **`config.py`**: Configuration - SHIYE_RERANKER, SHIYE_SEARCH_TOP_K, SHIYE_RRF_K, etc.

#### Phase 2.1: Foundation (Schema + FTS5) ✅

**Objective**: Extend schema for citation offsets and sparse search

1. **Schema Migration** [storage.py:_migrate_schema_v2(), lines 168-291]
   - [x] Add columns to `chunks`: `char_start`, `char_end`, `embedding_model`, `chunk_window`
   - [x] Create `chunks_fts` FTS5 virtual table for BM25 keyword search
   - [x] Add INSERT/UPDATE/DELETE triggers to sync chunks → chunks_fts (lines 211-263)
   - [x] Implement idempotency checks for columns, tables, and triggers
   - [x] FTS5 availability flag prevents log spam when unavailable (line 60, _fts5_available)

2. **Update Ingestion** [storage.py:add_messages(), save_note()]
   - [x] Modified `add_messages()` to store `char_start=0`, `char_end=len(content)` (lines 428-430)
   - [x] Updated `save_note()` to populate all citation fields on INSERT and UPDATE (lines 628-689)
   - [x] Store current `SHIYE_EMBED_MODEL` value in `embedding_model` column
   - [x] FTS5 automatically populated via INSERT triggers
   - [x] Citation offsets work for all ingestion paths (chat, notes, RSS, web)

#### Phase 2.2: Hybrid Retrieval + Fusion ✅

**Objective**: Combine dense (FAISS) + sparse (FTS5) with RRF fusion

3. **Dataclasses and Types** [retrieval.py:27-66]
   - [x] `SearchRequest` dataclass (query, filters, top_k, enable_rerank, etc.) - line 27
   - [x] `Candidate` dataclass with score_history dict for per-stage tracking - line 44
   - [x] `SearchHit` dataclass (full result with provenance) - line 65
   - [x] `Reranker` Protocol interface - line 89
   - [x] `PostProcessor` Protocol interface (implicit in post-processor implementations)

4. **Dense Retrieval Refactor** [storage.py:_dense_retrieval(), lines 893-942]
   - [x] FAISS over-retrieve (top 500), then filter by `deleted=0` and metadata
   - [x] Apply filters: `doc_type`, `before`/`after` timestamps, `tags` (chunk-level and doc-level)
   - [x] Time field selection: `time:event/created/ingested` with proper table prefixes
   - [x] Return `Candidate` objects with channel='dense' and timestamps aligned to time_field
   - [x] Handles sqlite3.Row properly with direct key access (lines 914-933)

5. **Sparse Retrieval (FTS5)** [storage.py:_sparse_retrieval(), lines 979-1040]
   - [x] Query `chunks_fts` with FTS5 MATCH syntax
   - [x] Normalize BM25 scores (negative → positive 0-1 range)
   - [x] Apply ALL metadata filters: doc_type, tags, before, after, time_field
   - [x] Returns empty list when FTS5 unavailable (no log spam) - lines 985-986
   - [x] Tag filtering checks both chunk and document tags

6. **Multi-Retriever Orchestration** [storage.py:search(), lines 1055-1126]
   - [x] Runs `_dense_retrieval()` and `_sparse_retrieval()` in parallel
   - [x] Exact match detection via `ExactMatchBooster` post-processor (not separate retriever)
   - [x] Handles empty results gracefully when FTS5 unavailable

7. **RRF Fusion** [storage.py:_fuse_rrf(), lines 1042-1053]
   - [x] Reciprocal Rank Fusion: score = sum(1/(k+rank)) across channels
   - [x] Uses `SHIYE_RRF_K` config value (default 60) - line 1031
   - [x] Updates Candidate.score_history['fused'] for traceability
   - [x] Deduplicates chunk_ids across retrievers

#### Phase 2.3: Reranking ✅

**Objective**: Add cross-encoder reranking for top candidates

8. **Reranker Interface** [retrieval.py:89-119]
   - [x] `Reranker` Protocol with `rerank(query, candidates, store)` method - line 89
   - [x] `FlashRankReranker` class using flashrank library - line 92
   - [x] Uses `SHIYE_RERANK_TOP_K` from config to limit candidates (default 50) - line 98
   - [x] Config: `SHIYE_RERANKER` env var ('flashrank', 'bge', 'none') - config.py

9. **Reranker Integration** [workspace.py:185-194, storage.py:1055+]
   - [x] Added `flashrank>=0.2.0` to requirements.txt
   - [x] workspace.py instantiates FlashRankReranker when `SHIYE_RERANKER=flashrank`
   - [x] Passes reranker to LocalStore constructor - workspace.py line 185
   - [x] Calls `reranker.rerank()` in `search()` pipeline after fusion - storage.py
   - [x] Updates Candidate.score_history['rerank'] for traceability

10. **Helper Methods** [storage.py]
    - [x] `get_chunk(chunk_id) -> StoredChunk` returns ALL fields including citation offsets - lines 838-860
    - [x] `get_document(doc_id) -> dict` for document metadata - lines 862-873
    - [x] StoredChunk dataclass extended with char_start, char_end, embedding_model, chunk_window - lines 30-37

#### Phase 2.4: Post-Processing (Multi-Cue Scoring) ✅

**Objective**: Add recency boosts, type preferences, exact matching, deduplication

11. **Post-Processor Implementations** [retrieval.py:121-209]
    - [x] `RecencyBooster(decay_days, boost_factor)` - lines 121-145, uses SHIYE_RECENCY_DECAY_DAYS
    - [x] `TypeBooster(boosts)` with doc_type preference weights - lines 147-162
    - [x] `ExactMatchBooster(boost_factor=1.5)` for query phrase hits - lines 164-180
    - [x] `Deduplicator(mode='by_doc')` keeps best chunk per document - lines 182-207
    - [x] All update score_history for traceability

12. **Post-Processor Pipeline** [storage.py:search(), lines 1055-1126]
    - [x] Applied in order: RecencyBooster → TypeBooster → ExactMatchBooster → Deduplicator
    - [x] Each processor updates Candidate.score_history with stage name and boost factor
    - [x] Final score after all boosts stored in score_history['final']
    - [x] Processors respect SearchRequest enable flags

#### Phase 2.5: Context Assembly & UI ✅

**Objective**: Expose search via UI and provide citeable context to LLM

13. **Context Packer** [retrieval.py:211-247]
    - [x] `ContextPacker(max_tokens=8000)` class with token budget enforcement
    - [x] `pack(hits, query)` returns structured context with citation_ids for LLM prompts
    - [x] Uses char-based estimation (1 token ≈ 4 chars)
    - [x] Returns dict with context_items, total_items, estimated_tokens

14. **Workspace Integration** [workspace.py:197-225]
    - [x] `search(request: SearchRequest) -> List[SearchHit]` entry point
    - [x] Converts `Candidate` → `SearchHit` with full metadata fetch
    - [x] Uses `store.get_chunk()` and `store.get_document()` for provenance
    - [x] Populates SearchHit with scores (including score_history), timestamps, offsets, source refs
    - [x] All citation fields (char_start, char_end, embedding_model, chunk_window) passed through

15. **Web UI: /find Command** [web.py:1698-1745]
    - [x] `/find <query>` command detection in chat_endpoint - line 1698
    - [x] `handle_search(query)` function parses query and calls workspace.search()
    - [x] Filter parsing: `type:note`, `tag:project`, `before:YYYY-MM-DD`, `after:YYYY-MM-DD`, `time:event/created/ingested`
    - [x] Uses `SHIYE_SEARCH_TOP_K` from config (not hardcoded) - line 1722
    - [x] HTML results show: doc_type, relevance score, timestamp, title, text preview, source link
    - [x] CSS styling in templates (search-results, search-hit classes)

16. **Orchestrator Integration** [orchestrator.py]
    - [ ] Replace `workspace.recall()` with `workspace.search()` for context retrieval (deferred)
    - [ ] Use `ContextPacker` to assemble token-aware context (deferred)
    - [ ] Include citation_id in LLM prompts (deferred to future work)

#### Phase 2.6: Testing & Documentation ✅

**Objective**: Comprehensive tests and updated documentation

17. **Unit Tests** [tests/test_retrieval.py - 280+ lines]
    - [x] Test FTS5 sparse search with keywords - test_fts5_sparse_search()
    - [x] Test RRF fusion with mock retriever results - test_rrf_fusion()
    - [x] Test FlashRankReranker with sample candidates - test_flashrank_reranker()
    - [x] Test each post-processor (recency, type, exact, dedupe) - test_*_booster()
    - [x] Test schema migration idempotency - test_schema_migration_v2_idempotent()
    - [x] Test context packer with token limits - test_context_packer()
    - [x] 13 comprehensive unit tests covering all components

18. **Integration Tests** [tests/test_storage.py]
    - [x] End-to-end: add document → search with filters → verify results
    - [x] Test deduplication (multiple chunks from same doc)
    - [x] Test context packer with token limits
    - [ ] Test filter combinations: type + time range, tags + recency (covered by manual testing)
    - [ ] Test search with reranking enabled vs disabled (covered by unit tests)

19. **Evaluation Framework** [eval/retrieval_eval.py - new file]
    - [ ] Create `eval/golden_queries.json` with 20-50 test queries (future work)
    - [ ] Format: `{query, expected_doc_ids, expected_types, time_constraint}`
    - [ ] Implement `evaluate_retrieval()` function
    - [ ] Compute metrics: Recall@5, Recall@10, MRR (Mean Reciprocal Rank)
    - [ ] Run baseline evaluation and save results to eval/results_v0.7.json
    - [ ] Document: Create eval/EVALUATION.md with methodology and results

20. **Documentation Updates**
    - [x] Update README.md: Add `/find` command to "Web UI Commands" section
    - [x] Update README.md: Add "Search" section explaining hybrid retrieval
    - [x] Update TODO.md: Mark Phase 2.1-2.6 tasks as completed with code entry points
    - [x] Created IMPLEMENTATION_SUMMARY.md with comprehensive overview
    - [ ] Add detailed docstrings for all new methods in storage.py, retrieval.py (can be done incrementally)
    - [ ] Create CHANGELOG.md: Document v0.7 changes (future work)

#### Phase 2.7: Optional Enhancements (Future Work)

21. **Exact-Match Retriever**
    - [ ] Implement as separate retrieval channel (not just post-processor boost)
    - [ ] SQL-based exact phrase matching with WHERE LIKE queries
    - [ ] Integrate into search_hybrid() as third channel

22. **Chunk Window Population**
    - [ ] `chunk_window` column exists but not populated (future enhancement)
    - [ ] Requires deciding window size (e.g., ±200 chars, sentence boundaries)
    - [ ] Would enable better citation display with surrounding context

23. **UI Notice for FTS5 Unavailability**
    - [ ] Currently only logs when FTS5 unavailable
    - [ ] Add user-facing banner on /find results or system message on startup
    - [ ] Consider graceful degradation message in UI

24. **Timeline Features** (defer to v0.8)
    - [ ] Timeline view UI for stored content (visual timeline of chunks by date)
    - [ ] Enhanced date range filtering in /find (natural language: "last week")
    - [ ] Show temporal context in chat responses (e.g., "from your notes 3 days ago")
    - [ ] Better event_at extraction from content (NLP-based date extraction)

25. **Focus Topics** (defer to v0.8)
    - [ ] Focus-topic pinning mechanism (persist user-selected focus areas)
    - [ ] Boost chunks matching active focus topics
    - [ ] Support explicit "reset context" command to clear focus

---

**Phase 2 Success Criteria**:
- ✅ `/find` command works in web UI with filters (type, tag, before, after, time)
- ✅ Hybrid retrieval (dense FAISS + sparse FTS5) operational with consistent filtering
- ✅ Reranking infrastructure in place (FlashRank properly wired from config)
- ✅ Search results show scores (including per-stage score_history), timestamps, citations
- ✅ Core tests pass (13 unit tests in test_retrieval.py, integration tests in test_storage.py)
- ✅ Config values properly applied (RRF_K, RECENCY_DECAY_DAYS, SEARCH_TOP_K, RERANK_TOP_K)
- ✅ FTS5 availability flag prevents log spam and enables graceful degradation
- ✅ Citation fields (char_start, char_end, embedding_model) populated for all ingestion paths
- ⚠️ Recall@10 > 0.7 on golden query set (evaluation framework pending)

### Phase 3: Extended Ingest (v0.9)

**Goal**: Support more content types

1. **File Support**
   - [ ] PDF ingestion with text extraction
   - [ ] EPUB/MOBI support for books
   - [ ] Image OCR for scanned documents
   - [ ] Email import (mbox, EML formats)

2. **Batch Operations**
   - [ ] Bulk URL import
   - [ ] Folder/zip file ingestion
   - [ ] Deduplication across imports
   - [ ] Progress tracking for large imports

### Phase 4: Tool Execution (v1.0)

**Goal**: Enable micro-automation

1. **Safe Execution**
   - [ ] Sandboxed Python code execution
   - [ ] Allowlisted packages only
   - [ ] Project-local file access
   - [ ] Audit log for tool runs

2. **Tool Management**
   - [ ] Store tool definitions in database
   - [ ] Version control for tools
   - [ ] Share tools between sessions
   - [ ] Tool discovery and suggestions

### Future (v2.0+)

1. **Proactive Features**
   - [ ] Timeline-based reminders
   - [ ] Suggested actions from commitments
   - [ ] Optional push notifications
   - [ ] Smart scheduling assistance

2. **Multi-device & Sync**
   - [ ] Encrypted sync between devices
   - [ ] Peer-to-peer sync option
   - [ ] Conflict resolution
   - [ ] Selective sync (tags, date ranges)

3. **Advanced Features**
   - [ ] WeChat reading history import
   - [ ] Browser history integration
   - [ ] Email monitoring and triage
   - [ ] Meeting notes and action items

## Architecture Decisions

### Storage: SQLite + FAISS

**Choice**: SQLite for metadata + FAISS for embeddings (v0)

**Rationale**:
- Simple, file-based, good locality
- Full control over schema and migrations
- Portable and Python-friendly
- FAISS GPU optional for scaling
- More wiring needed but flexible

**Alternatives Considered**:
- **Chroma**: Batteries-included but heavier, less control
- **LanceDB**: Good columnar performance but smaller ecosystem

### Data Flow

**Insertion**:
1. Write document + chunk rows to SQLite
2. Assign embedding_id (chunk id or UUID)
3. Generate embeddings and upsert to FAISS
4. Persist FAISS index to disk
5. Record sync timestamp

**Retrieval**:
1. Gather candidates via metadata filters (SQL)
2. Semantic search in FAISS for top-k
3. Merge results with combined scoring
4. Fetch full context from SQLite

**Updates/Deletes**:
- Soft-delete chunks in SQLite
- Maintain pending_delete list for FAISS
- Rebuild index periodically for cleanup

### LLM Integration

**Current**: DSPy with Deepseek

**Future**: Abstract provider interface supporting:
- OpenAI GPT-4
- Anthropic Claude
- Local models (Ollama, llama.cpp)
- Custom endpoints

## Design Principles

1. **Local-first**: User owns their data, no cloud required
2. **Privacy-focused**: Clear data boundaries, optional encryption
3. **Minimal dependencies**: Lean core, optional extensions
4. **Progressive enhancement**: Work offline, enhance online
5. **Extensible architecture**: Plugin system for adapters and tools

## Open Questions

1. **How to handle very large documents?**
   - Current: Chunk into smaller pieces
   - Consider: Hierarchical summarization, streaming ingestion

2. **How to prevent context overflow?**
   - Current: Simple top-k retrieval
   - Consider: Dynamic context budget, importance scoring

3. **How to handle conflicting information?**
   - Current: Show all retrieved chunks
   - Consider: Timestamp-based resolution, user preferences

4. **How to organize focus topics?**
   - Current: Ad-hoc tags
   - Consider: Hierarchical tags, auto-discovery, topic modeling

## RSS Feed Configuration

Default feeds (edit `rss_feeds.txt` to customize):
- Google AI Research Blog
- OpenAI Blog
- DeepMind Blog
- Microsoft Research Blog

**Selection Strategy**:
- Fetch latest items per feed
- Dedup by link/title hash
- Cap per feed (3 items) and total (20 items)
- Filter obvious promos/short posts
- Generate concise summary with references

**Storage**: Daily summaries stored as `rss_daily_summary` documents with feed metadata and keywords.

## Contributing

See README.md for contribution guidelines. Key areas where help is welcome:

- UI/UX improvements
- Additional ingest formats
- LLM provider integrations
- Performance optimizations
- Documentation and examples

## References

- [DSPy Documentation](https://dspy-docs.vercel.app/)
- [FAISS Wiki](https://github.com/facebookresearch/faiss/wiki)
- [FastAPI Documentation](https://fastapi.tiangolo.com/)
- [Sentence Transformers](https://www.sbert.net/)
