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

- `chunk_window` never populated; neighbor expansion/helpers not used in retrieval or UI.
- Chat flow still calls the older recall path; `ContextPacker` is not in LLM orchestration.
- Ingestion coverage: only notes/chat use chunkers and set `chunk_strategy`/`chunk_version`; web_page/paper/rss paths remain unchunked/unversioned.
- No golden-query evaluation harness; no UI notice for missing FTS5.

## Chunking & Context (v0.8) – Where We Are

**Working**
- Notes: header-aware chunking (auto threshold), cumulative offsets, FAISS cleanup on updates; `get_note()` rebuilds full content.
- Chat: per-message chunks with cumulative offsets; chunk_strategy on chat docs (including default chat).
- Schema/metadata: heading_path, page_number, parent_doc_seq, chunk_strategy, chunk_version present.
- Chunkers available: FixedToken, HeaderAware, SentenceWindow, Message; token-aware sizing with char fallback.
- Context helpers exist: expand_chunks_with_neighbors(), build_chunk_window(), provenance utilities.
- Tests: chunking and chunked ingestion suites pass (FAISS-dependent test skips when unavailable).

**Still to do for v0.8**
- Populate `chunk_window` during retrieval and surface heading/page/seq in SearchHit/UI.
- Integrate neighbor expansion/ContextPacker into search and chat context assembly.
- Apply chunkers + chunk_strategy/version to web_page/paper/rss ingestion, with integration tests.
- Provide migration/backfill tooling to re-chunk/re-embed legacy docs and fill strategy/heading metadata.
- Decide and document chat chunking policy (per-message default vs optional turn windows).

## Retrieval Flow (current)

Query → Parse filters → Dense + Sparse retrieval → RRF fusion → Optional rerank → Post-process (recency/type/exact/dedup) → `SearchHit` → (future) neighbor expansion + `ContextPacker` for LLM budget

## Debugging & Testing

- Web: enable the Debug toggle and run `/find <query>`; see `WEB_DEBUG_GUIDE.md` or `DEBUG_RETRIEVAL_GUIDE.md` for screenshots and troubleshooting.
- Tests: `python -m pytest tests/ -v` (includes retrieval and storage coverage).

## References

SQLite FTS5, FAISS, FlashRank; additional pointers live in the debug guides.
