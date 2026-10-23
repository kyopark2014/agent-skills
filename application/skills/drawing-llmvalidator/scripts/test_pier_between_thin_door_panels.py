"""나란한 두 얇은 DOOR 문짝 사이 BASE 피어는 WALL."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

import ezdxf

sys.path.insert(0, str(Path(__file__).resolve().parent))

import lib_llm_correct


def _rect(msp, x0, y0, x1, y1, *, layer="BASE"):
    entity = msp.add_lwpolyline(
        [(x0, y0), (x1, y0), (x1, y1), (x0, y1), (x0, y0)],
        close=False,
        dxfattribs={"layer": layer},
    )
    return entity


class PierBetweenThinDoorPanelsTests(unittest.TestCase):
    def test_base_pier_between_two_short_door_panels_is_wall(self):
        """바로유 sheet_01형: 벽 두께(~197) 문짝 사이 피어 → WALL."""
        doc = ezdxf.new()
        msp = doc.modelspace()
        y0, y1 = 61825.0, 62022.0
        left = _rect(msp, 45590.0, y0, 45647.0, y1, layer="DOOR")
        pier = _rect(msp, 46174.0, y0, 46231.0, y1, layer="BASE")
        right = _rect(msp, 46637.0, y0, 46701.0, y1, layer="DOOR")
        # 문 한쪽만 있는 칸은 올리지 않는다.
        lonely = _rect(msp, 43025.0, y0, 43082.0, y1, layer="BASE")
        # 멀리 떨어진 문 사이(>2.5 m)는 올리지 않는다.
        far = _rect(msp, 50000.0, y0, 50057.0, y1, layer="BASE")
        _rect(msp, 47000.0, y0, 47057.0, y1, layer="DOOR")
        _rect(msp, 53000.0, y0, 53057.0, y1, layer="DOOR")

        n = lib_llm_correct.promote_pier_between_thin_door_panels(msp)

        self.assertGreaterEqual(n, 1)
        self.assertEqual(pier.dxf.layer, "WALL")
        self.assertEqual(left.dxf.layer, "DOOR")
        self.assertEqual(right.dxf.layer, "DOOR")
        self.assertEqual(lonely.dxf.layer, "BASE")
        self.assertEqual(far.dxf.layer, "BASE")

    def test_horizontal_panels_also_work(self):
        doc = ezdxf.new()
        msp = doc.modelspace()
        x0, x1 = 1000.0, 1197.0
        _rect(msp, x0, 0.0, x1, 57.0, layer="DOOR")
        pier = _rect(msp, x0, 400.0, x1, 457.0, layer="BASE")
        _rect(msp, x0, 800.0, x1, 857.0, layer="DOOR")

        n = lib_llm_correct.promote_pier_between_thin_door_panels(msp)

        self.assertEqual(n, 1)
        self.assertEqual(pier.dxf.layer, "WALL")

    def test_wall_misclassified_leaf_still_counts_as_neighbor(self):
        """한쪽 문짝이 잠시 WALL이어도 사이 피어를 올린다."""
        doc = ezdxf.new()
        msp = doc.modelspace()
        y0, y1 = 0.0, 197.0
        _rect(msp, 0.0, y0, 57.0, y1, layer="DOOR")
        pier = _rect(msp, 400.0, y0, 457.0, y1, layer="BASE")
        _rect(msp, 800.0, y0, 857.0, y1, layer="WALL")

        n = lib_llm_correct.promote_pier_between_thin_door_panels(msp)

        self.assertEqual(n, 1)
        self.assertEqual(pier.dxf.layer, "WALL")

    def test_between_two_wall_sized_leaves_is_wall(self):
        """양옆이 문짝 크기 WALL이어도 사이 BASE 피어 → WALL."""
        doc = ezdxf.new()
        msp = doc.modelspace()
        y0, y1 = 61825.0, 62022.0
        _rect(msp, 50898.0, y0, 50955.0, y1, layer="WALL")
        pier = _rect(msp, 51888.0, y0, 51946.0, y1, layer="BASE")
        _rect(msp, 52473.0, y0, 52530.0, y1, layer="WALL")

        n = lib_llm_correct.promote_pier_between_thin_door_panels(msp)

        self.assertEqual(n, 1)
        self.assertEqual(pier.dxf.layer, "WALL")


if __name__ == "__main__":
    unittest.main()
