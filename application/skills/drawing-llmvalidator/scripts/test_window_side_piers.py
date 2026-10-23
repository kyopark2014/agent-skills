"""창문 유리 양옆 짧은 이중선(피어)은 WALL, 유리는 WINDOW."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

import ezdxf

sys.path.insert(0, str(Path(__file__).resolve().parent))

import lib_llm_correct


class WindowSidePierTests(unittest.TestCase):
    def test_glass_flank_piers_become_walls(self):
        doc = ezdxf.new()
        msp = doc.modelspace()
        # 세로 외벽: 피어 150 mm · 유리 70 mm (피어 면 안). 한 창 + 양옆 피어.
        # x=0/150 = 피어 면, x=40/110 = 유리.
        pier_below_a = msp.add_line((0.0, 0.0), (0.0, 400.0), dxfattribs={"layer": "BASE"})
        pier_below_b = msp.add_line((150.0, 0.0), (150.0, 400.0), dxfattribs={"layer": "BASE"})
        glass_a = msp.add_line((40.0, 400.0), (40.0, 1500.0), dxfattribs={"layer": "BASE"})
        glass_b = msp.add_line((110.0, 400.0), (110.0, 1500.0), dxfattribs={"layer": "BASE"})
        pier_above_a = msp.add_line((0.0, 1500.0), (0.0, 1900.0), dxfattribs={"layer": "BASE"})
        pier_above_b = msp.add_line((150.0, 1500.0), (150.0, 1900.0), dxfattribs={"layer": "BASE"})
        # 유리와 무관한 짧은 이중선은 올리지 않는다.
        lone_a = msp.add_line((500.0, 0.0), (500.0, 400.0), dxfattribs={"layer": "BASE"})
        lone_b = msp.add_line((650.0, 0.0), (650.0, 400.0), dxfattribs={"layer": "BASE"})

        n_window, n_wall = lib_llm_correct.promote_window_side_piers(msp)

        self.assertEqual(n_window, 2)
        self.assertEqual(n_wall, 4)
        self.assertEqual(glass_a.dxf.layer, "WINDOW")
        self.assertEqual(glass_b.dxf.layer, "WINDOW")
        self.assertEqual(pier_below_a.dxf.layer, "WALL")
        self.assertEqual(pier_below_b.dxf.layer, "WALL")
        self.assertEqual(pier_above_a.dxf.layer, "WALL")
        self.assertEqual(pier_above_b.dxf.layer, "WALL")
        self.assertEqual(lone_a.dxf.layer, "BASE")
        self.assertEqual(lone_b.dxf.layer, "BASE")

    def test_horizontal_facade_same_rule(self):
        doc = ezdxf.new()
        msp = doc.modelspace()
        pier_left_a = msp.add_line((0.0, 0.0), (400.0, 0.0), dxfattribs={"layer": "BASE"})
        pier_left_b = msp.add_line((0.0, 150.0), (400.0, 150.0), dxfattribs={"layer": "BASE"})
        glass_a = msp.add_line((400.0, 40.0), (1500.0, 40.0), dxfattribs={"layer": "BASE"})
        glass_b = msp.add_line((400.0, 110.0), (1500.0, 110.0), dxfattribs={"layer": "BASE"})
        pier_right_a = msp.add_line((1500.0, 0.0), (1900.0, 0.0), dxfattribs={"layer": "BASE"})
        pier_right_b = msp.add_line((1500.0, 150.0), (1900.0, 150.0), dxfattribs={"layer": "BASE"})

        n_window, n_wall = lib_llm_correct.promote_window_side_piers(msp)

        self.assertEqual(n_window, 2)
        self.assertEqual(n_wall, 4)
        self.assertEqual(glass_a.dxf.layer, "WINDOW")
        self.assertEqual(pier_left_a.dxf.layer, "WALL")
        self.assertEqual(pier_right_b.dxf.layer, "WALL")


if __name__ == "__main__":
    unittest.main()
