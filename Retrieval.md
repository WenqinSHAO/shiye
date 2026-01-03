# Enhanced Retrieval Design for Shiye

Status: v0.7 hybrid retrieval is shipped; chunking v0.8 is partly integrated (notes + chat), but retrieval/context wiring and ingestion coverage remain open.

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

- ~~All v0.8 retrieval/UI wiring complete: chunk_window, ContextPacker with neighbors, search policy~~.
- ~~Chat flow now uses search + ContextPacker with intelligent policy (skip/reuse/search)~~.
- ~~Ingestion coverage: all document types use chunkers and set chunk_strategy/chunk_version~~.
- ~~Note endpoints: UI notes automatically use chunked saves (header-aware)~~.
- No migration/backfill tooling for legacy documents (pre-v0.8).
- No golden-query evaluation harness; no UI notice for missing FTS5.

## Chunking & Context (v0.8) – Where We Are

**Working**
- All document types: chunked ingestion with appropriate chunkers (HeaderAware/SentenceWindow/FixedToken/Message).
- Notes: UI note endpoints use header-aware chunking by default; cumulative offsets; FAISS cleanup on updates.
- Chat: per-message chunks with cumulative offsets; search + ContextPacker with intelligent policy (skip/reuse/search based on intent).
- Schema/metadata: heading_path, page_number, parent_doc_seq, chunk_strategy, chunk_version populated.
- Context assembly: ContextPacker uses chunk_window (includes neighbors); search policy prevents unconditional searches.
- Retrieval: chunk_window populated during search; heading/page/seq in SearchHit and UI; neighbor-aware context in LLM.
- Tests: 47+ tests pass; chunking, ingestion, context assembly, and search policy validated.

**Still to do for v0.8**
- ~~All retrieval, ingestion, context assembly, and search policy items~~ (done in current PR).
- Provide migration/backfill tooling to re-chunk/re-embed legacy docs and fill strategy/heading metadata.
- Decide and document chat chunking policy (per-message default vs optional turn windows).
- Optional refinements: improve search policy heuristics, add alignment filter post-fusion.

## Retrieval Flow (current)

Query → Parse filters → Dense + Sparse retrieval → RRF fusion → Optional rerank → Post-process (recency/type/exact/dedup) → `SearchHit` → (future) neighbor expansion + `ContextPacker` for LLM budget

## Debugging & Testing

- Web: enable the Debug toggle and run `/find <query>`; see `WEB_DEBUG_GUIDE.md` or `DEBUG_RETRIEVAL_GUIDE.md` for screenshots and troubleshooting.
- Tests: `python -m pytest tests/ -v` (includes retrieval and storage coverage).

## References

SQLite FTS5, FAISS, FlashRank; additional pointers live in the debug guides.
