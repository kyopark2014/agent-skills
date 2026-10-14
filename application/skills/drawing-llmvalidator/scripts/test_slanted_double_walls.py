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


if __name__ == "__main__":
    unittest.main()
