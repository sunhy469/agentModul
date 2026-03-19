from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable

from .schemas import AgentMessage


class FileConversationMemory:
    """使用 JSON 文件保存上下文，模拟 LangChain ConversationBufferMemory。"""

    def __init__(self, memory_file: str | Path, max_messages: int = 12):
        self.memory_file = Path(memory_file)
        self.max_messages = max_messages
        self.memory_file.parent.mkdir(parents=True, exist_ok=True)
        if not self.memory_file.exists():
            self._write_messages([])

    def _read_messages(self) -> list[AgentMessage]:
        try:
            payload = json.loads(self.memory_file.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            return []
        return [AgentMessage.from_dict(item) for item in payload.get("messages", [])]

    def _write_messages(self, messages: Iterable[AgentMessage]) -> None:
        data = {"messages": [message.to_dict() for message in messages]}
        self.memory_file.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def load_messages(self) -> list[AgentMessage]:
        return self._read_messages()

    def append(self, role: str, content: str, metadata: dict | None = None) -> None:
        messages = self._read_messages()
        messages.append(AgentMessage(role=role, content=content, metadata=metadata or {}))
        self._write_messages(messages)

    def save_turn(
        self,
        user_content: str,
        assistant_content: str,
        metadata: dict | None = None,
    ) -> None:
        messages = self._read_messages()
        messages.extend(
            [
                AgentMessage(role="user", content=user_content, metadata=metadata or {}),
                AgentMessage(role="assistant", content=assistant_content, metadata=metadata or {}),
            ]
        )
        self._write_messages(messages)

    def build_context_messages(self) -> list[dict[str, str]]:
        messages = self._read_messages()
        if self.max_messages > 0:
            messages = messages[-self.max_messages :]
        return [message.to_openai_message() for message in messages]

    def clear(self) -> None:
        self._write_messages([])
