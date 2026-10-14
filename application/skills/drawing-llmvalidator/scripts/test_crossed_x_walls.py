"""Crossing X strokes become walls. A thin X inside a door opening does not."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

import ezdxf

sys.path.insert(0, str(Path(__file__).resolve().parent))

import lib_llm_correct


class CrossedXWallTests(unittest.TestCase):
    def test_crossed_lines_become_wall(self):
        doc = ezdxf.new()
        msp = doc.modelspace()
        left = msp.add_line((0, 0), (2000, 1800), dxfattribs={"layer": "BASE"})
        right = msp.add_line((0, 1800), (2000, 0), dxfattribs={"layer": "BASE"})
        plain = msp.add_line((0, 3000), (2000, 3000), dxfattribs={"layer": "BASE"})

        promoted = lib_llm_correct.promote_crossed_x_walls(msp)

        self.assertEqual(promoted, 2)
        self.assertEqual(left.dxf.layer, "WALL")
        self.assertEqual(right.dxf.layer, "WALL")
        self.assertEqual(plain.dxf.layer, "BASE")

    def test_door_thickness_x_stays_base(self):
        doc = ezdxf.new()
        msp = doc.modelspace()
        left = msp.add_line((0, 0), (900, 150), dxfattribs={"layer": "BASE"})
        right = msp.add_line((0, 150), (900, 0), dxfattribs={"layer": "BASE"})

        promoted = lib_llm_correct.promote_crossed_x_walls(msp)

        self.assertEqual(promoted, 0)
        self.assertEqual(left.dxf.layer, "BASE")
        self.assertEqual(right.dxf.layer, "BASE")


if __name__ == "__main__":
    unittest.main()
