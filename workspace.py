from dataclasses import dataclass, field
from typing import List, Optional

from datatypes import Message
from embeddings import EmbeddingProvider
from storage import LocalStore


@dataclass
class MemoryWorkspace:
    """Workspace backed by SQLite + FAISS, with in-memory fallback."""

    max_items: int = 500  # legacy cap for in-memory fallback
    _fallback_items: List[Message] = field(default_factory=list)
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
  
