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
            "피트니스센터, 에듀센터, 관리사무소 평면도": None,
            "스카이커뮤니티 평,입,단면도": None,
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(lib_sheet.parse_floor_label(text), expected)

    def test_facility_plan_title_and_drawing_number(self):
        self.assertEqual(lib_sheet.parse_drawing_number("AA-406"), "AA406")
        self.assertEqual(lib_sheet.normalize_floor_token("AA-406"), "AA406")
        self.assertEqual(lib_sheet.normalize_floor_token("aa406"), "AA406")
        self.assertIsNone(lib_sheet.parse_drawing_number("복도-1"))
        self.assertEqual(
            lib_sheet.parse_plan_title("피트니스센터, 에듀센터, 관리사무소 평면도"),
            "피트니스센터, 에듀센터, 관리사무소 평면도",
        )
        self.assertEqual(
            lib_sheet.parse_plan_title("스카이커뮤니티 평,입,단면도"),
            "스카이커뮤니티 평,입,단면도",
        )
        self.assertIsNone(lib_sheet.parse_plan_title("1층 평면도"))
        self.assertIsNone(lib_sheet.parse_plan_title("평면도"))

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


class FacilityPlanSheetTests(unittest.TestCase):
    def test_borders_without_floor_numbers_use_drawing_numbers(self):
        import ezdxf

        doc = ezdxf.new()
        msp = doc.modelspace()

        def border(x, y, w, h):
            msp.add_lwpolyline([(x, y), (x + w, y), (x + w, y + h), (x, y + h)], close=True)

        border(0, 0, 120_000, 83_000)
        border(0, -100_000, 80_000, 55_000)
        msp.add_text(
            "피트니스센터, 에듀센터, 관리사무소 평면도",
            dxfattribs={"insert": (40_000, 4_000), "height": 1200},
        )
        msp.add_text("AA-406", dxfattribs={"insert": (100_000, 4_000), "height": 1200})
        msp.add_text(
            "경비실-2, 티카페, 경로당 평면도",
            dxfattribs={"insert": (20_000, -96_000), "height": 800},
        )
        msp.add_text("경로당 평면도", dxfattribs={"insert": (20_000, -90_000), "height": 700})
        msp.add_text("AA-409", dxfattribs={"insert": (60_000, -96_000), "height": 800})

        found = lib_sheet.discover_layout(doc, mode="sheet")

        self.assertEqual(found.floors, ["AA409", "AA406"])
        titles = {sheet.floor: sheet.title for sheet in found.sheets}
        self.assertEqual(titles["AA409"], "경비실-2, 티카페, 경로당 평면도")
        self.assertEqual(titles["AA406"], "피트니스센터, 에듀센터, 관리사무소 평면도")
        self.assertTrue(any("도면번호" in note for note in found.warnings))


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
