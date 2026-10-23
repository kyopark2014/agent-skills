"""사선 외벽에 끼인 얇은 닫힌 직사각은 WINDOW."""

from __future__ import annotations

import math
import sys
import unittest
from pathlib import Path

import ezdxf

sys.path.insert(0, str(Path(__file__).resolve().parent))

import lib_llm_correct


def _point(origin, angle, along, depth):
    ux, uy = math.cos(angle), math.sin(angle)
    nx, ny = -uy, ux
    return (origin[0] + ux * along + nx * depth, origin[1] + uy * along + ny * depth)


def _rect(msp, origin, angle, length, depth, layer="BASE", close=True):
    pts = [
        _point(origin, angle, 0.0, 0.0),
        _point(origin, angle, length, 0.0),
        _point(origin, angle, length, depth),
        _point(origin, angle, 0.0, depth),
    ]
    if not close:
        pts.append(pts[0])
    return msp.add_lwpolyline(
        pts,
        close=close,
        dxfattribs={"layer": layer},
    )


class DiagonalThinRectWindowTests(unittest.TestCase):
    def test_diagonal_wall_pocket_rect_is_window(self):
        """바로유 sheet_01형: 깊이 ~60 mm × 긴 변 ~200 mm 사선 직사각."""
        doc = ezdxf.new()
        msp = doc.modelspace()
        angle = math.radians(30.0)
        pane = _rect(msp, (40000.0, 64000.0), angle, 196.0, 60.0)
        # 축정렬 얇은 칸은 올리지 않는다.
        axis = msp.add_lwpolyline(
            [(0.0, 0.0), (200.0, 0.0), (200.0, 60.0), (0.0, 60.0)],
            close=True,
            dxfattribs={"layer": "BASE"},
        )
        # 문짝 길이(≥600)는 올리지 않는다.
        doorish = _rect(msp, (42000.0, 64000.0), angle, 900.0, 60.0)
        # 깊이 과다(기둥·벽 띠)는 올리지 않는다.
        thick = _rect(msp, (44000.0, 64000.0), angle, 200.0, 180.0)

        n = lib_llm_correct.promote_diagonal_thin_rect_windows(msp)

        self.assertEqual(n, 1)
        self.assertEqual(pane.dxf.layer, "WINDOW")
        self.assertEqual(axis.dxf.layer, "BASE")
        self.assertEqual(doorish.dxf.layer, "BASE")
        self.assertEqual(thick.dxf.layer, "BASE")

    def test_open_polyline_that_returns_to_start_is_window(self):
        """PDF→DXF처럼 closed=False 이지만 끝점이 시작과 같은 4변도 창."""
        doc = ezdxf.new()
        msp = doc.modelspace()
        angle = math.radians(120.0)
        pane = _rect(
            msp, (41000.0, 64000.0), angle, 199.0, 65.0, close=False
        )

        n = lib_llm_correct.promote_diagonal_thin_rect_windows(msp)

        self.assertEqual(n, 1)
        self.assertEqual(pane.dxf.layer, "WINDOW")


if __name__ == "__main__":
    unittest.main()
