# Shiye Planning and Roadmap

## Current Snapshot (v0.7)

- Hybrid retrieval shipped: FAISS dense + SQLite FTS5 BM25 with RRF fusion, optional rerankers, multi-cue scoring, `/find` UI with debug panel.
- Data types: chat, note, web_page, paper, rss_daily_summary with SQLite + FAISS storage; note-taking UI, web fetcher, RSS summaries.
- Provenance fields (`char_start`, `char_end`, `embedding_model`) are populated; `chunk_window` exists but unused.
- Tests cover retrieval and storage, but golden-query evaluation is still missing.
- Orchestrator chat flow still uses the older recall path rather than the new search + ContextPacker.

## Completed: Chunking & Context for v0.8 ✅

**Status**: Complete - Production-ready for notes and chat

**Delivered**
- [x] Pluggable chunker abstraction: FixedTokenChunker, HeaderAwareChunker, SentenceWindowChunker, MessageChunker
- [x] Token-aware measurement with character fallback (works without network)
- [x] Context assembly module: neighbor expansion, provenance tracking, chunk windows
- [x] Schema migration v3: heading_path, page_number, parent_doc_seq, chunk_strategy, chunk_version
- [x] **Notes ingestion**: save_note_chunked() with header-aware chunking (>200 chars with headers)
- [x] **Chat ingestion**: add_messages() with per-message chunking and cumulative offsets
- [x] **Note retrieval**: get_note() reconstructs full content from multiple chunks
- [x] **FAISS management**: Proper cleanup of old embeddings on note updates
- [x] UI: Search results display chunk location badges (heading path, page, sequence)
- [x] Tests: 27 tests (19 unit + 8 integration), all passing
- [x] Documentation: CHUNKING_GUIDE.md, implementation summaries, review responses

**Design Decisions**
- Minimal overlap (0-30 tokens) during chunking; neighbor expansion deferred to retrieval time
- Per-message chat (not turn windows) - simpler, works well for current use cases
- Character heuristic (chars/4 ≈ tokens) enables operation without network access

**Next PR: Retrieval Pipeline Integration**
- [ ] Populate chunk_window field during retrieval
- [ ] Wire expand_chunks_with_neighbors() into ContextPacker
- [ ] Update search UI to show expanded context with chunk windows
- [ ] Ensure heading_path/page_number/seq flow through SearchHit to UI
- [ ] Add tests for neighbor expansion in retrieval

**Future Work (Separate PRs)**
- [ ] Web/paper/RSS ingestion with appropriate chunkers (HeaderAware, SentenceWindow)
- [ ] Migration tooling: backfill/rechunk command for legacy documents
- [ ] Optional: MessageChunker turn windows (if retrieval benefits proven)
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
