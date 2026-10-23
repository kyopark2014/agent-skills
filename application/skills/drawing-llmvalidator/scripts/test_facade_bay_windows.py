"""외벽 이중면 위 유리·피어 교차 → WINDOW + 양옆 WALL."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

import ezdxf

sys.path.insert(0, str(Path(__file__).resolve().parent))

import lib_llm_correct


class FacadeBayWindowTests(unittest.TestCase):
    def test_collinear_glass_pier_bays_become_windows(self):
        """같은 이중면 팩에서 유리 베이 반복(+리턴 갭 피어)이면 창."""
        doc = ezdxf.new()
        msp = doc.modelspace()
        # 유리 베이 4중면(두께 안 평행선). 유리 900 ×2 베이 + 중간 피어.
        ys = (0.0, 95.0, 108.0, 203.0)
        glass_a = [
            msp.add_lwpolyline([(0.0, y), (900.0, y)], dxfattribs={"layer": "BASE"})
            for y in ys
        ]
        pier_m = [
            msp.add_lwpolyline(
                [(1500.0, y), (1900.0, y)], dxfattribs={"layer": "BASE"}
            )
            for y in ys[:2]
        ]
        glass_b = [
            msp.add_lwpolyline(
                [(2500.0, y), (3400.0, y)], dxfattribs={"layer": "BASE"}
            )
            for y in ys
        ]
        mullion = msp.add_lwpolyline(
            [(0.0, ys[0]), (0.0, ys[-1])], dxfattribs={"layer": "BASE"}
        )
        glass_a0, glass_a1 = glass_a[0], glass_a[1]
        glass_b0, glass_b1 = glass_b[0], glass_b[1]
        pier_m0, pier_m1 = pier_m[0], pier_m[1]

        n_window, n_wall = lib_llm_correct.promote_facade_bay_windows(msp)

        self.assertGreaterEqual(n_window, 4)
        self.assertGreaterEqual(n_wall, 2)
        self.assertEqual(glass_a0.dxf.layer, "WINDOW")
        self.assertEqual(glass_a1.dxf.layer, "WINDOW")
        self.assertEqual(glass_b0.dxf.layer, "WINDOW")
        self.assertEqual(glass_b1.dxf.layer, "WINDOW")
        self.assertEqual(pier_m0.dxf.layer, "WALL")
        self.assertEqual(pier_m1.dxf.layer, "WALL")
        self.assertEqual(mullion.dxf.layer, "WALL")

    def test_reclaims_inwall_sliding_door_false_positive(self):
        """미닫이로 올라간 DOOR 유리도 피어가 있으면 WINDOW로 되돌린다."""
        doc = ezdxf.new()
        msp = doc.modelspace()
        ys = (0.0, 95.0, 108.0, 203.0)
        pier_l = [
            msp.add_lwpolyline([(0.0, y), (400.0, y)], dxfattribs={"layer": "BASE"})
            for y in ys[:2]
        ]
        glass_a = [
            msp.add_lwpolyline(
                [(400.0, y), (1300.0, y)],
                dxfattribs={"layer": "DOOR", "color": 3},
            )
            for y in ys
        ]
        pier_r = [
            msp.add_lwpolyline(
                [(1300.0, y), (1700.0, y)], dxfattribs={"layer": "BASE"}
            )
            for y in ys[:2]
        ]
        pier_l0, glass_a0, glass_a1, pier_r1 = (
            pier_l[0],
            glass_a[0],
            glass_a[1],
            pier_r[1],
        )

        n_window, n_wall = lib_llm_correct.promote_facade_bay_windows(msp)

        self.assertGreaterEqual(n_window, 2)
        self.assertGreaterEqual(n_wall, 2)
        self.assertEqual(glass_a0.dxf.layer, "WINDOW")
        self.assertEqual(glass_a1.dxf.layer, "WINDOW")
        self.assertEqual(pier_l0.dxf.layer, "WALL")
        self.assertEqual(pier_r1.dxf.layer, "WALL")

    def test_inwall_sliding_skips_facade_glass_with_piers(self):
        """피어에 맞닿은 외벽 유리는 미닫이 탐지에서 빠진다."""
        doc = ezdxf.new()
        msp = doc.modelspace()
        y0, y1 = 0.0, 95.0
        # 포켓 바깥면(미닫이 조건용)
        msp.add_lwpolyline([(-50.0, -50.0), (1800.0, -50.0)], dxfattribs={"layer": "BASE"})
        msp.add_lwpolyline([(-50.0, 145.0), (1800.0, 145.0)], dxfattribs={"layer": "BASE"})
        msp.add_lwpolyline([(0.0, y0), (400.0, y0)], dxfattribs={"layer": "BASE"})
        msp.add_lwpolyline([(0.0, y1), (400.0, y1)], dxfattribs={"layer": "BASE"})
        glass_a0 = msp.add_lwpolyline(
            [(400.0, y0), (1300.0, y0)], dxfattribs={"layer": "BASE"}
        )
        glass_a1 = msp.add_lwpolyline(
            [(400.0, y1), (1300.0, y1)], dxfattribs={"layer": "BASE"}
        )
        msp.add_lwpolyline([(1300.0, y0), (1700.0, y0)], dxfattribs={"layer": "BASE"})
        msp.add_lwpolyline([(1300.0, y1), (1700.0, y1)], dxfattribs={"layer": "BASE"})

        found = lib_llm_correct._find_inwall_line_sliding_doors(msp)
        self.assertEqual(len(found), 0)
        self.assertEqual(glass_a0.dxf.layer, "BASE")
        self.assertEqual(glass_a1.dxf.layer, "BASE")


if __name__ == "__main__":
    unittest.main()
