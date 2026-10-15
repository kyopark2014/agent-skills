"""Room faces are taken in steps: closed wall, 90% wall, then a sealed seed."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

import ezdxf
from shapely.geometry import LineString

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

    def test_open_label_cluster_grows_one_shared_face(self):
        """큰 홀에 라벨이 여러 개면 묶음 씨앗으로 한 면을 잡는다."""
        doc = ezdxf.new()
        msp = doc.modelspace()
        wall = {"layer": "WALL"}
        # 24 m x 16 m, 오른쪽 6 m 개구. 단일 씨앗 2.4 m로는 안 닫힌다.
        msp.add_line((0, 0), (24000, 0), dxfattribs=wall)
        msp.add_line((24000, 0), (24000, 5000), dxfattribs=wall)
        msp.add_line((24000, 11000), (24000, 16000), dxfattribs=wall)
        msp.add_line((24000, 16000), (0, 16000), dxfattribs=wall)
        msp.add_line((0, 16000), (0, 0), dxfattribs=wall)
        segs = room.wall_segments(msp, [])
        lines = [LineString([(a, b), (c, d)]) for a, b, c, d, *_ in segs]
        origins = [(6000.0, 5000.0), (12000.0, 11000.0), (18000.0, 5000.0)]

        self.assertEqual(len(room.cluster_open_origins(origins)), 1)
        self.assertIsNone(room._seed_face(lines, origins[0]))
        face = room._seed_cluster_face(lines, origins)

        self.assertIsNotNone(face)
        self.assertGreater(face.area / 1_000_000.0, 100.0)
        self.assertLess(face.area / 1_000_000.0, 500.0)

    def test_open_label_cluster_does_not_cross_a_wall(self):
        """묶음 면은 벽을 넘어 옆 실로 들어가지 않는다."""
        from shapely.geometry import box as shapely_box

        doc = ezdxf.new()
        msp = doc.modelspace()
        wall = {"layer": "WALL"}
        # 왼쪽 오픈 홀 + 가운데 벽 + 오른쪽 닫힌 실
        msp.add_line((0, 0), (20000, 0), dxfattribs=wall)
        msp.add_line((20000, 0), (20000, 12000), dxfattribs=wall)
        msp.add_line((20000, 12000), (0, 12000), dxfattribs=wall)
        msp.add_line((0, 12000), (0, 0), dxfattribs=wall)
        msp.add_line((12000, 0), (12000, 12000), dxfattribs=wall)
        # 왼쪽만 위쪽 6 m 개구
        msp.add_line((0, 12000), (3000, 12000), dxfattribs=wall)
        segs = room.wall_segments(msp, [])
        lines = [LineString([(a, b), (c, d)]) for a, b, c, d, *_ in segs]
        origins = [(4000.0, 4000.0), (6000.0, 6000.0), (8000.0, 4000.0)]
        blocked = [shapely_box(12000.0, 0.0, 20000.0, 12000.0)]

        face = room._seed_cluster_face(lines, origins, blocked_faces=blocked)

        self.assertIsNotNone(face)
        self.assertFalse(face.intersects(shapely_box(12500.0, 2000.0, 19000.0, 10000.0)))


if __name__ == "__main__":
    unittest.main()
