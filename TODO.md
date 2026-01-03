# Shiye Planning and Roadmap

## Current Snapshot (v0.7)

- Hybrid retrieval shipped: FAISS dense + SQLite FTS5 BM25 with RRF fusion, optional rerankers, multi-cue scoring, `/find` UI with debug panel.
- Data types: chat, note, web_page, paper, rss_daily_summary with SQLite + FAISS storage; note-taking UI, web fetcher, RSS summaries.
- Provenance fields (`char_start`, `char_end`, `embedding_model`) are populated; `chunk_window` exists but unused.
- Tests cover retrieval and storage, but golden-query evaluation is still missing.
- Orchestrator chat flow still uses the older recall path rather than the new search + ContextPacker.

## Active Work: Chunking & Context (target v0.8)

**Goals**
- Token-aware chunking sized for `all-MiniLM-L6-v2` (~256 wordpiece tokens).
- Structure-first splitting for notes/web/papers; stable provenance (heading paths, pages, sequence ids, offsets).
- Minimal overlap; use neighbor expansion at context-build time to preserve coherence without ballooning the index.

**Plan**
- [ ] Introduce a pluggable chunker abstraction (fixed-token, header-aware, sentence-window, optional semantic) returning chunks with offsets + sequence/heading/page metadata.
- [ ] Define defaults: chunk_size 240–300 tokens, overlap 0–1 sentence (~10–30 tokens) with tokenizer-based measurement, neighbor expansion `seq ±1/2` when assembling context.
- [ ] Apply per type: chat = per message + optional 3–7 turn windows; note = header-aware then token windows; web_page = heading-aware with boilerplate stripped; paper = sentence grouping (~260 tokens) with page/section markers; rss_daily_summary = single chunk.
- [ ] Wire ingestion paths to use the chunker and populate `chunk_window`/heading/path metadata; ensure embeddings respect the chosen max length.
- [ ] Update context building to pull neighbor chunks by `seq`, and keep citations intact.
- [ ] Add tests for realized chunk size/overlap per doc type and document configuration knobs in docs.
- [ ] Migration strategy: version chunking configs, detect legacy chunks missing metadata, and offer a background “rechunk + re-embed + reindex” path (scoped by doc type/version) so strategy changes don’t force full rebuilds when unnecessary.

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
