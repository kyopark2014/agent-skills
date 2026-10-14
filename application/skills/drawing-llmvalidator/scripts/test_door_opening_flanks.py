"""문 개구 바로 바깥의 짧은 면은 벽이다."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

import ezdxf

sys.path.insert(0, str(Path(__file__).resolve().parent))

import lib_llm_correct


def _h(msp, x0, x1, y, layer):
    return msp.add_line((x0, y), (x1, y), dxfattribs={"layer": layer})


class DoorOpeningFlankTests(unittest.TestCase):
    def test_short_faces_beside_split_door_become_wall(self):
        doc = ezdxf.new()
        msp = doc.modelspace()
        # 일반교실 1-1 위쪽처럼 문짝이 여러 줄로 나뉜 개구.
        for y, x0, x1 in (
            (0, 100, 670),
            (30, 740, 1310),
            (68, 1380, 1950),
            (68, 750, 1300),
        ):
            _h(msp, x0, x1, y, "DOOR")
        left = _h(msp, -50, 100, 80, "BASE")
        right = _h(msp, 2050, 2200, 80, "BASE")
        inner = _h(msp, 100, 1420, 80, "BASE")
        far = _h(msp, 4000, 4150, 80, "BASE")

        promoted = lib_llm_correct.promote_door_opening_flanks(msp)

        self.assertEqual(promoted, 2)
        self.assertEqual(left.dxf.layer, "WALL")
        self.assertEqual(right.dxf.layer, "WALL")
        self.assertEqual(inner.dxf.layer, "BASE")
        self.assertEqual(far.dxf.layer, "BASE")


if __name__ == "__main__":
    unittest.main()
