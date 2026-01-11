# Enhanced Retrieval Design for Shiye

Status: v0.8 hybrid retrieval and chunked ingestion are live. Search uses dense + sparse + RRF with optional rerank, chunks carry structure metadata, and chat flow can reuse cached search context with token-aware packing. A migration script backfills legacy documents.

---

## Retrieval Snapshot (v0.8)

- Hybrid pipeline: FAISS dense search + SQLite FTS5 BM25 fused with RRF; optional FlashRank/BGE rerankers; top-k and RRF constants configurable in `config.py`.
- Multi-cue scoring: recency boost, document-type preferences, exact-match boost, and deduplication to keep the best chunk per document.
- Schema/provenance: chunks carry `char_start/char_end/heading_path/page_number/parent_doc_seq/embedding_model`; `chunk_window` is built on demand with ±1 neighbor for display/context.
- Interfaces: `/find` web command with filters and debug panel; `workspace.search()` returns `SearchHit` objects with `score_history`; `ContextPacker` packs search hits for the LLM.
- Storage: idempotent migrations, soft deletes, and FAISS index syncing keep retrieval consistent.
- Migration: `scripts/migrate_v08.py` re-chunks legacy documents, normalizes `chunk_strategy/chunk_version`, fills metadata, and refreshes FAISS embeddings.

### Code Entry Points

- `storage.py`: `search()`, `_dense_retrieval()`, `_sparse_retrieval()`, `_fuse_rrf()`
- `retrieval.py`: `SearchRequest` / `Candidate` / `SearchHit`, post-processors (Recency/Type/ExactMatch/Deduplicator), `FlashRankReranker`, `ContextPacker`
- `workspace.py`: `search()` wiring and chunk/document fetch
- `web.py`: `handle_search()` and UI debug panel
- `config.py`: `SHIYE_SEARCH_TOP_K`, `SHIYE_RRF_K`, `SHIYE_RERANKER`, `SHIYE_RERANK_TOP_K`, `SHIYE_RECENCY_DECAY_DAYS`

## Current Gaps

- No golden-query evaluation harness; no UI notice for missing FTS5 builds.
- Neighbor expansion beyond the lightweight `chunk_window` is available (`context_assembly.expand_chunks_with_neighbors`) but not yet wired into the chat packer.
- FAISS rebuild on embedding-dimension change still relies on manual cleanup (index mismatch logs a warning and disables dense search).

## Chunking & Context (v0.8) – Where We Are

**Working**
- All document types: chunked ingestion with appropriate chunkers (HeaderAware/SentenceWindow/FixedToken/Message).
- Notes: UI note endpoints use header-aware chunking by default; cumulative offsets; FAISS cleanup on updates.
- Chat: per-message chunks with cumulative offsets; search + ContextPacker with intelligent policy (skip/reuse/search based on intent).
- Schema/metadata: heading_path, page_number, parent_doc_seq, chunk_strategy, chunk_version populated.
- Context assembly: `build_chunk_window` provides ±1 neighbor for results; `ContextPacker` enforces token budgets for the LLM.
- Retrieval: chunk_window populated during search; heading/page/seq in SearchHit and UI; neighbor-aware context in LLM.
- Migration: `scripts/migrate_v08.py` re-chunks/re-embeds legacy docs with normalized strategies.
- Tests: retrieval, chunking, ingestion, and migration covered in `tests/`.

**Still to do**
- Expand neighbor-aware context beyond simple chunk windows.
- Decide and document chat chunking policy (per-message default vs optional turn windows).
- Optional refinements: improve search policy heuristics, add alignment filter post-fusion.

## Retrieval Flow (current)

Query → Parse filters → Dense + Sparse retrieval → RRF fusion → Optional rerank → Post-process (recency/type/exact/dedup) → `SearchHit` → (future) neighbor expansion + `ContextPacker` for LLM budget

## Debugging & Testing

- Web: enable the Debug toggle and run `/find <query>`; see `WEB_DEBUG_GUIDE.md` or `DEBUG_RETRIEVAL_GUIDE.md` for screenshots and troubleshooting.
- Tests: `python -m pytest tests/ -v` (includes retrieval and storage coverage).

## References

SQLite FTS5, FAISS, FlashRank; additional pointers live in the debug guides.
