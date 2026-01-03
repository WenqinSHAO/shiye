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

- `chunk_window` is unused; chunking is not standardized or token-aware for the MiniLM default.
- Chat flow still calls the older recall path; `ContextPacker` and the new search pipeline are not in the LLM orchestration loop.
- No golden-query evaluation harness for regression.
- UI notice for missing FTS5 is still absent (only logs).

## Active Plan: Chunking & Context (v0.8 target)

**Status**: In Progress - Core infrastructure complete

**Completed (v0.8)**
- ✅ Pluggable chunker abstraction with fixed-token, header-aware, and sentence-window strategies
- ✅ Token-aware measurement using embedding model tokenizer
- ✅ Context assembly module with neighbor expansion and provenance tracking
- ✅ Schema migration v3: heading_path, page_number, parent_doc_seq columns added
- ✅ UI enhancements: chunk metadata display in search results
- ✅ Comprehensive tests (19 tests passing)
- ✅ Documentation: CHUNKING_GUIDE.md created

**Remaining Work**
- [ ] Wire ingestion paths to use chunkers
- [ ] Update ContextPacker for neighbor expansion
- [ ] Add integration tests
- [ ] Migration strategy for existing chunks

See CHUNKING_GUIDE.md for detailed strategies per document type.

## Retrieval Flow (current)

Query → Parse filters → Dense + Sparse retrieval → RRF fusion → Optional rerank → Post-process (recency/type/exact/dedup) → `SearchHit` → (optional) `ContextPacker` for LLM budget

## Debugging & Testing

- Web: enable the Debug toggle and run `/find <query>`; see `WEB_DEBUG_GUIDE.md` or `DEBUG_RETRIEVAL_GUIDE.md` for screenshots and troubleshooting.
- Tests: `python -m pytest tests/ -v` (includes retrieval and storage coverage).

## References

SQLite FTS5, FAISS, FlashRank; additional pointers live in the debug guides.
