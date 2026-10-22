"""A slanted parallel pair at wall thickness is a wall."""

from __future__ import annotations

import math
import sys
import unittest
from pathlib import Path

import ezdxf

sys.path.insert(0, str(Path(__file__).resolve().parent))

import lib_llm_correct


def _line(msp, origin, angle, length, offset):
    ux, uy = math.cos(angle), math.sin(angle)
    nx, ny = -uy, ux
    x0, y0 = origin
    return msp.add_line(
        (x0 + nx * offset, y0 + ny * offset),
        (x0 + ux * length + nx * offset, y0 + uy * length + ny * offset),
        dxfattribs={"layer": "BASE"},
    )


class SlantedDoubleWallTests(unittest.TestCase):
    def test_near_vertical_double_becomes_wall(self):
        doc = ezdxf.new()
        msp = doc.modelspace()
        angle = math.radians(99.6)
        face = _line(msp, (0.0, 0.0), angle, 9000.0, 0.0)
        mate = _line(msp, (0.0, 0.0), angle, 4200.0, 200.0)
        thin = [
            _line(msp, (20000.0, 0.0), angle, 9000.0, 0.0),
            _line(msp, (20000.0, 0.0), angle, 9000.0, 24.0),
        ]

        promoted = lib_llm_correct.promote_slanted_double_walls(msp)

        self.assertEqual(promoted, 2)
        self.assertEqual(face.dxf.layer, "WALL")
        self.assertEqual(mate.dxf.layer, "WALL")
        self.assertTrue(all(line.dxf.layer == "BASE" for line in thin))

    def test_long_diagonal_beside_a_short_stub_stays_base(self):
        doc = ezdxf.new()
        msp = doc.modelspace()
        angle = math.radians(21.8)
        long = _line(msp, (0.0, 0.0), angle, 88797.0, 0.0)
        _line(msp, (70000.0, 0.0), angle, 2768.0, 188.0)

        lib_llm_correct.promote_slanted_double_walls(msp)

        self.assertEqual(long.dxf.layer, "BASE")

    def test_polyline_slanted_double_and_mullion_become_wall(self):
        """오송클리닉처럼 2점 폴리 대각 이중면과 사이 칸도 WALL."""
        doc = ezdxf.new()
        msp = doc.modelspace()
        angle = math.radians(-135.0)
        ux, uy = math.cos(angle), math.sin(angle)
        nx, ny = -uy, ux
        length = 8000.0
        gap = 148.0

        def _poly_face(offset):
            x0, y0 = nx * offset, ny * offset
            return msp.add_lwpolyline(
                [(x0, y0), (x0 + ux * length, y0 + uy * length)],
                dxfattribs={"layer": "BASE"},
            )

        face = _poly_face(0.0)
        mate = _poly_face(gap)
        # 면 사이·면에 평행한 얇은 멀리언(~18 mm × ~950 mm)
        a0 = 2000.0
        a1 = 2950.0
        mullion = msp.add_lwpolyline(
            [
                (ux * a0 + nx * 60.0, uy * a0 + ny * 60.0),
                (ux * a1 + nx * 60.0, uy * a1 + ny * 60.0),
                (ux * a1 + nx * 78.0, uy * a1 + ny * 78.0),
                (ux * a0 + nx * 78.0, uy * a0 + ny * 78.0),
            ],
            close=True,
            dxfattribs={"layer": "BASE"},
        )

        promoted = lib_llm_correct.promote_slanted_double_walls(msp)

        self.assertGreaterEqual(promoted, 3)
        self.assertEqual(face.dxf.layer, "WALL")
        self.assertEqual(mate.dxf.layer, "WALL")
        self.assertEqual(mullion.dxf.layer, "WALL")


if __name__ == "__main__":
    unittest.main()
