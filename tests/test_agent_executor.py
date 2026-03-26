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

    async def test_duplicate_side_effect_tool_is_blocked(self):
        first_tool_msg = SimpleNamespace(
            content=None,
            tool_calls=[
                SimpleNamespace(
                    id="call-1",
                    function=SimpleNamespace(name="send_message_by_request", arguments=json.dumps({"request": "发消息"})),
                )
            ],
            model_dump=lambda: {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call-1",
                        "type": "function",
                        "function": {"name": "send_message_by_request", "arguments": json.dumps({"request": "发消息"})},
                    }
                ],
            },
        )
        second_tool_msg = SimpleNamespace(
            content=None,
            tool_calls=[
                SimpleNamespace(
                    id="call-2",
                    function=SimpleNamespace(name="send_message_by_request", arguments=json.dumps({"request": "发消息"})),
                )
            ],
            model_dump=lambda: {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call-2",
                        "type": "function",
                        "function": {"name": "send_message_by_request", "arguments": json.dumps({"request": "发消息"})},
                    }
                ],
            },
        )
        final_message = SimpleNamespace(content="已完成", tool_calls=[])
        responses = [
            SimpleNamespace(choices=[SimpleNamespace(finish_reason="tool_calls", message=first_tool_msg)]),
            SimpleNamespace(choices=[SimpleNamespace(finish_reason="tool_calls", message=second_tool_msg)]),
            SimpleNamespace(choices=[SimpleNamespace(finish_reason="stop", message=final_message)]),
        ]

        with tempfile.TemporaryDirectory() as tmpdir:
            memory = FileConversationMemory(Path(tmpdir) / "memory.json")
            registry = FakeToolRegistry()
            executor = LangChainStyleAgentExecutor(
                openai_client=FakeOpenAIClient(responses),
                model="fake-model",
                tool_registry=registry,
                memory=memory,
            )

            result = await executor.run("帮我发送一次消息")
            self.assertEqual(result, "已完成")
            self.assertEqual(len(registry.calls), 1)


if __name__ == "__main__":
    unittest.main()
