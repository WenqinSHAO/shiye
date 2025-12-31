# Shiye Planning and Roadmap

## Project Vision

**Value Promise**: Personal "off-brain" memory plus on-demand micro-automation, grounded in your own data.

**Target Personas**:
- Power researcher (fast ingest/recall)
- Workflow hacker (ad-hoc tools)
- Inbox triager (cleanup + reminders)

## Current Status (v0)

### ✅ Completed Features

#### Core Infrastructure
- [x] Web-based UI with FastAPI (terminal UI deprecated)
- [x] Multi-round chat with DSPy backend
- [x] Local SQLite + FAISS storage under `~/.shiye`
- [x] Sentence transformer embeddings (all-MiniLM-L6-v2)
- [x] Configurable via environment variables

#### Data Model
- [x] Unified document schema (type, timestamps, tags, source)
- [x] Multiple document types: chat, note, web_page, paper, rss_daily_summary
- [x] Timestamp system: created_at, event_at, ingested_at (UTC ISO format)
- [x] Soft deletion for chunks with FAISS sync

#### Ingest Capabilities
- [x] Web URL fetching via `/add fetch <url>`
- [x] URL reference storage via `/add refs <urls>`
- [x] ArXiv paper metadata extraction
- [x] RSS feed aggregation with daily digest
- [x] Note-taking mode with markdown support
- [x] Image handling in notes

#### UI Features
- [x] Chat interface with history
- [x] 3-panel note editor (`/note` command)
- [x] Markdown rendering with math support (MathJax)
- [x] Image uploads and display
- [x] Command system: /note, /add, /rss, /summarize, /clear

#### Storage & Retrieval
- [x] SQLite schema for documents, chunks, events
- [x] FAISS vector index for semantic search
- [x] Basic retrieval with context assembly
- [x] Time-aware chunking and timestamps

### 🚧 Known Limitations

1. **No active semantic search UI**: Semantic recall exists in backend but not exposed as first-class UI action
2. **Limited error handling**: Backend LLM failures not always gracefully surfaced to UI
3. **No draft recovery**: Unsaved notes can be lost on browser issues
4. **Single LLM provider**: Only Deepseek currently supported
5. **No multi-device sync**: Local-only storage
6. **Limited ingest formats**: Only URLs and text; no PDFs, EPUBs, emails yet
7. **No tool execution**: Code generation/execution not implemented
8. **No proactive reminders**: No timeline-based notifications

## Next Steps (Prioritized)

### Phase 1: Polish Current Features (v0.5)
**Goal**: Make existing features production-ready

1. **UI Resilience**
   - [ ] Surface backend errors in web UI (LLM failures, storage issues)
   - [ ] Add draft recovery for notes (localStorage backup)
   - [ ] Improve loading states and error messages
   - [ ] Add toast notifications for background operations

2. **Semantic Search UX**
   - [ ] Add dedicated search interface in web UI
   - [ ] Expose semantic search as first-class action (not just implicit in context)
   - [ ] Add filters: date range, document type, tags
   - [ ] Show search results with relevance scores

3. **Note Improvements**
   - [ ] Add search/filter in note list
   - [ ] Implement autosave with conflict resolution
   - [ ] Add note tagging and categorization
   - [ ] Export notes to markdown files

### Phase 2: Enhanced Retrieval (v0.7)
**Goal**: Better context assembly and grounding

1. **Multi-cue Retrieval**
   - [ ] Combine semantic + time filters + focus topics
   - [ ] Implement reranking with combined scores
   - [ ] Add focus-topic pinning mechanism
   - [ ] Support explicit "reset context" command

2. **Timeline Features**
   - [ ] Timeline view for stored content
   - [ ] Filter by date range in chat
   - [ ] Show temporal context in responses
   - [ ] Better event_at extraction from content

### Phase 3: Extended Ingest (v0.9)
**Goal**: Support more content types

1. **File Support**
   - [ ] PDF ingestion with text extraction
   - [ ] EPUB/MOBI support for books
   - [ ] Image OCR for scanned documents
   - [ ] Email import (mbox, EML formats)

2. **Batch Operations**
   - [ ] Bulk URL import
   - [ ] Folder/zip file ingestion
   - [ ] Deduplication across imports
   - [ ] Progress tracking for large imports

### Phase 4: Tool Execution (v1.0)
**Goal**: Enable micro-automation

1. **Safe Execution**
   - [ ] Sandboxed Python code execution
   - [ ] Allowlisted packages only
   - [ ] Project-local file access
   - [ ] Audit log for tool runs

2. **Tool Management**
   - [ ] Store tool definitions in database
   - [ ] Version control for tools
   - [ ] Share tools between sessions
   - [ ] Tool discovery and suggestions

### Phase 5: Multi-Model Support (v1.2)
**Goal**: Flexible LLM backends

1. **Provider Abstraction**
   - [ ] Abstract LLM interface in orchestrator
   - [ ] Support OpenAI, Anthropic, local models
   - [ ] Model routing (cheap vs smart)
   - [ ] Fallback chains for reliability

2. **Offline Mode**
   - [ ] Local model support (llama.cpp, ollama)
   - [ ] Graceful degradation without API
   - [ ] Offline-first features

### Future (v2.0+)

1. **Proactive Features**
   - [ ] Timeline-based reminders
   - [ ] Suggested actions from commitments
   - [ ] Optional push notifications
   - [ ] Smart scheduling assistance

2. **Multi-device & Sync**
   - [ ] Encrypted sync between devices
   - [ ] Peer-to-peer sync option
   - [ ] Conflict resolution
   - [ ] Selective sync (tags, date ranges)

3. **Advanced Features**
   - [ ] WeChat reading history import
   - [ ] Browser history integration
   - [ ] Email monitoring and triage
   - [ ] Meeting notes and action items

## Architecture Decisions

### Storage: SQLite + FAISS

**Choice**: SQLite for metadata + FAISS for embeddings (v0)

**Rationale**:
- Simple, file-based, good locality
- Full control over schema and migrations
- Portable and Python-friendly
- FAISS GPU optional for scaling
- More wiring needed but flexible

**Alternatives Considered**:
- **Chroma**: Batteries-included but heavier, less control
- **LanceDB**: Good columnar performance but smaller ecosystem

### Data Flow

**Insertion**:
1. Write document + chunk rows to SQLite
2. Assign embedding_id (chunk id or UUID)
3. Generate embeddings and upsert to FAISS
4. Persist FAISS index to disk
5. Record sync timestamp

**Retrieval**:
1. Gather candidates via metadata filters (SQL)
2. Semantic search in FAISS for top-k
3. Merge results with combined scoring
4. Fetch full context from SQLite

**Updates/Deletes**:
- Soft-delete chunks in SQLite
- Maintain pending_delete list for FAISS
- Rebuild index periodically for cleanup

### LLM Integration

**Current**: DSPy with Deepseek

**Future**: Abstract provider interface supporting:
- OpenAI GPT-4
- Anthropic Claude
- Local models (Ollama, llama.cpp)
- Custom endpoints

## Design Principles

1. **Local-first**: User owns their data, no cloud required
2. **Privacy-focused**: Clear data boundaries, optional encryption
3. **Minimal dependencies**: Lean core, optional extensions
4. **Progressive enhancement**: Work offline, enhance online
5. **Extensible architecture**: Plugin system for adapters and tools

## Open Questions

1. **How to handle very large documents?**
   - Current: Chunk into smaller pieces
   - Consider: Hierarchical summarization, streaming ingestion

2. **How to prevent context overflow?**
   - Current: Simple top-k retrieval
   - Consider: Dynamic context budget, importance scoring

3. **How to handle conflicting information?**
   - Current: Show all retrieved chunks
   - Consider: Timestamp-based resolution, user preferences

4. **How to organize focus topics?**
   - Current: Ad-hoc tags
   - Consider: Hierarchical tags, auto-discovery, topic modeling

## RSS Feed Configuration

Default feeds (edit `rss_feeds.txt` to customize):
- Google AI Research Blog
- OpenAI Blog
- DeepMind Blog
- Microsoft Research Blog

**Selection Strategy**:
- Fetch latest items per feed
- Dedup by link/title hash
- Cap per feed (3 items) and total (20 items)
- Filter obvious promos/short posts
- Generate concise summary with references

**Storage**: Daily summaries stored as `rss_daily_summary` documents with feed metadata and keywords.

## Contributing

See README.md for contribution guidelines. Key areas where help is welcome:

- UI/UX improvements
- Additional ingest formats
- LLM provider integrations
- Performance optimizations
- Documentation and examples

## References

- [DSPy Documentation](https://dspy-docs.vercel.app/)
- [FAISS Wiki](https://github.com/facebookresearch/faiss/wiki)
- [FastAPI Documentation](https://fastapi.tiangolo.com/)
- [Sentence Transformers](https://www.sbert.net/)
