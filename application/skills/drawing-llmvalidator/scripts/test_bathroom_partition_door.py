"""병실 화장실처럼 짧은 칸막이 안의 외여닫이 — 양옆 이중선은 벽."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

import ezdxf

sys.path.insert(0, str(Path(__file__).resolve().parent))

import lib_llm_correct


def _h(msp, x0, x1, y, layer="BASE"):
    return msp.add_line((x0, y), (x1, y), dxfattribs={"layer": layer, "color": 8})


def _v(msp, x, y0, y1, layer="BASE"):
    return msp.add_line((x, y0), (x, y1), dxfattribs={"layer": layer, "color": 8})


class BathroomPartitionDoorTests(unittest.TestCase):
    def test_short_partition_swing_door_flanks_become_wall(self):
        """512호 화장실: 1.5 m 칸막이 + 0.84 m 1/4 스윙 → 양옆 이중선 WALL."""
        doc = ezdxf.new()
        msp = doc.modelspace()
        # 칸막이 이중선 (두께 150 mm, 길이 1485 mm)
        face0 = _h(msp, 0, 1485, 0)
        face1 = _h(msp, 0, 1485, 150)
        # 힌지 쪽·맞은편 짧은 세로면
        _v(msp, 0, 150, 990)
        _v(msp, 150, 150, 990)
        # 1/4 스윙: 힌지 (150, 150), 개구는 +x, 열림은 +y
        msp.add_arc(
            center=(150, 150),
            radius=840,
            start_angle=2.4,
            end_angle=90.0,
            dxfattribs={"layer": "BASE", "color": 8},
        )
        # 열린 문짝(가로)
        msp.add_line(
            (150, 185),
            (150 + 839.3, 185),
            dxfattribs={"layer": "BASE", "color": 8},
        )

        doubles = lib_llm_correct.find_double_door_openings(msp)
        singles, _leaves, _sides = lib_llm_correct.find_single_door_openings(
            msp, doubles
        )
        near = [
            o
            for o in singles
            if (not o[0]) and abs(o[1] - 150) <= 40.0
        ]
        self.assertTrue(near, msg=f"칸막이 개구를 못 찾음: {singles}")
        open0, open1 = near[0][2], near[0][3]
        self.assertGreaterEqual(open1 - open0, 650.0)
        self.assertLess(abs(open0 - 150), 40.0)

        _cut, _flank = lib_llm_correct.correct_walls_around_doors(msp)

        def _wall_h_cover(y: float, x0: float, x1: float) -> float:
            cover = 0.0
            for e in msp:
                if e.dxftype() != "LINE" or e.dxf.layer != "WALL":
                    continue
                a0, b0 = e.dxf.start.x, e.dxf.start.y
                a1, b1 = e.dxf.end.x, e.dxf.end.y
                if abs(b0 - b1) > 30.0 or abs((b0 + b1) * 0.5 - y) > 40.0:
                    continue
                lo, hi = min(a0, a1), max(a0, a1)
                cover += max(0.0, min(hi, x1) - max(lo, x0))
            return cover

        # 문 왼쪽·오른쪽 턱이 WALL
        self.assertGreaterEqual(_wall_h_cover(150, 0, 150), 100.0)
        self.assertGreaterEqual(_wall_h_cover(150, 990, 1485), 200.0)
        self.assertGreaterEqual(_wall_h_cover(0, 990, 1485), 200.0)
        # 개구 구간은 WALL로 막히지 않는다
        self.assertLess(_wall_h_cover(150, 200, 900), 100.0)
        self.assertEqual(face0.dxf.layer, "BASE")
        self.assertEqual(face1.dxf.layer, "BASE")


if __name__ == "__main__":
    unittest.main()
