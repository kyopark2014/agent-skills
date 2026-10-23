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

    def test_void_x_and_sheet_border_stay_base(self):
        """AABB만 겹치는 보이드 X·도곽은 WALL로 올리지 않는다."""
        doc = ezdxf.new()
        msp = doc.modelspace()
        wall_left = _rect(msp, 0, 0, 900, 150)
        panel = _rect(msp, 1000, 50, 2100, 120)
        wall_right = _rect(msp, 2200, 0, 3200, 150)
        arrow = msp.add_lwpolyline(
            [(1000.0, -400.0), (2167.0, -400.0), (1987.0, -535.0)],
            dxfattribs={"layer": "BASE"},
        )
        arrow.closed = False
        # 문 면 근처에서 시작하는 긴 대각선(주차장 상부 OPEN X)
        void_x = msp.add_line((1500.0, 80.0), (-20000.0, -40000.0), dxfattribs={"layer": "BASE"})
        sheet = _rect(msp, -50000, -50000, 120000, 80000)

        demoted, promoted = lib_llm_correct.separate_lr_arrow_sliding_doors(msp)

        self.assertGreaterEqual(demoted, 2)
        self.assertGreaterEqual(promoted, 1)
        self.assertEqual(panel.dxf.layer, "DOOR")
        self.assertEqual(arrow.dxf.layer, "DOOR")
        self.assertEqual(wall_left.dxf.layer, "WALL")
        self.assertEqual(wall_right.dxf.layer, "WALL")
        self.assertEqual(void_x.dxf.layer, "BASE")
        self.assertEqual(sheet.dxf.layer, "BASE")

    def test_hook_arrow_thin_panel_becomes_door(self):
        """클럽하우스 1F 유리 베이: 4점 → 훅 화살표 + 얇은 문짝 → DOOR."""
        doc = ezdxf.new()
        msp = doc.modelspace()
        wall_left = _rect(msp, 0, 0, 900, 150)
        # 두께 35 mm 이중 레일 문짝 (5점, 끝점=시작점)
        panel = msp.add_lwpolyline(
            [
                (1000.0, 50.0),
                (2130.0, 50.0),
                (2130.0, 85.0),
                (1000.0, 85.0),
                (1000.0, 50.0),
            ],
            dxfattribs={"layer": "WALL"},
        )
        panel.closed = False
        wall_right = _rect(msp, 2200, 0, 3200, 150)
        # → 훅: 샤프트 450, 미늘 60, 팁 30
        arrow = msp.add_lwpolyline(
            [(1000.0, -40.0), (1450.0, -40.0), (1450.0, 20.0), (1420.0, 20.0)],
            dxfattribs={"layer": "BASE"},
        )
        arrow.closed = False
        head = _rect(msp, 1270, -40, 1450, 10)
        # 팁≈샤프트인 얇은 U는 훅 화살표가 아니다. 문 옆 promote 창 밖.
        thin_u = msp.add_lwpolyline(
            [(50000.0, 0.0), (50000.0, 600.0), (49980.0, 600.0), (49980.0, 0.0)],
            dxfattribs={"layer": "BASE"},
        )
        thin_u.closed = False

        demoted, promoted = lib_llm_correct.separate_lr_arrow_sliding_doors(msp)

        self.assertGreaterEqual(demoted, 2)
        self.assertGreaterEqual(promoted, 1)
        self.assertEqual(panel.dxf.layer, "DOOR")
        self.assertEqual(arrow.dxf.layer, "DOOR")
        self.assertEqual(head.dxf.layer, "DOOR")
        self.assertEqual(wall_left.dxf.layer, "WALL")
        self.assertEqual(wall_right.dxf.layer, "WALL")
        self.assertEqual(thin_u.dxf.layer, "BASE")


if __name__ == "__main__":
    unittest.main()
