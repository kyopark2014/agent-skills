"""Vision promote X 가 문 개구·문짝 크기면 normalize 에서 버린다."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import lib_llm_correct


class VisionDoorXBboxTests(unittest.TestCase):
    def test_door_pocket_x_bbox_is_skipped(self):
        boxes = lib_llm_correct.normalize_bbox_list(
            [
                {
                    "label": "X",
                    "xmin": 0.0,
                    "ymin": 0.0,
                    "xmax": 200.0,
                    "ymax": 1000.0,
                }
            ],
            field="promote_bboxes",
        )
        self.assertEqual(boxes, [])

    def test_square_door_x_bbox_is_skipped(self):
        boxes = lib_llm_correct.normalize_bbox_list(
            [
                {
                    "label": "X",
                    "xmin": 0.0,
                    "ymin": 0.0,
                    "xmax": 500.0,
                    "ymax": 500.0,
                }
            ],
            field="promote_bboxes",
        )
        self.assertEqual(boxes, [])

    def test_shaft_x_bbox_is_kept(self):
        boxes = lib_llm_correct.normalize_bbox_list(
            [
                {
                    "label": "X",
                    "xmin": 0.0,
                    "ymin": 0.0,
                    "xmax": 1300.0,
                    "ymax": 900.0,
                }
            ],
            field="promote_bboxes",
        )
        self.assertEqual(len(boxes), 1)
        self.assertEqual(boxes[0]["label"], "X")


if __name__ == "__main__":
    unittest.main()
