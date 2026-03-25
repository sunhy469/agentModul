from __future__ import annotations

import json
from typing import Any

from .memory import FileConversationMemory
from .toolkit import MCPToolRegistry


class LangChainStyleAgentExecutor:
    """参考 LangChain AgentExecutor 的职责拆分：Prompt + Tools + Memory + Loop。"""

    def __init__(
        self,
        openai_client: Any,
        model: str,
        tool_registry: MCPToolRegistry,
        memory: FileConversationMemory,
        max_iterations: int = 6,
    ):
        self.openai_client = openai_client
        self.model = model
        self.tool_registry = tool_registry
        self.memory = memory
        self.max_iterations = max_iterations

    def _build_system_prompt(self) -> str:
        return (
            "你是一个采用 ReAct 与状态机协同的智能体系统。"
            "你的工作流必须遵循 Thought -> Action -> Observation -> Final Answer。"
            "你需要为每个任务维护状态：pending / in_progress / completed / failed。"
            "当问题可直接回答时，直接给出结论；当需要工具时，主动调用工具。"
            "禁止输出 Thought/Action/Observation 等中间推理内容。"
            "当用户要求发送飞书消息时，优先直接调用 send_message_by_request 或 send_feishu_robot_message；"
            "若未报错，不要反复向用户索取地址参数。"
            "回答请保持结构化，优先中文，并结合压缩记忆和当前上下文共同推理。"
        )

    @staticmethod
    def _serialize_for_memory(message: Any) -> str:
        if isinstance(message, str):
            return message
        return json.dumps(message, ensure_ascii=False)

    @staticmethod
    def _is_simple_greeting(query: str, has_attachment: bool) -> bool:
        if has_attachment:
            return False
        text = (query or "").strip().lower()
        return text in {"hi", "hello", "你好", "嗨", "在吗", "在么", "早上好", "晚上好"}

    def _compose_user_message(self, query: str, attachment_context: Any = "") -> str | list[dict[str, Any]]:
        parts: list[str] = []
        if query:
            parts.append(query)
        if isinstance(attachment_context, str) and attachment_context:
            parts.append(attachment_context)
        if isinstance(attachment_context, list):
            text_part = "\n\n".join(parts).strip()
            payload: list[dict[str, Any]] = []
            if text_part:
                payload.append({"type": "text", "text": text_part})
            payload.extend(attachment_context)
            return payload
        return "\n\n".join(parts).strip()

    async def run(self, query: str, attachment_context: Any = "", metadata: dict | None = None) -> str:
        user_message = self._compose_user_message(query, attachment_context)
        if not user_message or (isinstance(user_message, list) and len(user_message) == 0):
            return "请输入问题或上传文件后再发送。"

        plain_query = query or ""
        if self._is_simple_greeting(plain_query, isinstance(attachment_context, list) and len(attachment_context) > 0):
            direct = "你好呀！我在的～你可以直接告诉我想完成的任务。"
            self.memory.save_turn(self._serialize_for_memory(user_message), direct, metadata=metadata)
            return direct

        allow_browser_search = self.tool_registry.is_browser_search_explicit(plain_query)
        require_browser_tools = self.tool_registry.is_browser_task(plain_query)
        has_explicit_url = bool(self.tool_registry.extract_urls(plain_query))

        messages: list[dict[str, Any]] = [
            {"role": "system", "content": self._build_system_prompt()},
            *self.memory.build_context_messages(query=plain_query),
            {"role": "user", "content": user_message},
        ]

        if require_browser_tools:
            messages.insert(
                1,
                {
                    "role": "system",
                    "content": (
                        "用户要求处理网页内容。你必须先调用 browser 相关工具读取内容，再输出翻译/总结。"
                        "若用户未提供 URL，严禁跳转到任何示例站点（如 example.com），先读取当前活动页面；"
                        "只有工具明确失败时，才向用户索取 URL。不要先回复操作建议。"
                    ),
                },
            )

        available_tools = await self.tool_registry.format_openai_tools(allow_browser_search)

        try:
            task_status = "pending"
            for _ in range(self.max_iterations):
                task_status = "in_progress"
                response = self.openai_client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    tools=available_tools,
                    tool_choice="required" if require_browser_tools else "auto",
                )

                choice = response.choices[0]
                if choice.finish_reason != "tool_calls":
                    final_answer = choice.message.content or "模型未返回内容。"
                    task_status = "completed"
                    self.memory.save_turn(self._serialize_for_memory(user_message), final_answer, metadata=metadata)
                    return final_answer

                assistant_message = choice.message.model_dump()
                messages.append(assistant_message)

                for tool_call in choice.message.tool_calls or []:
                    tool_name = tool_call.function.name
                    tool_args = json.loads(tool_call.function.arguments or "{}")

                    if tool_name == "search_web" and not allow_browser_search:
                        final_answer = "你没有明确要求“在浏览器中搜索”，我已按普通问答处理（未打开浏览器）。"
                        self.memory.save_turn(self._serialize_for_memory(user_message), final_answer, metadata=metadata)
                        return final_answer

                    if require_browser_tools and not has_explicit_url and self.tool_registry.contains_navigation_intent(tool_args):
                        messages.append(
                            {
                                "role": "tool",
                                "content": (
                                    "已阻止本次跳转：用户未提供 URL，禁止导航到示例页面。"
                                    "请改为读取当前 Chrome 活动页面内容后再翻译/总结。"
                                ),
                                "tool_call_id": tool_call.id,
                            }
                        )
                        continue

                    try:
                        tool_text = await self.tool_registry.call_tool(tool_name, tool_args)
                    except Exception as tool_exc:
                        # 对短暂失败做一次轻量重试
                        retry_payload = dict(tool_args)
                        retry_payload["_retry"] = True
                        try:
                            tool_text = await self.tool_registry.call_tool(tool_name, retry_payload)
                        except Exception:
                            task_status = "failed"
                            tool_text = f"工具调用失败: {tool_exc}"
                    messages.append(
                        {
                            "role": "tool",
                            "content": tool_text,
                            "tool_call_id": tool_call.id,
                        }
                    )

            fallback = "已达到最大工具调用轮次，请缩小问题范围后再试。"
            if task_status != "completed":
                task_status = "failed"
            self.memory.save_turn(
                self._serialize_for_memory(user_message),
                f"{fallback}\n任务状态: {task_status}",
                metadata=metadata,
            )
            return fallback
        except Exception as exc:
            return f"处理查询时出错 {exc}"
