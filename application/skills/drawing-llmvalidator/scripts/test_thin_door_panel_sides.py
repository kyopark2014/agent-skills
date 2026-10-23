"""Thin closed DOOR panels promote adjacent jamb faces and wall bars to WALL."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

import ezdxf

sys.path.insert(0, str(Path(__file__).resolve().parent))

import lib_llm_correct


def _rect(msp, x0, y0, x1, y1, *, layer="BASE"):
    entity = msp.add_lwpolyline(
        [(x0, y0), (x1, y0), (x1, y1), (x0, y1)],
        dxfattribs={"layer": layer},
    )
    entity.closed = True
    return entity


class ThinDoorPanelSideTests(unittest.TestCase):
    def test_jambs_and_wall_bar_beside_thin_door_become_wall(self):
        """클럽하우스 1F 창고: 얇은 DOOR 옆 문틀·위쪽 150 mm 띠 → WALL."""
        doc = ezdxf.new()
        msp = doc.modelspace()
        # 세로 문짝 25×672
        door = _rect(msp, 1000, 0, 1025, 672, layer="DOOR")
        left_outer = _rect(msp, 984, -50, 990, 904)
        left_inner = _rect(msp, 990, -40, 995, 872)
        right_inner = _rect(msp, 1025, -40, 1030, 872)
        right_outer = _rect(msp, 1030, -50, 1036, 904)
        # 문 위쪽 벽 띠 (끊김 180 mm)
        wall_bar = _rect(msp, 900, 852, 1050, 3752)
        # 스윙 열린 사선 문짝 — 올리지 않음
        slant = msp.add_lwpolyline(
            [(1025, 0), (1300, 650), (1340, 640), (1065, -10)],
            dxfattribs={"layer": "BASE"},
        )
        slant.closed = True
        far = _rect(msp, 5000, 0, 5150, 3000)

        promoted = lib_llm_correct.promote_thin_door_panel_sides(msp)

        self.assertGreaterEqual(promoted, 5)
        self.assertEqual(door.dxf.layer, "DOOR")
        self.assertEqual(left_outer.dxf.layer, "WALL")
        self.assertEqual(left_inner.dxf.layer, "WALL")
        self.assertEqual(right_inner.dxf.layer, "WALL")
        self.assertEqual(right_outer.dxf.layer, "WALL")
        self.assertEqual(wall_bar.dxf.layer, "WALL")
        self.assertEqual(slant.dxf.layer, "BASE")
        self.assertEqual(far.dxf.layer, "BASE")

    def test_no_door_keeps_bar(self):
        doc = ezdxf.new()
        msp = doc.modelspace()
        bar = _rect(msp, 0, 0, 150, 3000)

        promoted = lib_llm_correct.promote_thin_door_panel_sides(msp)

        self.assertEqual(promoted, 0)
        self.assertEqual(bar.dxf.layer, "BASE")


if __name__ == "__main__":
    unittest.main()
