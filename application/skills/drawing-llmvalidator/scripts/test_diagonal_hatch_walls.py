"""Diagonal hatch (poche) filling a thin band is a wall."""

from __future__ import annotations

import math
import sys
import unittest
from pathlib import Path

import ezdxf

sys.path.insert(0, str(Path(__file__).resolve().parent))

import lib_llm_correct


def _stroke(msp, x0, y0, length=90.0, angle_deg=45.0, layer="BASE"):
    ang = math.radians(angle_deg)
    x1 = x0 + length * math.cos(ang)
    y1 = y0 + length * math.sin(ang)
    return msp.add_lwpolyline(
        [(x0, y0), (x1, y1)],
        dxfattribs={"layer": layer},
    )


class DiagonalHatchWallTests(unittest.TestCase):
    def test_closed_shell_with_diagonal_strokes_becomes_wall(self):
        doc = ezdxf.new()
        msp = doc.modelspace()
        shell = msp.add_lwpolyline(
            [(0, 0), (100, 0), (100, 2000), (0, 2000)],
            close=True,
            dxfattribs={"layer": "BASE"},
        )
        strokes = [
            _stroke(msp, 10, y, length=80.0, angle_deg=45.0)
            for y in range(50, 1950, 80)
        ]
        empty = msp.add_lwpolyline(
            [(400, 0), (500, 0), (500, 2000), (400, 2000)],
            close=True,
            dxfattribs={"layer": "BASE"},
        )

        promoted = lib_llm_correct.promote_diagonal_hatch_walls(msp)

        self.assertGreaterEqual(promoted, 1 + len(strokes))
        self.assertEqual(shell.dxf.layer, "WALL")
        self.assertTrue(all(stroke.dxf.layer == "WALL" for stroke in strokes))
        self.assertEqual(empty.dxf.layer, "BASE")

    def test_zigzag_band_becomes_wall(self):
        doc = ezdxf.new()
        msp = doc.modelspace()
        pts = []
        x0, x1 = 0.0, 60.0
        y = 0.0
        while y < 700.0:
            pts.append((x0, y))
            pts.append((x1, y + 40.0))
            y += 80.0
        zigzag = msp.add_lwpolyline(pts, dxfattribs={"layer": "BASE"})

        promoted = lib_llm_correct.promote_diagonal_hatch_walls(msp)

        self.assertGreaterEqual(promoted, 1)
        self.assertEqual(zigzag.dxf.layer, "WALL")


if __name__ == "__main__":
    unittest.main()
