from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:
    from mcp import ClientSession


class MCPToolRegistry:
    """统一管理 MCP tools，提供 LangChain tools-like 适配层。"""

    def __init__(
        self,
        sessions: dict[str, "ClientSession"],
        tool_to_session: dict[str, "ClientSession"],
        default_session: Optional["ClientSession"] = None,
    ):
        self.sessions = sessions
        self.tool_to_session = tool_to_session
        self.default_session = default_session

    async def list_tools(self) -> list[Any]:
        all_tools: list[Any] = []
        for session in self.sessions.values():
            response = await session.list_tools()
            all_tools.extend(response.tools)
        return all_tools

    async def format_openai_tools(self, allow_browser_search: bool, user_query: str = "") -> list[dict[str, Any]]:
        tools = await self.list_tools()
        openai_tools: list[dict[str, Any]] = []
        snapshot_requested = self.is_snapshot_request(user_query)
        noisy_browser_keywords = ("snapshot", "screenshot", "markdown", "md", "dump", "capture", "export")

        for tool in tools:
            if tool.name == "search_web" and not allow_browser_search:
                continue

            lowered_name = (tool.name or "").lower()
            if not snapshot_requested and any(k in lowered_name for k in noisy_browser_keywords):
                continue

            openai_tools.append(
                {
                    "type": "function",
                    "function": {
                        "name": tool.name,
                        "description": tool.description,
                        "parameters": tool.inputSchema,
                    },
                }
            )
        return openai_tools

    async def call_tool(self, tool_name: str, tool_args: dict[str, Any]) -> str:
        call_session = self.tool_to_session.get(tool_name, self.default_session)
        result = await call_session.call_tool(tool_name, tool_args)
        if getattr(result, "content", None):
            return "\n".join(getattr(item, "text", str(item)) for item in result.content)
        return json.dumps(getattr(result, "model_dump", lambda: result)(), ensure_ascii=False)

    @staticmethod
    def is_browser_search_explicit(query: str) -> bool:
        q = (query or "").lower()
        triggers = [
            "在浏览器", "浏览器中", "打开浏览器", "用浏览器", "browser",
            "search_web", "网页搜索", "上网搜", "去搜索引擎", "联网", "在线",
            "arxiv", "google scholar", "谷歌学术", "scholar", "论文检索", "文献检索",
        ]
        actions = [
            "搜索", "检索", "查一下", "查一查", "搜一下", "搜一搜", "search", "query",
            "找", "收集", "整理", "论文", "文献",
        ]
        return any(t in q for t in triggers) and any(a in q for a in actions)

    @staticmethod
    def is_browser_task(query: str) -> bool:
        q = (query or "").lower()
        browser_terms = ["网页", "网站", "browser", "chrome", "页面", "url", "链接", "devtools"]
        task_terms = ["翻译", "总结", "提取", "分析", "抓取", "读取", "自动", "操作", "automation"]
        return any(t in q for t in browser_terms) and any(t in q for t in task_terms)

    @staticmethod
    def is_snapshot_request(query: str) -> bool:
        q = (query or "").lower()
        marks = ["快照", "截图", "screenshot", "snapshot", "页面存档", "保存页面", "导出md", "markdown"]
        return any(m in q for m in marks)

    @staticmethod
    def extract_urls(text: str) -> list[str]:
        if not text:
            return []
        return re.findall(r"https?://\S+", text)

    @staticmethod
    def contains_navigation_intent(value: Any) -> bool:
        if isinstance(value, dict):
            return any(MCPToolRegistry.contains_navigation_intent(v) for v in value.values())
        if isinstance(value, list):
            return any(MCPToolRegistry.contains_navigation_intent(v) for v in value)
        if isinstance(value, str):
            lowered = value.lower()
            signals = [
                "page.goto(",
                "browser_navigate",
                "goto('https://www.example.com'",
                "https://www.example.com",
            ]
            return any(s in lowered for s in signals)
        return False
