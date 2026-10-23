"""벽 두께 안 LINE 이중 미닫이(양끝 턱)는 DOOR이다."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

import ezdxf

sys.path.insert(0, str(Path(__file__).resolve().parent))

from correct._rules import (
    WALL_LAYER,
    WALL_COLOR,
    BASE_LAYER,
    DOOR_LAYER,
    separate_inwall_line_sliding_doors,
)


class InwallLineSlidingDoorTests(unittest.TestCase):
    def test_capped_1420_leaf_becomes_door(self):
        """김해보음 3F 4인실#3처럼 ~1.42 m 이중선+양끝 턱 → DOOR."""
        doc = ezdxf.new()
        msp = doc.modelspace()
        # 이중벽 양면 (두께 150 mm)
        msp.add_line((0, -2000), (0, 4000), dxfattribs={"layer": WALL_LAYER, "color": WALL_COLOR})
        msp.add_line((150, -2000), (150, 4000), dxfattribs={"layer": WALL_LAYER, "color": WALL_COLOR})
        # 개구 안 문짝 (gap 40, span 1420)
        leaf_a = msp.add_line((55, 0), (55, 1420), dxfattribs={"layer": BASE_LAYER})
        leaf_b = msp.add_line((95, 0), (95, 1420), dxfattribs={"layer": BASE_LAYER})
        # 양끝 턱
        msp.add_line((55, 0), (95, 0), dxfattribs={"layer": BASE_LAYER})
        msp.add_line((55, 1420), (95, 1420), dxfattribs={"layer": BASE_LAYER})

        demoted, _promoted = separate_inwall_line_sliding_doors(msp)

        self.assertGreaterEqual(demoted, 2)
        self.assertEqual(leaf_a.dxf.layer, DOOR_LAYER)
        self.assertEqual(leaf_b.dxf.layer, DOOR_LAYER)

    def test_wall_mispromoted_leaf_becomes_door(self):
        doc = ezdxf.new()
        msp = doc.modelspace()
        msp.add_line((0, -2000), (0, 4000), dxfattribs={"layer": WALL_LAYER, "color": WALL_COLOR})
        msp.add_line((150, -2000), (150, 4000), dxfattribs={"layer": WALL_LAYER, "color": WALL_COLOR})
        leaf_a = msp.add_line((55, 0), (55, 1420), dxfattribs={"layer": WALL_LAYER, "color": WALL_COLOR})
        leaf_b = msp.add_line((95, 0), (95, 1420), dxfattribs={"layer": WALL_LAYER, "color": WALL_COLOR})
        msp.add_line((55, 0), (95, 0), dxfattribs={"layer": WALL_LAYER, "color": WALL_COLOR})
        msp.add_line((55, 1420), (95, 1420), dxfattribs={"layer": WALL_LAYER, "color": WALL_COLOR})

        demoted, _promoted = separate_inwall_line_sliding_doors(msp)

        self.assertGreaterEqual(demoted, 2)
        self.assertEqual(leaf_a.dxf.layer, DOOR_LAYER)
        self.assertEqual(leaf_b.dxf.layer, DOOR_LAYER)

    def test_uncapped_leaf_stays(self):
        doc = ezdxf.new()
        msp = doc.modelspace()
        msp.add_line((0, -2000), (0, 4000), dxfattribs={"layer": WALL_LAYER, "color": WALL_COLOR})
        msp.add_line((150, -2000), (150, 4000), dxfattribs={"layer": WALL_LAYER, "color": WALL_COLOR})
        leaf = msp.add_line((75, 0), (75, 1000), dxfattribs={"layer": WALL_LAYER, "color": WALL_COLOR})
        # 평행 짝 없이 단선만 — 검출 안 됨
        demoted, promoted = separate_inwall_line_sliding_doors(msp)
        self.assertEqual(demoted, 0)
        self.assertEqual(promoted, 0)
        self.assertEqual(leaf.dxf.layer, WALL_LAYER)

    def test_lwpolyline_capped_leaf_and_side_walls(self):
        """PDF→DXF 2점 LWPOLY 문짝 + 양끝 턱 → DOOR, 양옆 BASE 면 → WALL."""
        doc = ezdxf.new()
        msp = doc.modelspace()
        face_a = msp.add_lwpolyline(
            [(0, -2000), (0, 4000)], dxfattribs={"layer": BASE_LAYER}
        )
        face_b = msp.add_lwpolyline(
            [(150, -2000), (150, 4000)], dxfattribs={"layer": BASE_LAYER}
        )
        leaf_a = msp.add_lwpolyline(
            [(55, 0), (55, 900)], dxfattribs={"layer": BASE_LAYER}
        )
        leaf_b = msp.add_lwpolyline(
            [(95, 0), (95, 900)], dxfattribs={"layer": BASE_LAYER}
        )
        msp.add_lwpolyline([(55, 0), (95, 0)], dxfattribs={"layer": BASE_LAYER})
        msp.add_lwpolyline([(55, 900), (95, 900)], dxfattribs={"layer": BASE_LAYER})

        demoted, promoted = separate_inwall_line_sliding_doors(msp)

        self.assertGreaterEqual(demoted, 2)
        self.assertEqual(leaf_a.dxf.layer, DOOR_LAYER)
        self.assertEqual(leaf_b.dxf.layer, DOOR_LAYER)
        self.assertGreaterEqual(promoted, 1)
        self.assertEqual(face_a.dxf.layer, WALL_LAYER)
        self.assertEqual(face_b.dxf.layer, WALL_LAYER)


if __name__ == "__main__":
    unittest.main()
