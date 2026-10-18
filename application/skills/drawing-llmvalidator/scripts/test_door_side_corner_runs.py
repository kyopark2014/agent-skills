"""문 옆 이중선이 직각으로 이어지면 그 끝까지 벽이다."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

import ezdxf

sys.path.insert(0, str(Path(__file__).resolve().parent))

import lib_llm_correct


def _v(msp, x, y0, y1, layer):
    return msp.add_line((x, y0), (x, y1), dxfattribs={"layer": layer})


def _h(msp, x0, x1, y, layer):
    return msp.add_line((x0, y), (x1, y), dxfattribs={"layer": layer})


class DoorSideCornerRunTests(unittest.TestCase):
    def test_door_side_run_turns_onto_the_room_head(self):
        doc = ezdxf.new()
        msp = doc.modelspace()
        msp.add_lwpolyline(
            [(0, 0), (100, 0), (100, 840), (0, 840)],
            close=True,
            dxfattribs={"layer": "WALL"},
        )
        below_outer = _v(msp, 0, -2100, -30, "BASE")
        below_inner = _v(msp, 100, -2000, -30, "BASE")
        outer = _v(msp, 0, 870, 4480, "BASE")
        inner_a = _v(msp, 100, 870, 2870, "BASE")
        inner_b = _v(msp, 100, 2970, 4380, "BASE")
        head_outer = _h(msp, 0, 5800, 4480, "BASE")
        head_inner = _h(msp, 100, 2200, 4380, "BASE")
        head_inner_b = _h(msp, 2300, 5700, 4380, "BASE")
        stray = _h(msp, 9000, 11000, 4480, "BASE")

        promoted = lib_llm_correct.promote_door_side_corner_runs(msp)

        self.assertGreaterEqual(promoted, 6)
        for line in (below_outer, below_inner, outer, inner_a, inner_b, head_outer, head_inner, head_inner_b):
            self.assertEqual(line.dxf.layer, "WALL", line.dxf.start)
        self.assertEqual(stray.dxf.layer, "BASE")

    def test_flush_door_side_end_promotes_room_bottom(self):
        """양쪽 세로면이 같은 높이로 끝나도 문 쪽 벽두께 가로는 벽이다.

        레이저실(7) 하단처럼 outer/inner far가 같고, 가로 이중면이 문 쪽
        벽두께 띠(far+thick, far+2·thick)에 있는 경우.
        """
        doc = ezdxf.new()
        msp = doc.modelspace()
        msp.add_lwpolyline(
            [(0, 0), (100, 0), (100, 840), (0, 840)],
            close=True,
            dxfattribs={"layer": "WALL"},
        )
        stub_outer = _v(msp, 0, -330, -30, "BASE")
        stub_inner_near = _v(msp, 100, -130, -30, "BASE")
        stub_inner_far = _v(msp, 100, -330, -230, "BASE")
        bottom_outer = _h(msp, 100, 2200, -230, "BASE")
        bottom_inner = _h(msp, 100, 2200, -130, "BASE")
        stray = _h(msp, 100, 2200, -500, "BASE")

        promoted = lib_llm_correct.promote_door_side_corner_runs(msp)

        self.assertGreaterEqual(promoted, 4)
        for line in (stub_outer, stub_inner_near, stub_inner_far, bottom_outer, bottom_inner):
            self.assertEqual(line.dxf.layer, "WALL", line.dxf.start)
        self.assertEqual(stray.dxf.layer, "BASE")


if __name__ == "__main__":
    unittest.main()
