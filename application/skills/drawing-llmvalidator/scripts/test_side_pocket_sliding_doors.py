"""개구 옆 포켓 사각형이 있는 미닫이 문짝은 DOOR이다."""

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
    separate_side_pocket_sliding_doors,
)


class SidePocketSlidingDoorTests(unittest.TestCase):
    def test_inwall_leaf_with_side_pocket_becomes_door(self):
        """김해보음 2F 심장검사실처럼 이중벽 개구 문짝 + 옆 포켓 → DOOR."""
        doc = ezdxf.new()
        msp = doc.modelspace()
        # 이중벽 양면 (두께 200 mm), 개구는 x=0–800
        msp.add_line((800, 0), (4800, 0), dxfattribs={"layer": WALL_LAYER, "color": WALL_COLOR})
        msp.add_line((800, 200), (4800, 200), dxfattribs={"layer": WALL_LAYER, "color": WALL_COLOR})
        # 개구 안 문짝
        leaf = msp.add_line((0, 100), (800, 100), dxfattribs={"layer": WALL_LAYER, "color": WALL_COLOR})
        # 옆 포켓 (폭 800, 깊이 600) — 바깥 면 위로
        msp.add_line((1040, 200), (1040, 800), dxfattribs={"layer": WALL_LAYER, "color": WALL_COLOR})
        msp.add_line((1840, 200), (1840, 800), dxfattribs={"layer": BASE_LAYER})
        msp.add_line((1040, 800), (1840, 800), dxfattribs={"layer": BASE_LAYER})

        demoted, promoted = separate_side_pocket_sliding_doors(msp)

        self.assertGreaterEqual(demoted, 1)
        self.assertEqual(leaf.dxf.layer, DOOR_LAYER)
        self.assertGreaterEqual(promoted, 1)

    def test_no_pocket_keeps_leaf_as_wall(self):
        doc = ezdxf.new()
        msp = doc.modelspace()
        msp.add_line((800, 0), (4800, 0), dxfattribs={"layer": WALL_LAYER, "color": WALL_COLOR})
        msp.add_line((800, 200), (4800, 200), dxfattribs={"layer": WALL_LAYER, "color": WALL_COLOR})
        leaf = msp.add_line((0, 100), (800, 100), dxfattribs={"layer": WALL_LAYER, "color": WALL_COLOR})

        demoted, promoted = separate_side_pocket_sliding_doors(msp)

        self.assertEqual(demoted, 0)
        self.assertEqual(promoted, 0)
        self.assertEqual(leaf.dxf.layer, WALL_LAYER)

    def test_vertical_orientation(self):
        doc = ezdxf.new()
        msp = doc.modelspace()
        msp.add_line((0, 800), (0, 4800), dxfattribs={"layer": WALL_LAYER, "color": WALL_COLOR})
        msp.add_line((200, 800), (200, 4800), dxfattribs={"layer": WALL_LAYER, "color": WALL_COLOR})
        leaf = msp.add_line((100, 0), (100, 800), dxfattribs={"layer": BASE_LAYER})
        msp.add_line((200, 1040), (800, 1040), dxfattribs={"layer": WALL_LAYER, "color": WALL_COLOR})
        msp.add_line((200, 1840), (800, 1840), dxfattribs={"layer": BASE_LAYER})
        msp.add_line((800, 1040), (800, 1840), dxfattribs={"layer": BASE_LAYER})

        demoted, _promoted = separate_side_pocket_sliding_doors(msp)

        self.assertGreaterEqual(demoted, 1)
        self.assertEqual(leaf.dxf.layer, DOOR_LAYER)


if __name__ == "__main__":
    unittest.main()
