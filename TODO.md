# Shiye Planning and Roadmap

## Current Snapshot (v0.7)

- Hybrid retrieval shipped: FAISS dense + SQLite FTS5 BM25 with RRF fusion, optional rerankers, multi-cue scoring, `/find` UI with debug panel.
- Data types: chat, note, web_page, paper, rss_daily_summary with SQLite + FAISS storage; note-taking UI, web fetcher, RSS summaries.
- Provenance fields (`char_start`, `char_end`, `embedding_model`) are populated; `chunk_window` exists but unused.
- Tests cover retrieval and storage, but golden-query evaluation is still missing.
- Orchestrator chat flow still uses the older recall path rather than the new search + ContextPacker.

## Chunking & Context (v0.8) – Current Status

**Working now**
- Pluggable chunkers: FixedToken, HeaderAware, SentenceWindow, Message; token-aware sizing with char fallback.
- Schema v3: heading_path/page_number/parent_doc_seq on chunks; chunk_strategy/chunk_version on documents.
- Notes: `save_note_chunked()` header-aware chunking (auto-threshold), cumulative offsets, FAISS cleanup on update; `get_note()` reconstructs full content.
- Chat: `add_messages()` per-message chunks with cumulative offsets; chunk_strategy set on chat docs (including default chat).
- Tests: chunking + chunked ingestion suites pass (FAISS-dependent test skips if unavailable).
- Docs: CHUNKING_GUIDE.md and review response docs summarize design/decisions.

**Gaps to close v0.8**
- ~~Retrieval/UI wiring: `chunk_window` now populated via `build_chunk_window()` during search; heading/page/seq now surfaced in UI via SearchHit; ContextPacker wired into chat flow using search-based context~~.
- Ingestion coverage: web_page/paper/rss ingestion not using chunkers yet and don’t set chunk_strategy/chunk_version.
- Migration: no backfill/rechunk command for legacy documents (pre-strategy/pre-heading metadata).
- Chat policy: only per-message chunks; MessageChunker turn windows unused—decide default vs opt-in and document.

**Next PRs to finish v0.8**
- ~~All retrieval/UI wiring, chunked ingestion, context assembly, and search policy~~ (done in current PR).
- Provide a migration/backfill command to set strategy/version and re-chunk/re-embed legacy docs.
- Decide/document chat chunking policy (per-message default vs optional turn windows) and reflect in config/docs.
- Optional: Refine search policy heuristics based on usage patterns.
- Optional: Add alignment filter for post-fusion validation of query vs chunk relevance.
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
