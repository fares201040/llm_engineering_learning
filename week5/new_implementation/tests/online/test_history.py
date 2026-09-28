from __future__ import annotations

import unittest

from week5.new_implementation.online.history import model_history


class ModelHistoryTests(unittest.TestCase):
    def test_full_short_history_includes_both_speakers_in_order(self):
        history = (
            {"role": "user", "content": "First question"},
            {"role": "assistant", "content": "First answer"},
            {"role": "user", "content": "Follow-up"},
        )
        self.assertEqual(model_history(history), history)

    def test_long_history_keeps_recent_turns_and_labels_older_excerpts(self):
        history = tuple(
            {
                "role": "user" if i % 2 == 0 else "assistant",
                "content": f"turn {i} " + "x" * 300,
            }
            for i in range(100)
        )
        prepared = model_history(history)
        self.assertEqual(prepared[0]["role"], "history_compaction")
        self.assertIn("88 messages", prepared[0]["content"])
        self.assertEqual(prepared[1:], history[-12:])
        self.assertLessEqual(sum(len(item["content"]) for item in prepared), 24000)

    def test_oversized_recent_history_is_also_bounded(self):
        history = (
            {"role": "user", "content": "x" * 30000},
            {"role": "assistant", "content": "y" * 30000},
        )
        prepared = model_history(history)
        self.assertLessEqual(sum(len(item["content"]) for item in prepared), 24000)


if __name__ == "__main__":
    unittest.main()
