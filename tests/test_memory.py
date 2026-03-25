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

    def test_memory_compression(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            memory_file = Path(tmpdir) / "memory.json"
            memory = FileConversationMemory(
                memory_file,
                max_messages=6,
                compress_trigger_messages=10,
                summary_max_chars=500,
            )
            for idx in range(8):
                memory.save_turn(f"问题{idx}", f"回答{idx}")

            payload = json.loads(memory_file.read_text(encoding="utf-8"))
            messages = payload["messages"]
            self.assertEqual(messages[0]["role"], "system")
            self.assertTrue(messages[0]["metadata"].get("memory_compressed"))


if __name__ == "__main__":
    unittest.main()
