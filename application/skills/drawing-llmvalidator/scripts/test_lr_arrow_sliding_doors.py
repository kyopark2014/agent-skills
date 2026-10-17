"""Left/right arrow marks on axis-aligned sliding doors become DOOR; flanks become WALL."""

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


class LrArrowSlidingDoorTests(unittest.TestCase):
    def test_left_right_arrows_promote_door_and_flank_walls(self):
        doc = ezdxf.new()
        msp = doc.modelspace()
        # 벽면 y=0..150, 문짝 y=50..120, 화살표는 아래
        wall_left = _rect(msp, 0, 0, 900, 150)
        panel = _rect(msp, 1000, 50, 2100, 120)
        wall_right = _rect(msp, 2200, 0, 3200, 150)
        # 좌→우 화살표 (샤프트 1167, 미늘 224, 꺾임 ~37°)
        arrow = msp.add_lwpolyline(
            [(1000.0, -400.0), (2167.0, -400.0), (1987.0, -535.0)],
            dxfattribs={"layer": "BASE"},
        )
        arrow.closed = False
        far = _rect(msp, 20000, 0, 21000, 150)

        demoted, promoted = lib_llm_correct.separate_lr_arrow_sliding_doors(msp)

        self.assertGreaterEqual(demoted, 2)
        self.assertGreaterEqual(promoted, 1)
        self.assertEqual(panel.dxf.layer, "DOOR")
        self.assertEqual(arrow.dxf.layer, "DOOR")
        self.assertEqual(wall_left.dxf.layer, "WALL")
        self.assertEqual(wall_right.dxf.layer, "WALL")
        self.assertEqual(far.dxf.layer, "BASE")

    def test_no_arrow_keeps_panel(self):
        doc = ezdxf.new()
        msp = doc.modelspace()
        panel = _rect(msp, 1000, 50, 2100, 120)
        wall = _rect(msp, 0, 0, 900, 150)

        demoted, promoted = lib_llm_correct.separate_lr_arrow_sliding_doors(msp)

        self.assertEqual(demoted, 0)
        self.assertEqual(promoted, 0)
        self.assertEqual(panel.dxf.layer, "BASE")
        self.assertEqual(wall.dxf.layer, "BASE")


if __name__ == "__main__":
    unittest.main()
