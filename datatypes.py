from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional, Dict, Any
import json

class Role(Enum):
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"

@dataclass
class Message:
    content: str
    role: Role
    created_at: datetime = field(default_factory=datetime.now)
    reference_time: Optional[datetime] = None
    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        """Convert message to dictionary format"""
        return {
            "content": self.content,
            "role": self.role.value,
            "created_at": self.created_at.isoformat(),
            "reference_time": self.reference_time.isoformat() if self.reference_time else None,
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
            created_at=datetime.fromisoformat(data["created_at"]),
            reference_time=datetime.fromisoformat(data["reference_time"]) if data.get("reference_time") else None,
            metadata=data.get("metadata", {})
            )
    
    @classmethod
    def from_json(cls, json_str: str) -> "Message":
        """Create message from JSON string"""
        return cls.from_dict(json.loads(json_str))