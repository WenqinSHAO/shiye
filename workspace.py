from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import List, Optional
import json

from datatypes import Message, Role, ensure_utc
from lifelong_summary import LifelongSummary, build_lifelong_summary
from embeddings import EmbeddingProvider
from storage import LocalStore, NoteConflictError, StoredChunk
from retrieval import SearchRequest, SearchHit
from context_assembly import build_chunk_window


@dataclass
class MemoryWorkspace:
    """Workspace backed by SQLite + FAISS, with in-memory fallback."""

    max_items: int = 500  # legacy cap for in-memory fallback
    _fallback_items: List[Message] = field(default_factory=list)
    _fallback_notes: List[dict] = field(default_factory=list)
    _note_counter: int = 0
    store: Optional[LocalStore] = None

    def __post_init__(self) -> None:
        if not self.store:
            try:
                from config import SHIYE_RERANKER
                from retrieval import FlashRankReranker
                
                # Initialize reranker based on config
                reranker = None
                if SHIYE_RERANKER and SHIYE_RERANKER.lower() != 'none':
                    try:
                        if SHIYE_RERANKER.lower() == 'flashrank':
                            from config import DATA_DIR
                            reranker = FlashRankReranker(cache_dir=DATA_DIR / 'models')
                        # Add other rerankers here as needed (bge, etc.)
                    except Exception as e:
                        print(f"[warn] Failed to initialize reranker {SHIYE_RERANKER}: {e}")
                
                self.store = LocalStore(embedder=EmbeddingProvider(), reranker=reranker)
            except Exception as e:
                print(f"[warn] falling back to in-memory store: {e}")
                self.store = None

    # --- primary operations -------------------------------------------------
    def add(self, m: Message) -> None:
        if self.store:
            self.store.add_messages([m])
            return
        self._fallback_items.append(m)
        if len(self._fallback_items) > self.max_items:
            overflow = len(self._fallback_items) - self.max_items
            del self._fallback_items[:overflow]

    def add_with_document(self, messages: List[Message], document_meta: dict) -> None:
        if self.store:
            self.store.add_messages(messages, document_meta=document_meta)
            return
        # fallback: just add to memory
        for m in messages:
            self.add(m)

    def save_lifelong_summary(
        self,
        payload: dict,
        markdown: str,
        summary_date: Optional[datetime] = None,
        title: Optional[str] = None,
        summary_source: str = "system",
        facet: Optional[str] = None,
        topic: Optional[str] = None,
        tags: Optional[dict] = None,
    ) -> Optional[dict]:
        summary: LifelongSummary = build_lifelong_summary(
            payload=payload,
            markdown=markdown,
            summary_date=summary_date,
            title=title,
            summary_source=summary_source,
            facet=facet,
            topic=topic,
            tags=tags,
        )
        content = summary.render_document()
        document_meta = summary.document_meta()
        if self.store:
            result = self.store.add_document_chunked(content=content, document_meta=document_meta)
            result["title"] = document_meta.get("title")
            return result
        self.add(Message(content=content, role=Role.SYSTEM))
        return None

    def list_lifelong_summaries(
        self,
        limit: int = 20,
        facet: Optional[str] = None,
        topic: Optional[str] = None,
    ) -> List[dict]:
        if self.store:
            return self.store.list_lifelong_summaries(limit=limit, facet=facet, topic=topic)
        return []

    def get_latest_lifelong_summary(
        self,
        facet: Optional[str] = None,
        topic: Optional[str] = None,
    ) -> Optional[dict]:
        if self.store:
            return self.store.get_latest_lifelong_summary(facet=facet, topic=topic)
        return None

    def list_messages_since(self, since: datetime, limit: int = 200) -> List[Message]:
        if self.store:
            return self.store.list_messages_since(since=since, limit=limit)
        return [m for m in self._fallback_items if ensure_utc(m.created_at) >= ensure_utc(since)][:limit]

    def list_recent(self, n: int = 20) -> List[Message]:
        if self.store:
            return self.store.list_recent(n)
        return self._fallback_items[-n:]

    def list_messages_by_day(self, day: str, limit: int = 500) -> List[Message]:
        """Return messages for a given calendar day (YYYY-MM-DD), ordered by creation."""
        if self.store:
            return self.store.list_messages_by_day(day=day, limit=limit)
        results: List[Message] = []
        for msg in self._fallback_items:
            if not getattr(msg, "created_at", None):
                continue
            dt = ensure_utc(msg.created_at)
            if dt.date().isoformat() == day:
                results.append(msg)
        results.sort(key=lambda m: ensure_utc(m.created_at))
        return results[:limit]

    def list_message_days(self, limit: int = 180) -> List[dict]:
        """Return available message days with counts, newest first."""
        if self.store:
            return self.store.list_message_days(limit=limit)
        counts = {}
        for msg in self._fallback_items:
            if not getattr(msg, "created_at", None):
                continue
            day = ensure_utc(msg.created_at).date().isoformat()
            counts[day] = counts.get(day, 0) + 1
        days = [{"day": k, "count": v} for k, v in counts.items()]
        days.sort(key=lambda d: d["day"], reverse=True)
        return days[:limit]

    def clear(self) -> None:
        if self.store:
            self.store.clear()
            return
        self._fallback_items.clear()

    def recall(self, query: str) -> Optional[Message]:
        if self.store:
            return self.store.recall(query)
        q = query.lower().strip()
        for item in reversed(self._fallback_items):
            if q in item.content.lower():
                return item
        return None

    def context_block(self, n: int = 13) -> List[Message]:
        if self.store:
            return self.store.context_block(n)
        return self._fallback_items[-n:]

    def delete_chunk(self, chunk_id: int) -> bool:
        if self.store:
            return self.store.delete_chunk(chunk_id)
        # fallback: delete by metadata if present
        for idx, msg in enumerate(self._fallback_items):
            if msg.metadata.get("chunk_id") == chunk_id:
                del self._fallback_items[idx]
                return True
        return False

    def save_note(
        self,
        content: str,
        title: Optional[str] = None,
        note_id: Optional[int] = None,
        expected_updated_at: Optional[str] = None,
    ) -> Optional[dict]:
        if self.store:
            # Use chunked notes by default for better retrieval
            return self.store.save_note_chunked(content, title=title, note_id=note_id, expected_updated_at=expected_updated_at)
        derived_title = (title or "").strip()
        if not derived_title:
            for line in content.splitlines():
                candidate = line.strip().lstrip("#").strip()
                if candidate:
                    derived_title = candidate
                    break
        derived_title = derived_title or "Untitled note"
        now_iso = datetime.now(UTC).isoformat()
        expected_dt = None
        if expected_updated_at:
            try:
                expected_dt = ensure_utc(datetime.fromisoformat(expected_updated_at))
            except Exception:
                expected_dt = None
        images = []
        if "!(" in content:
            import re as _re  # local import to avoid top-level dependency when unused
            images = [m for m in _re.findall(r"!\[[^\]]*\]\(([^)]+)\)", content) if m]
        if note_id:
            for note in self._fallback_notes:
                if note["id"] == note_id:
                    if expected_dt:
                        try:
                            current_ts = note.get("updated_at") or note.get("created_at")
                            if current_ts:
                                current_dt = ensure_utc(datetime.fromisoformat(current_ts))
                                if current_dt > expected_dt:
                                    raise NoteConflictError(note)
                        except NoteConflictError:
                            raise
                        except Exception:
                            pass
                    note.update(
                        {
                            "title": derived_title,
                            "content": content,
                            "updated_at": now_iso,
                            "images": images,
                        }
                    )
                    return note
            return None
        self._note_counter += 1
        note = {
            "id": self._note_counter,
            "title": derived_title,
            "content": content,
            "created_at": now_iso,
            "updated_at": now_iso,
            "images": images,
        }
        self._fallback_notes.append(note)
        return note

    def list_notes(self, limit: int = 50) -> List[dict]:
        if self.store:
            return self.store.list_notes(limit=limit)
        return sorted(self._fallback_notes, key=lambda n: n.get("updated_at") or n.get("created_at"), reverse=True)[
            :limit
        ]

    def get_note(self, note_id: int) -> Optional[dict]:
        if self.store:
            return self.store.get_note(note_id)
        for note in self._fallback_notes:
            if note["id"] == note_id:
                return note
        return None

    def search(self, request: SearchRequest) -> List[SearchHit]:
        """Enhanced semantic search with hybrid retrieval."""
        if not self.store:
            return []
        
        candidates = self.store.search(request)
        if not candidates:
            return []

        chunk_ids = [candidate.chunk_id for candidate in candidates]

        chunk_rows = []
        with self.store._connect() as conn:
            cur = conn.cursor()
            placeholders = ",".join("?" for _ in chunk_ids)
            cur.execute(
                f"""
                SELECT c.id as chunk_id,
                       c.document_id,
                       c.text,
                       c.role,
                       c.created_at,
                       c.event_at,
                       c.embedding_id,
                       c.tags,
                       c.focus_hint,
                       c.char_start,
                       c.char_end,
                       c.embedding_model,
                       c.chunk_window,
                       c.heading_path,
                       c.page_number,
                       c.parent_doc_seq,
                       c.seq,
                       d.id as doc_id,
                       d.doc_type,
                       d.title,
                       d.uri,
                       d.source,
                       d.ingested_at
                FROM chunks c
                JOIN documents d ON c.document_id = d.id
                WHERE c.id IN ({placeholders}) AND c.deleted = 0
                """,
                chunk_ids,
            )
            chunk_rows = cur.fetchall()

        chunks_by_id = {}
        docs_by_id = {}
        chunk_seqs = {}
        for row in chunk_rows:
            chunk_id = row["chunk_id"]
            doc_id = row["doc_id"]
            chunk_seqs[chunk_id] = row["seq"]
            chunks_by_id[chunk_id] = StoredChunk(
                id=chunk_id,
                document_id=row["document_id"],
                text=row["text"],
                role=Role(row["role"]) if row["role"] else Role.USER,
                created_at=ensure_utc(datetime.fromisoformat(row["created_at"])) if row["created_at"] else datetime.now(UTC),
                reference_time=ensure_utc(datetime.fromisoformat(row["event_at"])) if row["event_at"] else None,
                embedding_id=row["embedding_id"],
                tags=json.loads(row["tags"]) if row["tags"] else None,
                focus_hint=row["focus_hint"],
                char_start=row["char_start"] if "char_start" in row.keys() else 0,
                char_end=row["char_end"] if "char_end" in row.keys() else -1,
                embedding_model=row["embedding_model"] if "embedding_model" in row.keys() else None,
                chunk_window=row["chunk_window"] if "chunk_window" in row.keys() else None,
                heading_path=row["heading_path"] if "heading_path" in row.keys() else None,
                page_number=row["page_number"] if "page_number" in row.keys() else None,
                parent_doc_seq=row["parent_doc_seq"] if "parent_doc_seq" in row.keys() else None,
            )
            if doc_id not in docs_by_id:
                docs_by_id[doc_id] = {
                    "id": doc_id,
                    "doc_type": row["doc_type"],
                    "title": row["title"],
                    "uri": row["uri"],
                    "source": row["source"],
                    "ingested_at": row["ingested_at"],
                }

        chunk_window_by_id = {}
        chunk_window_targets = {
            chunk_id: chunk
            for chunk_id, chunk in chunks_by_id.items()
            if not chunk.chunk_window
        }
        if chunk_window_targets:
            doc_ids = sorted({chunk.document_id for chunk in chunk_window_targets.values()})
            with self.store._connect() as conn:
                cur = conn.cursor()
                placeholders = ",".join("?" for _ in doc_ids)
                cur.execute(
                    f"""
                    SELECT document_id, seq, text
                    FROM chunks
                    WHERE document_id IN ({placeholders}) AND deleted = 0
                    ORDER BY document_id, seq
                    """,
                    doc_ids,
                )
                rows = cur.fetchall()
            chunks_by_doc = {}
            for row in rows:
                chunks_by_doc.setdefault(row["document_id"], []).append((row["seq"], row["text"]))

            window_size = 1
            for chunk_id, chunk in chunk_window_targets.items():
                seq = chunk_seqs.get(chunk_id)
                if seq is None:
                    continue
                parts = []
                for neighbor_seq, text in chunks_by_doc.get(chunk.document_id, []):
                    if neighbor_seq < seq - window_size or neighbor_seq > seq + window_size:
                        continue
                    text = text or ""
                    if len(text) > 200:
                        if neighbor_seq < seq:
                            text = "..." + text[-150:]
                        elif neighbor_seq > seq:
                            text = text[:150] + "..."
                        else:
                            text = text[:100] + " ... " + text[-100:]
                    parts.append(text)
                chunk_window_by_id[chunk_id] = " ".join(parts) if parts else None
        
        # Convert Candidate → SearchHit with full metadata
        hits = []
        for rank, candidate in enumerate(candidates, start=1):
            try:
                chunk = chunks_by_id.get(candidate.chunk_id)
                if not chunk:
                    raise ValueError(f"Chunk {candidate.chunk_id} not found")
                doc = docs_by_id.get(candidate.doc_id, {})
                
                # Build chunk_window if not already populated
                chunk_window = chunk.chunk_window or chunk_window_by_id.get(chunk.id)
                if not chunk_window:
                    chunk_window = build_chunk_window(self.store, chunk.id, window_size=1)
                
                hit = SearchHit(
                    chunk_id=chunk.id,
                    doc_id=doc.get('id', candidate.doc_id),
                    doc_type=doc.get('doc_type', candidate.doc_type or 'unknown'),
                    doc_title=doc.get('title'),
                    doc_source=doc.get('uri') or doc.get('source'),
                    text=chunk.text,
                    char_start=chunk.char_start,
                    char_end=chunk.char_end,
                    chunk_window=chunk_window,
                    created_at=chunk.created_at,
                    event_at=chunk.reference_time,
                    ingested_at=ensure_utc(datetime.fromisoformat(doc.get('ingested_at'))) if doc.get('ingested_at') else None,
                    scores=candidate.score_history.copy(),  # Pass complete score history
                    rank=rank,
                    tags=chunk.tags if isinstance(chunk.tags, list) else (list(chunk.tags.keys()) if isinstance(chunk.tags, dict) else []),
                    focus_hint=chunk.focus_hint,
                    # v0.8 chunking metadata
                    heading_path=chunk.heading_path,
                    page_number=chunk.page_number,
                    seq=chunk_seqs.get(chunk.id)
                )
                hits.append(hit)
            except Exception as e:
                print(f"[warn] Failed to convert candidate {candidate.chunk_id} to SearchHit: {e}")
                continue
        
        return hits

    def get_document(self, doc_id: int) -> dict:
        """Return a document with content and chunk ranges for highlighting."""
        if self.store:
            return self.store.get_document_with_chunks(doc_id)
        return {}
    
    def get_last_search_debug_info(self) -> Optional[dict]:
        """Get debug info from last search operation."""
        if self.store and hasattr(self.store, '_last_debug_info'):
            return self.store._last_debug_info
        return None
  
