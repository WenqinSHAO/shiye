# Shiye Planning and Roadmap

## Current Snapshot (v0.8)

- **Hybrid retrieval shipped**: FAISS dense + SQLite FTS5 BM25 with RRF fusion, optional rerankers, multi-cue scoring, `/find` UI with debug panel.
- **Intelligent chunking shipped**: Token-aware chunking with structure preservation (header-aware, sentence-window, per-message, fixed-token).
- **Complete ingestion**: All document types use appropriate chunkers and set chunk_strategy/chunk_version.
- **Context assembly**: ContextPacker with neighbor expansion and intelligent search policy integrated into chat flow.
- **Migration tool**: scripts/migrate_v08.py re-chunks legacy documents and updates FAISS index.
- **Schema v3**: heading_path, page_number, parent_doc_seq on chunks; chunk_strategy/chunk_version on documents.
- **Tests**: 82/84 passing, including full migration coverage.

## Chunking & Context (v0.8) – Completed ✅

**All features delivered:**
- ✅ Pluggable chunkers with token-aware sizing (FixedToken, HeaderAware, SentenceWindow, Message)
- ✅ Schema v3: heading_path/page_number/parent_doc_seq on chunks; chunk_strategy/chunk_version on documents
- ✅ Complete ingestion coverage: all document types use appropriate chunkers
- ✅ Context assembly: ContextPacker with neighbor expansion integrated into chat flow
- ✅ Retrieval wiring: chunk_window populated; heading/page/seq surfaced in UI
- ✅ Migration tool: scripts/migrate_v08.py for backfilling legacy documents (with comprehensive tests)
- ✅ Chat policy: per-message chunking (turn windows available but optional)

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
