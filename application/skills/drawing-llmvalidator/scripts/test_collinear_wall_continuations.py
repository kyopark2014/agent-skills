"""이어진 이중선 승격 — 침대/패널 닫힌 윤곽은 올리지 않는다."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

import ezdxf

sys.path.insert(0, str(Path(__file__).resolve().parent))

import lib_llm_correct


class CollinearWallContinuationsTests(unittest.TestCase):
    def test_bed_panel_outline_not_promoted(self):
        """507호처럼 1500×2000 침대/패널이 벽에 이어져도 BASE 유지."""
        doc = ezdxf.new()
        msp = doc.modelspace()
        # 위쪽 이중 WALL (패널 윗변과 끝으로 이어짐)
        msp.add_line(
            (0.0, 2000.0), (1500.0, 2000.0),
            dxfattribs={"layer": "WALL", "color": 1},
        )
        msp.add_line(
            (0.0, 2150.0), (1500.0, 2150.0),
            dxfattribs={"layer": "WALL", "color": 1},
        )
        # 패널 윗변이 WALL 아래에 끝으로 맞닿고, 아랫변이 평행 짝
        bed = msp.add_lwpolyline(
            [
                (1500.0, 0.0),
                (3000.0, 0.0),
                (3000.0, 2000.0),
                (1500.0, 2000.0),
            ],
            close=True,
            dxfattribs={"layer": "BASE", "color": 8},
        )

        lib_llm_correct.promote_collinear_wall_continuations(msp)

        self.assertEqual(bed.dxf.layer, "BASE")

    def test_open_corridor_face_still_promoted(self):
        """복도처럼 열린 이중 BASE 면은 기존처럼 WALL로 올린다."""
        doc = ezdxf.new()
        msp = doc.modelspace()
        msp.add_line(
            (0.0, 0.0), (2000.0, 0.0),
            dxfattribs={"layer": "WALL", "color": 1},
        )
        msp.add_line(
            (0.0, 150.0), (2000.0, 150.0),
            dxfattribs={"layer": "WALL", "color": 1},
        )
        cont = msp.add_line(
            (2000.0, 0.0), (4000.0, 0.0),
            dxfattribs={"layer": "BASE", "color": 8},
        )
        mate = msp.add_line(
            (2000.0, 150.0), (4000.0, 150.0),
            dxfattribs={"layer": "BASE", "color": 8},
        )

        n = lib_llm_correct.promote_collinear_wall_continuations(msp)

        self.assertGreaterEqual(n, 1)
        self.assertEqual(cont.dxf.layer, "WALL")
        self.assertEqual(mate.dxf.layer, "WALL")


if __name__ == "__main__":
    unittest.main()
