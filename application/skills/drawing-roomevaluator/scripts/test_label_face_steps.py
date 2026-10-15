"""Room faces are taken in steps: closed wall, 90% wall, then a sealed seed."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

import ezdxf

sys.path.insert(0, str(Path(__file__).resolve().parent))

import evaluate_room as room


def _box(msp, gap: float):
    """10 m by 8 m. The left side keeps a gap of about `gap` millimeters."""
    wall = {"layer": "WALL"}
    height = 8000.0
    stub = max(200.0, (height - gap) / 2.0)
    msp.add_line((0, 0), (10000, 0), dxfattribs=wall)
    msp.add_line((10000, 0), (10000, height), dxfattribs=wall)
    msp.add_line((10000, height), (0, height), dxfattribs=wall)
    msp.add_line((0, 0), (0, stub), dxfattribs=wall)
    msp.add_line((0, height - stub), (0, height), dxfattribs=wall)


class LabelFaceStepTests(unittest.TestCase):
    def test_closed_room_is_the_first_step(self):
        doc = ezdxf.new()
        msp = doc.modelspace()
        wall = {"layer": "WALL"}
        msp.add_line((0, 0), (8000, 0), dxfattribs=wall)
        msp.add_line((8000, 0), (8000, 6000), dxfattribs=wall)
        msp.add_line((8000, 6000), (0, 6000), dxfattribs=wall)
        msp.add_line((0, 6000), (0, 0), dxfattribs=wall)
        segs = room.wall_segments(msp, [])

        face, _x, _y, _swings = room._room_lines(msp, segs, (4000.0, 3000.0), [], [])

        self.assertIsNotNone(face)
        self.assertAlmostEqual(face.area / 1_000_000.0, 48.0, delta=1.0)

    def test_gap_under_the_seed_limit_becomes_the_room(self):
        doc = ezdxf.new()
        msp = doc.modelspace()
        _box(msp, 4000.0)
        msp.add_line((3000, 4000), (5000, 4000), dxfattribs={"layer": "BASE"})
        segs = room.wall_segments(msp, [])

        face, _x, _y, _swings = room._room_lines(msp, segs, (5000.0, 2000.0), [], [])

        self.assertIsNotNone(face)
        self.assertAlmostEqual(face.area / 1_000_000.0, 80.0, delta=3.0)

    def test_long_open_side_is_not_a_room(self):
        doc = ezdxf.new()
        msp = doc.modelspace()
        _box(msp, 9000.0)
        segs = room.wall_segments(msp, [])

        face, _x, _y, _swings = room._room_lines(msp, segs, (5000.0, 4000.0), [], [])

        self.assertIsNone(face)


if __name__ == "__main__":
    unittest.main()
