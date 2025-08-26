from dataclasses import dataclass, field
from typing import List, Dict, Optional
from datatypes import Message

@dataclass
class MemoryWorkspace:
    items: List[Message] = field(default_factory=list)
    max_items: int = 500  # simple cap

    def add(self, m: Message) -> None:
        self.items.append(m)
        if len(self.items) > self.max_items:
            # Trim oldest when exceeding cap
            overflow = len(self.items) - self.max_items
            del self.items[:overflow]

    def list_recent(self, n: int = 20) -> List[Message]:
        return self.items[-n:]

    def clear(self) -> None:
        self.items.clear()

    def recall(self, query: str) -> Optional[Message]:
        q = query.lower().strip()
        for item in reversed(self.items):
            if q in item.content.lower():
                return item
        return None

    def context_block(self, n: int = 13) -> List[Message]:
        """Return last n messages as a formatted context block."""
        return self.items[-n:]
  
