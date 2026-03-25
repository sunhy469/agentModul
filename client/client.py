import mimetypes
import os
import shlex
import base64
import shutil
from contextlib import AsyncExitStack
from typing import Optional, Any

import httpx
import whisper
from dotenv import load_dotenv
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from openai import OpenAI
from zhconv import convert

from agent.executor import LangChainStyleAgentExecutor
from agent.memory import FileConversationMemory
from agent.toolkit import MCPToolRegistry

# 加载 .env 文件，确保 API Key 受到保护
load_dotenv()
model = whisper.load_model(os.getenv("LOCAL_ASR_MODEL", "small"))


class MCPClient:
    def __init__(self):
        """初始化 MCP 客户端"""
        self.exit_stack = AsyncExitStack()
        self.openai_api_key = os.getenv("OPENAI_API_KEY")
        self.base_url = os.getenv("OPENAI_BASE_URL")
        self.model = os.getenv("MODEL") or "gpt-5.4-nano"

        if not self.openai_api_key:
            print("⚠️ 未找到 API Key, 使用模拟模式")
            self.openai_api_key = "dummy-key"
            self.base_url = self.base_url or "http://localhost:8080"
            self.model = "gpt-5.4-nano"

        http_client_kwargs = {
            "timeout": 60.0,
            "follow_redirects": True,
        }
        if self.base_url:
            http_client_kwargs["base_url"] = self.base_url
        http_client = httpx.Client(**http_client_kwargs)

        self.client = OpenAI(
            api_key=self.openai_api_key,
            http_client=http_client,
        )

        self.session: Optional[ClientSession] = None
        self.sessions: dict[str, ClientSession] = {}
        self.tool_to_session: dict[str, ClientSession] = {}
        self.tool_registry: Optional[MCPToolRegistry] = None

        memory_file = os.getenv(
            "AGENT_MEMORY_FILE",
            os.path.join(os.getcwd(), "generated_files", "conversation_memory.db"),
        )
        self.memory = FileConversationMemory(
            memory_file=memory_file,
            max_messages=12,
            compress_trigger_messages=20,
            summary_max_chars=1400,
        )
        self.agent_executor: Optional[LangChainStyleAgentExecutor] = None

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

        self.session = self.sessions["local"]

        all_tools = []
        for tool in local_tools:
            self.tool_to_session[tool.name] = self.sessions["local"]
            all_tools.append(tool)

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
                print("\n已接入 Browser MCP，新增工具:", [tool.name for tool in browser_tools])
            except Exception as e:
                print(f"\n⚠️ Browser MCP 启动失败，已降级为仅本地工具: {e}")

        windows_enabled = os.getenv("ENABLE_WINDOWS_MCP", "0").strip().lower() in {"1", "true", "yes"}
        if windows_enabled:
            windows_cmd = os.getenv("WINDOWS_MCP_COMMAND", "uv")
            raw_args = os.getenv("WINDOWS_MCP_ARGS", "")
            if raw_args.strip():
                windows_args = shlex.split(raw_args)
            else:
                windows_dir = os.getenv("WINDOWS_MCP_DIR", "").strip()
                if windows_dir:
                    # 对齐 Cursor MCP 配置风格：
                    # command: uv
                    # args: ["--directory", "C:/AI/Windows-MCP", "run", "main.py"]
                    windows_args = ["--directory", windows_dir, "run", "main.py"]
                else:
                    print("\n⚠️ ENABLE_WINDOWS_MCP 已开启，但未配置 WINDOWS_MCP_ARGS 或 WINDOWS_MCP_DIR，跳过接入。")
                    windows_args = []

            if windows_args:
                # 兼容：若 uv 不在 PATH，回退到 `python -m uv`
                if not shutil.which(windows_cmd):
                    if windows_cmd in {"uv", "uvx"} and shutil.which("python"):
                        print(f"\n⚠️ 未找到命令 {windows_cmd}，自动回退为 `python -m uv`。")
                        windows_cmd = "python"
                        windows_args = ["-m", "uv", *windows_args]
                    else:
                        fallback_cmd = "python"
                        if shutil.which(fallback_cmd):
                            print(f"\n⚠️ 未找到命令 {windows_cmd}，自动回退为 {fallback_cmd}。")
                            windows_cmd = fallback_cmd

                # 兼容：若 args 仅是 *.py 脚本路径，则应使用 python 执行而非 uv
                if (
                    len(windows_args) == 1
                    and str(windows_args[0]).lower().endswith(".py")
                    and windows_cmd in {"uv", "uvx"}
                ):
                    if shutil.which("python"):
                        print("\n⚠️ 检测到 WINDOWS_MCP_ARGS 为 Python 脚本路径，自动改为 python 执行。")
                        windows_cmd = "python"

                try:
                    windows_tools = await self._connect_stdio_server("windows", windows_cmd, windows_args)
                    for tool in windows_tools:
                        self.tool_to_session[tool.name] = self.sessions["windows"]
                        all_tools.append(tool)
                    print("\n已接入 Windows MCP，新增工具:", [tool.name for tool in windows_tools])
                except Exception as e:
                    print(f"\n⚠️ Windows MCP 启动失败，已降级为仅现有工具: {e}")

        self.tool_registry = MCPToolRegistry(
            sessions=self.sessions,
            tool_to_session=self.tool_to_session,
            default_session=self.session,
        )
        self.agent_executor = LangChainStyleAgentExecutor(
            openai_client=self.client,
            model=self.model,
            tool_registry=self.tool_registry,
            memory=self.memory,
        )

        print("\n已连接到服务器，支持以下工具:", [tool.name for tool in all_tools])
        return all_tools

    async def transcribe_audio_file(self, audio_path: str) -> str:
        """将音频文件转写为文本。"""
        if not os.path.exists(audio_path):
            return "语音文件不存在，请重新选择后再试。"

        try:
            asr_backend = os.getenv("ASR_BACKEND", "openai").strip().lower()
            text = ""

            if asr_backend == "openai" and self.openai_api_key and self.openai_api_key != "dummy-key":
                with open(audio_path, "rb") as audio_file:
                    transcript = self.client.audio.transcriptions.create(
                        model=os.getenv("ASR_MODEL", "gpt-4o-mini-transcribe"),
                        file=audio_file,
                    )
                    text = getattr(transcript, "text", "") or ""

            if not text:
                result = model.transcribe(
                    audio_path,
                    language='zh',
                    task='transcribe',
                    fp16=False,
                )
                text = result["text"]

            text = convert(text, 'zh-cn')

            if not text:
                return "未识别到语音内容，请重试。"

            return text.strip()
        except Exception as e:
            return f"语音识别失败: {e}"

    def _build_attachment_context(self, file_path: str) -> str | list[dict[str, Any]]:
        if not file_path:
            return ""

        if not os.path.exists(file_path):
            raise FileNotFoundError("上传的文件不存在，请重新选择后再试。")

        mime_type, _ = mimetypes.guess_type(file_path)
        mime_type = mime_type or "application/octet-stream"
        filename = os.path.basename(file_path)

        if mime_type.startswith("image/"):
            with open(file_path, "rb") as image_file:
                image_data = base64.b64encode(image_file.read()).decode("utf-8")
            return [
                {"type": "text", "text": f"用户上传了图片文件：{filename}，请结合用户问题进行分析。"},
                {"type": "image_url", "image_url": {"url": f"data:{mime_type};base64,{image_data}"}},
            ]

        with open(file_path, "r", encoding="utf-8") as file:
            file_text = file.read()

        return f"以下是用户上传文件（{filename}）的内容，请进行解析并总结：\n{file_text}"

    async def process_query(self, query: str, file_path: str = "") -> str:
        """使用 LangChain 风格 Agent Executor 处理查询并调用 MCP 工具。"""
        if not self.agent_executor:
            return "智能体尚未初始化，请先连接服务器。"

        try:
            attachment_context = self._build_attachment_context(file_path) if file_path else ""
        except UnicodeDecodeError:
            return "当前模型接口仅支持文本消息。非 UTF-8 文本文件请先转换为 UTF-8，或粘贴主要内容后再试。"
        except FileNotFoundError as exc:
            return str(exc)

        display_query = query or "请结合上传内容进行分析。"
        metadata = {"file_path": file_path} if file_path else None
        return await self.agent_executor.run(
            query=display_query,
            attachment_context=attachment_context,
            metadata=metadata,
        )

    def clear_memory(self) -> None:
        self.memory.clear()

    async def close(self):
        await self.exit_stack.aclose()
