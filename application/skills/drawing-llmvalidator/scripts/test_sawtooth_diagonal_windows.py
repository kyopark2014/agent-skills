"""Sawtooth facade long diagonals are windows; perpendicular returns are walls."""

from __future__ import annotations

import math
import sys
import unittest
from pathlib import Path

import ezdxf

sys.path.insert(0, str(Path(__file__).resolve().parent))

import lib_llm_correct


def _line(msp, origin, angle, length, offset, layer="WALL"):
    ux, uy = math.cos(angle), math.sin(angle)
    nx, ny = -uy, ux
    x0 = origin[0] + nx * offset
    y0 = origin[1] + ny * offset
    return msp.add_line(
        (x0, y0),
        (x0 + ux * length, y0 + uy * length),
        dxfattribs={"layer": layer},
    )


def _return(msp, x, y, angle, length, layer="BASE"):
    ux, uy = math.cos(angle), math.sin(angle)
    return msp.add_line(
        (x, y),
        (x + ux * length, y + uy * length),
        dxfattribs={"layer": layer},
    )


class SawtoothDiagonalWindowTests(unittest.TestCase):
    def test_long_face_becomes_window_and_returns_become_walls(self):
        doc = ezdxf.new()
        msp = doc.modelspace()
        angle = math.radians(115.6)
        length = 2400.0
        origin = (0.0, 0.0)
        glass = [
            _line(msp, origin, angle, length, 0.0, "WALL"),
            _line(msp, origin, angle, length, 24.0, "WALL"),
        ]
        # deeper parallel stays wall (slanted-double mate), not glass
        deep = _line(msp, origin, angle, length, 241.0, "WALL")
        ret_ang = angle - math.pi / 2.0
        fold_ang = math.radians(67.5)
        ux, uy = math.cos(angle), math.sin(angle)
        returns = [
            _return(msp, 0.0, 0.0, ret_ang, 136.0),
            _return(msp, ux * length, uy * length, ret_ang, 136.0),
            _return(msp, 0.0, 0.0, ret_ang, 1250.0),
            _return(msp, ux * length, uy * length, ret_ang, 1250.0),
        ]
        # 양쪽 창 사이 삼각 접힘(직교가 아닌 67.5°)
        fold = _return(msp, ux * length, uy * length, fold_ang, 1274.0)
        outsider = _line(msp, (0.0, 5000.0), angle, length, 0.0, "WALL")
        _line(msp, (0.0, 5000.0), angle, length, 24.0, "WALL")

        n_window, n_wall = lib_llm_correct.separate_sawtooth_diagonal_windows(msp)

        self.assertEqual(n_window, 2)
        self.assertGreaterEqual(n_wall, 3)
        self.assertTrue(all(line.dxf.layer == "WINDOW" for line in glass))
        self.assertEqual(deep.dxf.layer, "WALL")
        self.assertTrue(any(line.dxf.layer == "WALL" for line in returns))
        self.assertEqual(fold.dxf.layer, "WALL")
        self.assertEqual(outsider.dxf.layer, "WALL")

    def test_axis_aligned_pair_is_not_a_sawtooth_window(self):
        doc = ezdxf.new()
        msp = doc.modelspace()
        a = msp.add_line((0.0, 0.0), (2400.0, 0.0), dxfattribs={"layer": "WALL"})
        b = msp.add_line((0.0, 24.0), (2400.0, 24.0), dxfattribs={"layer": "WALL"})
        msp.add_line((0.0, 0.0), (0.0, 200.0), dxfattribs={"layer": "BASE"})
        msp.add_line((2400.0, 0.0), (2400.0, 200.0), dxfattribs={"layer": "BASE"})

        n_window, n_wall = lib_llm_correct.separate_sawtooth_diagonal_windows(msp)

        self.assertEqual(n_window, 0)
        self.assertEqual(n_wall, 0)
        self.assertEqual(a.dxf.layer, "WALL")
        self.assertEqual(b.dxf.layer, "WALL")


if __name__ == "__main__":
    unittest.main()
