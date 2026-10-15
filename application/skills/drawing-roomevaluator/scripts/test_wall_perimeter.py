"""A face whose boundary is under 90% wall is not an area."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import evaluate_room as room


class WallPerimeterTests(unittest.TestCase):
    def test_closed_square_stays(self):
        segs = [
            (0.0, 0.0, 10000.0, 0.0),
            (10000.0, 0.0, 10000.0, 10000.0),
            (10000.0, 10000.0, 0.0, 10000.0),
            (0.0, 10000.0, 0.0, 0.0),
        ]
        pts = [(0.0, 0.0), (10000.0, 0.0), (10000.0, 10000.0), (0.0, 10000.0)]
        self.assertGreaterEqual(room.wall_perimeter_ratio(segs, pts), room.WALL_PERIMETER_MIN)

    def test_one_open_side_is_excluded(self):
        segs = [
            (0.0, 0.0, 10000.0, 0.0),
            (10000.0, 0.0, 10000.0, 10000.0),
            (10000.0, 10000.0, 0.0, 10000.0),
        ]
        pts = [(0.0, 0.0), (10000.0, 0.0), (10000.0, 10000.0), (0.0, 10000.0)]
        self.assertLess(room.wall_perimeter_ratio(segs, pts), room.WALL_PERIMETER_MIN)


if __name__ == "__main__":
    unittest.main()
