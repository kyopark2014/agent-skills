"""Curved corner arcs between straight walls become walls."""

from __future__ import annotations

import math
import sys
import unittest
from pathlib import Path

import ezdxf

sys.path.insert(0, str(Path(__file__).resolve().parent))

import lib_llm_correct


class CurvedCornerWallTests(unittest.TestCase):
    def test_concentric_arcs_between_walls_become_wall(self):
        """소화수조 모서리처럼 동심 ARC가 가로·사선 WALL 사이에 있으면 올린다."""
        doc = ezdxf.new()
        msp = doc.modelspace()
        cx, cy = 0.0, 0.0
        radii = (6400.0, 6500.0, 6600.0, 6900.0)
        start_ang, end_ang = -90.0, -36.1
        arcs = []
        for radius in radii:
            arcs.append(
                msp.add_arc(
                    (cx, cy),
                    radius,
                    start_ang,
                    end_ang,
                    dxfattribs={"layer": "BASE"},
                )
            )
            a0 = math.radians(start_ang)
            a1 = math.radians(end_ang)
            sx, sy = cx + radius * math.cos(a0), cy + radius * math.sin(a0)
            ex, ey = cx + radius * math.cos(a1), cy + radius * math.sin(a1)
            msp.add_line((sx - 8000.0, sy), (sx, sy), dxfattribs={"layer": "WALL"})
            msp.add_line((ex, ey), (ex + 6000.0, ey + 8000.0), dxfattribs={"layer": "WALL"})

        promoted = lib_llm_correct.promote_curved_corner_walls(msp)

        self.assertEqual(promoted, 4)
        for arc in arcs:
            self.assertEqual(arc.dxf.layer, "WALL")

    def test_arcs_stay_base_without_wall_anchors(self):
        doc = ezdxf.new()
        msp = doc.modelspace()
        msp.add_arc((0.0, 0.0), 6500.0, -90.0, -36.0, dxfattribs={"layer": "BASE"})
        msp.add_arc((0.0, 0.0), 6600.0, -90.0, -36.0, dxfattribs={"layer": "BASE"})

        promoted = lib_llm_correct.promote_curved_corner_walls(msp)

        self.assertEqual(promoted, 0)


if __name__ == "__main__":
    unittest.main()
