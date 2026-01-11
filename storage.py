import json
import re
import sqlite3

# Check if the standard sqlite3 has FTS5 support
_has_fts5 = False
try:
    _conn = sqlite3.connect(':memory:')
    _cur = _conn.cursor()
    _cur.execute('PRAGMA compile_options')
    _has_fts5 = any('FTS5' in row[0] for row in _cur.fetchall())
    _conn.close()
except Exception:
    pass

# If standard sqlite3 doesn't have FTS5, try pysqlite3
if not _has_fts5:
    try:
        import pysqlite3.dbapi2 as sqlite3  # FTS5-enabled SQLite
    except ImportError:
        pass  # Keep using standard sqlite3

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Dict, List, Optional, Sequence

import numpy as np

from config import DATA_DIR, DB_PATH, MODEL_NAME
from datatypes import Message, Role, ensure_utc
from chunking import Chunk, HeaderAwareChunker, count_tokens, _get_tokenizer
from embeddings import EmbeddingProvider
from vector_store import FaissIndex, faiss
from retrieval import SearchRequest, Candidate, Reranker, RecencyBooster, TypeBooster, ExactMatchBooster, Deduplicator

# Order to present score components in debug output and UI
SCORE_BREAKDOWN_ORDER = [
    "dense",
    "sparse",
    "exact",
    "fused",
    "rerank",
    "recency_boost",
    "type_boost",
    "exact_match_boost",
    "final",
]


@dataclass
class StoredChunk:
    id: int
    document_id: int
    text: str
    role: Role
    created_at: datetime
    reference_time: Optional[datetime]
    embedding_id: Optional[int]
    tags: Optional[dict]
    focus_hint: Optional[str]
    char_start: int = 0
    char_end: int = -1
    embedding_model: Optional[str] = None
    chunk_window: Optional[str] = None
    heading_path: Optional[str] = None  # v0.8 chunking metadata
    page_number: Optional[int] = None   # v0.8 chunking metadata
    parent_doc_seq: Optional[int] = None  # v0.8 chunking metadata


class NoteConflictError(Exception):
    """Raised when a note save collides with a newer version on disk."""

    def __init__(self, note: Optional[dict], message: str | None = None) -> None:
        super().__init__(message or "note conflict")
        self.note = note or {}


class LocalStore:
    """SQLite + FAISS-backed store for documents/chunks/events."""

    def __init__(
        self,
        db_path: Path = DB_PATH,
        data_dir: Path = DATA_DIR,
        embedder: Optional[EmbeddingProvider] = None,
        reranker: Optional[Reranker] = None,
    ) -> None:
        self.db_path = db_path
        self.data_dir = data_dir
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.embedder = embedder
        self.reranker = reranker
        self._faiss_index: Optional[FaissIndex] = None
        self._fts5_available: bool = False  # Track FTS5 availability
        self._ensure_schema()
        self._migrate_schema_v2()
        self._migrate_schema_v3()  # v0.8 chunking enhancements
        self.default_doc_id = self._ensure_default_document()
        if self.embedder:
            try:
                self.embedder.load()
                self._faiss_index = FaissIndex(dim=self.embedder.dim)
            except Exception as e:
                print(f"[warn] Embedding index unavailable: {e}")

    def _init_debug_info(self, request: SearchRequest) -> dict:
        """Prepare structured debug info for the retrieval pipeline."""
        debug_info = {
            "query": request.query,
            "filters": request.filters,
            "queries": {
                "raw": request.query,
                "dense": {"query": request.query, "filters": request.filters},
                "sparse": {"query": request.query, "filters": request.filters},
                "exact": {"query": request.query, "filters": request.filters},
            },
            "stages": {
                "dense": {"retrieved": 0, "after_filters": 0},
                "sparse": {"retrieved": 0},
                "exact": {"retrieved": 0},
                "fusion": {"unique": 0},
                "rerank": {"applied": False, "top_k": 0},
                "post_processors": [],
                "final": {"returned": 0},
            },
            "score_keys": SCORE_BREAKDOWN_ORDER,
            "candidates": [],
            # Legacy fields kept for templates/UI that expect them
            "dense_query": request.query,
            "dense_results_count": 0,
            "dense_filtered_count": 0,
            "sparse_query": request.query,
            "sparse_results_count": 0,
            "exact_query": request.query,
            "exact_results_count": 0,
            "fused_count": 0,
            "reranked": False,
            "rerank_count": 0,
            "post_processors": [],
            "final_count": 0,
            "top_candidates": [],
        }
        self._last_debug_info = debug_info
        return debug_info

    def _format_score_history(self, score_history: Dict[str, float]) -> List[Dict[str, float]]:
        """Return score history as an ordered list for UI display."""
        ordered = []
        for key in SCORE_BREAKDOWN_ORDER:
            if key in score_history:
                ordered.append({"stage": key, "value": score_history[key]})
        for key, value in score_history.items():
            if key not in SCORE_BREAKDOWN_ORDER:
                ordered.append({"stage": key, "value": value})
        return ordered

    @contextmanager
    def _connect(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _ensure_schema(self) -> None:
        with self._connect() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS documents (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source TEXT,
                    uri TEXT,
                    doc_type TEXT,
                    created_at TEXT,
                    event_at TEXT,
                    ingested_at TEXT,
                    title TEXT,
                    tags TEXT,
                    sensitivity TEXT,
                    hash TEXT,
                    status TEXT,
                    raw_content TEXT
                )
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS chunks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    document_id INTEGER NOT NULL,
                    seq INTEGER,
                    text TEXT NOT NULL,
                    role TEXT,
                    token_count INTEGER,
                    embedding_id INTEGER,
                    created_at TEXT,
                    event_at TEXT,
                    tags TEXT,
                    focus_hint TEXT,
                    deleted INTEGER DEFAULT 0,
                    FOREIGN KEY (document_id) REFERENCES documents(id)
                )
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    type TEXT,
                    payload TEXT,
                    created_at TEXT,
                    event_at TEXT,
                    related_chunk_id INTEGER
                )
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS focus_state (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    topics TEXT,
                    updated_at TEXT
                )
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS vector_index_meta (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    vector_dim INTEGER,
                    index_type TEXT,
                    trained INTEGER,
                    path TEXT,
                    last_sync_ts TEXT
                )
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS rss_items (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    item_hash TEXT UNIQUE NOT NULL,
                    title TEXT,
                    link TEXT,
                    feed_url TEXT,
                    published TEXT,
                    fetched_at TEXT,
                    processed INTEGER DEFAULT 0
                )
                """
            )

    def _migrate_schema_v2(self) -> None:
        """Apply schema migrations for v0.7 retrieval enhancements.
        
        Adds:
        - char_start, char_end, embedding_model, chunk_window columns to chunks table
        - chunks_fts FTS5 virtual table for sparse search
        - Triggers to keep FTS5 table in sync
        """
        try:
            with self._connect() as conn:
                cur = conn.cursor()
                
                # Check if columns need to be added
                cursor = cur.execute("PRAGMA table_info(chunks)")
                columns = [row[1] for row in cursor.fetchall()]
                
                if 'char_start' not in columns:
                    # Add new columns to chunks table
                    cur.execute("ALTER TABLE chunks ADD COLUMN char_start INTEGER DEFAULT 0")
                    cur.execute("ALTER TABLE chunks ADD COLUMN char_end INTEGER DEFAULT -1")
                    cur.execute(f"ALTER TABLE chunks ADD COLUMN embedding_model TEXT DEFAULT '{MODEL_NAME}'")
                    cur.execute("ALTER TABLE chunks ADD COLUMN chunk_window TEXT")
                    print("[info] Added citation columns to chunks table")
                
                # Check if FTS5 table exists (independent of column check)
                cursor = cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='chunks_fts'")
                fts_exists = cursor.fetchone() is not None
                needs_rebuild = False
                
                if fts_exists:
                    cursor = cur.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='chunks_fts'")
                    row = cursor.fetchone()
                    if row and row[0] and 'porter' in row[0].lower():
                        needs_rebuild = True
                
                if needs_rebuild:
                    # Drop old FTS and triggers to rebuild with unicode tokenizer (better CJK support)
                    cur.execute("DROP TRIGGER IF EXISTS chunks_fts_insert")
                    cur.execute("DROP TRIGGER IF EXISTS chunks_fts_delete")
                    cur.execute("DROP TRIGGER IF EXISTS chunks_fts_update")
                    cur.execute("DROP TABLE IF EXISTS chunks_fts")
                    fts_exists = False
                    print("[info] Rebuilding chunks_fts with unicode tokenizer (removed porter)")
                
                if fts_exists:
                    # Table already exists, mark FTS5 as available
                    self._fts5_available = True
                    print("[info] FTS5 table already exists")
                else:
                    # Test FTS5 support before attempting to create
                    try:
                        cur.execute("PRAGMA compile_options")
                        compile_options = [row[0] for row in cur.fetchall()]
                        if not any('FTS5' in opt for opt in compile_options):
                            raise Exception("FTS5 not enabled in compile options")
                        print(f"[info] FTS5 is available")
                    except Exception as e:
                        print(f"[ERROR] FTS5 is not available in this SQLite build: {e}")
                        print("[ERROR] Sparse search will not be available. Please upgrade SQLite with FTS5 support.")
                        self._fts5_available = False
                        return
                    
                    # Create FTS5 virtual table for sparse search
                    cur.execute("""
                        CREATE VIRTUAL TABLE chunks_fts USING fts5(
                            chunk_id UNINDEXED,
                            text,
                            doc_type UNINDEXED,
                            tokenize='unicode61'
                        )
                    """)
                
                    # Populate FTS5 from existing chunks
                    cur.execute("""
                        INSERT INTO chunks_fts(chunk_id, text, doc_type)
                        SELECT 
                            c.id, 
                            c.text, 
                            d.doc_type
                        FROM chunks c
                        JOIN documents d ON c.document_id = d.id
                        WHERE c.deleted = 0
                    """)
                    print("[info] Created FTS5 table and populated with existing chunks")
                    # Mark FTS5 as available since we just created it
                    self._fts5_available = True
                
                # Check and create triggers independently (they may be missing even if table exists)
                # Check if triggers exist
                cursor = cur.execute("SELECT name FROM sqlite_master WHERE type='trigger' AND name='chunks_fts_insert'")
                insert_trigger_exists = cursor.fetchone() is not None
                
                cursor = cur.execute("SELECT name FROM sqlite_master WHERE type='trigger' AND name='chunks_fts_delete'")
                delete_trigger_exists = cursor.fetchone() is not None
                
                cursor = cur.execute("SELECT name FROM sqlite_master WHERE type='trigger' AND name='chunks_fts_update'")
                update_trigger_exists = cursor.fetchone() is not None
                
                if not insert_trigger_exists:
                    # Create trigger to keep FTS5 in sync on INSERT
                    cur.execute("""
                        CREATE TRIGGER chunks_fts_insert
                        AFTER INSERT ON chunks
                        BEGIN
                            INSERT INTO chunks_fts(chunk_id, text, doc_type)
                            SELECT NEW.id, NEW.text, d.doc_type
                            FROM documents d WHERE d.id = NEW.document_id;
                        END
                    """)
                    print("[info] Created chunks_fts_insert trigger")
                
                if not delete_trigger_exists:
                    # Create trigger to keep FTS5 in sync on DELETE (soft delete)
                    cur.execute("""
                        CREATE TRIGGER chunks_fts_delete
                        AFTER UPDATE OF deleted ON chunks
                        WHEN NEW.deleted = 1
                        BEGIN
                            DELETE FROM chunks_fts WHERE chunk_id = NEW.id;
                        END
                    """)
                    print("[info] Created chunks_fts_delete trigger")
                
                if not update_trigger_exists:
                    # Create trigger to keep FTS5 in sync on UPDATE
                    cur.execute("""
                        CREATE TRIGGER chunks_fts_update
                        AFTER UPDATE OF text ON chunks
                        WHEN NEW.deleted = 0
                        BEGIN
                            DELETE FROM chunks_fts WHERE chunk_id = NEW.id;
                            INSERT INTO chunks_fts(chunk_id, text, doc_type)
                            SELECT NEW.id, NEW.text, d.doc_type
                            FROM documents d WHERE d.id = NEW.document_id;
                        END
                    """)
                    print("[info] Created chunks_fts_update trigger")
                
                print("[info] Schema migration v2 completed successfully")
        except Exception as e:
            print(f"[ERROR] Schema migration v2 failed: {e}")
            import traceback
            traceback.print_exc()
            print("[WARN] Some features (sparse search) may not be available")
    
    def _migrate_schema_v3(self) -> None:
        """Apply schema migrations for v0.8 chunking enhancements.
        
        Adds:
        - heading_path, page_number, parent_doc_seq columns to chunks table
        - chunk_strategy, chunk_version columns to documents table
        """
        try:
            with self._connect() as conn:
                cur = conn.cursor()
                
                # Check if chunks columns need to be added
                cursor = cur.execute("PRAGMA table_info(chunks)")
                chunk_columns = [row[1] for row in cursor.fetchall()]
                
                if 'heading_path' not in chunk_columns:
                    # Add new chunking metadata columns to chunks table
                    cur.execute("ALTER TABLE chunks ADD COLUMN heading_path TEXT")
                    cur.execute("ALTER TABLE chunks ADD COLUMN page_number INTEGER")
                    cur.execute("ALTER TABLE chunks ADD COLUMN parent_doc_seq INTEGER")
                    print("[info] Added chunking metadata columns to chunks table")
                
                # Check if documents columns need to be added
                cursor = cur.execute("PRAGMA table_info(documents)")
                doc_columns = [row[1] for row in cursor.fetchall()]
                
                if 'chunk_strategy' not in doc_columns:
                    # Add chunking config columns to documents table
                    cur.execute("ALTER TABLE documents ADD COLUMN chunk_strategy TEXT")
                    print("[info] Added chunk_strategy column to documents table")
                
                if 'chunk_version' not in doc_columns:
                    cur.execute("ALTER TABLE documents ADD COLUMN chunk_version INTEGER")
                    print("[info] Added chunk_version column to documents table")
                
                if 'raw_content' not in doc_columns:
                    cur.execute("ALTER TABLE documents ADD COLUMN raw_content TEXT")
                    print("[info] Added raw_content column to documents table")
                
                # Ensure legacy rows without chunk_strategy are flagged for migration
                if 'chunk_version' in doc_columns and 'chunk_strategy' in doc_columns:
                    cur.execute(
                        """
                        UPDATE documents
                        SET chunk_version = NULL
                        WHERE chunk_strategy IS NULL AND (chunk_version IS NULL OR chunk_version = 1)
                        """
                    )
                
                print("[info] Schema migration v3 completed successfully")
        except Exception as e:
            print(f"[ERROR] Schema migration v3 failed: {e}")
            import traceback
            traceback.print_exc()
            print("[WARN] Some chunking features may not be available")

    def _maybe_embed(self, texts: Sequence[str]):
        if self.embedder and self._faiss_index:
            try:
                return self.embedder.embed(texts)
            except Exception as e:
                print(f"[warn] embedding failed: {e}")
        return None

    def _get_embedding_max_tokens(self) -> Optional[int]:
        """Best-effort detection of the embedder's max token length."""
        if not self.embedder:
            return None
        model = getattr(self.embedder, "model", None)
        if model is None:
            return None
        max_tokens = None
        try:
            max_tokens = getattr(model, "max_seq_length", None)
        except Exception:
            max_tokens = None
        if not max_tokens:
            try:
                tok = getattr(model, "tokenizer", None)
                max_tokens = getattr(tok, "model_max_length", None) or getattr(tok, "max_len_single_sentence", None)
            except Exception:
                max_tokens = None
        try:
            max_tokens = int(max_tokens) if max_tokens else None
        except Exception:
            max_tokens = None
        if max_tokens and 0 < max_tokens < 100000:
            return max_tokens
        return None

    def _split_chunk_for_limit(self, chunk: Chunk, max_tokens: int) -> List[Chunk]:
        """Split a chunk into sub-chunks that respect the embedder's token limit."""
        if not chunk.text:
            return [chunk]
        tok = _get_tokenizer()
        pieces: List[Chunk] = []

        def _char_split() -> List[Chunk]:
            approx_chars = max_tokens * 4
            result: List[Chunk] = []
            start = 0
            while start < len(chunk.text):
                end = min(len(chunk.text), start + approx_chars)
                piece_text = chunk.text[start:end]
                result.append(
                    Chunk(
                        text=piece_text,
                        char_start=chunk.char_start + start,
                        char_end=chunk.char_start + end,
                        seq=0,
                        heading_path=chunk.heading_path,
                        page_number=chunk.page_number,
                        token_count=count_tokens(piece_text),
                    )
                )
                start = end
            return result

        if tok in (None, "fallback"):
            return _char_split()

        try:
            encoded = tok(chunk.text, add_special_tokens=False, return_offsets_mapping=True)
            offsets = encoded.get("offset_mapping")
        except Exception:
            offsets = None

        if not offsets:
            return _char_split()

        start_idx = 0
        while start_idx < len(offsets):
            end_idx = min(start_idx + max_tokens, len(offsets))
            start_char = offsets[start_idx][0]
            end_char = offsets[end_idx - 1][1]
            piece_text = chunk.text[start_char:end_char]
            pieces.append(
                Chunk(
                    text=piece_text,
                    char_start=chunk.char_start + start_char,
                    char_end=chunk.char_start + end_char,
                    seq=0,
                    heading_path=chunk.heading_path,
                    page_number=chunk.page_number,
                    token_count=end_idx - start_idx,
                )
            )
            start_idx = end_idx

        return pieces

    def _enforce_chunk_token_limit(self, chunks: List[Chunk], max_tokens: int) -> List[Chunk]:
        """Ensure no chunk exceeds the embedder's token limit."""
        limited: List[Chunk] = []
        seq = 0
        for chunk in chunks:
            token_count = chunk.token_count if chunk.token_count is not None else None
            if token_count is None:
                try:
                    token_count = count_tokens(chunk.text)
                except Exception:
                    token_count = None
            if token_count is not None and token_count <= max_tokens:
                limited.append(
                    Chunk(
                        text=chunk.text,
                        char_start=chunk.char_start,
                        char_end=chunk.char_end,
                        seq=seq,
                        heading_path=chunk.heading_path,
                        page_number=chunk.page_number,
                        token_count=token_count,
                    )
                )
                seq += 1
                continue

            for piece in self._split_chunk_for_limit(chunk, max_tokens):
                piece.seq = seq
                if piece.token_count is None:
                    try:
                        piece.token_count = count_tokens(piece.text)
                    except Exception:
                        piece.token_count = None
                limited.append(piece)
                seq += 1

        return limited

    def _write_index_meta(self, cur, now_iso: str) -> None:
        if not self._faiss_index:
            return
        cur.execute(
            """
            INSERT INTO vector_index_meta (id, vector_dim, index_type, trained, path, last_sync_ts)
            VALUES (1, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                vector_dim=excluded.vector_dim,
                index_type=excluded.index_type,
                trained=excluded.trained,
                path=excluded.path,
                last_sync_ts=excluded.last_sync_ts
            """,
            (
                self._faiss_index.dim if hasattr(self._faiss_index, "dim") else None,
                "flat",
                1 if (faiss and self._faiss_index) else 0,
                str(self._faiss_index.index_path) if self._faiss_index else None,
                now_iso,
            ),
        )

    def _extract_image_refs(self, content: str) -> List[str]:
        if not content:
            return []
        matches = re.findall(r"!\[[^\]]*\]\(([^)]+)\)", content)
        return [m for m in matches if m]

    def _derive_note_title(self, content: str, provided: Optional[str] = None) -> str:
        if provided and provided.strip():
            return provided.strip()
        for line in content.splitlines():
            candidate = line.strip().lstrip("#").strip()
            if candidate:
                return candidate[:160]
        return "Untitled note"

    def _ensure_default_document(self) -> int:
        with self._connect() as conn:
            cur = conn.cursor()
            cur.execute("SELECT id FROM documents WHERE status = 'default_chat'")
            row = cur.fetchone()
            if row:
                return row["id"]
            now = datetime.now(UTC).isoformat()
            cur.execute(
                """
                INSERT INTO documents (source, uri, doc_type, created_at, ingested_at, title, status)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                ("local", None, "chat", now, now, "chat-log", "default_chat"),
            )
            return cur.lastrowid

    def _row_to_message(self, row: sqlite3.Row, use_raw: bool = True) -> Message:
        """Hydrate a Message preferring raw_content when available."""
        metadata = json.loads(row["tags"]) if row["tags"] else {}
        metadata["chunk_id"] = row["id"]
        # Enrich with document/strategy metadata when present
        doc_id = row["doc_id"] if "doc_id" in row.keys() else row["document_id"] if "document_id" in row.keys() else None
        if doc_id is not None:
            metadata["doc_id"] = doc_id
        if "doc_type" in row.keys():
            metadata["doc_type"] = row["doc_type"]
        if "doc_chunk_strategy" in row.keys():
            metadata["chunk_strategy"] = row["doc_chunk_strategy"]
        if "doc_chunk_version" in row.keys():
            metadata["chunk_version"] = row["doc_chunk_version"]
        if "doc_chunk_count" in row.keys() and row["doc_chunk_count"] is not None:
            metadata["chunk_count"] = row["doc_chunk_count"]
        if "seq" in row.keys():
            metadata.setdefault("chunk_seq", row["seq"])
        
        content = row["text"]
        role = row["role"]
        created_at = row["created_at"]
        reference_time = row["event_at"]
        
        if use_raw:
            doc_raw = None
            if "doc_raw_content" in row.keys():
                doc_raw = row["doc_raw_content"]
            elif "raw_content" in row.keys():
                doc_raw = row["raw_content"]
            doc_type = row["doc_type"] if "doc_type" in row.keys() else None
            
            if doc_raw and doc_type == "chat":
                try:
                    data = json.loads(doc_raw)
                    if isinstance(data, dict):
                        content = data.get("content", content)
                        role = data.get("role") or role
                        created_at = data.get("created_at") or created_at
                        reference_time = data.get("event_at") or data.get("reference_time") or reference_time
                        msg_tags = data.get("tags") or data.get("metadata")
                        if isinstance(msg_tags, dict):
                            metadata.update(msg_tags)
                    elif isinstance(data, list) and row["seq"] is not None and 0 <= row["seq"] < len(data):
                        item = data[row["seq"]]
                        if isinstance(item, dict):
                            content = item.get("content", content)
                            role = item.get("role") or role
                            created_at = item.get("created_at") or created_at
                            reference_time = item.get("event_at") or item.get("reference_time") or reference_time
                            msg_tags = item.get("tags") or item.get("metadata")
                            if isinstance(msg_tags, dict):
                                metadata.update(msg_tags)
                except Exception:
                    pass
        
        return Message(
            content=content,
            role=Role(role) if role else Role.USER,
            created_at=ensure_utc(datetime.fromisoformat(created_at)) if created_at else datetime.now(UTC),
            reference_time=ensure_utc(datetime.fromisoformat(reference_time)) if reference_time else None,
            metadata=metadata,
        )

    def _insert_document(self, doc_meta: dict) -> int:
        def _iso(dt_val: Optional[datetime]) -> Optional[str]:
            if dt_val is None:
                return None
            if isinstance(dt_val, str):
                return dt_val
            return ensure_utc(dt_val).isoformat()

        now = datetime.now(UTC)
        with self._connect() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO documents (source, uri, doc_type, created_at, event_at, ingested_at, title, tags, sensitivity, hash, status, raw_content, chunk_strategy, chunk_version)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    doc_meta.get("source"),
                    doc_meta.get("uri"),
                    doc_meta.get("doc_type"),
                    _iso(doc_meta.get("created_at", now)),
                    _iso(doc_meta.get("event_at")),
                    _iso(doc_meta.get("ingested_at", now)),
                    doc_meta.get("title"),
                    json.dumps(doc_meta.get("tags")) if doc_meta.get("tags") else None,
                    doc_meta.get("sensitivity"),
                    doc_meta.get("hash"),
                    doc_meta.get("status"),
                    doc_meta.get("raw_content"),
                    doc_meta.get("chunk_strategy"),  # v0.8: track chunking strategy
                    doc_meta.get("chunk_version", 1),  # v0.8: version for migrations
                ),
            )
            return cur.lastrowid

    def add_messages(self, messages: Sequence[Message], document_meta: Optional[dict] = None) -> List[int]:
        ids: List[int] = []
        if not messages:
            return ids

        # If this looks like a non-chat document without a chunking strategy, route through chunked ingestion
        if document_meta and document_meta.get("doc_type") != "chat" and not document_meta.get("chunk_strategy"):
            if len(messages) == 1:
                try:
                    result = self.add_document_chunked(content=messages[0].content, document_meta=document_meta.copy())
                    return result.get("chunk_ids", [])
                except Exception as e:
                    print(f"[warn] chunked ingestion fallback failed, continuing with add_messages: {e}")
            else:
                print("[warn] chunked ingestion skipped: multiple messages provided without chunk_strategy")

        embeddings = self._maybe_embed([m.content for m in messages])

        def _use_structure_chunking(text: str) -> bool:
            """Heuristic: prefer structure-aware chunking for long/markdown chat messages."""
            if not text:
                return False
            if len(text) >= 1200:
                return True
            if len(text) >= 600 and re.search(r"(?m)^#{1,6}\s+", text):
                return True
            return False

        for idx, msg in enumerate(messages):
            base_meta = document_meta.copy() if document_meta else {}
            doc_type = base_meta.get("doc_type") or "chat"
            chunk_strategy = base_meta.get("chunk_strategy")
            if not chunk_strategy and doc_type == "chat":
                chunk_strategy = "per-message"
            chunk_version = base_meta.get("chunk_version", 1)
            content = msg.content or ""

            # Use structure-aware chunking for long/markdown chat messages
            if doc_type == "chat" and _use_structure_chunking(content):
                chunk_strategy = "header-aware"
                created_at = ensure_utc(msg.created_at) or datetime.now(UTC)
                reference_time = ensure_utc(msg.reference_time) if msg.reference_time else None
                chunker = HeaderAwareChunker()
                chunks = chunker.chunk(content)
                if not chunks:
                    chunks = [Chunk(text=content, char_start=0, char_end=len(content), seq=0, token_count=count_tokens(content))]
                max_tokens = self._get_embedding_max_tokens()
                if max_tokens:
                    chunks = self._enforce_chunk_token_limit(chunks, max_tokens)
                chunk_texts = [c.text for c in chunks]
                chunk_embeddings = self._maybe_embed(chunk_texts)

                doc_meta = {
                    "doc_type": doc_type,
                    "source": base_meta.get("source"),
                    "uri": base_meta.get("uri"),
                    "title": base_meta.get("title"),
                    "tags": base_meta.get("tags"),
                    "sensitivity": base_meta.get("sensitivity"),
                    "hash": base_meta.get("hash"),
                    "status": base_meta.get("status"),
                    "raw_content": content,
                    "chunk_strategy": chunk_strategy,
                    "chunk_version": chunk_version,
                    "created_at": base_meta.get("created_at") or created_at,
                    "event_at": base_meta.get("event_at") or reference_time,
                    "ingested_at": base_meta.get("ingested_at") or datetime.now(UTC),
                }

                doc_id = self._insert_document(doc_meta)

                with self._connect() as conn:
                    cur = conn.cursor()
                    chunk_ids: List[int] = []
                    for c_idx, chunk in enumerate(chunks):
                        cur.execute(
                            """
                            INSERT INTO chunks (document_id, seq, text, role, token_count, embedding_id, created_at, event_at, tags, focus_hint, char_start, char_end, embedding_model, heading_path, page_number, parent_doc_seq)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """,
                            (
                                doc_id,
                                chunk.seq if chunk.seq is not None else c_idx,
                                chunk.text,
                                msg.role.value,
                                chunk.token_count,
                                None,
                                created_at.isoformat(),
                                reference_time.isoformat() if reference_time else None,
                                json.dumps(msg.metadata) if msg.metadata else None,
                                msg.metadata.get("focus_hint") if msg.metadata else None,
                                chunk.char_start,
                                chunk.char_end,
                                MODEL_NAME,
                                chunk.heading_path,
                                getattr(chunk, "page_number", None),
                                chunk.seq if chunk.seq is not None else c_idx,
                            ),
                        )
                        chunk_id = cur.lastrowid
                        chunk_ids.append(chunk_id)
                        if chunk_embeddings is not None:
                            emb_vec = chunk_embeddings[c_idx : c_idx + 1]
                            embedding_id = chunk_id
                            cur.execute(
                                "UPDATE chunks SET embedding_id = ? WHERE id = ?",
                                (embedding_id, chunk_id),
                            )
                            if self._faiss_index:
                                self._faiss_index.add([embedding_id], emb_vec)
                    if self._faiss_index:
                        self._write_index_meta(cur, datetime.now(UTC).isoformat())
                    ids.extend(chunk_ids)
                continue

            # Raw content: persist the single message for fidelity
            raw_content = base_meta.get("raw_content")
            if raw_content is None:
                if doc_type == "chat":
                    try:
                        raw_content = json.dumps(msg.to_dict())
                    except Exception:
                        raw_content = content
                else:
                    raw_content = content

            doc_meta = {
                "doc_type": doc_type,
                "source": base_meta.get("source"),
                "uri": base_meta.get("uri"),
                "title": base_meta.get("title"),
                "tags": base_meta.get("tags"),
                "sensitivity": base_meta.get("sensitivity"),
                "hash": base_meta.get("hash"),
                "status": base_meta.get("status"),
                "raw_content": raw_content,
                "chunk_strategy": chunk_strategy,
                "chunk_version": chunk_version,
                "created_at": base_meta.get("created_at") or ensure_utc(msg.created_at),
                "event_at": base_meta.get("event_at") or ensure_utc(msg.reference_time),
                "ingested_at": base_meta.get("ingested_at") or datetime.now(UTC),
            }

            doc_id = self._insert_document(doc_meta)

            with self._connect() as conn:
                cur = conn.cursor()
                created_at = ensure_utc(msg.created_at) or datetime.now(UTC)
                reference_time = ensure_utc(msg.reference_time) if msg.reference_time else None
                char_start = 0
                char_end = len(msg.content)

                cur.execute(
                    """
                    INSERT INTO chunks (document_id, seq, text, role, token_count, embedding_id, created_at, event_at, tags, focus_hint, char_start, char_end, embedding_model, parent_doc_seq)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        doc_id,
                        0,
                        msg.content,
                        msg.role.value,
                        None,
                        None,
                        created_at.isoformat(),
                        reference_time.isoformat() if reference_time else None,
                        json.dumps(msg.metadata) if msg.metadata else None,
                        msg.metadata.get("focus_hint") if msg.metadata else None,
                        char_start,
                        char_end,
                        MODEL_NAME,
                        0,  # parent_doc_seq tracks message sequence within a single-message doc
                    ),
                )
                chunk_id = cur.lastrowid
                ids.append(chunk_id)
                if embeddings is not None:
                    emb_vec = embeddings[idx : idx + 1]
                    embedding_id = chunk_id
                    cur.execute(
                        "UPDATE chunks SET embedding_id = ? WHERE id = ?",
                        (embedding_id, chunk_id),
                    )
                    if self._faiss_index:
                        self._faiss_index.add([embedding_id], emb_vec)
                if self._faiss_index:
                    self._write_index_meta(cur, datetime.now(UTC).isoformat())

        return ids

    def add_document_chunked(
        self, 
        content: str, 
        document_meta: dict,
        chunker_kwargs: Optional[dict] = None
    ) -> dict:
        """Add a document with automatic chunking based on document type.
        
        Args:
            content: Document content to chunk
            document_meta: Document metadata (must include doc_type, title, source, uri)
            chunker_kwargs: Optional kwargs to pass to the chunker
            
        Returns:
            Dict with document_id and chunk_ids
        """
        from chunking import get_chunker_for_doctype
        
        doc_type = document_meta.get("doc_type", "web_page")
        chunker_kwargs = chunker_kwargs or {}
        chunker = get_chunker_for_doctype(doc_type, **chunker_kwargs)
        
        # Chunk the content
        chunks = chunker.chunk(content)
        
        if not chunks:
            # Fallback to single chunk if chunker returns nothing
            from chunking import Chunk
            chunks = [Chunk(text=content, char_start=0, char_end=len(content), seq=0)]
        
        # Enforce embedder token limit before embedding
        max_tokens = self._get_embedding_max_tokens()
        if max_tokens:
            chunks = self._enforce_chunk_token_limit(chunks, max_tokens)
        
        # Embed all chunks
        chunk_texts = [c.text for c in chunks]
        embeddings = self._maybe_embed(chunk_texts)
        
        now_iso = datetime.now(UTC).isoformat()
        
        # Determine chunk strategy based on chunker type
        chunk_strategy = type(chunker).__name__.replace('Chunker', '').lower()
        if 'headeraware' in chunk_strategy:
            chunk_strategy = 'header-aware'
        elif 'sentencewindow' in chunk_strategy:
            chunk_strategy = 'sentence-window'
        elif 'fixedtoken' in chunk_strategy:
            chunk_strategy = 'fixed-token'
        elif 'message' in chunk_strategy:
            chunk_strategy = 'per-message'
        
        document_meta["chunk_strategy"] = chunk_strategy
        document_meta["chunk_version"] = 1
        document_meta.setdefault("raw_content", content)
        if "created_at" not in document_meta:
            document_meta["created_at"] = now_iso
        if "ingested_at" not in document_meta:
            document_meta["ingested_at"] = now_iso
        
        with self._connect() as conn:
            cur = conn.cursor()
            
            # Insert document
            doc_id = self._insert_document(document_meta)
            
            # Insert chunks
            chunk_ids = []
            for chunk in chunks:
                cur.execute(
                    """
                    INSERT INTO chunks (document_id, seq, text, role, token_count, embedding_id, 
                                       created_at, event_at, tags, focus_hint, char_start, char_end, 
                                       embedding_model, heading_path, page_number, parent_doc_seq)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        doc_id, chunk.seq, chunk.text, Role.SYSTEM.value, chunk.token_count,
                        None, now_iso, now_iso, json.dumps(document_meta.get("tags", {})), None,
                        chunk.char_start, chunk.char_end, MODEL_NAME, 
                        chunk.heading_path, chunk.page_number, chunk.seq
                    ),
                )
                chunk_ids.append(cur.lastrowid)
            
            # Add embeddings to FAISS in batch for efficiency
            if embeddings is not None and self._faiss_index:
                self._faiss_index.add(chunk_ids, embeddings)
                for chunk_id in chunk_ids:
                    cur.execute("UPDATE chunks SET embedding_id = ? WHERE id = ?", (chunk_id, chunk_id))
                self._write_index_meta(cur, now_iso)
        
        return {
            "document_id": doc_id,
            "chunk_ids": chunk_ids,
            "chunk_count": len(chunk_ids),
        }

    def list_recent(self, n: int = 20) -> List[Message]:
        with self._connect() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT c.*, d.doc_type, d.raw_content AS doc_raw_content
                FROM chunks c
                JOIN documents d ON c.document_id = d.id
                WHERE deleted = 0
                ORDER BY datetime(c.created_at) DESC
                LIMIT ?
                """,
                (n,),
            )
            rows = cur.fetchall()
        return [self._row_to_message(r) for r in reversed(rows)]

    def list_messages_by_day(self, day: str, limit: int = 500) -> List[Message]:
        """Return messages for a given calendar day using raw documents when available.
        
        We purposely fetch all candidate chunks for the day and then filter in
        Python so we can drop derived chunks (e.g., chat windows) and rely on
        raw_content instead of chunk text.
        """
        with self._connect() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT c.*,
                       d.doc_type,
                       d.raw_content AS doc_raw_content,
                       d.chunk_strategy AS doc_chunk_strategy,
                       d.chunk_version AS doc_chunk_version,
                       d.id AS doc_id,
                       (SELECT COUNT(*) FROM chunks c2 WHERE c2.document_id = c.document_id AND c2.deleted = 0) AS doc_chunk_count
                FROM chunks c
                JOIN documents d ON c.document_id = d.id
                WHERE c.deleted = 0 AND c.created_at IS NOT NULL AND date(c.created_at) = ?
                ORDER BY datetime(c.created_at)
                """,
                (day,),
            )
            rows = cur.fetchall()

        messages: List[Message] = []
        seen_raw_docs: set[int] = set()
        seen_chat_docs: set[int] = set()

        for row in rows:
            doc_id = row["document_id"]
            doc_type = row["doc_type"]
            raw = row["doc_raw_content"]
            msg: Optional[Message] = None

            # For chat docs with stored raw_content
            if doc_type == "chat" and raw:
                strategy = ""
                if "doc_chunk_strategy" in row.keys() and row["doc_chunk_strategy"]:
                    strategy = row["doc_chunk_strategy"]
                is_per_message = "per-message" in strategy if strategy else False
                # Collapse multi-chunk chat docs (structure-aware) into a single message
                if (not is_per_message) or (row["doc_chunk_count"] and row["doc_chunk_count"] > 1 if "doc_chunk_count" in row.keys() else False):
                    if doc_id in seen_chat_docs:
                        continue
                    seen_chat_docs.add(doc_id)
                    metadata = json.loads(row["tags"]) if row["tags"] else {}
                    metadata["chunk_id"] = row["id"]
                    metadata["doc_id"] = doc_id
                    metadata["doc_type"] = doc_type
                    if strategy:
                        metadata["chunk_strategy"] = strategy
                    if "doc_chunk_version" in row.keys() and row["doc_chunk_version"] is not None:
                        metadata["chunk_version"] = row["doc_chunk_version"]
                    if "doc_chunk_count" in row.keys() and row["doc_chunk_count"] is not None:
                        metadata["chunk_count"] = row["doc_chunk_count"]
                    if "seq" in row.keys() and row["seq"] is not None:
                        metadata["chunk_seq"] = row["seq"]
                    try:
                        created_at = ensure_utc(datetime.fromisoformat(row["created_at"])) if row["created_at"] else datetime.now(UTC)
                    except Exception:
                        created_at = datetime.now(UTC)
                    try:
                        reference_time = ensure_utc(datetime.fromisoformat(row["event_at"])) if row["event_at"] else None
                    except Exception:
                        reference_time = None
                    role_val = row["role"] or Role.SYSTEM.value
                    msg = Message(
                        content=str(raw),
                        role=Role(role_val) if role_val in Role._value2member_map_ else Role.SYSTEM,
                        created_at=created_at,
                        reference_time=reference_time,
                        metadata=metadata,
                    )
                else:
                    msg = self._row_to_message(row)

            # For non-chat docs with raw_content, only emit one entry per document (first seen)
            elif raw:
                if doc_id in seen_raw_docs:
                    continue
                seen_raw_docs.add(doc_id)
                metadata = json.loads(row["tags"]) if row["tags"] else {}
                metadata["chunk_id"] = row["id"]
                if doc_id is not None:
                    metadata["doc_id"] = doc_id
                if doc_type:
                    metadata["doc_type"] = doc_type
                if "doc_chunk_strategy" in row.keys() and row["doc_chunk_strategy"]:
                    metadata["chunk_strategy"] = row["doc_chunk_strategy"]
                if "doc_chunk_version" in row.keys() and row["doc_chunk_version"] is not None:
                    metadata["chunk_version"] = row["doc_chunk_version"]
                if "doc_chunk_count" in row.keys() and row["doc_chunk_count"] is not None:
                    metadata["chunk_count"] = row["doc_chunk_count"]
                if "seq" in row.keys() and row["seq"] is not None:
                    metadata["chunk_seq"] = row["seq"]
                try:
                    created_at = ensure_utc(datetime.fromisoformat(row["created_at"])) if row["created_at"] else datetime.now(UTC)
                except Exception:
                    created_at = datetime.now(UTC)
                try:
                    reference_time = ensure_utc(datetime.fromisoformat(row["event_at"])) if row["event_at"] else None
                except Exception:
                    reference_time = None
                role_val = row["role"] or Role.SYSTEM.value
                msg = Message(
                    content=str(raw),
                    role=Role(role_val) if role_val in Role._value2member_map_ else Role.SYSTEM,
                    created_at=created_at,
                    reference_time=reference_time,
                    metadata=metadata,
                )

            else:
                msg = self._row_to_message(row)

            if not msg:
                continue
            msg_day = ensure_utc(msg.created_at).date().isoformat()
            if msg_day != day:
                continue
            meta = msg.metadata or {}
            if "seq" in row.keys() and row["seq"] is not None:
                meta["chunk_seq"] = row["seq"]
            if "doc_chunk_count" in row.keys() and row["doc_chunk_count"] is not None:
                meta["chunk_count"] = row["doc_chunk_count"]
            msg.metadata = meta
            messages.append(msg)

        messages.sort(key=lambda m: ensure_utc(m.created_at))
        return messages[:limit]

    def list_message_days(self, limit: int = 180) -> List[dict]:
        """Return available message days with counts, skipping derived chunks."""
        with self._connect() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT c.id, c.document_id, c.seq, c.created_at, c.event_at, c.tags, c.role,
                       d.doc_type, d.raw_content AS doc_raw_content, d.chunk_strategy AS doc_chunk_strategy,
                       (SELECT COUNT(*) FROM chunks c2 WHERE c2.document_id = c.document_id AND c2.deleted = 0) AS doc_chunk_count
                FROM chunks c
                JOIN documents d ON c.document_id = d.id
                WHERE c.deleted = 0 AND c.created_at IS NOT NULL
                """
            )
            rows = cur.fetchall()

        day_counts: dict[str, int] = {}
        processed_raw_docs: set[int] = set()

        for row in rows:
            doc_id = row["document_id"]
            doc_type = row["doc_type"]
            raw = row["doc_raw_content"]
            strategy = (row["doc_chunk_strategy"] or "") if "doc_chunk_strategy" in row.keys() else ""
            is_per_message = "per-message" in strategy if strategy else False

            # Count chat messages from raw_content only once per document
            if doc_type == "chat":
                if not is_per_message or (row["doc_chunk_count"] and row["doc_chunk_count"] > 1 if "doc_chunk_count" in row.keys() else False):
                    if doc_id in processed_raw_docs:
                        continue
                    processed_raw_docs.add(doc_id)
                ts_val = row["created_at"]
                try:
                    day = ensure_utc(datetime.fromisoformat(ts_val)).date().isoformat() if ts_val else None
                except Exception:
                    day = None
                if day:
                    day_counts[day] = day_counts.get(day, 0) + 1
                continue

            # For non-chat docs with raw_content, count once per document
            if doc_type != "chat" and raw:
                if doc_id in processed_raw_docs:
                    continue
                processed_raw_docs.add(doc_id)
                ts_val = row["created_at"] or row["event_at"]
            else:
                ts_val = row["created_at"]

            try:
                day = ensure_utc(datetime.fromisoformat(ts_val)).date().isoformat() if ts_val else None
            except Exception:
                continue
            if not day:
                continue
            day_counts[day] = day_counts.get(day, 0) + 1

        ordered_days = sorted(day_counts.items(), key=lambda kv: kv[0], reverse=True)[:limit]
        return [{"day": day, "count": count} for day, count in ordered_days]

    def context_block(self, n: int = 13) -> List[Message]:
        with self._connect() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT c.*, d.doc_type, d.raw_content AS doc_raw_content
                FROM chunks c
                JOIN documents d ON c.document_id = d.id
                WHERE deleted = 0
                ORDER BY datetime(c.created_at) DESC
                LIMIT ?
                """,
                (n,),
            )
            rows = cur.fetchall()
        # return oldest-first for context continuity
        return [self._row_to_message(r) for r in reversed(rows)]

    def recall(self, query: str) -> Optional[Message]:
        q = query.lower().strip()
        # semantic first, fall back to substring
        if self.embedder and self._faiss_index:
            try:
                vec = self.embedder.embed([query])
                ids, _scores = self._faiss_index.search(vec, top_k=1)
                if ids:
                    return self._get_chunk_by_id(ids[0])
            except Exception as e:
                print(f"[warn] semantic recall failed: {e}")
        with self._connect() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT * FROM chunks
                WHERE deleted = 0 AND lower(text) LIKE ?
                ORDER BY datetime(created_at) DESC
                LIMIT 1
                """,
                (f"%{q}%",),
            )
            row = cur.fetchone()
        return self._row_to_message(row) if row else None

    def _build_note_payload(
        self,
        doc_row: sqlite3.Row,
        chunk_row: Optional[sqlite3.Row],
        images: List[str],
        updated_at: Optional[str],
        created_at: Optional[str],
        note_id: int,
        title: str,
    ) -> dict:
        raw_content = doc_row["raw_content"] if "raw_content" in doc_row.keys() else None
        content = raw_content if raw_content is not None else (chunk_row["text"] if chunk_row else "")
        return {
            "id": note_id,
            "title": title,
            "content": content,
            "created_at": ensure_utc(datetime.fromisoformat(created_at)).isoformat() if created_at else None,
            "updated_at": ensure_utc(datetime.fromisoformat(updated_at)).isoformat() if updated_at else None,
            "images": images,
        }

    def save_note(
        self,
        content: str,
        title: Optional[str] = None,
        note_id: Optional[int] = None,
        expected_updated_at: Optional[str] = None,
    ) -> Optional[dict]:
        now = datetime.now(UTC)
        now_iso = ensure_utc(now).isoformat()
        title = self._derive_note_title(content, provided=title)
        images = self._extract_image_refs(content)
        base_tags = {"note_type": "markdown", "last_changed": now_iso}
        if images:
            base_tags["images"] = images
        embeddings = self._maybe_embed([content])
        expected_dt = None
        if expected_updated_at:
            try:
                expected_dt = ensure_utc(datetime.fromisoformat(expected_updated_at))
            except Exception:
                expected_dt = None
        with self._connect() as conn:
            cur = conn.cursor()
            if note_id:
                cur.execute("SELECT * FROM documents WHERE id = ? AND doc_type = 'note'", (note_id,))
                doc_row = cur.fetchone()
                if not doc_row:
                    return None
                cur.execute(
                    """
                    SELECT * FROM chunks
                    WHERE document_id = ? AND deleted = 0
                    ORDER BY seq ASC
                    LIMIT 1
                    """,
                    (note_id,),
                )
                chunk_row = cur.fetchone()
                tags = json.loads(doc_row["tags"]) if doc_row["tags"] else {}
                server_title = doc_row["title"] or tags.get("note_title") or "Untitled note"
                server_updated_raw = doc_row["event_at"] or doc_row["created_at"]
                server_created_raw = doc_row["created_at"]
                server_images = tags.get("images") or []
                if expected_dt and server_updated_raw:
                    try:
                        server_dt = ensure_utc(datetime.fromisoformat(server_updated_raw))
                        if server_dt and server_dt > expected_dt:
                            conflict_note = self._build_note_payload(
                                doc_row,
                                chunk_row,
                                server_images,
                                server_updated_raw,
                                server_created_raw,
                                note_id,
                                server_title,
                            )
                            raise NoteConflictError(conflict_note)
                    except NoteConflictError:
                        raise
                    except Exception:
                        pass
                cur.execute(
                    "UPDATE documents SET title = ?, event_at = ?, tags = ?, chunk_strategy = ?, raw_content = ? WHERE id = ?",
                    (title, now_iso, json.dumps(base_tags | {"note_title": title}), "single-chunk", content, note_id),
                )
                chunk_id = chunk_row["id"] if chunk_row else None
                chunk_tags = base_tags | {"note_id": note_id, "note_title": title}
                tags_json = json.dumps(chunk_tags)
                if chunk_id:
                    cur.execute(
                        """
                        UPDATE chunks
                        SET text = ?, event_at = ?, tags = ?, char_start = ?, char_end = ?, embedding_model = ?
                        WHERE id = ?
                        """,
                        (content, now_iso, tags_json, 0, len(content), MODEL_NAME, chunk_id),
                    )
                else:
                    cur.execute(
                        """
                        INSERT INTO chunks (document_id, seq, text, role, token_count, embedding_id, created_at, event_at, tags, focus_hint, char_start, char_end, embedding_model)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            note_id,
                            0,
                            content,
                            Role.USER.value,
                            None,
                            None,
                            now_iso,
                            now_iso,
                            tags_json,
                            None,
                            0,
                            len(content),
                            MODEL_NAME,
                        ),
                    )
                    chunk_id = cur.lastrowid
                created_at = doc_row["created_at"] or now_iso
            else:
                doc_id = self._insert_document(
                    {
                        "doc_type": "note",
                        "title": title,
                        "source": "note",
                        "created_at": now_iso,
                        "event_at": now_iso,
                        "ingested_at": now_iso,
                        "tags": base_tags | {"note_title": title},
                        "raw_content": content,
                        "chunk_strategy": "single-chunk",  # Legacy single-chunk strategy
                        "chunk_version": 1,
                    }
                )
                chunk_tags = base_tags | {"note_id": doc_id, "note_title": title}
                tags_json = json.dumps(chunk_tags)
                cur.execute(
                    """
                    INSERT INTO chunks (document_id, seq, text, role, token_count, embedding_id, created_at, event_at, tags, focus_hint, char_start, char_end, embedding_model)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        doc_id,
                        0,
                        content,
                        Role.USER.value,
                        None,
                        None,
                        now_iso,
                        now_iso,
                        tags_json,
                        None,
                        0,
                        len(content),
                        MODEL_NAME,
                    ),
                )
                chunk_id = cur.lastrowid
                note_id = doc_id
                created_at = now_iso
            if embeddings is not None and self._faiss_index:
                emb_vec = embeddings[:1]
                try:
                    selector = faiss.IDSelectorBatch(np.array([chunk_id], dtype="int64"))
                    self._faiss_index.index.remove_ids(selector)
                except Exception:
                    # ok to continue; add will overwrite
                    pass
                self._faiss_index.add([chunk_id], emb_vec)
                cur.execute(
                    "UPDATE chunks SET embedding_id = ? WHERE id = ?",
                    (chunk_id, chunk_id),
                )
                self._write_index_meta(cur, now_iso)
        return {
            "id": note_id,
            "title": title,
            "content": content,
            "created_at": ensure_utc(datetime.fromisoformat(created_at)).isoformat() if created_at else now_iso,
            "updated_at": now_iso,
            "images": images,
        }
    
    def save_note_chunked(
        self,
        content: str,
        title: Optional[str] = None,
        note_id: Optional[int] = None,
        expected_updated_at: Optional[str] = None,
        use_chunking: bool = True,
    ) -> Optional[dict]:
        """Save note with optional chunking support.
        
        This is the v0.8 enhanced version that supports header-aware chunking.
        When use_chunking=True, long notes are split into chunks by headers.
        
        Args:
            content: Note content
            title: Optional title (derived from content if not provided)
            note_id: Existing note ID for updates
            expected_updated_at: Expected last update timestamp for conflict detection
            use_chunking: Whether to use header-aware chunking (default True)
            
        Returns:
            Note dict with metadata
        """
        from chunking import HeaderAwareChunker, count_tokens
        
        now = datetime.now(UTC)
        now_iso = ensure_utc(now).isoformat()
        title = self._derive_note_title(content, provided=title)
        images = self._extract_image_refs(content)
        base_tags = {"note_type": "markdown", "last_changed": now_iso}
        if images:
            base_tags["images"] = images
        
        # Decide whether to use chunking based on content length and structure
        # Check if note has headers (indicates structure worth preserving)
        has_headers = '\n#' in content or content.startswith('#')
        # Use simple length check when tokenizer unavailable (char count / 4 ~= tokens)
        estimated_tokens = len(content) // 4
        should_chunk = use_chunking and (estimated_tokens > 300 or (has_headers and estimated_tokens > 200))
        
        if not should_chunk:
            # Fall back to single-chunk behavior for short notes
            return self.save_note(content, title, note_id, expected_updated_at)
        
        # Use header-aware chunking for long notes
        chunker = HeaderAwareChunker(max_tokens=300)
        chunks = chunker.chunk(content)
        
        # Embed all chunks at once
        chunk_texts = [c.text for c in chunks]
        embeddings = self._maybe_embed(chunk_texts)
        
        expected_dt = None
        if expected_updated_at:
            try:
                expected_dt = ensure_utc(datetime.fromisoformat(expected_updated_at))
            except Exception:
                expected_dt = None
        
        with self._connect() as conn:
            cur = conn.cursor()
            
            if note_id:
                # Update existing note
                cur.execute("SELECT * FROM documents WHERE id = ? AND doc_type = 'note'", (note_id,))
                doc_row = cur.fetchone()
                if not doc_row:
                    return None
                
                # Check for conflicts
                server_updated_raw = doc_row["event_at"] or doc_row["created_at"]
                if expected_dt and server_updated_raw:
                    try:
                        server_dt = ensure_utc(datetime.fromisoformat(server_updated_raw))
                        if server_dt and server_dt > expected_dt:
                            # Get first chunk for conflict payload
                            cur.execute("SELECT * FROM chunks WHERE document_id = ? AND deleted = 0 ORDER BY seq ASC LIMIT 1", (note_id,))
                            chunk_row = cur.fetchone()
                            tags = json.loads(doc_row["tags"]) if doc_row["tags"] else {}
                            conflict_note = self._build_note_payload(
                                doc_row, chunk_row, tags.get("images", []),
                                server_updated_raw, doc_row["created_at"],
                                note_id, doc_row["title"]
                            )
                            raise NoteConflictError(conflict_note)
                    except NoteConflictError:
                        raise
                    except Exception:
                        pass
                
                # Update document metadata
                cur.execute(
                    "UPDATE documents SET title = ?, event_at = ?, tags = ?, raw_content = ?, chunk_strategy = ?, chunk_version = ? WHERE id = ?",
                    (title, now_iso, json.dumps(base_tags | {"note_title": title}), content, "header-aware", 1, note_id)
                )
                
                # Get old chunk IDs to remove from FAISS before deleting
                cur.execute("SELECT id FROM chunks WHERE document_id = ? AND deleted = 0", (note_id,))
                old_chunk_ids = [row['id'] for row in cur.fetchall()]
                
                # Remove old embeddings from FAISS
                if old_chunk_ids and self._faiss_index:
                    try:
                        import numpy as np
                        selector = faiss.IDSelectorBatch(np.array(old_chunk_ids, dtype="int64"))
                        self._faiss_index.index.remove_ids(selector)
                    except Exception as e:
                        print(f"[warn] Failed to remove old embeddings from FAISS: {e}")
                
                # Delete old chunks
                cur.execute("UPDATE chunks SET deleted = 1 WHERE document_id = ?", (note_id,))
                
                # Insert new chunks
                chunk_ids = []
                for chunk in chunks:
                    chunk_tags = base_tags | {"note_id": note_id, "note_title": title}
                    cur.execute(
                        """
                        INSERT INTO chunks (document_id, seq, text, role, token_count, embedding_id, created_at, event_at, tags, focus_hint, char_start, char_end, embedding_model, heading_path, parent_doc_seq)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            note_id, chunk.seq, chunk.text, Role.USER.value, chunk.token_count,
                            None, now_iso, now_iso, json.dumps(chunk_tags), None,
                            chunk.char_start, chunk.char_end, MODEL_NAME, chunk.heading_path, chunk.seq
                        ),
                    )
                    chunk_ids.append(cur.lastrowid)
                
                created_at = doc_row["created_at"] or now_iso
            else:
                # Create new note
                doc_id = self._insert_document({
                    "doc_type": "note",
                    "title": title,
                    "source": "note",
                    "created_at": now_iso,
                    "event_at": now_iso,
                    "ingested_at": now_iso,
                    "tags": base_tags | {"note_title": title},
                    "raw_content": content,
                    "chunk_strategy": "header-aware",
                    "chunk_version": 1,
                })
                
                # Insert chunks
                chunk_ids = []
                for chunk in chunks:
                    chunk_tags = base_tags | {"note_id": doc_id, "note_title": title}
                    cur.execute(
                        """
                        INSERT INTO chunks (document_id, seq, text, role, token_count, embedding_id, created_at, event_at, tags, focus_hint, char_start, char_end, embedding_model, heading_path, parent_doc_seq)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            doc_id, chunk.seq, chunk.text, Role.USER.value, chunk.token_count,
                            None, now_iso, now_iso, json.dumps(chunk_tags), None,
                            chunk.char_start, chunk.char_end, MODEL_NAME, chunk.heading_path, chunk.seq
                        ),
                    )
                    chunk_ids.append(cur.lastrowid)
                
                note_id = doc_id
                created_at = now_iso
            
            # Add embeddings to FAISS
            if embeddings is not None and self._faiss_index:
                for idx, chunk_id in enumerate(chunk_ids):
                    emb_vec = embeddings[idx:idx+1]
                    self._faiss_index.add([chunk_id], emb_vec)
                    cur.execute("UPDATE chunks SET embedding_id = ? WHERE id = ?", (chunk_id, chunk_id))
                self._write_index_meta(cur, now_iso)
        
        return {
            "id": note_id,
            "title": title,
            "content": content,
            "created_at": ensure_utc(datetime.fromisoformat(created_at)).isoformat() if created_at else now_iso,
            "updated_at": now_iso,
            "images": images,
        }

    def list_notes(self, limit: int = 50) -> List[dict]:
        with self._connect() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT * FROM documents
                WHERE doc_type = 'note'
                ORDER BY datetime(COALESCE(event_at, created_at)) DESC
                LIMIT ?
                """,
                (limit,),
            )
            rows = cur.fetchall()
        notes: List[dict] = []
        for row in rows:
            tags = json.loads(row["tags"]) if row["tags"] else {}
            updated_at = row["event_at"] or row["created_at"]
            notes.append(
                {
                    "id": row["id"],
                    "title": row["title"] or tags.get("note_title") or "Untitled note",
                    "created_at": ensure_utc(datetime.fromisoformat(row["created_at"])).isoformat()
                    if row["created_at"]
                    else None,
                    "updated_at": ensure_utc(datetime.fromisoformat(updated_at)).isoformat() if updated_at else None,
                    "tags": tags,
                }
            )
        return notes

    def get_note(self, note_id: int) -> Optional[dict]:
        with self._connect() as conn:
            cur = conn.cursor()
            cur.execute("SELECT * FROM documents WHERE id = ? AND doc_type = 'note'", (note_id,))
            doc_row = cur.fetchone()
            if not doc_row:
                return None
            
            # Get ALL chunks for the note, ordered by sequence
            cur.execute(
                """
                SELECT * FROM chunks
                WHERE document_id = ? AND deleted = 0
                ORDER BY seq ASC
                """,
                (note_id,),
            )
            chunk_rows = cur.fetchall()
        
        tags = json.loads(doc_row["tags"]) if doc_row["tags"] else {}
        updated_at = doc_row["event_at"] or doc_row["created_at"]
        images = tags.get("images") or []
        raw_content = doc_row["raw_content"] if "raw_content" in doc_row.keys() else None
        
        if raw_content is not None:
            content = raw_content
        else:
            # Reconstruct full content from all chunks
            if chunk_rows:
                # Check if this is a chunked note (multiple chunks or has chunk_strategy)
                chunk_strategy = doc_row["chunk_strategy"] if "chunk_strategy" in doc_row.keys() else None
                if len(chunk_rows) > 1 or chunk_strategy == "header-aware":
                    # Reconstruct from multiple chunks by concatenating in order
                    content_parts = [row["text"] for row in chunk_rows]
                    content = "\n\n".join(content_parts)
                else:
                    # Single chunk - use as-is
                    content = chunk_rows[0]["text"]
            else:
                content = ""
        
        return {
            "id": note_id,
            "title": doc_row["title"] or tags.get("note_title") or "Untitled note",
            "content": content,
            "created_at": ensure_utc(datetime.fromisoformat(doc_row["created_at"])).isoformat()
            if doc_row["created_at"]
            else None,
            "updated_at": ensure_utc(datetime.fromisoformat(updated_at)).isoformat() if updated_at else None,
            "images": images,
        }

    def _get_chunk_by_id(self, chunk_id: int) -> Optional[Message]:
        with self._connect() as conn:
            cur = conn.cursor()
            cur.execute("SELECT * FROM chunks WHERE id = ? AND deleted = 0", (chunk_id,))
            row = cur.fetchone()
        return self._row_to_message(row) if row else None

    def delete_chunk(self, chunk_id: int) -> bool:
        """Delete a chunk; for non-chat docs purge the whole document's chunks + embeddings."""
        with self._connect() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT c.id, c.document_id, c.embedding_id, d.doc_type
                FROM chunks c
                JOIN documents d ON c.document_id = d.id
                WHERE c.id = ? AND c.deleted = 0
                """,
                (chunk_id,),
            )
            row = cur.fetchone()
            if not row:
                return False
            doc_id = row["document_id"]
            doc_type = (row["doc_type"] or "").lower()

            # Collect embedding IDs to remove from FAISS
            embedding_ids_to_remove = []

            # For non-chat docs, remove all chunks belonging to the document
            if doc_type != "chat":
                cur.execute("SELECT id, embedding_id FROM chunks WHERE document_id = ? AND deleted = 0", (doc_id,))
                doc_chunks = cur.fetchall()
                if not doc_chunks:
                    return False
                doc_chunk_ids = [r["id"] for r in doc_chunks]
                embedding_ids_to_remove = [r["embedding_id"] for r in doc_chunks if r["embedding_id"] is not None]
                
                cur.execute("UPDATE chunks SET deleted = 1 WHERE document_id = ?", (doc_id,))
                # Mark document as deleted for bookkeeping (keeps row for audit)
                cur.execute("UPDATE documents SET status = 'deleted' WHERE id = ?", (doc_id,))
                removed_ids = doc_chunk_ids
            else:
                # Chat messages: only delete the specific chunk
                cur.execute("UPDATE chunks SET deleted = 1 WHERE id = ?", (chunk_id,))
                removed_ids = [chunk_id]
                if row["embedding_id"] is not None:
                    embedding_ids_to_remove = [row["embedding_id"]]
        
        # Remove embeddings from FAISS index after DB transaction
        if self._faiss_index and embedding_ids_to_remove:
            try:
                ids_array = np.array(embedding_ids_to_remove, dtype='int64')
                self._faiss_index.index.remove_ids(ids_array)
                self._faiss_index.persist()
                if len(removed_ids) > 0:
                    # Update index metadata timestamp
                    now_iso = datetime.now(UTC).isoformat()
                    with self._connect() as conn:
                        cur = conn.cursor()
                        self._write_index_meta(cur, now_iso)
            except Exception as e:
                print(f"[warn] Failed to remove {len(embedding_ids_to_remove)} embeddings from FAISS: {e}")
        
        return True

    def get_rss_item_hashes(self) -> set:
        """Get all processed RSS item hashes."""
        with self._connect() as conn:
            cur = conn.cursor()
            cur.execute("SELECT item_hash FROM rss_items WHERE processed = 1")
            rows = cur.fetchall()
        return {row["item_hash"] for row in rows}

    def store_rss_items(self, items: List[dict]) -> None:
        """Store RSS items with their hashes."""
        if not items:
            return
        now_iso = datetime.now(UTC).isoformat()
        with self._connect() as conn:
            cur = conn.cursor()
            for item in items:
                import hashlib
                h = hashlib.md5(f"{item.get('title')}|{item.get('link')}".encode("utf-8")).hexdigest()
                try:
                    cur.execute(
                        """
                        INSERT INTO rss_items (item_hash, title, link, feed_url, published, fetched_at, processed)
                        VALUES (?, ?, ?, ?, ?, ?, 1)
                        ON CONFLICT(item_hash) DO UPDATE SET processed = 1, fetched_at = ?
                        """,
                        (
                            h,
                            item.get("title"),
                            item.get("link"),
                            item.get("feed"),
                            item.get("published").isoformat() if item.get("published") else None,
                            now_iso,
                            now_iso,
                        ),
                    )
                except Exception as e:
                    print(f"[warn] Failed to store RSS item hash: {e}")

    def clear(self) -> None:
        with self._connect() as conn:
            cur = conn.cursor()
            cur.execute("DELETE FROM chunks")
            cur.execute("DELETE FROM events")
        if self._faiss_index and self._faiss_index.index_path.exists():
            try:
                self._faiss_index.rebuild([], np.zeros((0, self._faiss_index.dim), dtype="float32"))
            except Exception as e:
                print(f"[warn] failed to reset FAISS index: {e}")

    def get_chunk(self, chunk_id: int) -> StoredChunk:
        """Fetch single chunk by ID with all metadata."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM chunks WHERE id = ? AND deleted = 0",
                (chunk_id,)
            ).fetchone()
            if not row:
                raise ValueError(f"Chunk {chunk_id} not found")
            return StoredChunk(
                id=row['id'],
                document_id=row['document_id'],
                text=row['text'],
                role=Role(row['role']) if row['role'] else Role.USER,
                created_at=ensure_utc(datetime.fromisoformat(row['created_at'])) if row['created_at'] else datetime.now(UTC),
                reference_time=ensure_utc(datetime.fromisoformat(row['event_at'])) if row['event_at'] else None,
                embedding_id=row['embedding_id'],
                tags=json.loads(row['tags']) if row['tags'] else None,
                focus_hint=row['focus_hint'],
                char_start=row['char_start'] if 'char_start' in row.keys() else 0,
                char_end=row['char_end'] if 'char_end' in row.keys() else -1,
                embedding_model=row['embedding_model'] if 'embedding_model' in row.keys() else None,
                chunk_window=row['chunk_window'] if 'chunk_window' in row.keys() else None,
                heading_path=row['heading_path'] if 'heading_path' in row.keys() else None,
                page_number=row['page_number'] if 'page_number' in row.keys() else None,
                parent_doc_seq=row['parent_doc_seq'] if 'parent_doc_seq' in row.keys() else None
            )
    
    def get_document(self, doc_id: int) -> dict:
        """Fetch document metadata."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM documents WHERE id = ?",
                (doc_id,)
            ).fetchone()
            return dict(row) if row else {}

    def get_document_with_chunks(self, doc_id: int) -> dict:
        """Fetch a document plus all chunks with basic positioning for highlighting."""
        with self._connect() as conn:
            cur = conn.cursor()
            cur.execute("SELECT * FROM documents WHERE id = ?", (doc_id,))
            doc_row = cur.fetchone()
            if not doc_row:
                return {}
            cur.execute(
                """
                SELECT * FROM chunks
                WHERE document_id = ? AND deleted = 0
                ORDER BY seq ASC
                """,
                (doc_id,),
            )
            chunk_rows = cur.fetchall()

        tags = json.loads(doc_row["tags"]) if doc_row["tags"] else {}
        doc_type = doc_row["doc_type"] if "doc_type" in doc_row.keys() else None
        raw_content = doc_row["raw_content"] if "raw_content" in doc_row.keys() else None

        # Reconstruct chat content from stored JSON for consistent rendering/offets
        chat_ranges: list[tuple[int, int]] = []
        if doc_type == "chat" and raw_content:
            try:
                data = json.loads(raw_content)
                # Stored as a list of messages (common for multi-turn/chat migrations)
                if isinstance(data, list):
                    parts: list[str] = []
                    cursor = 0
                    for item in data:
                        text = ""
                        if isinstance(item, dict):
                            text = item.get("content") or ""
                        else:
                            text = str(item)
                        parts.append(text)
                        start = cursor
                        end = start + len(text)
                        chat_ranges.append((start, end))
                        cursor = end + 1  # message chunker used +1 offset between messages
                    raw_content = "\n".join(parts)
                # Stored as a single message dict (default per-message ingestion)
                elif isinstance(data, dict):
                    text = data.get("content") or ""
                    chat_ranges = [(0, len(text))]
                    raw_content = text
                else:
                    # Unknown shape; fallback to chunk text path
                    chat_ranges = []
                    raw_content = None
            except Exception:
                chat_ranges = []
                raw_content = None

        content = raw_content if raw_content is not None else ""

        derived_ranges: list[tuple[int, int]] = []
        if raw_content is None:
            # Reconstruct content and ranges by concatenating chunk text
            parts = []
            cursor = 0
            for idx, row in enumerate(chunk_rows):
                text = row["text"] or ""
                start = cursor
                end = start + len(text)
                derived_ranges.append((start, end))
                parts.append(text)
                cursor = end
                if idx < len(chunk_rows) - 1:
                    parts.append("\n\n")
                    cursor += 2
            content = "".join(parts)

        chunk_entries = []
        search_cursor = 0
        for idx, row in enumerate(chunk_rows):
            text = row["text"] or ""
            if raw_content is None:
                start, end = derived_ranges[idx]
            else:
                start = row["char_start"] if "char_start" in row.keys() else None
                end = row["char_end"] if "char_end" in row.keys() else None
                # If we reconstructed chat content from JSON, prefer its computed ranges
                if chat_ranges and idx < len(chat_ranges):
                    start, end = chat_ranges[idx]
                if start is None or end is None or end < start:
                    if text:
                        found = content.find(text, search_cursor)
                        if found == -1:
                            found = content.find(text)
                        if found != -1:
                            start = found
                            end = found + len(text)
                            search_cursor = end
            chunk_entries.append(
                {
                    "id": row["id"],
                    "seq": row["seq"],
                    "text": text,
                    "char_start": start,
                    "char_end": end,
                    "heading_path": row["heading_path"] if "heading_path" in row.keys() else None,
                    "page_number": row["page_number"] if "page_number" in row.keys() else None,
                }
            )

        return {
            "id": doc_id,
            "title": doc_row["title"],
            "doc_type": doc_row["doc_type"],
            "source": doc_row["source"],
            "uri": doc_row["uri"],
            "tags": tags,
            "created_at": doc_row["created_at"],
            "updated_at": doc_row["event_at"] or doc_row["created_at"],
            "ingested_at": doc_row["ingested_at"] if "ingested_at" in doc_row.keys() else None,
            "chunk_strategy": doc_row["chunk_strategy"] if "chunk_strategy" in doc_row.keys() else None,
            "chunk_version": doc_row["chunk_version"] if "chunk_version" in doc_row.keys() else None,
            "content": content,
            "chunks": chunk_entries,
        }
    
    def _dense_retrieval(self, request: SearchRequest) -> List[Candidate]:
        """FAISS semantic search with metadata post-filtering."""
        from config import SHIYE_DEBUG_RETRIEVAL
        
        if SHIYE_DEBUG_RETRIEVAL:
            print(f"\n[DEBUG] Dense Retrieval Query: '{request.query}'")
            print(f"[DEBUG] Filters: {request.filters}")
        
        if not self.embedder or not self._faiss_index:
            return []
        
        # 1. Embed query
        try:
            query_vec = self.embedder.embed([request.query])[0]
        except Exception as e:
            print(f"[warn] dense retrieval embedding failed: {e}")
            return []
        
        # 2. FAISS over-retrieval (top 500 to account for filtering)
        try:
            chunk_ids, scores = self._faiss_index.search(query_vec, top_k=500)
        except Exception as e:
            print(f"[warn] FAISS search failed: {e}")
            return []
        
        if not chunk_ids:
            return []
        
        # 3. Fetch metadata and apply filters
        with self._connect() as conn:
            placeholders = ','.join('?' * len(chunk_ids))
            sql = f"""
            SELECT c.id, c.document_id, c.text, c.created_at, c.event_at, d.ingested_at, d.doc_type, c.tags as chunk_tags, d.tags as doc_tags
            FROM chunks c
            JOIN documents d ON c.document_id = d.id
            WHERE c.id IN ({placeholders})
              AND c.deleted = 0
            """
            
            params = list(chunk_ids)
            
            # Apply filters
            if request.filters.get('doc_type'):
                sql += " AND d.doc_type = ?"
                params.append(request.filters['doc_type'])
            
            if request.filters.get('before'):
                time_field = request.filters.get('time_field', 'created_at')
                # Use correct table prefix: d. for ingested_at, c. for others
                table_prefix = 'd.' if time_field == 'ingested_at' else 'c.'
                sql += f" AND {table_prefix}{time_field} < ?"
                params.append(request.filters['before'])
            
            if request.filters.get('after'):
                time_field = request.filters.get('time_field', 'created_at')
                # Use correct table prefix: d. for ingested_at, c. for others
                table_prefix = 'd.' if time_field == 'ingested_at' else 'c.'
                sql += f" AND {table_prefix}{time_field} > ?"
                params.append(request.filters['after'])
            
            # Apply tags filter - check both chunk and document tags
            if request.filters.get('tags'):
                sql += " AND (c.tags LIKE ? OR d.tags LIKE ?)"
                tag_pattern = f'%"{request.filters["tags"]}"%'
                params.append(tag_pattern)
                params.append(tag_pattern)
            
            rows = conn.execute(sql, params).fetchall()
        
        # 4. Build candidates with original FAISS scores
        results = []
        score_map = dict(zip(chunk_ids, scores))
        
        if SHIYE_DEBUG_RETRIEVAL:
            print(f"[DEBUG] Dense retrieval: {len(chunk_ids)} FAISS results, {len(rows)} after filtering")
        
        # Determine which timestamp field to use based on filter
        time_field = request.filters.get('time_field', 'created_at')
        
        for row in rows:
            # Extract the appropriate timestamp based on time_field filter
            if time_field == 'ingested_at':
                timestamp_str = row['ingested_at'] if row['ingested_at'] else None
            elif time_field == 'event_at':
                timestamp_str = row['event_at'] if row['event_at'] else None
            else:  # created_at (default)
                timestamp_str = row['created_at'] if row['created_at'] else None
            
            # Fallback to event_at or created_at if selected field is None
            if not timestamp_str:
                timestamp_str = row['event_at'] if row['event_at'] else row['created_at']
            
            results.append(Candidate(
                chunk_id=row['id'],
                score=float(score_map[row['id']]),
                channel='dense',
                full_text=row['text'] if row['text'] else "",
                doc_id=row['document_id'],
                doc_type=row['doc_type'],
                timestamp=ensure_utc(datetime.fromisoformat(timestamp_str)) if timestamp_str else None,
                text_preview=row['text'][:200] if row['text'] else "",
                score_history={'dense': float(score_map[row['id']])}
            ))
        
        results_sorted = sorted(results, key=lambda x: x.score, reverse=True)[:request.top_k * 2]
        
        if request.debug and getattr(self, '_last_debug_info', None) is not None:
            debug_info = self._last_debug_info
            debug_info['dense_query'] = request.query
            if "queries" in debug_info:
                debug_info["queries"]["dense"] = {"query": request.query, "filters": request.filters}
            if "stages" in debug_info:
                debug_info["stages"]["dense"]["retrieved"] = len(chunk_ids) if chunk_ids else 0
                debug_info["stages"]["dense"]["after_filters"] = len(results_sorted)
            debug_info['dense_results_count'] = len(chunk_ids) if chunk_ids else 0
            debug_info['dense_filtered_count'] = len(results_sorted)
        
        if SHIYE_DEBUG_RETRIEVAL and results_sorted:
            print(f"[DEBUG] Dense top-5 scores:")
            for i, c in enumerate(results_sorted[:5], 1):
                print(f"  {i}. chunk_id={c.chunk_id}, score={c.score:.4f}, doc_type={c.doc_type}")
        
        return results_sorted
    
    def _sparse_retrieval(self, request: SearchRequest) -> List[Candidate]:
        """SQLite FTS5 BM25 search."""
        from config import SHIYE_DEBUG_RETRIEVAL
        
        if SHIYE_DEBUG_RETRIEVAL:
            print(f"\n[DEBUG] Sparse Retrieval Query: '{request.query}'")
            print(f"[DEBUG] Filters: {request.filters}")
        
        # Skip sparse retrieval if FTS5 is not available
        if not self._fts5_available:
            return []
        
        with self._connect() as conn:
            # FTS5 full-text search with joins for filtering
            sql = """
            SELECT 
                f.chunk_id,
                f.text,
                bm25(chunks_fts) as score,
                c.document_id,
                c.created_at,
                c.event_at,
                d.ingested_at,
                f.doc_type,
                c.tags as chunk_tags,
                d.tags as doc_tags
            FROM chunks_fts AS f
            JOIN chunks c ON f.chunk_id = c.id
            JOIN documents d ON c.document_id = d.id
            WHERE chunks_fts MATCH ?
              AND c.deleted = 0
            """
            
            params = [request.query]
            
            # Apply filters (same as dense retrieval for consistency)
            if request.filters.get('doc_type'):
                sql += " AND f.doc_type = ?"
                params.append(request.filters['doc_type'])
            
            if request.filters.get('before'):
                time_field = request.filters.get('time_field', 'created_at')
                # Use correct table prefix: d. for ingested_at, c. for others
                table_prefix = 'd.' if time_field == 'ingested_at' else 'c.'
                sql += f" AND {table_prefix}{time_field} < ?"
                params.append(request.filters['before'])
            
            if request.filters.get('after'):
                time_field = request.filters.get('time_field', 'created_at')
                # Use correct table prefix: d. for ingested_at, c. for others
                table_prefix = 'd.' if time_field == 'ingested_at' else 'c.'
                sql += f" AND {table_prefix}{time_field} > ?"
                params.append(request.filters['after'])
            
            # Apply tags filter - check both chunk and document tags
            if request.filters.get('tags'):
                sql += " AND (c.tags LIKE ? OR d.tags LIKE ?)"
                tag_pattern = f'%"{request.filters["tags"]}"%'
                params.append(tag_pattern)
                params.append(tag_pattern)
            
            sql += " ORDER BY bm25(chunks_fts) LIMIT ?"
            params.append(request.top_k * 2)
            
            try:
                rows = conn.execute(sql, params).fetchall()
            except Exception as e:
                print(f"[warn] Sparse retrieval failed: {e}")
                return []
        
        # BM25 scores are negative (lower is better), normalize to positive
        results = []
        
        # Determine which timestamp field to use based on filter
        time_field = request.filters.get('time_field', 'created_at')
        
        for row in rows:
            # Extract the appropriate timestamp based on time_field filter
            if time_field == 'ingested_at':
                timestamp_str = row['ingested_at'] if row['ingested_at'] else None
            elif time_field == 'event_at':
                timestamp_str = row['event_at'] if row['event_at'] else None
            else:  # created_at (default)
                timestamp_str = row['created_at'] if row['created_at'] else None
            
            # Fallback to event_at or created_at if selected field is None
            if not timestamp_str:
                event_at = row['event_at'] if row['event_at'] else None
                created_at = row['created_at'] if row['created_at'] else None
                timestamp_str = event_at or created_at
            
            normalized_score = 1.0 / (1.0 + abs(row['score']))  # convert to 0-1 range
            results.append(Candidate(
                chunk_id=row['chunk_id'],
                score=normalized_score,
                channel='sparse',
                full_text=row['text'] if row['text'] else "",
                doc_id=row['document_id'],
                doc_type=row['doc_type'],
                timestamp=ensure_utc(datetime.fromisoformat(timestamp_str)) if timestamp_str else None,
                text_preview=row['text'][:200] if row['text'] else "",
                score_history={'sparse': normalized_score}
            ))
        
        if request.debug and getattr(self, '_last_debug_info', None) is not None:
            debug_info = self._last_debug_info
            debug_info['sparse_query'] = request.query
            if "queries" in debug_info:
                debug_info["queries"]["sparse"] = {"query": request.query, "filters": request.filters}
            if "stages" in debug_info:
                debug_info["stages"]["sparse"]["retrieved"] = len(results)
            debug_info['sparse_results_count'] = len(results)
        
        if SHIYE_DEBUG_RETRIEVAL and results:
            print(f"[DEBUG] Sparse retrieval: {len(results)} results")
            print(f"[DEBUG] Sparse top-5 scores:")
            for i, c in enumerate(results[:5], 1):
                print(f"  {i}. chunk_id={c.chunk_id}, score={c.score:.4f}, doc_type={c.doc_type}")
        
        return results
    
    def search_hybrid(self, request: SearchRequest) -> List[List[Candidate]]:
        """Run multiple retrievers and return channel-separated results."""
        # Retriever 1: Dense FAISS search
        dense_candidates = self._dense_retrieval(request)
        
        # Retriever 2: Sparse FTS5 search
        sparse_candidates = self._sparse_retrieval(request)
        
        return [dense_candidates, sparse_candidates]
    
    def _fuse_rrf(self, retriever_results: List[List[Candidate]], k: int = 60) -> List[Candidate]:
        """Reciprocal Rank Fusion: combine rankings from multiple retrievers.
        
        RRF score = sum over all retrievers of: 1 / (k + rank)
        where k=60 is standard constant, rank is 1-indexed position.
        """
        from collections import defaultdict
        from config import SHIYE_DEBUG_RETRIEVAL
        
        rrf_scores = defaultdict(float)
        chunk_map = {}  # chunk_id -> best Candidate object
        
        for channel_results in retriever_results:
            for rank, candidate in enumerate(channel_results, start=1):
                rrf_scores[candidate.chunk_id] += 1.0 / (k + rank)
                
                # Keep candidate with most metadata and merge score history
                if candidate.chunk_id not in chunk_map:
                    chunk_map[candidate.chunk_id] = candidate
                else:
                    # Merge score histories
                    chunk_map[candidate.chunk_id].score_history.update(candidate.score_history)
        
        # Build fused candidates
        fused = []
        for chunk_id, rrf_score in sorted(rrf_scores.items(), key=lambda x: x[1], reverse=True):
            candidate = chunk_map[chunk_id]
            candidate.score = rrf_score
            candidate.channel = 'fused'
            candidate.score_history['fused'] = rrf_score
            fused.append(candidate)
        
        if getattr(self, '_last_debug_info', None) is not None:
            self._last_debug_info['fused_count'] = len(fused)
            if "stages" in self._last_debug_info:
                self._last_debug_info["stages"]["fusion"]["unique"] = len(fused)
        
        if SHIYE_DEBUG_RETRIEVAL and fused:
            print(f"\n[DEBUG] RRF Fusion: {len(fused)} unique candidates")
            print(f"[DEBUG] RRF top-5 fused scores:")
            for i, c in enumerate(fused[:5], 1):
                print(f"  {i}. chunk_id={c.chunk_id}, rrf_score={c.score:.4f}, history={c.score_history}")
        
        return fused
    
    def search(self, request: SearchRequest) -> List[Candidate]:
        """Full search pipeline with hybrid retrieval, fusion, rerank, and post-processing."""
        from config import SHIYE_RRF_K, SHIYE_RECENCY_DECAY_DAYS, SHIYE_DEBUG_RETRIEVAL, SHIYE_RERANK_TOP_K
        
        # Initialize debug info if requested
        debug_info = self._init_debug_info(request) if request.debug else None
        if not request.debug:
            self._last_debug_info = None
        
        if SHIYE_DEBUG_RETRIEVAL:
            print(f"\n{'='*80}")
            print(f"[DEBUG] SEARCH PIPELINE START")
            print(f"[DEBUG] Query: '{request.query}'")
            print(f"[DEBUG] Filters: {request.filters}")
            print(f"[DEBUG] Top-K: {request.top_k}")
            print(f"[DEBUG] Rerank: {request.enable_rerank}, Time Boost: {request.enable_time_boost}, Exact Boost: {request.enable_exact_boost}")
            print(f"{'='*80}")
        
        # Stage B: Multi-retrieval
        retriever_results = self.search_hybrid(request)
        
        # Stage C: Fusion with configured RRF k
        fused = self._fuse_rrf(retriever_results, k=SHIYE_RRF_K)
        
        if not fused:
            return []
        
        # Stage D: Reranking
        if request.enable_rerank and self.reranker:
            try:
                rerank_top = min(SHIYE_RERANK_TOP_K, len(fused))
                if SHIYE_DEBUG_RETRIEVAL:
                    print(f"\n[DEBUG] Reranking top {rerank_top} candidates...")
                fused = self.reranker.rerank(request.query, fused, self)
                if request.debug and getattr(self, '_last_debug_info', None) is not None:
                    debug_info = self._last_debug_info
                    debug_info['reranked'] = True
                    debug_info['rerank_count'] = rerank_top
                    if "stages" in debug_info:
                        debug_info["stages"]["rerank"]["applied"] = True
                        debug_info["stages"]["rerank"]["top_k"] = rerank_top
                if SHIYE_DEBUG_RETRIEVAL and fused:
                    print(f"[DEBUG] Rerank top-5 scores:")
                    for i, c in enumerate(fused[:5], 1):
                        print(f"  {i}. chunk_id={c.chunk_id}, rerank_score={c.score:.4f}, history={c.score_history}")
            except Exception as e:
                print(f"[warn] Reranking failed: {e}")
        
        # Stage E: Post-processing with configured values
        post_processors = [
            RecencyBooster(decay_days=SHIYE_RECENCY_DECAY_DAYS),
            TypeBooster(),
            ExactMatchBooster(),
            Deduplicator(mode='by_doc')
        ]
        
        result = fused
        for processor in post_processors:
            try:
                if SHIYE_DEBUG_RETRIEVAL:
                    print(f"\n[DEBUG] Applying {processor.__class__.__name__}...")
                result = processor.process(request, result)
                if request.debug and getattr(self, '_last_debug_info', None) is not None:
                    debug_info = self._last_debug_info
                    debug_info['post_processors'].append(processor.__class__.__name__)
                    if "stages" in debug_info:
                        debug_info["stages"]["post_processors"].append(processor.__class__.__name__)
                if SHIYE_DEBUG_RETRIEVAL and result:
                    print(f"[DEBUG] After {processor.__class__.__name__} top-5:")
                    for i, c in enumerate(result[:5], 1):
                        print(f"  {i}. chunk_id={c.chunk_id}, score={c.score:.4f}, history={c.score_history}")
            except Exception as e:
                print(f"[warn] Post-processor {processor.__class__.__name__} failed: {e}")

        # Track how many candidates received an exact-match boost
        if request.debug and getattr(self, '_last_debug_info', None) is not None:
            matches = sum(1 for c in result if 'exact_match_boost' in c.score_history)
            self._last_debug_info['exact_results_count'] = matches
            if "stages" in self._last_debug_info:
                self._last_debug_info["stages"].setdefault("exact", {})
                self._last_debug_info["stages"]["exact"]["retrieved"] = matches
        
        # Add final score to history
        for candidate in result:
            candidate.score_history['final'] = candidate.score
        
        final_results = result[:request.top_k]
        
        # Collect debug info for top candidates
        if request.debug and getattr(self, '_last_debug_info', None) is not None:
            debug_info = self._last_debug_info
            debug_info['final_count'] = len(final_results)
            if "stages" in debug_info:
                debug_info["stages"]["final"]["returned"] = len(final_results)
            debug_info['candidates'] = []
            debug_info['top_candidates'] = []
            for i, c in enumerate(final_results, 1):
                preview = c.text_preview[:140] if c.text_preview else ""
                score_history = dict(c.score_history)
                candidate_entry = {
                    'rank': i,
                    'chunk_id': c.chunk_id,
                    'doc_id': c.doc_id,
                    'doc_type': c.doc_type,
                    'final_score': c.score,
                    'score_history': score_history,
                    'score_trace': self._format_score_history(score_history),
                    'text_preview': preview
                }
                debug_info['candidates'].append(candidate_entry)
                if i <= 10:
                    debug_info['top_candidates'].append(candidate_entry)
        
        if SHIYE_DEBUG_RETRIEVAL:
            print(f"\n[DEBUG] FINAL RESULTS ({len(final_results)} candidates):")
            for i, c in enumerate(final_results, 1):
                print(f"  {i}. chunk_id={c.chunk_id}, doc_id={c.doc_id}, doc_type={c.doc_type}")
                print(f"     Final Score: {c.score:.4f}")
                print(f"     Score History: {c.score_history}")
                preview = c.text_preview[:100] if c.text_preview else ""
                print(f"     Text Preview: {preview}...")
                print()
            print(f"{'='*80}")
            print(f"[DEBUG] SEARCH PIPELINE END\n")
        
        return final_results
