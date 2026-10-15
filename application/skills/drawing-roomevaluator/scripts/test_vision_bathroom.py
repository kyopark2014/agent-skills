"""A label inside a vision bathroom box is not an area, whatever its text."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import evaluate_room as room


class VisionBathroomTests(unittest.TestCase):
    def test_lt07_inside_a_bathroom_box_is_excluded(self):
        boxes = room.vision_bathroom_boxes(_review(self, [{"label": "화장실", "xmin": 0, "ymin": 0, "xmax": 3000, "ymax": 2000}]))
        self.assertTrue(room.in_vision_bathroom(1500, 1000, boxes))
        self.assertFalse(room.in_vision_bathroom(4000, 1000, boxes))

    def test_other_promote_labels_stay(self):
        boxes = room.vision_bathroom_boxes(
            _review(self, [{"label": "외벽", "xmin": 0, "ymin": 0, "xmax": 3000, "ymax": 2000}])
        )
        self.assertEqual(boxes, [])
        self.assertFalse(room.is_excluded_area_label("LT-07"))


def _review(case: unittest.TestCase, promote: list[dict]) -> Path:
    folder = Path(tempfile.mkdtemp())
    case.addCleanup(lambda: None)
    review = folder / "llm_review"
    review.mkdir()
    (review / "review.json").write_text(
        json.dumps({"promote_bboxes": promote}),
        encoding="utf-8",
    )
    return folder / "floor_wall_validated.dxf"


if __name__ == "__main__":
    unittest.main()
