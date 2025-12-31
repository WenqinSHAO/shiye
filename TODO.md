# Shiye planning log

- **Value promise**: personal “off-brain” memory plus on-demand micro-automation, grounded in your own data.
- **Personas**: power researcher (fast ingest/recall), workflow hacker (ad-hoc tools), inbox triager (cleanup + reminders).

## End-state feature set (v1 vision)
- Unified ingest for files/URLs/feeds (web, WeChat/reading exports, PDFs/EPUBs, email), dedup + normalize with rich metadata (source, created_at, event_at, ingested_at, topics/tags, sensitivity).
- Retrieval + grounding: semantic + time-aware search; focus-topic pinning; explicit “reset context”; sessionless but topic-aware chat.
- Chat-as-OS: natural-language tool creation/execution with safety rails; results stored back into the corpus; audit trail for actions.
- Proactive cues: timely reminders and suggested next actions based on timeline + commitments; optional notifications.
- Multi-model: pluggable LLM backends with routing (cheap vs smart, local vs remote) and offline fallbacks.
- Privacy/ownership: local-first storage with optional encrypted sync and clear redaction controls.

## Near-term milestones (ordered)
1) Data spine: agree on unified document schema (type, timestamps, tags, source, sensitivity), choose local store (SQLite + embeddings via FAISS/Chroma/LanceDB), set up indices.
2) Ingest v0: web-first drop-in for URLs/text/notes via `/add` and note mode; chunk + embed; dedup by hash + title/url fingerprint; minimal adapters are fine.
3) Retrieval v0: semantic + time filter; focus-topic pinning; explicit “reset context” verb.
4) Chat loop v0.5: abstraction over LLM providers; ground responses with top-k retrieved + timeline snippets; context budgeting.
5) Logging & timeline: store every exchange as events; summarize sessions into the corpus; simple timeline query/view to test temporal reasoning.
6) Tool safety: sandboxed code execution path with allowlisted packages; capture outputs back into corpus; keep an audit log.
7) UX: web-first experience (chat + note shell + history); surface verbs inline; CLI/Textual deprecated.

## Architecture sketch
- Services: Ingest pipeline → Storage (docs + embeddings + events) → Retrieval/rerank → Orchestrator (LLM routing, tool exec, context assembly) → Interface layer (CLI/Web).
- Data model: Documents (source, type, timestamps, tags, sensitivity, status), Chunks (embedding, parent doc), Events (chat turns, tool runs), Focus state (topic pins, session hints).
- Extensibility: provider interfaces for LLMs and tools; ingestion adapters per source; execution runner abstraction (local python/js).

## Decisions (v0)
- Storage/sync: local-only for now. Future: multi-device without central cloud (e.g., browser-history sync, peer-to-peer).
- Ingestion v0 sources: local files, URLs (web pages), raw text (emails/notes), imported chat logs (e.g., ChatGPT).
- Proactivity: lightweight push reminders.
- Code execution: Python only, project-local file access.

## Current focus / next steps
- Finish web-only command surface (no CLI): `/note`, `/add`, `/rss`, `/summarize`, `/clear` (UI only); tighten history rendering and timestamps.
- Notes: polish image handling and math rendering; add search/filter in note list; autosave without collisions.
- Retrieval loop: reintroduce semantic recall in web UI as a first-class action (without legacy `/recall` command).
- Resilience: surface backend errors in UI (LLM failures, storage issues) and ensure unsaved draft recovery.

## Storage options: quick comparison
- SQLite + FAISS (or AnnLite): simple, file-based, good locality; you manage migrations and ANN config; portable; Python-first; FAISS GPU optional; more wiring needed (schema + ANN sync).
- Chroma: batteries-included vector DB; simpler API; runs local; metadata filters; but heavier deps and less control over storage layout; migration story still evolving.
- LanceDB: columnar + vector in one; good scan performance; simple APIs; local-friendly; smaller ecosystem and fewer examples; versioning support is nice.

Lean default: start with SQLite for metadata + FAISS for embeddings; wrap with a small repository layer so swapping later is low-friction.

## SQLite + FAISS mapping (for multi-cue recall)
- SQLite schema (draft):
  - `documents(id PK, source, uri, doc_type, created_at, event_at, ingested_at, title, tags JSON, sensitivity, hash, status)`
  - `chunks(id PK, document_id FK, seq, text, token_count, embedding_id, created_at, event_at, tags JSON, focus_hint, deleted BOOLEAN DEFAULT 0)`
  - `events(id PK, type, payload JSON, created_at, event_at, related_chunk_id)`
  - `focus_state(id PK=1, topics JSON, updated_at)`
- FAISS index:
  - Store vectors for `chunks.embedding_id` in a flat/IVF index on disk.
  - Keep index metadata (vector_dim, index_type, trained flag, path, last_sync_ts) in SQLite table `vector_index_meta`.
- Insertion flow:
  1) Write document + chunk rows; assign `embedding_id` = chunk id (or UUID) to be the FAISS key.
  2) After committing, upsert embeddings into FAISS keyed by `embedding_id`; persist index to disk; record sync time.
- Deletion/updates:
  - Soft-delete chunks in SQLite; maintain a `pending_delete` list for FAISS removals; periodically rebuild or apply deletes if index type supports it.
  - On re-embedding, bump `embedding_id` and mark old chunk deleted for FAISS cleanup.
- Multi-cue retrieval flow:
  1) Gather candidate chunk_ids by metadata filters (time range via `event_at`, tags, focus topics) in SQLite.
  2) Semantic search in FAISS: query embedding to get top-k chunk_ids.
  3) Merge: score candidates that pass metadata filters and appear in semantic hits; optionally rerank with combined score (semantic + recency + focus-topic boost).
  4) Fetch chunk + document context from SQLite for grounding.
- Resilience:
  - On mismatch between FAISS ids and SQLite, rebuild index from `chunks` where `deleted=0`.
  - Version and snapshot the FAISS file alongside SQLite backups.

## Micro-app: daily high-signal RSS brief (design)
- Feeds config: `rss_feeds.txt` lists trusted feeds (low-frequency changes). Initial set: Google Research, OpenAI, DeepMind, MSR.
- Command: `/rss` runs a daily brief on demand (later: scheduled). No auto pop-ups.
- Selection: fetch latest items per feed, dedup by link/title hash, cap per feed (e.g., 3) and total (e.g., 20). Drop obvious promos/too-short posts if possible.
- Summary: single LLM call to produce concise bullets with inline references (title + URL), aiming for low token use. Store the daily summary as a document (`doc_type=rss_daily_summary`, tags include feeds/date/item count/keywords).
- Storage: keep item metadata (title, url, published_at, feed) alongside the summary for traceability; no per-article embeddings unless explicitly archived.
- Archive later: `/add` could detect URLs and fetch content for archival; or provide an interactive prompt in `/rss` output to archive selected URLs (future).
- Topics/keywords (bootstrap bias): AI infra, LLM, AI coding, Agent/Agentic AI, machine learning, attention, memory. Longer-term: derive from user persona/usage.
