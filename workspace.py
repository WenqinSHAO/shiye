from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import List, Optional

from datatypes import Message, ensure_utc
from embeddings import EmbeddingProvider
from storage import LocalStore, NoteConflictError


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
                self.store = LocalStore(embedder=EmbeddingProvider())
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
            return self.store.save_note(content, title=title, note_id=note_id, expected_updated_at=expected_updated_at)
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
  
