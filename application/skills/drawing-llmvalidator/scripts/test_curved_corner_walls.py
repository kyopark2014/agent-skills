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

    def test_small_fillet_arcs_between_walls_become_wall(self):
        """카운터·복도처럼 반지름 ~1 m 동심 필렛도 벽에 닿으면 올린다."""
        doc = ezdxf.new()
        msp = doc.modelspace()
        cx, cy = 0.0, 0.0
        radii = (1000.0, 1200.0)
        start_ang, end_ang = 270.0, 360.0
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
            msp.add_line((sx - 4000.0, sy), (sx, sy), dxfattribs={"layer": "WALL"})
            msp.add_line((ex, ey), (ex, ey + 4000.0), dxfattribs={"layer": "WALL"})

        promoted = lib_llm_correct.promote_curved_corner_walls(msp)

        self.assertEqual(promoted, 2)
        for arc in arcs:
            self.assertEqual(arc.dxf.layer, "WALL")

    def test_door_misclassified_fillet_arcs_become_wall(self):
        """벽 필렛이 문 스윙(DOOR)으로 잘못 올라간 경우에도 벽으로 되돌린다."""
        doc = ezdxf.new()
        msp = doc.modelspace()
        cx, cy = 0.0, 0.0
        radii = (1000.0, 1200.0)
        start_ang, end_ang = 270.0, 360.0
        arcs = []
        for radius in radii:
            arcs.append(
                msp.add_arc(
                    (cx, cy),
                    radius,
                    start_ang,
                    end_ang,
                    dxfattribs={"layer": "DOOR", "color": 3},
                )
            )
            a0 = math.radians(start_ang)
            a1 = math.radians(end_ang)
            sx, sy = cx + radius * math.cos(a0), cy + radius * math.sin(a0)
            ex, ey = cx + radius * math.cos(a1), cy + radius * math.sin(a1)
            msp.add_line((sx - 4000.0, sy), (sx, sy), dxfattribs={"layer": "WALL"})
            msp.add_line((ex, ey), (ex, ey + 4000.0), dxfattribs={"layer": "WALL"})

        promoted = lib_llm_correct.promote_curved_corner_walls(msp)

        self.assertEqual(promoted, 2)
        for arc in arcs:
            self.assertEqual(arc.dxf.layer, "WALL")

    def test_arcs_stay_base_without_wall_anchors(self):
        doc = ezdxf.new()
        msp = doc.modelspace()
        msp.add_arc((0.0, 0.0), 6500.0, -90.0, -36.0, dxfattribs={"layer": "BASE"})
        msp.add_arc((0.0, 0.0), 6600.0, -90.0, -36.0, dxfattribs={"layer": "BASE"})

        promoted = lib_llm_correct.promote_curved_corner_walls(msp)

        self.assertEqual(promoted, 0)

    def test_lone_door_swing_stays_door(self):
        """두께 쌍이 없는 단독 문 스윙은 그대로 둔다."""
        doc = ezdxf.new()
        msp = doc.modelspace()
        arc = msp.add_arc((0.0, 0.0), 1000.0, 0.0, 90.0, dxfattribs={"layer": "DOOR"})
        msp.add_line((-2000.0, 0.0), (0.0, 0.0), dxfattribs={"layer": "WALL"})
        msp.add_line((0.0, 1000.0), (0.0, 3000.0), dxfattribs={"layer": "WALL"})

        promoted = lib_llm_correct.promote_curved_corner_walls(msp)

        self.assertEqual(promoted, 0)
        self.assertEqual(arc.dxf.layer, "DOOR")


if __name__ == "__main__":
    unittest.main()
