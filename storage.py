import json
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import List, Optional, Sequence

import numpy as np

from config import DATA_DIR, DB_PATH
from datatypes import Message, Role
from embeddings import EmbeddingProvider
from vector_store import FaissIndex, faiss


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


class LocalStore:
    """SQLite + FAISS-backed store for documents/chunks/events."""

    def __init__(
        self,
        db_path: Path = DB_PATH,
        data_dir: Path = DATA_DIR,
        embedder: Optional[EmbeddingProvider] = None,
    ) -> None:
        self.db_path = db_path
        self.data_dir = data_dir
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.embedder = embedder
        self._faiss_index: Optional[FaissIndex] = None
        self._ensure_schema()
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
        return Message(
            content=row["text"],
            role=Role(row["role"]) if row["role"] else Role.USER,
            created_at=datetime.fromisoformat(row["created_at"]),
            reference_time=datetime.fromisoformat(row["event_at"]) if row["event_at"] else None,
            metadata=json.loads(row["tags"]) if row["tags"] else {},
        )

    def _insert_document(self, doc_meta: dict) -> int:
        now = datetime.now(UTC).isoformat()
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
                    doc_meta.get("created_at", now),
                    doc_meta.get("event_at"),
                    doc_meta.get("ingested_at", now),
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
        embeddings = None
        if self.embedder and self._faiss_index:
            try:
                embeddings = self.embedder.embed(m.content for m in messages)
            except Exception as e:
                print(f"[warn] embedding failed: {e}")
                embeddings = None
        doc_id = self.default_doc_id
        if document_meta:
            try:
                doc_id = self._insert_document(document_meta)
            except Exception as e:
                print(f"[warn] failed to insert document, falling back to default: {e}")
        with self._connect() as conn:
            cur = conn.cursor()
            now = datetime.now(UTC).isoformat()
            for idx, msg in enumerate(messages):
                embedding_id = None
                if embeddings is not None:
                    embedding_id = None  # filled after chunk id known
                cur.execute(
                    """
                    INSERT INTO chunks (document_id, seq, text, role, token_count, embedding_id, created_at, event_at, tags, focus_hint)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        doc_id,
                        idx,
                        msg.content,
                        msg.role.value,
                        None,
                        None,
                        msg.created_at.isoformat(),
                        msg.reference_time.isoformat() if msg.reference_time else None,
                        json.dumps(msg.metadata) if msg.metadata else None,
                        msg.metadata.get("focus_hint") if msg.metadata else None,
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
                        now,
                    ),
                )
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

    def _get_chunk_by_id(self, chunk_id: int) -> Optional[Message]:
        with self._connect() as conn:
            cur = conn.cursor()
            cur.execute("SELECT * FROM chunks WHERE id = ? AND deleted = 0", (chunk_id,))
            row = cur.fetchone()
        return self._row_to_message(row) if row else None

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
