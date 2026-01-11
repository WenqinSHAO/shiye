# Shiye Planning and Roadmap

## Current Snapshot (v0.8)

- **Hybrid retrieval shipped**: FAISS dense + SQLite FTS5 BM25 with RRF fusion, optional rerankers, multi-cue scoring, `/find` UI with debug panel.
- **Chunking shipped**: Token-aware chunking with structure preservation (header-aware, sentence-window, per-message, fixed-token) and strategy/version tracking.
- **Ingestion**: All document types set `chunk_strategy/chunk_version`; search hits carry heading/page/seq metadata and chunk windows for context.
- **Context assembly**: Chat flow uses `ContextPacker` with cached search policy; deeper neighbor expansion is available as a helper but not yet wired into the packer.
- **Migration tool**: `scripts/migrate_v08.py` re-chunks legacy documents, fills metadata, and refreshes FAISS (embedder required).
- **Schema v3**: heading_path, page_number, parent_doc_seq on chunks; chunk_strategy/chunk_version on documents.
- **Tests**: See `tests/`; run with `PYTHONPATH=. workon dspytest && pytest`.

## Chunking & Context (v0.8)

**Delivered**
- Pluggable chunkers with token-aware sizing (FixedToken, HeaderAware, SentenceWindow, Message)
- Schema v3: heading_path/page_number/parent_doc_seq on chunks; chunk_strategy/chunk_version on documents
- Complete ingestion coverage: all document types use appropriate chunkers
- Retrieval wiring: chunk_window populated; heading/page/seq surfaced in UI; chat flow uses ContextPacker
- Migration tool: scripts/migrate_v08.py for backfilling legacy documents (with tests)

## Near-Term Backlog

- Move orchestrator/chat context to `workspace.search()` + `ContextPacker`, retiring `recall()`.
- Build golden-query evaluation harness (queries + expected hits, Recall@K/MRR reporting).
- Surface FTS5 availability in the UI (banner/system message) instead of logs only.
- Add safe rebuild path for FAISS when embedding dimensions change.
- Add CHANGELOG.md and richer docstrings for retrieval/storage helpers.

## Later Roadmap (v0.9+)

- Extended ingest: PDF/EPUB/email/zip/bulk URL import with dedup and progress tracking.
- Tool execution (v1.0): sandboxed Python runner with audit logs and allowlisted deps.
- Proactive/timeline features: timeline view, natural-language date filters, reminders, focus-topic pinning.
- Multi-device direction: encrypted/optional sync once local-first stability is solid.
