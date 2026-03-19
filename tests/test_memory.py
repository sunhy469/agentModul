import json
import tempfile
import unittest
from pathlib import Path

from agent.memory import FileConversationMemory


class FileConversationMemoryTest(unittest.TestCase):
    def test_save_turn_and_clear(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            memory_file = Path(tmpdir) / "memory.json"
            memory = FileConversationMemory(memory_file, max_messages=4)

            memory.save_turn("你好", "你好，我在")
            memory.save_turn("继续", "好的")

            payload = json.loads(memory_file.read_text(encoding="utf-8"))
            self.assertEqual(len(payload["messages"]), 4)
            self.assertEqual(memory.build_context_messages()[-1]["content"], "好的")

            memory.clear()
            payload = json.loads(memory_file.read_text(encoding="utf-8"))
            self.assertEqual(payload["messages"], [])


if __name__ == "__main__":
    unittest.main()
