"""bbox_mm 가 DXF 기하와 겹치는지 판정."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import lib_llm_correct


class BboxOverlapsXyTest(unittest.TestCase):
    def test_overlap(self):
        bbox = {"xmin": 0.0, "ymin": 0.0, "xmax": 100.0, "ymax": 100.0}
        self.assertTrue(lib_llm_correct.bbox_overlaps_xy(bbox, [10.0, 20.0], [10.0, 20.0]))

    def test_miss_y(self):
        bbox = {"xmin": 0.0, "ymin": 200.0, "xmax": 100.0, "ymax": 300.0}
        self.assertFalse(lib_llm_correct.bbox_overlaps_xy(bbox, [10.0, 20.0], [-50.0, -10.0]))

    def test_empty_points_keep_bbox(self):
        bbox = {"xmin": 0.0, "ymin": 0.0, "xmax": 1.0, "ymax": 1.0}
        self.assertTrue(lib_llm_correct.bbox_overlaps_xy(bbox, [], []))


if __name__ == "__main__":
    unittest.main()
