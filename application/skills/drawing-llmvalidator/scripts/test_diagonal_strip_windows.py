"""A diagonal strip of mullion caps and glass is a window. The flanks are walls."""

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


def _rect(msp, origin, angle, start, length, depth, layer="BASE"):
    return msp.add_lwpolyline(
        [
            _point(origin, angle, start, 0.0),
            _point(origin, angle, start + length, 0.0),
            _point(origin, angle, start + length, depth),
            _point(origin, angle, start, depth),
        ],
        close=True,
        dxfattribs={"layer": layer},
    )


class DiagonalStripWindowTests(unittest.TestCase):
    def test_diagonal_strip_is_window_and_flanks_are_walls(self):
        doc = ezdxf.new()
        msp = doc.modelspace()
        origin = (0.0, 0.0)
        angle = math.radians(135.0)
        depth = 100.0
        pieces = []
        cursor = 0.0
        for length in (40.0, 900.0, 40.0, 900.0, 40.0, 900.0, 40.0):
            pieces.append(_rect(msp, origin, angle, cursor, length, depth))
            cursor += length
        glass_line = msp.add_line(
            _point(origin, angle, 40.0, 0.0),
            _point(origin, angle, 940.0, 0.0),
            dxfattribs={"layer": "BASE"},
        )
        left = msp.add_lwpolyline(
            [
                _point(origin, angle, -800.0, 0.0),
                _point(origin, angle, 0.0, 0.0),
                _point(origin, angle, 0.0, depth),
                _point(origin, angle, -800.0, depth),
                _point(origin, angle, -1000.0, depth + 180.0),
            ],
            close=False,
            dxfattribs={"layer": "BASE"},
        )
        right = msp.add_lwpolyline(
            [
                _point(origin, angle, cursor, 0.0),
                _point(origin, angle, cursor + 1200.0, 0.0),
                _point(origin, angle, cursor + 1200.0, depth),
                _point(origin, angle, cursor, depth),
                _point(origin, angle, cursor + 1400.0, depth + 180.0),
            ],
            close=False,
            dxfattribs={"layer": "BASE"},
        )
        outsider = msp.add_line(
            _point(origin, angle, 0.0, 400.0),
            _point(origin, angle, 2860.0, 400.0),
            dxfattribs={"layer": "BASE"},
        )

        n_window, n_wall = lib_llm_correct.promote_diagonal_strip_windows(msp)

        self.assertEqual(n_window, len(pieces) + 1)
        self.assertEqual(n_wall, 2)
        self.assertTrue(all(piece.dxf.layer == "WINDOW" for piece in pieces))
        self.assertEqual(glass_line.dxf.layer, "WINDOW")
        self.assertEqual(left.dxf.layer, "WALL")
        self.assertEqual(right.dxf.layer, "WALL")
        self.assertEqual(outsider.dxf.layer, "BASE")


if __name__ == "__main__":
    unittest.main()
