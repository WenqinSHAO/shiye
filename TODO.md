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
   - [ ] Surface backend errors in web UI (LLM failures, storage issues)
   - [ ] Add draft recovery for notes (localStorage backup)
   - [ ] Improve loading states and error messages
   - [ ] Add toast notifications for background operations

2. **Note Improvements**
   - [ ] Implement autosave with conflict resolution
   - [ ] Autosave when going back to chat mode

### Phase 2: Enhanced Retrieval (v0.7)

**Goal**: Hybrid retrieval with multi-cue scoring and traceable results  
**Reference**: See [Retrieval.md](Retrieval.md) for detailed design specifications

#### Phase 2.1: Foundation (Schema + FTS5)

**Objective**: Extend schema for citation offsets and sparse search

1. **Schema Migration** [storage.py]
   - [ ] Add columns to `chunks`: `char_start`, `char_end`, `embedding_model`, `chunk_window`
   - [ ] Create `chunks_fts` FTS5 virtual table for BM25 keyword search
   - [ ] Add INSERT/UPDATE triggers to sync chunks → chunks_fts
   - [ ] Implement `storage.py: _migrate_schema_v2()` with idempotency check
   - [ ] Test: Verify new columns exist, FTS5 table queryable

2. **Update Ingestion** [storage.py, handlers.py]
   - [ ] Modify `add_messages()` to accept and store `char_start`/`char_end` per chunk
   - [ ] Update chunking logic to calculate character offsets during split
   - [ ] Store current `SHIYE_EMBED_MODEL` value in `embedding_model` column
   - [ ] Populate `chunks_fts` on every chunk insert
   - [ ] Test: Add document, verify FTS5 contains searchable text

#### Phase 2.2: Hybrid Retrieval + Fusion

**Objective**: Combine dense (FAISS) + sparse (FTS5) with RRF fusion

3. **Dataclasses and Types** [new retrieval.py module]
   - [ ] Create `SearchRequest` dataclass (query, filters, top_k, enable_rerank, etc.)
   - [ ] Create `Candidate` dataclass (chunk_id, score, channel, metadata)
   - [ ] Create `SearchHit` dataclass (full result with provenance)
   - [ ] Add `Reranker` Protocol interface
   - [ ] Add `PostProcessor` Protocol interface
   - [ ] Test: Import dataclasses in storage.py and workspace.py

4. **Dense Retrieval Refactor** [storage.py]
   - [ ] Refactor `recall()` into `_dense_retrieval(request: SearchRequest) -> List[Candidate]`
   - [ ] FAISS over-retrieve (top 500), then filter by `deleted=0` and metadata
   - [ ] Apply filters: `doc_type`, `before`/`after` timestamps, `tags`
   - [ ] Return `Candidate` objects with channel='dense'
   - [ ] Test: Verify dense search with filters returns correct chunks

5. **Sparse Retrieval (FTS5)** [storage.py]
   - [ ] Implement `_sparse_retrieval(request: SearchRequest) -> List[Candidate]`
   - [ ] Query `chunks_fts` with FTS5 MATCH syntax
   - [ ] Normalize BM25 scores (negative → positive 0-1 range)
   - [ ] Apply same metadata filters as dense retrieval
   - [ ] Return `Candidate` objects with channel='sparse'
   - [ ] Test: FTS5 search for keywords returns relevant chunks

6. **Multi-Retriever Orchestration** [storage.py]
   - [ ] Implement `search_hybrid(request) -> List[List[Candidate]]`
   - [ ] Run `_dense_retrieval()` and `_sparse_retrieval()` 
   - [ ] Optional: Implement `_exact_match_retrieval()` for regex/substring
   - [ ] Return separate candidate lists per channel
   - [ ] Test: Verify both retrievers return non-overlapping + overlapping results

7. **RRF Fusion** [storage.py]
   - [ ] Implement `_fuse_rrf(retriever_results, k=60) -> List[Candidate]`
   - [ ] Compute reciprocal rank fusion: score = sum(1/(k+rank)) across channels
   - [ ] Update Candidate.score to RRF score, set channel='fused'
   - [ ] Test: Verify chunks appearing in both retrievers rank higher

#### Phase 2.3: Reranking

**Objective**: Add cross-encoder reranking for top candidates

8. **Reranker Interface** [retrieval.py]
   - [ ] Define `Reranker` Protocol with `rerank(query, candidates, store)` method
   - [ ] Implement `FlashRankReranker` class using flashrank library
   - [ ] Handle top-N candidate selection (rerank only top 50)
   - [ ] Add config: `SHIYE_RERANKER` env var ('flashrank', 'bge', 'none')
   - [ ] Test: Unit test FlashRankReranker with mock candidates

9. **Reranker Integration** [storage.py, config.py]
   - [ ] Add `flashrank>=0.2.0` to requirements.txt
   - [ ] Add `reranker: Optional[Reranker]` parameter to LocalStore.__init__
   - [ ] Initialize reranker based on `SHIYE_RERANKER` config
   - [ ] Call `reranker.rerank()` in `search()` pipeline after fusion
   - [ ] Update Candidate.channel='rerank' and scores
   - [ ] Test: End-to-end search with reranking enabled/disabled

10. **Helper Methods** [storage.py]
    - [ ] Implement `get_chunk(chunk_id) -> StoredChunk` for single chunk retrieval
    - [ ] Implement `get_document(doc_id) -> dict` for document metadata
    - [ ] Test: Fetch chunk by ID, verify all fields present

#### Phase 2.4: Post-Processing (Multi-Cue Scoring)

**Objective**: Add recency boosts, type preferences, exact matching, deduplication

11. **Post-Processor Implementations** [retrieval.py]
    - [ ] Implement `RecencyBooster(decay_days=30, boost_factor=0.2)`
    - [ ] Implement `TypeBooster(boosts={'note':1.2, 'web_page':1.1, ...})`
    - [ ] Implement `ExactMatchBooster(boost_factor=1.5)` for query phrase hits
    - [ ] Implement `Deduplicator(mode='by_doc')` to keep best chunk per document
    - [ ] Test: Each post-processor independently with mock candidates

12. **Post-Processor Pipeline** [storage.py]
    - [ ] Add post-processor chain in `search()` method
    - [ ] Apply processors conditionally based on SearchRequest flags
    - [ ] Chain: RecencyBooster → TypeBooster → ExactMatchBooster → Deduplicator
    - [ ] Test: Full pipeline, verify score changes and deduplication

#### Phase 2.5: Context Assembly & UI

**Objective**: Expose search via UI and provide citeable context to LLM

13. **Context Packer** [retrieval.py]
    - [ ] Implement `ContextPacker(max_tokens=8000)` class
    - [ ] Implement `pack(hits, query) -> dict` with token budget enforcement
    - [ ] Return structured context with citation_ids for LLM prompts
    - [ ] Test: Pack 20 hits, verify token budget not exceeded

14. **Workspace Integration** [workspace.py]
    - [ ] Add `search(request: SearchRequest) -> List[SearchHit]` method
    - [ ] Convert `Candidate` → `SearchHit` with full metadata fetch
    - [ ] Use `store.get_chunk()` and `store.get_document()` for provenance
    - [ ] Populate SearchHit with scores, timestamps, offsets, source refs
    - [ ] Test: Call workspace.search(), verify SearchHit completeness

15. **Web UI: /find Command** [web.py]
    - [ ] Add `/find <query>` command detection in chat_endpoint
    - [ ] Implement `handle_search(query)` function
    - [ ] Parse query for filters: `type:note`, `tag:project`, `before:date`, `after:date`
    - [ ] Call `workspace.search(SearchRequest(...))` and format results as HTML
    - [ ] Display: doc_type, relevance score, timestamp, title, text preview, source link
    - [ ] Add CSS styling for `.search-results`, `.search-hit` classes
    - [ ] Test: Web UI manual test with various queries and filters

16. **Orchestrator Integration** [orchestrator.py]
    - [ ] Replace `workspace.recall()` with `workspace.search()` for context retrieval
    - [ ] Use `ContextPacker` to assemble token-aware context
    - [ ] Include citation_id in LLM prompts (e.g., "[1] note from 2024-12-15: ...")
    - [ ] Test: Chat with context, verify LLM receives properly formatted citations

#### Phase 2.6: Testing & Documentation

**Objective**: Comprehensive tests and updated documentation

17. **Unit Tests** [tests/test_retrieval.py - new file]
    - [ ] Test FTS5 sparse search with keywords
    - [ ] Test RRF fusion with mock retriever results
    - [ ] Test FlashRankReranker with sample candidates
    - [ ] Test each post-processor (recency, type, exact, dedupe)
    - [ ] Test schema migration idempotency (run twice, no errors)
    - [ ] Test SearchRequest filter parsing

18. **Integration Tests** [tests/test_storage.py]
    - [ ] End-to-end: add document → search with filters → verify results
    - [ ] Test filter combinations: type + time range, tags + recency
    - [ ] Test search with reranking enabled vs disabled
    - [ ] Test deduplication (multiple chunks from same doc)
    - [ ] Test context packer with token limits

19. **Evaluation Framework** [eval/retrieval_eval.py - new file]
    - [ ] Create `eval/golden_queries.json` with 20-50 test queries
    - [ ] Format: `{query, expected_doc_ids, expected_types, time_constraint}`
    - [ ] Implement `evaluate_retrieval()` function
    - [ ] Compute metrics: Recall@5, Recall@10, MRR (Mean Reciprocal Rank)
    - [ ] Run baseline evaluation and save results to eval/results_v0.7.json
    - [ ] Document: Create eval/EVALUATION.md with methodology and results

20. **Documentation Updates**
    - [ ] Update README.md: Add `/find` command to "Web UI Commands" section
    - [ ] Update README.md: Add "Search" section explaining hybrid retrieval
    - [ ] Update TODO.md: Mark Phase 2.1-2.6 tasks as completed
    - [ ] Add code comments: Docstrings for all new methods in storage.py, retrieval.py
    - [ ] Create CHANGELOG.md: Document v0.7 changes and breaking changes (if any)

#### Phase 2.7: Optional Enhancements (Time Permitting)

21. **Timeline Features**
    - [ ] Timeline view UI for stored content (visual timeline of chunks by date)
    - [ ] Enhanced date range filtering in /find (natural language: "last week")
    - [ ] Show temporal context in chat responses (e.g., "from your notes 3 days ago")
    - [ ] Better event_at extraction from content (NLP-based date extraction)

22. **Focus Topics** (defer to v0.8 if not completed)
    - [ ] Focus-topic pinning mechanism (persist user-selected focus areas)
    - [ ] Boost chunks matching active focus topics
    - [ ] Support explicit "reset context" command to clear focus

---

**Phase 2 Success Criteria**:
- ✅ `/find` command works in web UI with filters
- ✅ Hybrid retrieval (dense + sparse) active
- ✅ Reranking improves top-5 result quality
- ✅ Search results show scores, timestamps, citations
- ✅ All tests pass (unit + integration)
- ✅ Recall@10 > 0.7 on golden query set

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
