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


class OpenRoomMaxAreaTests(unittest.TestCase):
    def test_closed_room_keeps_its_wall_face(self):
        doc = ezdxf.new()
        msp = doc.modelspace()
        wall = {"layer": "WALL"}
        msp.add_line((0, 0), (8000, 0), dxfattribs=wall)
        msp.add_line((8000, 0), (8000, 6000), dxfattribs=wall)
        msp.add_line((8000, 6000), (0, 6000), dxfattribs=wall)
        msp.add_line((0, 6000), (0, 0), dxfattribs=wall)
        base = {"layer": "BASE"}
        msp.add_line((2000, 2500), (3200, 2500), dxfattribs=base)
        msp.add_line((2000, 2650), (3200, 2650), dxfattribs=base)
        segs = room.wall_segments(msp, [])

        face, _x, _y, _swings = room._room_lines(msp, segs, (4000.0, 4000.0), [], [])

        self.assertIsNotNone(face)
        self.assertAlmostEqual(face.area / 1_000_000.0, 48.0, delta=1.0)

    def test_open_room_uses_the_larger_entity_face(self):
        doc = ezdxf.new()
        msp = doc.modelspace()
        wall = {"layer": "WALL"}
        msp.add_line((0, 0), (0, 3000), dxfattribs=wall)
        msp.add_line((0, 3000), (3500, 3000), dxfattribs=wall)
        msp.add_line((3500, 3000), (3500, 0), dxfattribs=wall)
        base = {"layer": "BASE"}
        msp.add_line((0, 0), (3500, 0), dxfattribs=base)
        msp.add_line((0, 120), (3500, 120), dxfattribs=base)
        msp.add_line((400, 0), (400, 1100), dxfattribs=base)
        msp.add_line((550, 0), (550, 1100), dxfattribs=base)
        msp.add_line((2950, 0), (2950, 1100), dxfattribs=base)
        msp.add_line((3100, 0), (3100, 1100), dxfattribs=base)
        segs = room.wall_segments(msp, [])

        face, _x, _y, _swings = room._room_lines(msp, segs, (1750.0, 2000.0), [], [])

        self.assertIsNotNone(face)
        self.assertAlmostEqual(face.area / 1_000_000.0, 10.5, delta=1.0)


class DoorLeafTests(unittest.TestCase):
    def test_leaf_rectangle_does_not_cut_a_closed_room(self):
        doc = ezdxf.new()
        msp = doc.modelspace()
        wall = {"layer": "WALL"}
        msp.add_line((1000, 0), (3000, 0), dxfattribs=wall)
        msp.add_line((3000, 0), (3000, 3000), dxfattribs=wall)
        msp.add_line((3000, 3000), (1000, 3000), dxfattribs=wall)
        msp.add_line((1000, 3000), (1000, 0), dxfattribs=wall)
        msp.add_lwpolyline(
            [(1100, 3000), (1100, 2200), (1120, 2200), (1120, 3000)],
            close=True,
            dxfattribs={"layer": "DOOR"},
        )
        msp.add_lwpolyline(
            [(3050, 3000), (3050, 2200), (3070, 2200), (3070, 3000)],
            close=True,
            dxfattribs={"layer": "DOOR"},
        )
        msp.add_line((1400, 3000), (2200, 3000), dxfattribs={"layer": "DOOR"})
        segs = room.wall_segments(msp, [])

        face, _x, _y, _swings = room._room_lines(msp, segs, (2000.0, 2600.0), [], [])

        self.assertIsNotNone(face)
        self.assertAlmostEqual(face.area / 1_000_000.0, 6.0, delta=0.2)


class CornerEntityTests(unittest.TestCase):
    def test_corner_square_closes_an_open_room(self):
        doc = ezdxf.new()
        msp = doc.modelspace()
        wall = {"layer": "WALL"}
        msp.add_line((0, 200), (0, 3000), dxfattribs=wall)
        msp.add_line((0, 3000), (4000, 3000), dxfattribs=wall)
        msp.add_line((4000, 3000), (4000, 0), dxfattribs=wall)
        msp.add_line((4000, 0), (800, 0), dxfattribs=wall)
        msp.add_lwpolyline(
            [(0, -400), (800, -400), (800, 200), (0, 200)],
            close=True,
            dxfattribs={"layer": "BASE"},
        )
        segs = room.wall_segments(msp, [])

        face, _x, _y, _swings = room._room_lines(msp, segs, (2000.0, 1500.0), [], [])

        self.assertIsNotNone(face)
        self.assertAlmostEqual(face.area / 1_000_000.0, 11.84, delta=0.3)


if __name__ == "__main__":
    unittest.main()
