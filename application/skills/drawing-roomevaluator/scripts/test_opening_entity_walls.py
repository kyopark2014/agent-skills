"""A labeled room uses lines inside a long opening as walls."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

import ezdxf

sys.path.insert(0, str(Path(__file__).resolve().parent))

import evaluate_room as room


def _hall(msp, with_filler: bool):
    # 10 m by 12 m hall. The right side is open for 7 m between 2.5 m wall ends.
    msp.add_line((0, 0), (10000, 0), dxfattribs={"layer": "WALL"})
    msp.add_line((10000, 0), (10000, 2500), dxfattribs={"layer": "WALL"})
    msp.add_line((10000, 9500), (10000, 12000), dxfattribs={"layer": "WALL"})
    msp.add_line((10000, 12000), (0, 12000), dxfattribs={"layer": "WALL"})
    msp.add_line((0, 12000), (0, 0), dxfattribs={"layer": "WALL"})
    if with_filler:
        msp.add_line((10000, 3300), (10000, 8700), dxfattribs={"layer": "BASE"})


class OpeningEntityWallTests(unittest.TestCase):
    def test_line_in_the_opening_closes_the_room(self):
        doc = ezdxf.new()
        msp = doc.modelspace()
        _hall(msp, True)
        segs = room.wall_segments(msp, [])

        face, _x, _y, _swings = room._room_lines(msp, segs, (4000.0, 6000.0), [], [])

        self.assertIsNotNone(face)
        self.assertAlmostEqual(face.area / 1_000_000.0, 120.0, delta=2.0)

    def test_empty_opening_stays_open(self):
        doc = ezdxf.new()
        msp = doc.modelspace()
        _hall(msp, False)
        segs = room.wall_segments(msp, [])

        face, _x, _y, _swings = room._room_lines(msp, segs, (4000.0, 6000.0), [], [])

        self.assertIsNone(face)


def _classroom(msp, header: str):
    """9 m x 8 m. 왼쪽 벽은 아래에서 3 m에서 끊기고, 천장까지는 5 m다.

    천장까지 3.6 m보다 멀어서 빈 틈은 그대로 열린다.
    header가 base면 그 틈 1.2 m 앞에 인방 선이 있어 벽을 닫는다.
    """
    msp.add_line((0, 0), (9000, 0), dxfattribs={"layer": "WALL"})
    msp.add_line((9000, 0), (9000, 8000), dxfattribs={"layer": "WALL"})
    msp.add_line((9000, 8000), (0, 8000), dxfattribs={"layer": "WALL"})
    msp.add_line((0, 0), (0, 3000), dxfattribs={"layer": "WALL"})
    if header == "base":
        msp.add_line((0, 4200), (0, 8000), dxfattribs={"layer": "BASE"})
        msp.add_line((200, 4400), (200, 7800), dxfattribs={"layer": "BASE"})
    elif header == "inside":
        msp.add_line((3000, 4000), (5000, 4000), dxfattribs={"layer": "BASE"})


class GapEntityWallTests(unittest.TestCase):
    def test_entity_in_the_gap_closes_the_room(self):
        doc = ezdxf.new()
        msp = doc.modelspace()
        _classroom(msp, "base")
        segs = room.wall_segments(msp, [])

        face, _x, _y, _swings = room._room_lines(msp, segs, (4500.0, 3500.0), [], [])

        self.assertIsNotNone(face)
        self.assertAlmostEqual(face.area / 1_000_000.0, 72.0, delta=2.0)

    def test_empty_gap_stays_open(self):
        doc = ezdxf.new()
        msp = doc.modelspace()
        _classroom(msp, "none")
        segs = room.wall_segments(msp, [])

        face, _x, _y, _swings = room._room_lines(msp, segs, (4500.0, 3500.0), [], [])

        self.assertIsNone(face)

    def test_entity_away_from_the_wall_does_not_close(self):
        doc = ezdxf.new()
        msp = doc.modelspace()
        _classroom(msp, "inside")
        segs = room.wall_segments(msp, [])

        face, _x, _y, _swings = room._room_lines(msp, segs, (4500.0, 3500.0), [], [])

        self.assertIsNone(face)


if __name__ == "__main__":
    unittest.main()
