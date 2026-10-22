"""포켓 이중벽 안의 얇은 닫힌 문짝은 DOOR이다."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

import ezdxf

sys.path.insert(0, str(Path(__file__).resolve().parent))

from correct._rules import (
    WALL_LAYER,
    WALL_COLOR,
    DOOR_LAYER,
    DOOR_COLOR,
    _mark_sliding_doors_and_windows,
    demote_thin_swing_leaf_lines,
    promote_leaf_side_walls,
)


def _rect(msp, x0, y0, x1, y1, *, layer="BASE"):
    entity = msp.add_lwpolyline(
        [(x0, y0), (x1, y0), (x1, y1), (x0, y1)],
        dxfattribs={"layer": layer},
    )
    entity.closed = True
    return entity


class PocketThinSlidingDoorTests(unittest.TestCase):
    def test_thin_leaf_in_wall_pocket_becomes_door(self):
        """오송클리닉처럼 포켓(~146 mm) 안 ~19 mm×1 m 문짝도 DOOR."""
        doc = ezdxf.new()
        msp = doc.modelspace()
        # 포켓 양면
        msp.add_line((0, 0), (0, 6000), dxfattribs={"layer": WALL_LAYER, "color": WALL_COLOR})
        msp.add_line((146, 0), (146, 6000), dxfattribs={"layer": WALL_LAYER, "color": WALL_COLOR})
        leaf = _rect(msp, 20, 100, 39, 1091)

        doors, windows = _mark_sliding_doors_and_windows(msp)

        self.assertEqual(leaf.dxf.layer, "DOOR")
        self.assertEqual(len(doors), 1)
        self.assertEqual(doors[0]["kind"], "sliding")
        self.assertEqual(windows, [])

    def test_stacked_pocket_leaves_all_become_door(self):
        doc = ezdxf.new()
        msp = doc.modelspace()
        msp.add_line((0, 0), (0, 7000), dxfattribs={"layer": WALL_LAYER, "color": WALL_COLOR})
        msp.add_line((146, 0), (146, 7000), dxfattribs={"layer": WALL_LAYER, "color": WALL_COLOR})
        leaves = [
            _rect(msp, 20, 100, 39, 1091),
            _rect(msp, 20, 1120, 39, 2143),
            _rect(msp, 20, 2170, 39, 3187),
        ]

        doors, _windows = _mark_sliding_doors_and_windows(msp)

        self.assertEqual(len(doors), 3)
        for leaf in leaves:
            self.assertEqual(leaf.dxf.layer, "DOOR")

    def test_panel_drawn_on_wall_face_stays_base(self):
        """벽 선 위에 그대로 겹친 칸은 문이 아니다."""
        doc = ezdxf.new()
        msp = doc.modelspace()
        msp.add_line((0, 0), (0, 3000), dxfattribs={"layer": WALL_LAYER, "color": WALL_COLOR})
        msp.add_line((200, 0), (200, 3000), dxfattribs={"layer": WALL_LAYER, "color": WALL_COLOR})
        on_face = _rect(msp, -10, 100, 10, 1100)

        doors, _windows = _mark_sliding_doors_and_windows(msp)

        self.assertEqual(on_face.dxf.layer, "BASE")
        self.assertEqual(doors, [])

    def test_existing_door_leaf_not_demoted_to_base(self):
        """이미 DOOR인 포켓 문짝은 여닫이 잎 demote에 안 내려간다."""
        doc = ezdxf.new()
        msp = doc.modelspace()
        msp.add_line((0, 0), (0, 6000), dxfattribs={"layer": WALL_LAYER, "color": WALL_COLOR})
        msp.add_line((95, 0), (95, 6000), dxfattribs={"layer": WALL_LAYER, "color": WALL_COLOR})
        leaf = _rect(msp, 20, 100, 44, 900, layer=DOOR_LAYER)
        leaf.dxf.color = DOOR_COLOR

        demote_thin_swing_leaf_lines(msp)
        promote_leaf_side_walls(msp)

        self.assertEqual(leaf.dxf.layer, "DOOR")


if __name__ == "__main__":
    unittest.main()
