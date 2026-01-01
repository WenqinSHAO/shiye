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

### Configuration

Environment variables:
- `SHIYE_DATA_DIR` - Data directory (default: `~/.shiye`)
- `SHIYE_HOST` - Server host (default: `127.0.0.1`)
- `SHIYE_PORT` - Server port (default: `8000`)
- `SHIYE_RELOAD` - Enable auto-reload (default: `false`)
- `SHIYE_EMBED_MODEL` - Embedding model (default: `sentence-transformers/all-MiniLM-L6-v2`)
- `SHIYE_MODEL_CACHE` - Model cache directory (default: `~/.shiye/models`)
- `SHIYE_RERANKER` - Reranker to use: `flashrank` (default), `bge`, or `none`
- `SHIYE_SEARCH_TOP_K` - Number of results to return (default: `20`)
- `SHIYE_RECENCY_DECAY_DAYS` - Days for recency boost decay (default: `30`)
- `SHIYE_DEBUG_RETRIEVAL` - Enable debug logging for retrieval pipeline (default: `false`)

## Features

### Core Capabilities

- **Multi-round Chat**: Conversational interface with DSPy-powered LLM backend
- **Persistent Memory**: Local SQLite database + FAISS vector embeddings for semantic search
- **Hybrid Retrieval**: Dense (FAISS) + Sparse (FTS5) search with RRF fusion and optional reranking
- **Note Taking**: Rich markdown editor with image support and math rendering
- **Web Content Fetching**: Extract and store content from URLs
- **RSS Feed Aggregation**: Daily summaries from configured feeds
- **Time-Aware Context**: Automatic timestamp handling and temporal reasoning

### Enhanced Retrieval (v0.7)

Shiye now features a powerful hybrid search system that combines:

1. **Dense Retrieval**: FAISS-based semantic search using sentence embeddings
2. **Sparse Retrieval**: SQLite FTS5 keyword search with BM25 scoring
3. **Reciprocal Rank Fusion (RRF)**: Intelligent combination of dense and sparse results
4. **Cross-Encoder Reranking**: Optional FlashRank reranking for improved top-k accuracy
5. **Multi-Cue Scoring**: Recency boost, document type preference, exact match detection
6. **Smart Deduplication**: Keeps only the best chunk per document

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

**In Web UI** (recommended for visual debugging):

1. **Enable the debug toggle** in the header (checkbox labeled "Debug")
2. **Use the `/find` command** as normal:
   ```
   /find kubernetes container
   ```

This displays an interactive debug panel showing:
- Queries sent to dense (FAISS) and sparse (FTS5) retrievers
- Number of results at each pipeline stage
- RRF fusion statistics
- Whether reranking was applied
- Post-processors that were run
- **Top 5 candidates with complete score evolution**:
  - `dense`: FAISS semantic similarity score
  - `sparse`: BM25 keyword matching score
  - `fused`: Reciprocal Rank Fusion combined score
  - `rerank`: Cross-encoder reranking score (if enabled)
  - Boost multipliers from post-processors (recency, type, exact match)
  - `final`: Final score after all processing
- Text previews for each candidate

The debug panel is collapsible by clicking on the header.

**Example** (with debug toggle enabled):
```
/find kubernetes container orchestration
```

**Output:**
```
Found 5 results

[Standard search results display here]

🔍 Debug Info (click to toggle)
├─ Query & Filters
│  Query: kubernetes container orchestration
│  Filters: {}
├─ Retrieval Pipeline
│  Dense (FAISS): 500 results → 67 after filtering
│  Sparse (FTS5): 23 results  
│  RRF Fusion: 82 unique candidates
│  Reranked: Yes (top 50)
│  Post-processors: RecencyBooster, TypeBooster, ExactMatchBooster, Deduplicator
│  Final: 5 results
└─ Top Candidates Score Evolution
   #1 - Chunk 142 (doc 45, note)
   Final Score: 1.7454
   Score Evolution:
     dense: 0.7892    ← FAISS semantic similarity
     sparse: 0.6891   ← BM25 keyword match
     fused: 0.0298    ← RRF combined
     rerank: 0.9234   ← Cross-encoder rerank
     recency_boost: 1.05
     type_boost: 1.2
     exact_match_boost: 1.5
     final: 1.7454
   Preview: Kubernetes container orchestration allows...
```

See [WEB_DEBUG_GUIDE.md](WEB_DEBUG_GUIDE.md) for detailed examples and troubleshooting tips.

**In Terminal** (for detailed logging):

Set the environment variable before starting the application:

```bash
export SHIYE_DEBUG_RETRIEVAL=true
python main.py
```

When enabled, the system will display detailed information about:
- Queries sent to dense (FAISS) and sparse (FTS5) retrievers
- Number of results at each stage (FAISS search, filtering, RRF fusion)
- Scores for each candidate at every processing stage
- Top-5 candidates with scores after each stage
- Complete score history for final results

This is particularly useful for understanding why certain results rank higher and for tuning retrieval parameters.

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
