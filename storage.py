import json
import re
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import List, Optional, Sequence

import numpy as np

from config import DATA_DIR, DB_PATH, MODEL_NAME
from datatypes import Message, Role, ensure_utc
from embeddings import EmbeddingProvider
from vector_store import FaissIndex, faiss
from retrieval import SearchRequest, Candidate, Reranker, RecencyBooster, TypeBooster, ExactMatchBooster, Deduplicator


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
        self._ensure_schema()
        self._migrate_schema_v2()
        self.default_doc_id = self._ensure_default_document()
        if self.embedder:
            try:
                self.embedder.load()
                self._faiss_index = FaissIndex(dim=self.embedder.dim)
            except Exception as e:
                print(f"[warn] Embedding index unavailable: {e}")

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
                    status TEXT
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
                
                if not fts_exists:
                    # Test FTS5 support before attempting to create
                    try:
                        cur.execute("SELECT fts5_version()")
                        fts5_version = cur.fetchone()[0]
                        print(f"[info] FTS5 version: {fts5_version}")
                    except Exception as e:
                        print(f"[ERROR] FTS5 is not available in this SQLite build: {e}")
                        print("[ERROR] Sparse search will not be available. Please upgrade SQLite with FTS5 support.")
                        return
                    
                    # Create FTS5 virtual table for sparse search
                    cur.execute("""
                        CREATE VIRTUAL TABLE chunks_fts USING fts5(
                            chunk_id UNINDEXED,
                            text,
                            doc_type UNINDEXED,
                            tokenize='porter unicode61'
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

    def _maybe_embed(self, texts: Sequence[str]):
        if self.embedder and self._faiss_index:
            try:
                return self.embedder.embed(texts)
            except Exception as e:
                print(f"[warn] embedding failed: {e}")
        return None

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

    def _row_to_message(self, row: sqlite3.Row) -> Message:
        metadata = json.loads(row["tags"]) if row["tags"] else {}
        metadata["chunk_id"] = row["id"]
        return Message(
            content=row["text"],
            role=Role(row["role"]) if row["role"] else Role.USER,
            created_at=ensure_utc(datetime.fromisoformat(row["created_at"])) if row["created_at"] else datetime.now(UTC),
            reference_time=ensure_utc(datetime.fromisoformat(row["event_at"])) if row["event_at"] else None,
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
                INSERT INTO documents (source, uri, doc_type, created_at, event_at, ingested_at, title, tags, sensitivity, hash, status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                ),
            )
            return cur.lastrowid

    def add_messages(self, messages: Sequence[Message], document_meta: Optional[dict] = None) -> List[int]:
        ids: List[int] = []
        if not messages:
            return ids
        embeddings = self._maybe_embed([m.content for m in messages])
        doc_id = self.default_doc_id
        if document_meta:
            try:
                doc_id = self._insert_document(document_meta)
            except Exception as e:
                print(f"[warn] failed to insert document, falling back to default: {e}")
        with self._connect() as conn:
            cur = conn.cursor()
            now_iso = datetime.now(UTC).isoformat()
            for idx, msg in enumerate(messages):
                embedding_id = None
                if embeddings is not None:
                    embedding_id = None  # filled after chunk id known
                created_at = ensure_utc(msg.created_at) or datetime.now(UTC)
                cur.execute(
                    """
                    INSERT INTO chunks (document_id, seq, text, role, token_count, embedding_id, created_at, event_at, tags, focus_hint, char_start, char_end, embedding_model)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        doc_id,
                        idx,
                        msg.content,
                        msg.role.value,
                        None,
                        None,
                        created_at.isoformat(),
                        ensure_utc(msg.reference_time).isoformat() if msg.reference_time else None,
                        json.dumps(msg.metadata) if msg.metadata else None,
                        msg.metadata.get("focus_hint") if msg.metadata else None,
                        msg.metadata.get("char_start", 0) if msg.metadata else 0,
                        msg.metadata.get("char_end", -1) if msg.metadata else -1,
                        MODEL_NAME,
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
                self._write_index_meta(cur, now_iso)
        return ids

    def list_recent(self, n: int = 20) -> List[Message]:
        with self._connect() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT * FROM chunks
                WHERE deleted = 0
                ORDER BY datetime(created_at) DESC
                LIMIT ?
                """,
                (n,),
            )
            rows = cur.fetchall()
        return [self._row_to_message(r) for r in reversed(rows)]

    def list_messages_by_day(self, day: str, limit: int = 500) -> List[Message]:
        with self._connect() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT * FROM chunks
                WHERE deleted = 0 AND created_at IS NOT NULL AND date(created_at) = ?
                ORDER BY datetime(created_at)
                LIMIT ?
                """,
                (day, limit),
            )
            rows = cur.fetchall()
        return [self._row_to_message(r) for r in rows]

    def list_message_days(self, limit: int = 180) -> List[dict]:
        with self._connect() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT date(created_at) AS day, COUNT(*) AS count
                FROM chunks
                WHERE deleted = 0 AND created_at IS NOT NULL
                GROUP BY day
                ORDER BY day DESC
                LIMIT ?
                """,
                (limit,),
            )
            rows = cur.fetchall()
        return [{"day": row["day"], "count": row["count"]} for row in rows if row["day"]]

    def context_block(self, n: int = 13) -> List[Message]:
        with self._connect() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT * FROM chunks
                WHERE deleted = 0
                ORDER BY datetime(created_at) DESC
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
        return {
            "id": note_id,
            "title": title,
            "content": chunk_row["text"] if chunk_row else "",
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
                    "UPDATE documents SET title = ?, event_at = ?, tags = ? WHERE id = ?",
                    (title, now_iso, json.dumps(base_tags | {"note_title": title}), note_id),
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
        updated_at = doc_row["event_at"] or doc_row["created_at"]
        images = tags.get("images") or []
        return {
            "id": note_id,
            "title": doc_row["title"] or tags.get("note_title") or "Untitled note",
            "content": chunk_row["text"] if chunk_row else "",
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
        """Soft-delete a chunk and remove from FAISS if possible."""
        with self._connect() as conn:
            cur = conn.cursor()
            cur.execute("UPDATE chunks SET deleted = 1 WHERE id = ?", (chunk_id,))
            changed = cur.rowcount
        if changed and self._faiss_index:
            try:
                selector = faiss.IDSelectorBatch(np.array([chunk_id], dtype="int64"))
                self._faiss_index.index.remove_ids(selector)
            except Exception:
                pass  # ignore FAISS removal errors
        return bool(changed)

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
                chunk_window=row['chunk_window'] if 'chunk_window' in row.keys() else None
            )
    
    def get_document(self, doc_id: int) -> dict:
        """Fetch document metadata."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM documents WHERE id = ?",
                (doc_id,)
            ).fetchone()
            return dict(row) if row else {}
    
    def _dense_retrieval(self, request: SearchRequest) -> List[Candidate]:
        """FAISS semantic search with metadata post-filtering."""
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
                doc_id=row['document_id'],
                doc_type=row['doc_type'],
                timestamp=ensure_utc(datetime.fromisoformat(timestamp_str)) if timestamp_str else None,
                text_preview=row['text'][:200] if row['text'] else "",
                score_history={'dense': float(score_map[row['id']])}
            ))
        
        return sorted(results, key=lambda x: x.score, reverse=True)[:request.top_k * 2]
    
    def _sparse_retrieval(self, request: SearchRequest) -> List[Candidate]:
        """SQLite FTS5 BM25 search."""
        with self._connect() as conn:
            # FTS5 full-text search with joins for filtering
            sql = """
            SELECT 
                f.chunk_id,
                f.text,
                bm25(f) as score,
                c.document_id,
                c.created_at,
                c.event_at,
                d.ingested_at,
                f.doc_type,
                c.tags as chunk_tags,
                d.tags as doc_tags
            FROM chunks_fts f
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
            
            sql += " ORDER BY bm25(f) LIMIT ?"
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
                doc_id=row['document_id'],
                doc_type=row['doc_type'],
                timestamp=ensure_utc(datetime.fromisoformat(timestamp_str)) if timestamp_str else None,
                text_preview=row['text'][:200] if row['text'] else "",
                score_history={'sparse': normalized_score}
            ))
        
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
        
        return fused
    
    def search(self, request: SearchRequest) -> List[Candidate]:
        """Full search pipeline with hybrid retrieval, fusion, rerank, and post-processing."""
        from config import SHIYE_RRF_K, SHIYE_RECENCY_DECAY_DAYS
        
        # Stage B: Multi-retrieval
        retriever_results = self.search_hybrid(request)
        
        # Stage C: Fusion with configured RRF k
        fused = self._fuse_rrf(retriever_results, k=SHIYE_RRF_K)
        
        if not fused:
            return []
        
        # Stage D: Reranking
        if request.enable_rerank and self.reranker:
            try:
                fused = self.reranker.rerank(request.query, fused, self)
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
                result = processor.process(request, result)
            except Exception as e:
                print(f"[warn] Post-processor {processor.__class__.__name__} failed: {e}")
        
        # Add final score to history
        for candidate in result:
            candidate.score_history['final'] = candidate.score
        
        return result[:request.top_k]
