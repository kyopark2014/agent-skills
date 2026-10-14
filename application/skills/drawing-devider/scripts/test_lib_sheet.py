"""Floor titles keep word spaces and accept an equipment-code prefix."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import lib_sheet


class ParseFloorLabelTests(unittest.TestCase):
    def test_word_spaces_and_equipment_prefix(self):
        cases = {
            "1층 평면도": "1F",
            "1 층 평 면 도": "1F",
            "옥 상 평 면 도": "RF",
            "1층 냉난방 평면도": "1F",
            "1층 냉난방 제어 평면도": "1F",
            "EHP 1층 냉난방 제어 평면도": "1F",
            "EHP 1층 냉난방 철거 평면도 ": "1F",
            "EHP 4층 냉난방 제어 평면도": "4F",
            "EHP 지붕층 냉난방 제어 평면도": "RF",
            "EHP 지붕층 냉난방 철거 평면도": "RF",
            "지하1층": "B1F",
            "제2층": "2F",
            "B1F": "B1F",
            "12F PLAN": "12F",
            "2층 참조": None,
            "복도-1": None,
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(lib_sheet.parse_floor_label(text), expected)

    def test_same_floor_control_and_demo_stay_one_sheet(self):
        rect = (0.0, 0.0, 126_000.0, 89_000.0)
        hits = [
            lib_sheet._TextHit("EHP 1층 냉난방 제어 평면도", "1F", 80_000.0, 70_000.0, 500.0),
            lib_sheet._TextHit("EHP 1층 냉난방 철거 평면도", "1F", 40_000.0, 20_000.0, 500.0),
        ]
        warnings: list[str] = []
        frames = lib_sheet._frames_from([rect], hits, warnings)
        self.assertEqual([frame.floor for frame in frames], ["1F"])
        self.assertEqual(warnings, [])


if __name__ == "__main__":
    unittest.main()
