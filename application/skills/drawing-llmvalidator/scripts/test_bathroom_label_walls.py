"""화장실 글자가 있으면 비전 상자가 없어도 근처 벽을 올린다."""

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


class BathroomLabelWallTests(unittest.TestCase):
    def test_label_promotes_tilted_enclosure_without_vision_box(self):
        doc = ezdxf.new()
        msp = doc.modelspace()
        msp.add_text("화장실", dxfattribs={"insert": (1500.0, 1200.0), "height": 200.0})
        left = _pair(msp, 200, 200, 200, 2400, 150)
        bottom = _pair(msp, 200, 200, 2800, 200, 150)
        top = _pair(msp, 200, 2400, 2800, 2400, 150)
        right = _pair(msp, 3000, 200, 3200, 2400, 150)
        far = _pair(msp, 10000, 0, 10000, 3000, 150)
        slash = msp.add_line((900, 500), (1700, 1300), dxfattribs={"layer": "BASE"})
        swing = msp.add_arc((400, 400), 700, 0, 90, dxfattribs={"layer": "BASE"})

        promoted = lib_llm_correct.promote_bathroom_label_walls(msp)

        self.assertGreaterEqual(promoted, 8)
        for line in left + bottom + top + right:
            self.assertEqual(line.dxf.layer, "WALL")
        for line in far:
            self.assertEqual(line.dxf.layer, "BASE")
        self.assertEqual(slash.dxf.layer, "BASE")
        self.assertEqual(swing.dxf.layer, "BASE")

    def test_outer_leaf_follows_wall_already_promoted(self):
        doc = ezdxf.new()
        msp = doc.modelspace()
        msp.add_text("화장실(가족형)", dxfattribs={"insert": (0.0, 0.0), "height": 200.0})
        inner = msp.add_line((-1200, 1000), (1200, 1000), dxfattribs={"layer": "WALL"})
        outer = msp.add_line((-1200, 1180), (1200, 1180), dxfattribs={"layer": "BASE"})

        lib_llm_correct.promote_bathroom_label_walls(msp)

        self.assertEqual(inner.dxf.layer, "WALL")
        self.assertEqual(outer.dxf.layer, "WALL")

    def test_tread_bundle_stays(self):
        doc = ezdxf.new()
        msp = doc.modelspace()
        msp.add_text("화장실", dxfattribs={"insert": (1000.0, -500.0), "height": 200.0})
        treads = [
            msp.add_line((0, y), (2000, y), dxfattribs={"layer": "BASE"})
            for y in (200, 450, 700, 950, 1200)
        ]
        behind = msp.add_line((0, 2200), (2000, 2200), dxfattribs={"layer": "BASE"})

        lib_llm_correct.promote_bathroom_label_walls(msp)

        self.assertTrue(all(line.dxf.layer == "BASE" for line in treads))
        self.assertEqual(behind.dxf.layer, "BASE")

    def test_other_room_label_stays(self):
        doc = ezdxf.new()
        msp = doc.modelspace()
        msp.add_text("창고", dxfattribs={"insert": (1000.0, 1000.0), "height": 200.0})
        wall, mate = _pair(msp, 0, 2000, 2000, 2000, 150)

        promoted = lib_llm_correct.promote_bathroom_label_walls(msp)

        self.assertEqual(promoted, 0)
        self.assertEqual(wall.dxf.layer, "BASE")
        self.assertEqual(mate.dxf.layer, "BASE")

    def test_wc_label_promotes_like_bathroom(self):
        doc = ezdxf.new()
        msp = doc.modelspace()
        msp.add_text("WC(여)", dxfattribs={"insert": (1500.0, 1200.0), "height": 200.0})
        left = _pair(msp, 200, 200, 200, 2400, 150)
        bottom = _pair(msp, 200, 200, 2800, 200, 150)
        top = _pair(msp, 200, 2400, 2800, 2400, 150)
        right = _pair(msp, 3000, 200, 3200, 2400, 150)

        promoted = lib_llm_correct.promote_bathroom_label_walls(msp)

        self.assertGreaterEqual(promoted, 8)
        for line in left + bottom + top + right:
            self.assertEqual(line.dxf.layer, "WALL")

    def test_bathroom_tokens_loaded_from_json(self):
        self.assertIn("bathroom", lib_llm_correct.LABEL_TOKENS)
        self.assertIn("화장실", lib_llm_correct.BATHROOM_LABEL_TOKENS)
        self.assertIn("WC", lib_llm_correct.BATHROOM_LABEL_TOKENS)
        self.assertTrue(lib_llm_correct._is_bathroom_label("WC(남)"))
        self.assertTrue(lib_llm_correct._is_bathroom_label("W.C."))
        self.assertFalse(lib_llm_correct._is_bathroom_label("창고"))


if __name__ == "__main__":
    unittest.main()
