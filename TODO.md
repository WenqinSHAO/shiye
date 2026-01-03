# Shiye Planning and Roadmap

## Current Snapshot (v0.7)

- Hybrid retrieval shipped: FAISS dense + SQLite FTS5 BM25 with RRF fusion, optional rerankers, multi-cue scoring, `/find` UI with debug panel.
- Data types: chat, note, web_page, paper, rss_daily_summary with SQLite + FAISS storage; note-taking UI, web fetcher, RSS summaries.
- Provenance fields (`char_start`, `char_end`, `embedding_model`) are populated; `chunk_window` exists but unused.
- Tests cover retrieval and storage, but golden-query evaluation is still missing.
- Orchestrator chat flow still uses the older recall path rather than the new search + ContextPacker.

## Active Work: Chunking & Context (target v0.8)

**Status**: In Progress - Core infrastructure complete, integration ongoing

**Completed**
- [x] Pluggable chunker abstraction with FixedTokenChunker, HeaderAwareChunker, SentenceWindowChunker
- [x] Token-aware measurement with fallback to character approximation
- [x] Context assembly module with neighbor expansion and provenance tracking
- [x] Schema migration v3 with heading_path, page_number, parent_doc_seq columns
- [x] UI updates to display chunk metadata in search results
- [x] Comprehensive tests for all chunkers (19 tests passing)
- [x] Documentation: CHUNKING_GUIDE.md created

**Goals**
- Token-aware chunking sized for `all-MiniLM-L6-v2` (~256 wordpiece tokens).
- Structure-first splitting for notes/web/papers; stable provenance (heading paths, pages, sequence ids, offsets).
- Minimal overlap; use neighbor expansion at context-build time to preserve coherence without ballooning the index.

**Remaining Work**
- [ ] Wire ingestion paths to use chunkers (notes, web pages, papers, chat, RSS)
- [ ] Update ContextPacker to support neighbor expansion
- [ ] Add integration tests for chunked retrieval workflow
- [ ] Migration strategy for existing chunks
- [ ] Background rechunking job for legacy data
## Near-Term Backlog

- Move orchestrator/chat context to `workspace.search()` + `ContextPacker`, retiring `recall()`.
- Build golden-query evaluation harness (queries + expected hits, Recall@K/MRR reporting).
- Surface FTS5 availability in the UI (banner/system message) instead of logs only.
- Add CHANGELOG.md and richer docstrings for retrieval/storage helpers.

## Later Roadmap (v0.9+)

- Extended ingest: PDF/EPUB/email/zip/bulk URL import with dedup and progress tracking.
- Tool execution (v1.0): sandboxed Python runner with audit logs and allowlisted deps.
- Proactive/timeline features: timeline view, natural-language date filters, reminders, focus-topic pinning.
- Multi-device direction: encrypted/optional sync once local-first stability is solid.
