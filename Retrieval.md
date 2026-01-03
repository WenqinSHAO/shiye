# Enhanced Retrieval Design for Shiye

Status: v0.7 hybrid retrieval is shipped; focus now shifts to chunking quality and context assembly.

---

## Retrieval Snapshot (v0.7)

- Hybrid pipeline: FAISS dense search + SQLite FTS5 BM25 fused with RRF; optional FlashRank/BGE rerankers; top-k and RRF constants configurable in `config.py`.
- Multi-cue scoring: recency boost, document-type preferences, exact-match boost, and deduplication to keep the best chunk per document.
- Schema/provenance: chunks carry `char_start`, `char_end`, `embedding_model`; `chunk_window` column exists but is empty today.
- Interfaces: `/find` web command with filters and debug panel; `workspace.search()` returns `SearchHit` objects with `score_history`; `ContextPacker` available for token-budgeted context (not yet wired into chat flow).
- Storage: idempotent migrations, soft deletes, and FAISS index syncing keep retrieval consistent.

### Code Entry Points

- `storage.py`: `search()`, `_dense_retrieval()`, `_sparse_retrieval()`, `_fuse_rrf()`
- `retrieval.py`: `SearchRequest` / `Candidate` / `SearchHit`, post-processors (Recency/Type/ExactMatch/Deduplicator), `FlashRankReranker`, `ContextPacker`
- `workspace.py`: `search()` wiring and chunk/document fetch
- `web.py`: `handle_search()` and UI debug panel
- `config.py`: `SHIYE_SEARCH_TOP_K`, `SHIYE_RRF_K`, `SHIYE_RERANKER`, `SHIYE_RERANK_TOP_K`, `SHIYE_RECENCY_DECAY_DAYS`

## Current Gaps

- `chunk_window` is populated during ingestion but not yet used in retrieval UI display.
- Chat flow still calls the older recall path; `ContextPacker` and the new search pipeline are not in the LLM orchestration loop.
- No golden-query evaluation harness for regression.
- UI notice for missing FTS5 is still absent (only logs).

## Completed: Chunking & Context for v0.8 ✅

**Status**: Production-ready for notes and chat

**What's Working**
- ✅ **Ingestion**: Notes use header-aware chunking (>200 chars with headers), chat uses per-message with cumulative offsets
- ✅ **Storage**: chunk_strategy, chunk_version, heading_path, page_number, parent_doc_seq populated
- ✅ **Retrieval**: get_note() reconstructs full content from all chunks
- ✅ **FAISS**: Proper cleanup on note updates (no stale embeddings)
- ✅ **UI**: Search results show chunk location badges
- ✅ **Tests**: 27 tests covering all chunking strategies and end-to-end workflows
- ✅ **Chunkers**: FixedTokenChunker, HeaderAwareChunker, SentenceWindowChunker, MessageChunker
- ✅ **Context assembly**: expand_chunks_with_neighbors(), build_chunk_window(), get_chunk_provenance()

**Design**
- Minimal overlap (0-30 tokens) during chunking
- Neighbor expansion deferred to retrieval time (efficient index, coherent context)
- Per-message chat (simpler than turn windows, works well)
- Character heuristic (chars/4 ≈ tokens) enables offline operation

See CHUNKING_GUIDE.md for detailed strategies and PR_REVIEW_RESPONSE.md, SECOND_REVIEW_RESPONSE.md, THIRD_REVIEW_RESPONSE.md for implementation details.

## Next: Retrieval Pipeline Integration

**Goals**
- Wire context_assembly helpers into retrieval/UI path
- Surface chunk metadata (heading_path, page, seq) in search results
- Enable neighbor expansion in ContextPacker for richer LLM context

**Tasks**
- [ ] Update ContextPacker to use expand_chunks_with_neighbors() for adjacent chunk context
- [ ] Populate chunk_window during search result assembly (via build_chunk_window)
- [ ] Update SearchHit display to show chunk windows and location info
- [ ] Ensure metadata flows: storage → workspace.search() → UI
- [ ] Add tests for neighbor expansion in retrieval pipeline
- [ ] Wire new search pipeline into chat orchestrator (replace recall)

## Retrieval Flow (current)

Query → Parse filters → Dense + Sparse retrieval → RRF fusion → Optional rerank → Post-process (recency/type/exact/dedup) → `SearchHit` → (optional) `ContextPacker` for LLM budget

## Debugging & Testing

- Web: enable the Debug toggle and run `/find <query>`; see `WEB_DEBUG_GUIDE.md` or `DEBUG_RETRIEVAL_GUIDE.md` for screenshots and troubleshooting.
- Tests: `python -m pytest tests/ -v` (includes retrieval and storage coverage).

## References

SQLite FTS5, FAISS, FlashRank; additional pointers live in the debug guides.
