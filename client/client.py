import asyncio
import os
import json
import sys
import base64
import mimetypes
import httpx
from typing import Optional
from contextlib import AsyncExitStack
from openai import OpenAI
from dotenv import load_dotenv
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

# 加载 .env 文件，确保 API Key 受到保护
load_dotenv()

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

    async def process_query(self, query: str, file_path: str = "") -> str:
        """使用大模型处理查询并调用可用的 MCP 工具"""
        user_content = []

        if query:
            user_content.append({"type": "text", "text": query})

        if file_path:
            if not os.path.exists(file_path):
                return "上传的文件不存在，请重新选择后再试。"

            mime_type, _ = mimetypes.guess_type(file_path)
            mime_type = mime_type or "application/octet-stream"

            if mime_type.startswith("image/"):
                with open(file_path, "rb") as f:
                    image_base64 = base64.b64encode(f.read()).decode("utf-8")

                user_content.append(
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:{mime_type};base64,{image_base64}"
                        }
                    }
                )
            else:
                try:
                    with open(file_path, "r", encoding="utf-8") as f:
                        file_text = f.read()
                except UnicodeDecodeError:
                    return "当前仅支持图片或 UTF-8 编码的文本文件解析。"

                user_content.append(
                    {
                        "type": "text",
                        "text": f"以下是用户上传文件（{os.path.basename(file_path)}）的内容，请进行解析并总结：\n{file_text}"
                    }
                )

        if not user_content:
            return "请输入问题或上传文件后再发送。"

        messages = [{"role": "user", "content": user_content}]

        try:
            response = await self.session.list_tools()
            available_tools = [
                {
                    "type": "function",
                    "function": {
                        "name": tool.name,
                        "description": tool.description,
                        "parameters": tool.inputSchema
                    }
                }
                for tool in response.tools
            ]

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
