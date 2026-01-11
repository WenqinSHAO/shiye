# Shiye (师爷) - Personal AI Assistant

Shiye is a personal AI assistant that serves as your "off-brain" memory system with chat-based interaction, persistent storage, and micro-automation capabilities.

## Quick Start

### Prerequisites

- Python 3.10+
- Required API keys:
  - `DS_API_KEY` - Deepseek API key for LLM functionality

### Installation

```bash
# Clone the repository
git clone https://github.com/WenqinSHAO/shiye.git
cd shiye

# Install dependencies
pip install -r requirements.txt

# Set up environment variables
export DS_API_KEY="your-deepseek-api-key"

# Optional: customize data directory (default: ~/.shiye)
export SHIYE_DATA_DIR="/path/to/data"
export SHIYE_EMBED_MODEL="sentence-transformers/all-MiniLM-L6-v2"
```

### Running the Application

```bash
# Start the web server
python main.py

# Or use uvicorn directly with auto-reload
uvicorn web:app --reload --port 8000
```

Visit `http://localhost:8000` in your browser.

### Documentation Map

- **Retrieval pipeline**: [Retrieval.md](Retrieval.md)
- **Chunking strategies**: [CHUNKING_GUIDE.md](CHUNKING_GUIDE.md)
- **Debugging retrieval**: [WEB_DEBUG_GUIDE.md](WEB_DEBUG_GUIDE.md) (UI) and [DEBUG_RETRIEVAL_GUIDE.md](DEBUG_RETRIEVAL_GUIDE.md) (pipeline details)
- **Migration + scripts**: [scripts/README.md](scripts/README.md)
- **Roadmap / TODO**: [TODO.md](TODO.md)

### Configuration

Environment variables:
- `SHIYE_DATA_DIR` - Data directory (default: `~/.shiye`)
- `SHIYE_HOST` - Server host (default: `127.0.0.1`)
- `SHIYE_PORT` - Server port (default: `8000`)
- `SHIYE_RELOAD` - Enable auto-reload (default: `false`)
- `SHIYE_EMBED_MODEL` - Embedding model (default: `sentence-transformers/all-MiniLM-L6-v2`)
- `SHIYE_MODEL_CACHE` - Model cache directory (default: `~/.shiye/models`)
- `SHIYE_RERANKER` - Reranker to use: `flashrank` (default), `bge`, or `none`
- `SHIYE_SEARCH_TOP_K` - Number of results to return (default: `5`)
- `SHIYE_RERANK_TOP_K` - How many candidates to send to the reranker (default: `50`)
- `SHIYE_RRF_K` - Reciprocal rank fusion constant (default: `60`)
- `SHIYE_RECENCY_DECAY_DAYS` - Days for recency boost decay (default: `30`)
- `SHIYE_DEBUG_RETRIEVAL` - Enable debug logging for retrieval pipeline (default: `false`)

### Upgrading from v0.7

1. **Back up data**: `python scripts/backup_restore.py backup`
2. **Run the migration** (requires embeddings to be available):
   ```bash
   python scripts/migrate_v08.py --verbose        # migrate everything
   python scripts/migrate_v08.py --dry-run -v     # preview only
   python scripts/migrate_v08.py --doc-type note  # migrate a specific type
   ```
   The script re-chunks documents with the v0.8 strategies, sets `chunk_strategy/chunk_version`, writes `heading_path/page_number/parent_doc_seq`, and refreshes FAISS embeddings. See `scripts/README.md` for details.

## Features

### Core Capabilities

- **Multi-round Chat**: Conversational interface with DSPy-powered LLM backend
- **Persistent Memory**: Local SQLite database + FAISS vector embeddings for semantic search
- **Hybrid Retrieval**: Dense (FAISS) + Sparse (FTS5) search with RRF fusion and optional reranking
- **Note Taking**: Rich markdown editor with image support and math rendering
- **Web Content Fetching**: Extract and store content from URLs
- **RSS Feed Aggregation**: Daily summaries from configured feeds
- **Time-Aware Context**: Automatic timestamp handling and temporal reasoning

### Enhanced Retrieval & Chunking (v0.8)

Shiye v0.8 ships hybrid retrieval (dense + sparse + RRF + optional rerank) and token-aware chunking with strategy/version tracking. For the full design notes and configuration details, see [Retrieval.md](Retrieval.md) and [CHUNKING_GUIDE.md](CHUNKING_GUIDE.md).

**Search Filters**:
- `type:<doc_type>` - Filter by document type (note, web_page, chat, paper, rss_daily_summary)
- `tag:<tag>` - Filter by tag (future implementation)
- `before:<date>` - Items before date (YYYY-MM-DD format)
- `after:<date>` - Items after date (YYYY-MM-DD format)

### Web UI Commands

- **Chat**: Natural conversation with context from stored memories
- `/note` - Open 3-panel note-taking mode with markdown support
- `/find <query>` - Semantic search over stored content with filters
  - Basic search: `/find kubernetes networking`
  - Filter by type: `/find type:note kubernetes`
  - Filter by date: `/find after:2024-01-01 kubernetes`
  - Combine filters: `/find type:note after:2024-12-01 kubernetes`
  - **Debug mode**: Enable the debug checkbox in the header, then use `/find` to see detailed retrieval pipeline information
- `/add <text>` - Add notes or fetch URL content
  - `/add fetch <url>` - Fetch and store web page content
  - `/add refs <urls>` - Store URL references without fetching
- `/rss` - Generate daily RSS digest from configured feeds
- `/summarize` - Summarize current conversation context
- `/clear` - Clear on-screen chat log (doesn't affect storage)

### Debugging Retrieval

- Web UI: enable the **Debug** toggle and run `/find <query>` to see dense/sparse/RRF/rerank traces. The panel is collapsible. Full walkthroughs live in [WEB_DEBUG_GUIDE.md](WEB_DEBUG_GUIDE.md) and [DEBUG_RETRIEVAL_GUIDE.md](DEBUG_RETRIEVAL_GUIDE.md).
- Terminal: start with `SHIYE_DEBUG_RETRIEVAL=true python main.py` for verbose pipeline logs when tuning retrieval parameters.

### UI Tips

- Debug traces are enabled by default to surface LLM reasoning; uncheck the header toggle if you want quieter responses.
- In note mode, use the **Insert date/time** button in the editor panel to drop in a current timestamp.
- History view lazy-loads by day: it opens today by default, and you can expand other days/months on demand to keep the UI fast.

### Data Storage

**Location**: Local storage under `~/.shiye/` (configurable)
- `shiye.db` - SQLite database for metadata
- `shiye.faiss` - FAISS index for embeddings
- `models/` - Cached embedding models

**Document Types**:
- `chat` - Conversation logs
- `note` - Markdown notes with images
- `web_page` - Fetched URL content
- `paper` - ArXiv paper metadata
- `rss_daily_summary` - RSS feed digests

**Timestamps**:
- `created_at` - When content was stored (UTC ISO format)
- `event_at` - When content actually occurred (optional, user-provided)
- `ingested_at` - When document was added to storage

## Architecture

### Components

```
┌─────────────────────────────────────────────────┐
│              Web Interface (web.py)             │
│                 FastAPI + HTML/JS               │
└───────────────────┬─────────────────────────────┘
                    │
┌───────────────────┴─────────────────────────────┐
│         Orchestrator (orchestrator.py)          │
│         DSPy-based LLM coordination             │
└───────────────────┬─────────────────────────────┘
                    │
        ┌───────────┴───────────┐
        │                       │
┌───────┴────────┐    ┌─────────┴─────────┐
│   Workspace    │    │     Handlers      │
│ (workspace.py) │    │  (handlers.py)    │
└───────┬────────┘    └─────────┬─────────┘
        │                       │
        └───────────┬───────────┘
                    │
        ┌───────────┴───────────────────┐
        │                               │
┌───────┴────────┐            ┌─────────┴─────────┐
│  LocalStore    │            │    Fetcher        │
│  (storage.py)  │            │  (fetcher.py)     │
│  SQLite + Tags │            │  URL extraction   │
└───────┬────────┘            └───────────────────┘
        │
        ├─────────────────┬──────────────────┐
        │                 │                  │
┌───────┴────────┐ ┌──────┴─────────┐ ┌─────┴─────────┐
│  Vector Store  │ │  Embeddings    │ │  Data Types   │
│(vector_store.py)│ │(embeddings.py) │ │(datatypes.py) │
│  FAISS index   │ │  Transformers  │ │  Message, Role│
└────────────────┘ └────────────────┘ └───────────────┘
```

### Key Modules

- **web.py** - FastAPI application and HTTP endpoints
- **orchestrator.py** - LLM coordination with DSPy
- **workspace.py** - High-level memory operations interface
- **storage.py** - SQLite + FAISS persistence layer
- **handlers.py** - Command handlers (add, fetch, etc.)
- **fetcher.py** - URL content extraction
- **rss.py** - RSS feed aggregation
- **embeddings.py** - Sentence transformer embeddings
- **vector_store.py** - FAISS index management
- **datatypes.py** - Core data structures

## Development

### Running Tests

```bash
# Install test dependencies
pip install pytest

# Some fetcher tests require lxml html cleaning support
pip install lxml_html_clean

# Run all tests
python -m pytest tests/ -v

# Run specific test file
python -m pytest tests/test_storage.py -v
```

### Project Structure

```
shiye/
├── main.py              # Application entry point
├── web.py               # FastAPI web interface
├── orchestrator.py      # LLM orchestration
├── workspace.py         # Memory workspace
├── storage.py           # Data persistence
├── handlers.py          # Command handlers
├── fetcher.py          # Content fetching
├── rss.py              # RSS aggregation
├── embeddings.py       # Embedding provider
├── vector_store.py     # FAISS vector index
├── datatypes.py        # Core data types
├── config.py           # Configuration
├── tests/              # Test suite
├── assets/             # Static files (images, CSS, JS)
├── requirements.txt    # Python dependencies
├── README.md          # This file
└── TODO.md            # Planning and roadmap
```

### Adding Features

1. **Storage**: Extend `storage.py` for new document types
2. **Commands**: Add handlers in `handlers.py` and wire in `web.py`
3. **UI**: Update HTML/JS in `web.py` or add to `assets/`
4. **LLM**: Modify signatures and logic in `orchestrator.py`

## Vision and Roadmap

See [TODO.md](TODO.md) for detailed planning, architectural decisions, and future milestones.

### Long-term Goals

- **Unified Ingest**: Support for files, emails, WeChat exports, EPUBs, PDFs
- **Smart Retrieval**: Semantic + temporal search with focus-topic awareness
- **Tool Execution**: Safe code execution with audit trails
- **Multi-Model**: Pluggable LLM backends with routing
- **Privacy First**: Local-first with optional encrypted sync

## Contributing

Contributions welcome! Please:
1. Fork the repository
2. Create a feature branch
3. Add tests for new functionality
4. Ensure existing tests pass
5. Submit a pull request

## License

See LICENSE file for details.

## Acknowledgments

Built with:
- [DSPy](https://github.com/stanfordnlp/dspy) - LLM programming framework
- [FastAPI](https://fastapi.tiangolo.com/) - Web framework
- [FAISS](https://github.com/facebookresearch/faiss) - Vector similarity search
- [Sentence Transformers](https://www.sbert.net/) - Text embeddings
