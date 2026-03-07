import os
import json
import mimetypes
import re
import shlex
import httpx
from typing import Optional, Any
from contextlib import AsyncExitStack
from openai import OpenAI
from dotenv import load_dotenv
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
import whisper
from zhconv import convert

# 加载 .env 文件，确保 API Key 受到保护
load_dotenv()
model = whisper.load_model("base")


class MCPClient:
    def __init__(self):
        """初始化 MCP 客户端"""
        self.exit_stack = AsyncExitStack()
        self.openai_api_key = os.getenv("OPENAI_API_KEY")
        self.base_url = os.getenv("OPENAI_BASE_URL")
        self.model = os.getenv("MODEL")

        # 创建自定义HTTP客户端
        http_client = httpx.Client(
            base_url=self.base_url,
            timeout=60.0,
            follow_redirects=True
        )

        if not self.openai_api_key:
            print("⚠️ 未找到 API Key, 使用模拟模式")
            self.openai_api_key = "dummy-key"
            self.base_url = "http://localhost:8080"
            self.model = "gpt-4o"

        self.client = OpenAI(
            api_key=self.openai_api_key,
            http_client=http_client
        )

        self.session: Optional[ClientSession] = None
        self.sessions: dict[str, ClientSession] = {}
        self.tool_to_session: dict[str, ClientSession] = {}

    async def _connect_stdio_server(self, name: str, command: str, args: list[str]) -> list[Any]:
        """连接一个 stdio MCP 服务并返回其工具列表。"""
        server_params = StdioServerParameters(command=command, args=args, env=None)

        stdio_transport = await self.exit_stack.enter_async_context(stdio_client(server_params))
        stdio, write = stdio_transport
        session = await self.exit_stack.enter_async_context(ClientSession(stdio, write))
        await session.initialize()

        self.sessions[name] = session
        response = await session.list_tools()
        return response.tools

    async def connect_to_server(self, server_script_path: str):
        """连接本地 MCP 服务器，并可选接入 Browser MCP（Node 服务）。"""
        is_python = server_script_path.endswith('.py')
        is_js = server_script_path.endswith('.js')

        if not (is_python or is_js):
            raise ValueError("服务器脚本必须是 .py 或 .js 文件")

        local_command = "python" if is_python else "node"
        local_tools = await self._connect_stdio_server("local", local_command, [server_script_path])

        # 保持兼容：默认 session 仍指向本地服务
        self.session = self.sessions["local"]

        all_tools = []
        for tool in local_tools:
            self.tool_to_session[tool.name] = self.sessions["local"]
            all_tools.append(tool)

        # 可选：接入 Browser MCP（Node 版）。默认关闭，不影响原有功能
        browser_enabled = os.getenv("ENABLE_BROWSER_MCP", "0").strip().lower() in {"1", "true", "yes"}
        if browser_enabled:
            browser_cmd = os.getenv("BROWSER_MCP_COMMAND", "npx")
            raw_args = os.getenv("BROWSER_MCP_ARGS", "@agentdeskai/browser-tools-mcp@latest")
            browser_args = shlex.split(raw_args)

            try:
                browser_tools = await self._connect_stdio_server("browser", browser_cmd, browser_args)
                for tool in browser_tools:
                    self.tool_to_session[tool.name] = self.sessions["browser"]
                    all_tools.append(tool)
                print("\n 已接入 Browser MCP，新增工具:", [tool.name for tool in browser_tools])
            except Exception as e:
                print(f"\n ⚠️ Browser MCP 启动失败，已降级为仅本地工具: {e}")

        print("\n 已连接到服务器，支持以下工具:", [tool.name for tool in all_tools])
        return all_tools



    def _is_browser_search_explicit(self, query: str) -> bool:
        """仅当用户明确要求在浏览器中搜索时，才允许调用 search_web 工具。"""
        q = (query or "").lower()
        triggers = [
            "在浏览器", "浏览器中", "打开浏览器", "用浏览器", "browser","在浏览器中",
            "search_web", "网页搜索", "上网搜", "去搜索引擎"
        ]
        actions = ["搜索", "查一下", "查一查", "搜一下", "搜一搜", "search", "query"]
        return any(t in q for t in triggers) and any(a in q for a in actions)

    def _is_browser_task(self, query: str) -> bool:
        """判断用户是否在请求网页自动化（翻译网页/总结网页等）。"""
        q = (query or "").lower()
        browser_terms = ["网页", "网站", "browser", "chrome", "页面", "url", "链接", "devtools"]
        task_terms = ["翻译", "总结", "提取", "分析", "抓取", "读取", "自动", "操作", "automation"]
        return any(t in q for t in browser_terms) and any(t in q for t in task_terms)


    def _extract_urls(self, text: str) -> list[str]:
        if not text:
            return []
        pattern = r"https?://\S+"
        return re.findall(pattern, text)

    def _contains_navigation_intent(self, value):
        """检测工具参数里是否包含 page.goto / browser_navigate 类跳转意图。"""
        if isinstance(value, dict):
            return any(self._contains_navigation_intent(v) for v in value.values())
        if isinstance(value, list):
            return any(self._contains_navigation_intent(v) for v in value)
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


    async def transcribe_audio_file(self, audio_path: str) -> str:
        """将音频文件转写为文本。"""

        if not os.path.exists(audio_path):
            return "语音文件不存在，请重新选择后再试。"

        print(audio_path)

        try:
            os.environ["FFMPEG_BINARY"] = r"D:\ffmpeg\bin\ffmpeg.exe"

            result = model.transcribe(
                audio_path,
                language='zh',  # 指定中文
                task='transcribe',  # 指定转写任务
                fp16=False
            )
            print(result["text"])
            text = result["text"]

            text = convert(text, 'zh-cn')

            if not text:
                return "未识别到语音内容，请重试。"

            return text.strip()
        except Exception as e:
            return f"语音识别失败: {e}"

    async def process_query(self, query: str, file_path: str = "") -> str:
        """使用大模型处理查询并调用可用的 MCP 工具"""
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
                user_prompt_parts.append(
                    f"用户上传了一张图片：{filename}（MIME: {mime_type}, 大小: {file_size} bytes）。"
                )
                user_prompt_parts.append(
                    "当前接入的模型接口仅支持文本消息，无法直接读取图片像素内容。"
                    "请先基于用户问题给出可执行建议，并提示用户改为上传可读文本（如 txt/md）或补充图片文字描述。"
                )
            else:
                try:
                    with open(file_path, "r", encoding="utf-8") as f:
                        file_text = f.read()
                except UnicodeDecodeError:
                    return "当前模型接口仅支持文本消息。非 UTF-8 文本文件请先转换为 UTF-8，或粘贴主要内容后再试。"

                user_prompt_parts.append(
                    f"以下是用户上传文件（{filename}）的内容，请进行解析并总结：\n{file_text}"
                )

        if not user_prompt_parts:
            return "请输入问题或上传文件后再发送。"

        messages = [{"role": "user", "content": "\n\n".join(user_prompt_parts)}]

        try:
            allow_browser_search = self._is_browser_search_explicit(query)
            require_browser_tools = self._is_browser_task(query)
            explicit_urls = self._extract_urls(query)
            has_explicit_url = len(explicit_urls) > 0
            available_tools = []
            all_tools = []
            for server_name, server_session in self.sessions.items():
                response = await server_session.list_tools()
                all_tools.extend(response.tools)

            for tool in all_tools:
                if tool.name == "search_web" and not allow_browser_search:
                    continue

                available_tools.append(
                    {
                        "type": "function",
                        "function": {
                            "name": tool.name,
                            "description": tool.description,
                            "parameters": tool.inputSchema
                        }
                    }
                )

            if require_browser_tools:
                browser_tool_count = sum(
                    1 for t in available_tools if "browser" in t["function"]["name"].lower()
                )
                if browser_tool_count == 0:
                    return (
                        "你当前问题需要浏览器自动化工具，但本次会话未检测到 Browser MCP 工具。\n"
                        "请确认：\n"
                        "1) .env 已设置 ENABLE_BROWSER_MCP=1\n"
                        "2) BROWSER_MCP_COMMAND=npx\n"
                        "3) BROWSER_MCP_ARGS=@agentdeskai/browser-tools-mcp@latest\n"
                        "4) 新终端已运行 npx @agentdeskai/browser-tools-server@latest\n"
                        "5) Chrome 扩展 BrowserToolsMCP 已安装并连接成功"
                    )

                messages.insert(0, {
                    "role": "system",
                    "content": (
                        "用户要求处理网页内容。你必须先调用 browser 相关工具读取内容，再输出翻译/总结。"
                        "若用户未提供 URL，严禁跳转到任何示例站点（如 example.com），先读取当前活动页面；"
                        "只有工具明确失败时，才向用户索取 URL。不要先回复操作建议。"
                    )
                })

            # 支持多轮工具调用（网页自动化通常需要多步）
            for _ in range(8):
                response = self.client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    tools=available_tools,
                    tool_choice="required" if require_browser_tools else "auto"
                )

                content = response.choices[0]
                if content.finish_reason != "tool_calls":
                    return content.message.content

                tool_calls = content.message.tool_calls or []
                messages.append(content.message.model_dump())

                for tool_call in tool_calls:
                    tool_name = tool_call.function.name
                    tool_args = json.loads(tool_call.function.arguments or "{}")

                    if tool_name == "search_web" and not self._is_browser_search_explicit(query):
                        return "你没有明确要求“在浏览器中搜索”，我已按普通问答处理（未打开浏览器）。"

                    if require_browser_tools and not has_explicit_url and self._contains_navigation_intent(tool_args):
                        messages.append({
                            "role": "tool",
                            "content": (
                                "已阻止本次跳转：用户未提供 URL，禁止导航到示例页面。"
                                "请改为读取当前 Chrome 活动页面内容后再翻译/总结。"
                            ),
                            "tool_call_id": tool_call.id,
                        })
                        continue

                    call_session = self.tool_to_session.get(tool_name, self.session)
                    result = await call_session.call_tool(tool_name, tool_args)
                    print(f"\n {result}")

                    tool_text = ""
                    if getattr(result, "content", None):
                        tool_text = "\n".join(
                            getattr(item, "text", str(item)) for item in result.content
                        )

                    messages.append({
                        "role": "tool",
                        "content": tool_text,
                        "tool_call_id": tool_call.id,
                    })

            return "工具调用轮次过多，已停止。请缩小任务范围后重试。"
        except Exception as e:
            return f"处理查询时出错 {str(e)}"