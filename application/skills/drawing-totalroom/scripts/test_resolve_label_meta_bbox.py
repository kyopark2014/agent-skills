"""meta bbox 가 DXF 와 어긋나면 기하 기준으로 고친다."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

import ezdxf

sys.path.insert(0, str(Path(__file__).resolve().parent))

import detect_labels


class ResolveLabelMetaBboxTest(unittest.TestCase):
    def test_keeps_overlapping_bbox(self):
        doc = ezdxf.new()
        msp = doc.modelspace()
        msp.add_line((0, 0), (1000, 0))
        msp.add_line((0, 0), (0, 1000))
        meta = {
            "bbox_mm": {"xmin": -100.0, "ymin": -100.0, "xmax": 2000.0, "ymax": 2000.0},
            "size_m": {"width": 2.1, "height": 2.1},
        }
        out = detect_labels.resolve_label_meta_bbox(meta, msp)
        self.assertEqual(out["bbox_mm"], meta["bbox_mm"])

    def test_replaces_missing_bbox(self):
        doc = ezdxf.new()
        msp = doc.modelspace()
        msp.add_line((0, -100000), (1000, -90000))
        meta = {
            "bbox_mm": {"xmin": 0.0, "ymin": 200000.0, "xmax": 1000.0, "ymax": 300000.0},
            "size_m": {"width": 1.0, "height": 100.0},
        }
        out = detect_labels.resolve_label_meta_bbox(meta, msp)
        self.assertNotEqual(out["bbox_mm"], meta["bbox_mm"])
        self.assertLess(out["bbox_mm"]["ymax"], 0.0)
        self.assertGreater(out["bbox_mm"]["ymin"], -200000.0)


if __name__ == "__main__":
    unittest.main()
