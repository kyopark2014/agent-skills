"""나란한 두 문 사이의 T자 칸막이 머리는 벽이다."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

import ezdxf

sys.path.insert(0, str(Path(__file__).resolve().parent))

import lib_llm_correct


def _h(msp, x0, x1, y, layer):
    return msp.add_line((x0, y), (x1, y), dxfattribs={"layer": layer, "color": 1})


def _v(msp, x, y0, y1, layer):
    return msp.add_line((x, y0), (x, y1), dxfattribs={"layer": layer, "color": 1})


def _swing(msp, cx, cy, r, sa, ea):
    return msp.add_arc(
        (cx, cy),
        r,
        sa,
        ea,
        dxfattribs={"layer": "DOOR", "color": 3},
    )


class WallBetweenAdjacentDoorsTests(unittest.TestCase):
    def test_t_stub_between_two_double_doors_becomes_wall(self):
        """펌프실·전기실처럼 양개문 사이 T자 머리가 WALL이 된다."""
        doc = ezdxf.new()
        msp = doc.modelspace()
        # 복도 벽 y=0. 왼쪽 양개문 0–1710, 오른쪽 양개문 3200–4910.
        # 사이 갭 1710–3200 (1490 mm).
        _swing(msp, 0, 0, 855, 270, 360)
        _swing(msp, 1710, 0, 855, 180, 270)
        _swing(msp, 3200, 0, 855, 270, 360)
        _swing(msp, 4910, 0, 855, 180, 270)
        # 문 양옆 긴 벽 (이미 WALL).
        _h(msp, -2000, 0, 0, "WALL")
        _h(msp, 4910, 7000, 0, "WALL")
        _h(msp, -2000, 0, 120, "WALL")
        _h(msp, 4910, 7000, 120, "WALL")
        # T자 머리: 갭 안 이중 수평면 + 짧은 리턴 + 칸막이 끝.
        face_a = _h(msp, 1760, 3150, 160, "BASE")
        face_b = _h(msp, 1740, 3170, 185, "BASE")
        ret_l = _v(msp, 1760, 0, 160, "BASE")
        ret_r = _v(msp, 3150, 0, 160, "BASE")
        tip = _v(msp, 2455, -8000, -40, "BASE")
        # 문짝(힌지 선)은 올리면 안 된다.
        leaf = _v(msp, 1710, -855, 0, "BASE")
        # 개구를 가로지르는 문턱선도 올리면 안 된다.
        sill = _h(msp, 0, 1710, 0, "BASE")

        n = lib_llm_correct.promote_wall_between_adjacent_doors(msp)

        self.assertGreaterEqual(n, 4)
        self.assertEqual(face_a.dxf.layer, "WALL")
        self.assertEqual(face_b.dxf.layer, "WALL")
        self.assertEqual(ret_l.dxf.layer, "WALL")
        self.assertEqual(ret_r.dxf.layer, "WALL")
        self.assertEqual(tip.dxf.layer, "WALL")
        self.assertEqual(leaf.dxf.layer, "BASE")
        self.assertEqual(sill.dxf.layer, "BASE")


if __name__ == "__main__":
    unittest.main()
