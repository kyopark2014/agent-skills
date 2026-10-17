"""사무실·PDR 글자/비전 둘레로 외곽 벽을 올린다. 가구는 남긴다."""

from __future__ import annotations

import math
import sys
import unittest
from pathlib import Path

import ezdxf

sys.path.insert(0, str(Path(__file__).resolve().parent))

import lib_llm_correct


def _pair(msp, x0, y0, x1, y1, gap, layer="BASE"):
    length = math.hypot(x1 - x0, y1 - y0)
    nx, ny = -(y1 - y0) / length, (x1 - x0) / length
    near = msp.add_line((x0, y0), (x1, y1), dxfattribs={"layer": layer})
    far = msp.add_line(
        (x0 + nx * gap, y0 + ny * gap),
        (x1 + nx * gap, y1 + ny * gap),
        dxfattribs={"layer": layer},
    )
    return near, far


class OfficeLabelWallTests(unittest.TestCase):
    def test_pdr_label_promotes_wall_bar_and_skips_table(self):
        doc = ezdxf.new()
        msp = doc.modelspace()
        msp.add_text("PDR-1", dxfattribs={"insert": (1500.0, 2500.0), "height": 200.0})
        right = msp.add_lwpolyline(
            [(2980, 200), (3130, 200), (3130, 4800), (2980, 4800)],
            close=True,
            dxfattribs={"layer": "BASE"},
        )
        bottom = msp.add_lwpolyline(
            [(200, 50), (2800, 50), (2800, 200), (200, 200)],
            close=True,
            dxfattribs={"layer": "BASE"},
        )
        table = msp.add_lwpolyline(
            [(900, 1800), (2100, 1800), (2100, 3200), (900, 3200)],
            close=True,
            dxfattribs={"layer": "BASE"},
        )
        far = msp.add_lwpolyline(
            [(9000, 0), (9150, 0), (9150, 5000), (9000, 5000)],
            close=True,
            dxfattribs={"layer": "BASE"},
        )

        promoted = lib_llm_correct.promote_office_label_walls(msp)

        self.assertGreaterEqual(promoted, 2)
        self.assertEqual(right.dxf.layer, "WALL")
        self.assertEqual(bottom.dxf.layer, "WALL")
        self.assertEqual(table.dxf.layer, "BASE")
        self.assertEqual(far.dxf.layer, "BASE")

    def test_office_label_promotes_double_lines(self):
        doc = ezdxf.new()
        msp = doc.modelspace()
        msp.add_text("사무실", dxfattribs={"insert": (1500.0, 1500.0), "height": 200.0})
        left = _pair(msp, 200, 200, 200, 2800, 150)
        right = _pair(msp, 3000, 200, 3000, 2800, 150)
        top = _pair(msp, 200, 2800, 3000, 2800, 150)
        bottom = _pair(msp, 200, 200, 3000, 200, 150)
        furniture = msp.add_line((1000, 1400), (2000, 1400), dxfattribs={"layer": "BASE"})

        promoted = lib_llm_correct.promote_office_label_walls(msp)

        self.assertGreaterEqual(promoted, 8)
        for line in left + right + top + bottom:
            self.assertEqual(line.dxf.layer, "WALL")
        self.assertEqual(furniture.dxf.layer, "BASE")

    def test_other_room_label_stays(self):
        doc = ezdxf.new()
        msp = doc.modelspace()
        msp.add_text("창고", dxfattribs={"insert": (1000.0, 1000.0), "height": 200.0})
        bar = msp.add_lwpolyline(
            [(0, 0), (150, 0), (150, 3000), (0, 3000)],
            close=True,
            dxfattribs={"layer": "BASE"},
        )

        promoted = lib_llm_correct.promote_office_label_walls(msp)

        self.assertEqual(promoted, 0)
        self.assertEqual(bar.dxf.layer, "BASE")

    def test_office_tokens_loaded_from_json(self):
        self.assertIn("office", lib_llm_correct.LABEL_TOKENS)
        self.assertIn("PDR", lib_llm_correct.OFFICE_LABEL_TOKENS)
        self.assertTrue(lib_llm_correct._is_office_label("PDR-4"))
        self.assertTrue(lib_llm_correct._is_office_label("회의실#2"))
        self.assertFalse(lib_llm_correct._is_office_label("화장실"))


class VisionOfficeWallTests(unittest.TestCase):
    def test_office_strip_promotes_perimeter_not_table(self):
        doc = ezdxf.new()
        msp = doc.modelspace()
        left = msp.add_line((0, 0), (0, 5000), dxfattribs={"layer": "BASE"})
        right = msp.add_line((4000, 0), (4000, 5000), dxfattribs={"layer": "BASE"})
        table = msp.add_line((1200, 2000), (2800, 2000), dxfattribs={"layer": "BASE"})
        swing = msp.add_arc((500, 100), 900, 0, 90, dxfattribs={"layer": "BASE"})

        promoted = lib_llm_correct.promote_vision_office_walls(
            msp,
            [
                {"label": "사무실", "xmin": -200, "ymin": 0, "xmax": 300, "ymax": 5000},
                {"label": "PDR", "xmin": 3800, "ymin": 0, "xmax": 4300, "ymax": 5000},
            ],
        )

        self.assertEqual(promoted, 2)
        self.assertEqual(left.dxf.layer, "WALL")
        self.assertEqual(right.dxf.layer, "WALL")
        self.assertEqual(table.dxf.layer, "BASE")
        self.assertEqual(swing.dxf.layer, "BASE")

    def test_full_room_box_is_ignored_like_outer(self):
        doc = ezdxf.new()
        msp = doc.modelspace()
        edge = msp.add_line((0, 0), (0, 5000), dxfattribs={"layer": "BASE"})

        promoted = lib_llm_correct.promote_vision_office_walls(
            msp,
            [{"label": "사무실", "xmin": 0, "ymin": 0, "xmax": 4000, "ymax": 5000}],
        )

        self.assertEqual(promoted, 0)
        self.assertEqual(edge.dxf.layer, "BASE")


if __name__ == "__main__":
    unittest.main()
