from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable

from .schemas import AgentMessage


class FileConversationMemory:
    """使用 JSON 文件保存上下文，模拟 LangChain ConversationBufferMemory。"""

    def __init__(
        self,
        memory_file: str | Path,
        max_messages: int = 12,
        compress_trigger_messages: int = 18,
        summary_max_chars: int = 1200,
    ):
        self.memory_file = Path(memory_file)
        self.max_messages = max_messages
        self.compress_trigger_messages = max(8, compress_trigger_messages)
        self.summary_max_chars = max(400, summary_max_chars)
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
        messages = self._compress_if_needed(messages)
        self._write_messages(messages)

    def build_context_messages(self) -> list[dict[str, str]]:
        messages = self._read_messages()
        if self.max_messages > 0:
            messages = messages[-self.max_messages :]
        return [message.to_openai_message() for message in messages]

    def _compress_if_needed(self, messages: list[AgentMessage]) -> list[AgentMessage]:
        """
        简易记忆压缩：当历史过长时，将旧消息压缩为一条 system 摘要，保留近期明细。
        """
        if len(messages) <= self.compress_trigger_messages:
            return messages

        tail_keep = max(6, self.max_messages)
        head = messages[:-tail_keep]
        tail = messages[-tail_keep:]
        if not head:
            return messages

        summary_parts: list[str] = []
        for message in head:
            role = "用户" if message.role == "user" else "助手"
            content = " ".join(message.content.split())
            if len(content) > 100:
                content = content[:100] + "..."
            summary_parts.append(f"{role}: {content}")

        summary = "历史记忆压缩摘要：\n" + "\n".join(summary_parts)
        if len(summary) > self.summary_max_chars:
            summary = summary[: self.summary_max_chars] + "\n...(摘要已截断)"

        compressed = AgentMessage(
            role="system",
            content=summary,
            metadata={"memory_compressed": True, "compressed_count": len(head)},
        )
        return [compressed, *tail]

    def clear(self) -> None:
        self._write_messages([])
