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
            "27F FLOOR PLAN": "27F",
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


class PaperspaceFloorTests(unittest.TestCase):
    def _doc(self, *, status: int = 1, viewport_id: int | None = None):
        import ezdxf

        doc = ezdxf.new("R2010")
        msp = doc.modelspace()
        for index in range(1200):
            y = (index % 40) * 1000.0
            msp.add_line((0, y), (60_000, y))
        for y in range(100_000, 140_000, 8_000):
            msp.add_line((0, y), (60_000, y))
        layout = doc.layouts.new("FLOOR PLAN")
        layout.add_text(
            "27F FLOOR PLAN",
            dxfattribs={"insert": (10, 10), "height": 400},
        )
        viewport = layout.add_viewport(
            center=(400, 300),
            size=(300, 200),
            view_center_point=(30_000, 120_000),
            view_height=50_000,
            status=status,
        )
        if viewport_id is not None:
            viewport.dxf.id = viewport_id
        return doc

    def test_viewport_and_lower_band_split_one_floor(self):
        found = lib_sheet.discover_layout(self._doc(), mode="auto")
        self.assertEqual(found.method, "paperspace")
        self.assertEqual(found.floors, ["27F", "27F_2"])
        boxes = [frame.bbox for frame in found.sheets]
        lower = min(boxes, key=lambda box: box[1])
        upper = max(boxes, key=lambda box: box[1])
        self.assertLess(lower[3], 90_000)
        self.assertGreater(upper[1], 90_000)
        self.assertIn("종이공간", found.warnings[0])

    def test_off_viewport_finds_no_floor(self):
        found = lib_sheet.discover_layout(self._doc(status=0), mode="auto")
        self.assertEqual(found.floors, [])

    def test_layout_viewport_id_is_ignored(self):
        found = lib_sheet.discover_layout(self._doc(viewport_id=1), mode="auto")
        self.assertEqual(found.floors, [])


if __name__ == "__main__":
    unittest.main()
