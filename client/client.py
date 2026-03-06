import os
import json
import mimetypes
import httpx
from typing import Optional
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

    async def connect_to_server(self, server_script_path: str):
        """连接到 MCP 服务器并列出可用工具"""
        is_python = server_script_path.endswith('.py')
        is_js = server_script_path.endswith('.js')

        if not (is_python or is_js):
            raise ValueError("服务器脚本必须是 .py 或 .js 文件")

        command = "python" if is_python else "node"
        server_params = StdioServerParameters(
            command=command,
            args=[server_script_path],
            env=None
        )

        # 启动 MCP 服务器并建立通信
        stdio_transport = await self.exit_stack.enter_async_context(
            stdio_client(server_params)
        )

        self.stdio, self.write = stdio_transport
        self.session = await self.exit_stack.enter_async_context(
            ClientSession(self.stdio, self.write)
        )

        await self.session.initialize()

        # 列出 MCP 服务器上的工具
        response = await self.session.list_tools()
        tools = response.tools
        print("\n 已连接到服务器，支持以下工具:", [tool.name for tool in tools])
        return tools



    def _is_browser_search_explicit(self, query: str) -> bool:
        """仅当用户明确要求在浏览器中搜索时，才允许调用 search_web 工具。"""
        q = (query or "").lower()
        triggers = [
            "在浏览器", "浏览器中", "打开浏览器", "用浏览器", "browser","在浏览器中",
            "search_web", "网页搜索", "上网搜", "去搜索引擎"
        ]
        actions = ["搜索", "查一下", "查一查", "搜一下", "搜一搜", "search", "query"]
        return any(t in q for t in triggers) and any(a in q for a in actions)


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
            response = await self.session.list_tools()
            allow_browser_search = self._is_browser_search_explicit(query)
            available_tools = []
            for tool in response.tools:
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

            response = self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                tools=available_tools
            )

            content = response.choices[0]

            if content.finish_reason == "tool_calls":
                # 如果需要使用工具，就解析工具
                tool_call = content.message.tool_calls[0]
                tool_name = tool_call.function.name
                tool_args = json.loads(tool_call.function.arguments)

                if tool_name == "search_web" and not self._is_browser_search_explicit(query):
                    return "你没有明确要求“在浏览器中搜索”，我已按普通问答处理（未打开浏览器）。"

                # 执行工具
                result = await self.session.call_tool(tool_name, tool_args)
                print(f"\n {result}")

                # 将工具调用结果存入messages
                messages.append(content.message.model_dump())
                messages.append({
                    "role": "tool",
                    "content": result.content[0].text,
                    "tool_call_id": tool_call.id,
                })

                # 再次调用模型生成最终回答
                response = self.client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                )

                return response.choices[0].message.content

            return content.message.content
        except Exception as e:
            return f"处理查询时出错 {str(e)}"