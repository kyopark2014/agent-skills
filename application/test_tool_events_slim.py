"""tool_events_slim keeps UI payloads bounded."""

from __future__ import annotations

import unittest

from application.tool_events_slim import (
    TOOL_EVENT_FIELD_MAX_CHARS,
    TOOL_EVENTS_MAX_COUNT,
    slim_messages_for_api,
    slim_tool_events,
)


class ToolEventsSlimTests(unittest.TestCase):
    def test_truncates_large_data_fields(self):
        huge = "x" * (TOOL_EVENT_FIELD_MAX_CHARS + 500)
        out = slim_tool_events([{"type": "tool_result", "data": huge, "tool": "bash"}])
        self.assertEqual(len(out), 1)
        self.assertLessEqual(len(out[0]["data"]), TOOL_EVENT_FIELD_MAX_CHARS + 40)
        self.assertIn("truncated", out[0]["data"])

    def test_keeps_recent_events_only(self):
        events = [
            {"type": "info", "data": f"n={i}"} for i in range(TOOL_EVENTS_MAX_COUNT + 25)
        ]
        out = slim_tool_events(events)
        self.assertEqual(len(out), TOOL_EVENTS_MAX_COUNT)
        self.assertEqual(out[0]["data"], f"n={25}")
        self.assertEqual(out[-1]["data"], f"n={TOOL_EVENTS_MAX_COUNT + 24}")

    def test_slim_messages_for_api(self):
        huge = "y" * (TOOL_EVENT_FIELD_MAX_CHARS + 10)
        messages = [
            {
                "id": "m1",
                "role": "assistant",
                "content": "ok",
                "tool_events": [{"type": "tool_result", "data": huge}],
            }
        ]
        out = slim_messages_for_api(messages)
        self.assertEqual(out[0]["content"], "ok")
        self.assertIn("truncated", out[0]["tool_events"][0]["data"])


if __name__ == "__main__":
    unittest.main()
