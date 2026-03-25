import sqlite3
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

            with sqlite3.connect(memory_file) as conn:
                count = conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
            self.assertEqual(count, 4)
            self.assertEqual(memory.build_context_messages()[-1]["content"], "好的")

            memory.clear()
            with sqlite3.connect(memory_file) as conn:
                count = conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
            self.assertEqual(count, 0)

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

            messages = memory.load_messages()
            self.assertEqual(messages[0].role, "system")
            self.assertTrue(messages[0].metadata.get("memory_compressed"))

    def test_greeting_should_not_load_context(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            memory_file = Path(tmpdir) / "memory.db"
            memory = FileConversationMemory(memory_file, max_messages=6)
            memory.save_turn("请记住：密码是123", "好的，已记录")
            self.assertEqual(memory.build_context_messages(query="你好"), [])


if __name__ == "__main__":
    unittest.main()
