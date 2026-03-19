from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


@dataclass
class AgentMessage:
    """统一描述会话消息，便于持久化和喂给模型。"""

    role: str
    content: str
    metadata: dict[str, Any] = field(default_factory=dict)
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "role": self.role,
            "content": self.content,
            "metadata": self.metadata,
            "timestamp": self.timestamp,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "AgentMessage":
        return cls(
            role=payload.get("role", "user"),
            content=payload.get("content", ""),
            metadata=payload.get("metadata", {}) or {},
            timestamp=payload.get("timestamp")
            or datetime.now(timezone.utc).isoformat(),
        )

    def to_openai_message(self) -> dict[str, str]:
        return {"role": self.role, "content": self.content}
