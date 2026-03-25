import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from agent.executor import LangChainStyleAgentExecutor
from agent.memory import FileConversationMemory


class FakeCompletions:
    def __init__(self, responses):
        self.responses = responses

    def create(self, **_kwargs):
        return self.responses.pop(0)


class FakeOpenAIClient:
    def __init__(self, responses):
        self.chat = SimpleNamespace(completions=FakeCompletions(responses))


class FakeToolRegistry:
    def __init__(self):
        self.calls = []

    @staticmethod
    def is_browser_search_explicit(_query):
        return False

    @staticmethod
    def is_browser_task(_query):
        return False

    @staticmethod
    def extract_urls(_query):
        return []

    @staticmethod
    def contains_navigation_intent(_value):
        return False

    async def format_openai_tools(self, _allow_browser_search):
        return [{"type": "function", "function": {"name": "echo", "parameters": {}}}]

    async def call_tool(self, tool_name, tool_args):
        self.calls.append((tool_name, tool_args))
        return "tool-result"


class AgentExecutorTest(unittest.IsolatedAsyncioTestCase):
    async def test_tool_loop_and_memory_persistence(self):
        tool_message = SimpleNamespace(
            content=None,
            tool_calls=[
                SimpleNamespace(
                    id="call-1",
                    function=SimpleNamespace(name="echo", arguments=json.dumps({"text": "hi"})),
                )
            ],
            model_dump=lambda: {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call-1",
                        "type": "function",
                        "function": {"name": "echo", "arguments": json.dumps({"text": "hi"})},
                    }
                ],
            },
        )
        final_message = SimpleNamespace(content="最终答案", tool_calls=[])
        responses = [
            SimpleNamespace(choices=[SimpleNamespace(finish_reason="tool_calls", message=tool_message)]),
            SimpleNamespace(choices=[SimpleNamespace(finish_reason="stop", message=final_message)]),
        ]

        with tempfile.TemporaryDirectory() as tmpdir:
            memory = FileConversationMemory(Path(tmpdir) / "memory.json")
            executor = LangChainStyleAgentExecutor(
                openai_client=FakeOpenAIClient(responses),
                model="fake-model",
                tool_registry=FakeToolRegistry(),
                memory=memory,
            )

            result = await executor.run("测试问题")
            self.assertEqual(result, "最终答案")
            self.assertEqual(memory.load_messages()[-1].content, "最终答案")

    async def test_multimodal_user_message_saved(self):
        final_message = SimpleNamespace(content="看到了图片", tool_calls=[])
        responses = [
            SimpleNamespace(choices=[SimpleNamespace(finish_reason="stop", message=final_message)]),
        ]
        with tempfile.TemporaryDirectory() as tmpdir:
            memory = FileConversationMemory(Path(tmpdir) / "memory.json")
            executor = LangChainStyleAgentExecutor(
                openai_client=FakeOpenAIClient(responses),
                model="fake-model",
                tool_registry=FakeToolRegistry(),
                memory=memory,
            )
            attachment = [{"type": "image_url", "image_url": {"url": "data:image/png;base64,aaa"}}]
            result = await executor.run("请描述图片", attachment_context=attachment)
            self.assertEqual(result, "看到了图片")
            self.assertIn("image_url", memory.load_messages()[-2].content)


if __name__ == "__main__":
    unittest.main()
