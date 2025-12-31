from dataclasses import dataclass, field
from datetime import datetime, UTC
from enum import Enum
from typing import Optional, Dict, Any
import json


def ensure_utc(dt: Optional[datetime]) -> Optional[datetime]:
    """Return a timezone-aware UTC datetime (or None)."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt

class Role(Enum):
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"

@dataclass
class Message:
    content: str
    role: Role
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    reference_time: Optional[datetime] = None
    metadata: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.created_at = ensure_utc(self.created_at) or datetime.now(UTC)
        if self.reference_time is not None:
            self.reference_time = ensure_utc(self.reference_time)

    def to_dict(self) -> dict:
        """Convert message to dictionary format"""
        return {
            "content": self.content,
            "role": self.role.value,
            "created_at": ensure_utc(self.created_at).isoformat(),
            "reference_time": ensure_utc(self.reference_time).isoformat() if self.reference_time else None,
            "metadata": self.metadata
        }

    def to_json(self) -> str:
        """Convert message to JSON string"""
        return json.dumps(self.to_dict())

    def to_text(self) -> str:
        """Convert message to simple text format"""
        time_info = f" [{self.reference_time.isoformat()}]" if self.reference_time else ""
        return f"{self.role.value}: {self.content}{time_info}"

    @classmethod
    def from_dict(cls, data: dict) -> "Message":
        """Create message from dictionary"""
        return cls(
            content=data["content"],
            role=Role(data["role"]),
            created_at=ensure_utc(datetime.fromisoformat(data["created_at"])) if data.get("created_at") else None,
            reference_time=ensure_utc(datetime.fromisoformat(data["reference_time"])) if data.get("reference_time") else None,
            metadata=data.get("metadata", {})
            )
    
    @classmethod
    def from_json(cls, json_str: str) -> "Message":
        """Create message from JSON string"""
        return cls.from_dict(json.loads(json_str))
