import json
import mimetypes
import os
import re
import shlex
import threading
import time
from collections import deque
from contextlib import AsyncExitStack
from dataclasses import dataclass
from typing import Any, Optional

import httpx
import whisper
from dotenv import load_dotenv
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from openai import OpenAI
from zhconv import convert

load_dotenv()


@dataclass(frozen=True)
class PlanDecision:
    """复杂任务规划结果。"""

    intent: str
    confidence: float
    required_tools: set[str]
    require_browser_tools: bool


class QueryPlanner:
    """基于轻量规则评分的任务规划器（可替换为分类模型）。"""

    _INTENT_KEYWORDS: dict[str, tuple[str, ...]] = {
        "qa": ("解释", "问", "为什么", "怎么", "总结", "建议"),
        "file": ("文件", "文档", "读取", "打开", "word", "docx", "pdf"),
        "browser": ("网页", "网站", "url", "浏览器", "chrome", "页面", "链接"),
        "app": ("打开", "启动", "运行", "应用", "软件", "qq", "wechat"),
        "complex": ("并且", "然后", "接着", "同时", "步骤", "复杂", "先", "后"),
    }

    _BROWSER_ACTIONS = (
        "打开",
        "访问",
        "进入",
        "跳转",
        "翻译",
        "提取",
        "抓取",
        "读取",
        "分析",
        "自动",
        "填写",
        "点击",
        "总结",
    )
    _BROWSER_CONTEXT = (
        "当前页面",
        "当前网页",
        "这个页面",
        "这个网页",
        "网页里",
        "页面里",
        "url",
        "链接",
        "浏览器",
        "网站",
        "官网",
        "网页",
        "页面",
    )

    def decide(self, query: str) -> PlanDecision:
        q = (query or "").lower()
        scores: dict[str, int] = {intent: 0 for intent in self._INTENT_KEYWORDS}

        for intent, words in self._INTENT_KEYWORDS.items():
            for word in words:
                if word in q:
                    scores[intent] += 1

        active_intents = sum(1 for k in ("file", "browser", "app") if scores[k] > 0)
        if active_intents >= 2:
            scores["complex"] += active_intents

        require_browser_tools = self._is_browser_automation_task(q)
        if require_browser_tools:
            scores["browser"] += 2

        best_intent = max(scores, key=scores.get)
        total = sum(scores.values()) or 1
        confidence = round(scores[best_intent] / total, 3)

        required_tools: set[str] = set()
        if scores["file"]:
            required_tools.update({"find_and_read_local_file", "read_file", "list_local_files"})
        if scores["app"]:
            required_tools.add("open_local_application")
        if scores["complex"]:
            required_tools.add("execute_complex_instruction")

        return PlanDecision(
            intent=best_intent,
            confidence=confidence,
            required_tools=required_tools,
            require_browser_tools=require_browser_tools,
        )

    def _is_browser_automation_task(self, q: str) -> bool:
        has_action = any(a in q for a in self._BROWSER_ACTIONS)
        has_context = any(c in q for c in self._BROWSER_CONTEXT)
        has_url = bool(re.search(r"(https?://\S+)|\b(?:[a-z0-9-]+\.)+[a-z]{2,}(?:/\S*)?", q, re.IGNORECASE))
        return has_action and (has_context or has_url)


class MetricsTracker:
    """记录任务规划和工具执行性能指标。"""

    def __init__(self, max_records: int = 200):
        self._records = deque(maxlen=max_records)

    def add(self, item: dict[str, Any]) -> None:
        self._records.append(item)

    def summary(self) -> dict[str, Any]:
        if not self._records:
            return {"count": 0, "avg_total_ms": 0.0, "avg_tool_calls": 0.0, "error_rate": 0.0}

        count = len(self._records)
        total_ms = sum(float(r.get("total_ms", 0)) for r in self._records)
        tool_calls = sum(int(r.get("tool_calls", 0)) for r in self._records)
        errors = sum(1 for r in self._records if r.get("error"))
        return {
            "count": count,
            "avg_total_ms": round(total_ms / count, 2),
            "avg_tool_calls": round(tool_calls / count, 2),
            "error_rate": round(errors / count, 3),
        }


class MCPClient:
    _whisper_model = None
    _whisper_lock = threading.Lock()

    def __init__(self):
        self.exit_stack = AsyncExitStack()
        self.openai_api_key = os.getenv("OPENAI_API_KEY", "").strip()
        self.base_url = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1").strip()
        self.model = os.getenv("MODEL", "gpt-4o-mini").strip()
        self.max_file_chars = int(os.getenv("MAX_FILE_CHARS", "12000"))
        self.max_tool_rounds = int(os.getenv("MAX_TOOL_ROUNDS", "20"))
        self.enable_llm_planner = os.getenv("ENABLE_LLM_PLANNER", "1").strip().lower() in {"1", "true", "yes"}
        self.planner_model = os.getenv("PLANNER_MODEL", self.model).strip()

        if not self.openai_api_key:
            raise RuntimeError("缺少 OPENAI_API_KEY，请在 .env 中配置后再启动。")

        http_client = httpx.Client(base_url=self.base_url, timeout=60.0, follow_redirects=True)
        self.client = OpenAI(api_key=self.openai_api_key, http_client=http_client)

        self.session: Optional[ClientSession] = None
        self.sessions: dict[str, ClientSession] = {}
        self.tool_to_session: dict[str, ClientSession] = {}
        self.query_planner = QueryPlanner()
        self.metrics = MetricsTracker()

    async def _connect_stdio_server(self, name: str, command: str, args: list[str]) -> list[Any]:
        server_params = StdioServerParameters(command=command, args=args, env=None)
        stdio_transport = await self.exit_stack.enter_async_context(stdio_client(server_params))
        stdio, write = stdio_transport
        session = await self.exit_stack.enter_async_context(ClientSession(stdio, write))
        await session.initialize()

        self.sessions[name] = session
        response = await session.list_tools()
        return response.tools

    async def connect_to_server(self, server_script_path: str):
        is_python = server_script_path.endswith(".py")
        is_js = server_script_path.endswith(".js")
        if not (is_python or is_js):
            raise ValueError("服务器脚本必须是 .py 或 .js 文件")

        local_command = "python" if is_python else "node"
        local_tools = await self._connect_stdio_server("local", local_command, [server_script_path])
        self.session = self.sessions["local"]

        all_tools = []
        for tool in local_tools:
            self.tool_to_session[tool.name] = self.sessions["local"]
            all_tools.append(tool)

        browser_enabled = os.getenv("ENABLE_BROWSER_MCP", "0").strip().lower() in {"1", "true", "yes"}
        if browser_enabled:
            browser_cmd = os.getenv("BROWSER_MCP_COMMAND", "npx")
            browser_args = shlex.split(os.getenv("BROWSER_MCP_ARGS", "@agentdeskai/browser-tools-mcp@latest"))
            try:
                browser_tools = await self._connect_stdio_server("browser", browser_cmd, browser_args)
                for tool in browser_tools:
                    self.tool_to_session[tool.name] = self.sessions["browser"]
                    all_tools.append(tool)
                print("\n已接入 Browser MCP，新增工具:", [tool.name for tool in browser_tools])
            except Exception as e:
                print(f"\n⚠️ Browser MCP 启动失败，已降级为仅本地工具: {e}")

        print("\n已连接到服务器，支持以下工具:", [tool.name for tool in all_tools])
        return all_tools

    @staticmethod
    def _extract_urls(text: str) -> list[str]:
        if not text:
            return []
        urls = re.findall(r"https?://\S+", text)
        if urls:
            return urls

        raw_domains = re.findall(r"\b(?:[a-z0-9-]+\.)+[a-z]{2,}(?:/\S*)?", text, flags=re.IGNORECASE)
        normalized = []
        for d in raw_domains:
            d = d.rstrip("，。,.!?)）]\"")
            normalized.append(f"https://{d}")
        return normalized

    def _has_browser_session(self) -> bool:
        return "browser" in self.sessions

    def _decide_with_llm(self, query: str) -> Optional[PlanDecision]:
        """使用大模型做意图规划；失败时返回 None，由规则规划器兜底。"""
        if not self.enable_llm_planner or not query.strip():
            return None

        prompt = (
            "你是任务路由器。请根据用户输入判断任务意图并仅输出 JSON。\n"
            "字段要求：\n"
            "- intent: 只能是 qa/file/browser/app/complex\n"
            "- confidence: 0~1 浮点\n"
            "- require_browser_tools: 布尔\n"
            "- required_tools: 数组，元素仅可为 find_and_read_local_file/read_file/list_local_files/open_local_application/execute_complex_instruction\n"
            "判断原则：如果用户要求打开/访问网站、操作网页、提取网页内容，require_browser_tools 应为 true。\n"
            "无协议域名（如 chaoxing.com）也应视作网页目标。"
        )

        try:
            response = self.client.chat.completions.create(
                model=self.planner_model,
                messages=[
                    {"role": "system", "content": prompt},
                    {"role": "user", "content": query},
                ],
                temperature=0,
                response_format={"type": "json_object"},
            )
            content = (response.choices[0].message.content or "").strip()
            data = json.loads(content)

            intent = str(data.get("intent", "qa")).lower()
            if intent not in {"qa", "file", "browser", "app", "complex"}:
                intent = "qa"

            confidence = float(data.get("confidence", 0.6))
            confidence = min(1.0, max(0.0, confidence))

            allowed = {
                "find_and_read_local_file",
                "read_file",
                "list_local_files",
                "open_local_application",
                "execute_complex_instruction",
            }
            required_tools = {str(x) for x in data.get("required_tools", []) if str(x) in allowed}
            require_browser_tools = bool(data.get("require_browser_tools", False))

            return PlanDecision(
                intent=intent,
                confidence=round(confidence, 3),
                required_tools=required_tools,
                require_browser_tools=require_browser_tools,
            )
        except Exception:
            return None

    def _decide_plan(self, query: str) -> PlanDecision:
        """优先使用大模型意图判断，规则规划器做兜底和合并。"""
        rule_decision = self.query_planner.decide(query)
        llm_decision = self._decide_with_llm(query)
        if llm_decision is None:
            return rule_decision

        # 合并：尽量采用 LLM 意图，同时用规则保证浏览器任务不漏判
        return PlanDecision(
            intent=llm_decision.intent,
            confidence=max(llm_decision.confidence, rule_decision.confidence),
            required_tools=llm_decision.required_tools | rule_decision.required_tools,
            require_browser_tools=llm_decision.require_browser_tools or rule_decision.require_browser_tools,
        )

    def _contains_navigation_intent(self, value: Any) -> bool:
        if isinstance(value, dict):
            return any(self._contains_navigation_intent(v) for v in value.values())
        if isinstance(value, list):
            return any(self._contains_navigation_intent(v) for v in value)
        if isinstance(value, str):
            lowered = value.lower()
            signals = ("page.goto(", "browser_navigate", "goto('https://www.example.com'", "https://www.example.com")
            return any(s in lowered for s in signals)
        return False

    def _load_whisper_model(self):
        if MCPClient._whisper_model is None:
            with MCPClient._whisper_lock:
                if MCPClient._whisper_model is None:
                    MCPClient._whisper_model = whisper.load_model(os.getenv("WHISPER_MODEL", "base"))
        return MCPClient._whisper_model

    async def transcribe_audio_file(self, audio_path: str) -> str:
        if not os.path.exists(audio_path):
            return "语音文件不存在，请重新选择后再试。"
        try:
            model = self._load_whisper_model()
            result = model.transcribe(audio_path, language="zh", task="transcribe", fp16=False)
            text = convert(result.get("text", ""), "zh-cn").strip()
            return text or "未识别到语音内容，请重试。"
        except Exception as e:
            return f"语音识别失败: {e}"

    async def process_query(self, query: str, file_path: str = "") -> str:
        start_time = time.perf_counter()
        record = {"error": False, "tool_calls": 0, "intent": "unknown", "confidence": 0.0}
        user_prompt_parts = []

        if query:
            user_prompt_parts.append(query)

        if file_path:
            if not os.path.exists(file_path):
                return "上传的文件不存在，请重新选择后再试。"

            mime_type, _ = mimetypes.guess_type(file_path)
            mime_type = mime_type or "application/octet-stream"
            filename = os.path.basename(file_path)

            if mime_type.startswith("image/"):
                file_size = os.path.getsize(file_path)
                user_prompt_parts.append(f"用户上传了一张图片：{filename}（MIME: {mime_type}, 大小: {file_size} bytes）。")
                user_prompt_parts.append("当前接入模型为文本优先。请先给出执行建议，并要求用户补充图片文字描述或 OCR 文本。")
            else:
                try:
                    with open(file_path, "r", encoding="utf-8") as f:
                        file_text = f.read(self.max_file_chars)
                except UnicodeDecodeError:
                    return "当前模型接口仅支持 UTF-8 文本。请先转换编码后再试。"

                user_prompt_parts.append(f"以下是用户上传文件（{filename}）内容（可能已截断）：\n{file_text}")

        if not user_prompt_parts:
            return "请输入问题或上传文件后再发送。"

        decision = self._decide_plan(query)
        record["intent"] = decision.intent
        record["confidence"] = decision.confidence
        messages = [{"role": "user", "content": "\n\n".join(user_prompt_parts)}]

        try:
            explicit_urls = self._extract_urls(query)
            has_explicit_url = len(explicit_urls) > 0

            all_tools = []
            for _, server_session in self.sessions.items():
                response = await server_session.list_tools()
                all_tools.extend(response.tools)

            available_tools = []
            for tool in all_tools:
                name = tool.name

                if decision.required_tools and name not in decision.required_tools and decision.intent != "qa":
                    if name not in {"query_weather", "get_weather_forecast"}:
                        continue

                available_tools.append(
                    {
                        "type": "function",
                        "function": {
                            "name": name,
                            "description": tool.description,
                            "parameters": tool.inputSchema,
                        },
                    }
                )

            if decision.require_browser_tools:
                if not self._has_browser_session():
                    return (
                        "你当前问题需要浏览器自动化工具，但本次会话未检测到 Browser MCP 工具。\n"
                        "请确认 ENABLE_BROWSER_MCP=1，且 browser server 与扩展已连接成功。"
                    )
                messages.insert(
                    0,
                    {
                        "role": "system",
                        "content": (
                            "仅在用户要求对当前网页或给定 URL 执行操作时，才调用 browser 工具。"
                            "不要进行浏览器搜索。"
                            "当用户提供域名（例如 chaoxing.com）但不带协议时，将其视为 https URL。"
                            "未明确 URL 时优先读取当前活动页面。"
                        ),
                    },
                )

            for _ in range(self.max_tool_rounds):
                response = self.client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    tools=available_tools,
                    tool_choice="required" if decision.require_browser_tools else "auto",
                )

                choice = response.choices[0]
                if choice.finish_reason != "tool_calls":
                    return choice.message.content

                tool_calls = choice.message.tool_calls or []
                messages.append(choice.message.model_dump())

                for tool_call in tool_calls:
                    record["tool_calls"] += 1
                    tool_name = tool_call.function.name
                    tool_args = json.loads(tool_call.function.arguments or "{}")

                    if decision.require_browser_tools and not has_explicit_url and self._contains_navigation_intent(tool_args):
                        messages.append(
                            {
                                "role": "tool",
                                "content": "已阻止跳转：用户未提供 URL，禁止导航到示例页面。请读取当前活动页面。",
                                "tool_call_id": tool_call.id,
                            }
                        )
                        continue

                    call_session = self.tool_to_session.get(tool_name, self.session)
                    result = await call_session.call_tool(tool_name, tool_args)
                    tool_text = ""
                    if getattr(result, "content", None):
                        tool_text = "\n".join(getattr(item, "text", str(item)) for item in result.content)

                    messages.append({"role": "tool", "content": tool_text, "tool_call_id": tool_call.id})

            return "工具调用轮次过多，已停止。请缩小任务范围后重试。"
        except Exception as e:
            record["error"] = True
            return f"处理查询时出错 {e}"
        finally:
            record["total_ms"] = round((time.perf_counter() - start_time) * 1000, 2)
            self.metrics.add(record)

    async def close(self) -> None:
        await self.exit_stack.aclose()
