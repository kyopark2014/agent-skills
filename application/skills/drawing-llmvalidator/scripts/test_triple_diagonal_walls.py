"""Three stacked diagonal lines are a wall. A pair is not."""

from __future__ import annotations

import math
import sys
import unittest
from pathlib import Path

import ezdxf

sys.path.insert(0, str(Path(__file__).resolve().parent))

import lib_llm_correct


def _line(msp, origin, angle, length, offset, layer="BASE"):
    ux, uy = math.cos(angle), math.sin(angle)
    nx, ny = -uy, ux
    x0, y0 = origin
    return msp.add_line(
        (x0 + nx * offset, y0 + ny * offset),
        (x0 + ux * length + nx * offset, y0 + uy * length + ny * offset),
        dxfattribs={"layer": layer},
    )


class TripleDiagonalWallTests(unittest.TestCase):
    def test_three_parallel_diagonals_become_walls(self):
        doc = ezdxf.new()
        msp = doc.modelspace()
        angle = math.radians(16.0)
        faces = [_line(msp, (0.0, 0.0), angle, 8000.0, offset) for offset in (0.0, 80.0, 160.0)]
        outsider = _line(msp, (0.0, 4000.0), angle, 8000.0, 0.0)
        pair = [
            _line(msp, (0.0, 8000.0), angle, 8000.0, 0.0),
            _line(msp, (0.0, 8000.0), angle, 8000.0, 80.0),
        ]

        promoted = lib_llm_correct.promote_triple_diagonal_walls(msp)

        self.assertEqual(promoted, 3)
        self.assertTrue(all(line.dxf.layer == "WALL" for line in faces))
        self.assertEqual(outsider.dxf.layer, "BASE")
        self.assertTrue(all(line.dxf.layer == "BASE" for line in pair))


if __name__ == "__main__":
    unittest.main()
