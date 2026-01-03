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

**Goals**
- Token-aware chunking that fits `all-MiniLM-L6-v2` (~256 wordpiece tokens).
- Structure-first splits for notes/web/papers with stable provenance (heading paths, page numbers, sequence ids, offsets).
- Minimal overlap; rely on neighbor expansion when building context.
- Keep hybrid retrieval efficient by avoiding near-duplicate embeddings.

**Defaults**
- `chunk_size`: 240–300 tokens measured by tokenizer.
- `overlap`: 0–1 sentence or ~10–30 tokens; avoid >15% overlap.
- Neighbor expansion: fetch `seq ±1` (or ±2 for long-form) when assembling context instead of indexing heavy overlap.

**Doc-type strategies**
- `chat`: chunk per message plus optional 3–7 turn windows for dense search; no token overlap.
- `note` (markdown): header-aware segmentation, then token windows inside each section; store `heading_path`.
- `web_page`: heading-aware (HTML/Unstructured by-title fallback), boilerplate stripped; store URL/title/heading path.
- `paper`: sentence grouping targeting ~260 tokens; keep page/section markers; optional semantic chunking mode for noisy docs.
- `rss_daily_summary`: typically single chunk; no overlap.

**Implementation tasks**
- [ ] Introduce a pluggable chunker abstraction (fixed-token, header-aware, sentence-window, optional semantic) that returns chunks with char offsets, sequence/heading/page metadata.
- [ ] Wire ingestion paths (chat, note, web_page, paper, RSS) to use the chunker and populate `chunk_window`/heading/page metadata where available.
- [ ] Update context building to support neighbor expansion using `seq` and to keep provenance for citations.
- [ ] Add tokenizer-based length checks and tests covering chunk size and realized overlap per doc type.
- [ ] Document configuration knobs (default chunk size, overlap, neighbor expansion) and expose safe defaults.
- [ ] Migration plan for existing content: version chunking configs, add a background “rechunk + re-embed + reindex” job for old chunks without chunk metadata, and allow partial reruns when defaults change (only reprocess affected doc types/versions).

## Retrieval Flow (current)

Query → Parse filters → Dense + Sparse retrieval → RRF fusion → Optional rerank → Post-process (recency/type/exact/dedup) → `SearchHit` → (optional) `ContextPacker` for LLM budget

## Debugging & Testing

- Web: enable the Debug toggle and run `/find <query>`; see `WEB_DEBUG_GUIDE.md` or `DEBUG_RETRIEVAL_GUIDE.md` for screenshots and troubleshooting.
- Tests: `python -m pytest tests/ -v` (includes retrieval and storage coverage).

## References

SQLite FTS5, FAISS, FlashRank; additional pointers live in the debug guides.
