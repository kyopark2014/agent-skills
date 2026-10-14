"""A diagonal double line with repeating mullions is a wall. Axis pairs are not."""

from __future__ import annotations

import math
import sys
import unittest
from pathlib import Path

import ezdxf

sys.path.insert(0, str(Path(__file__).resolve().parent))

import lib_llm_correct


def _add_pair(msp, origin, angle, length, gap, ticks):
    ux, uy = math.cos(angle), math.sin(angle)
    nx, ny = -uy, ux
    x0, y0 = origin
    msp.add_line((x0, y0), (x0 + ux * length, y0 + uy * length), dxfattribs={"layer": "BASE"})
    msp.add_line(
        (x0 + nx * gap, y0 + ny * gap),
        (x0 + ux * length + nx * gap, y0 + uy * length + ny * gap),
        dxfattribs={"layer": "BASE"},
    )
    msp.add_line(
        (x0 + nx * 200.0, y0 + ny * 200.0),
        (x0 + ux * (length - 1000.0) + nx * 200.0, y0 + uy * (length - 1000.0) + ny * 200.0),
        dxfattribs={"layer": "BASE"},
    )
    for index in range(ticks):
        along = 400.0 + index * 800.0
        if along >= length:
            break
        sx = x0 + ux * along
        sy = y0 + uy * along
        msp.add_line((sx, sy), (sx + nx * 200.0, sy + ny * 200.0), dxfattribs={"layer": "BASE"})


class MullionDiagonalWallTests(unittest.TestCase):
    def test_repeating_mullions_promote_the_long_faces_only(self):
        doc = ezdxf.new()
        msp = doc.modelspace()
        _add_pair(msp, (0.0, 0.0), math.radians(23.0), 12000.0, 24.0, 14)
        _add_pair(msp, (0.0, 5000.0), 0.0, 12000.0, 24.0, 14)
        msp.add_line((0.0, 8000.0), (12000.0, 8000.0 + 5000.0), dxfattribs={"layer": "BASE"})
        msp.add_line((0.0, 8030.0), (12000.0, 8030.0 + 5000.0), dxfattribs={"layer": "BASE"})

        promoted = lib_llm_correct.promote_mullion_diagonal_walls(msp)

        layers = [entity.dxf.layer for entity in msp]
        self.assertEqual(promoted, 3)
        self.assertEqual(layers.count("WALL"), 3)
        self.assertGreater(layers.count("BASE"), 10)


if __name__ == "__main__":
    unittest.main()
