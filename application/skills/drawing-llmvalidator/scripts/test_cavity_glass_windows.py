"""이중벽 면 사이 한가운데 평행 유리선은 WINDOW로 올린다."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

import ezdxf

sys.path.insert(0, str(Path(__file__).resolve().parent))

import lib_llm_correct


class CavityGlassWindowTests(unittest.TestCase):
    def test_glass_between_wall_faces_is_window(self):
        doc = ezdxf.new()
        msp = doc.modelspace()
        # 150 mm 이중벽, 가운데 10 mm 간격 유리 이중선(약 1 m + 멀리언 + 1 m)
        face0 = msp.add_line((0.0, 0.0), (2200.0, 0.0), dxfattribs={"layer": "WALL"})
        face1 = msp.add_line((0.0, 150.0), (2200.0, 150.0), dxfattribs={"layer": "WALL"})
        glass_a0 = msp.add_line((50.0, 70.0), (1075.0, 70.0), dxfattribs={"layer": "BASE"})
        glass_a1 = msp.add_line((50.0, 80.0), (1075.0, 80.0), dxfattribs={"layer": "BASE"})
        glass_b0 = msp.add_line((1125.0, 70.0), (2150.0, 70.0), dxfattribs={"layer": "BASE"})
        glass_b1 = msp.add_line((1125.0, 80.0), (2150.0, 80.0), dxfattribs={"layer": "BASE"})
        jamb = msp.add_line((50.0, 0.0), (50.0, 150.0), dxfattribs={"layer": "BASE"})
        outsider = msp.add_line((50.0, 400.0), (1075.0, 400.0), dxfattribs={"layer": "BASE"})

        n_window, n_wall = lib_llm_correct.promote_cavity_glass_windows(msp)

        self.assertEqual(n_window, 4)
        self.assertGreaterEqual(n_wall, 1)
        self.assertEqual(face0.dxf.layer, "WALL")
        self.assertEqual(face1.dxf.layer, "WALL")
        self.assertEqual(glass_a0.dxf.layer, "WINDOW")
        self.assertEqual(glass_a1.dxf.layer, "WINDOW")
        self.assertEqual(glass_b0.dxf.layer, "WINDOW")
        self.assertEqual(glass_b1.dxf.layer, "WINDOW")
        self.assertEqual(jamb.dxf.layer, "WALL")
        self.assertEqual(outsider.dxf.layer, "BASE")


if __name__ == "__main__":
    unittest.main()
