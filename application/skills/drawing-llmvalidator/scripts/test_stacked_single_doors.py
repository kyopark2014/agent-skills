"""같은 방향 적층 외여닫이 — 양개로 묶지 않고 문 옆 이중면을 WALL로."""

from __future__ import annotations

import math
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


class StackedSingleDoorTests(unittest.TestCase):
    def test_same_direction_stacked_swings_are_not_double(self):
        """간호사실: 위·아래 외여닫이가 같은 방향으로 열리면 양개가 아니다."""
        doc = ezdxf.new()
        msp = doc.modelspace()
        # 세로 이중벽 두께 150, 아래·위 문 높이 940 + 사이 간격 610
        for x in (0.0, 150.0):
            _v(msp, x, 0.0, 940.0)
            _v(msp, x, 1550.0, 2490.0)
        # 열린 문짝(가로) — 호스트로 오인되기 쉬운 선
        _h(msp, -940.0, 0.0, 0.0)
        _h(msp, -940.0, 0.0, 1550.0)
        # 같은 방향 1/4 스윙 두 개 (둘 다 +y 벽 끝, -x 열림)
        for cy in (0.0, 1550.0):
            msp.add_arc(
                center=(0.0, cy),
                radius=940.0,
                start_angle=90.0,
                end_angle=178.0,
                dxfattribs={"layer": "BASE", "color": 8},
            )

        doubles = lib_llm_correct.find_double_door_openings(msp)
        self.assertFalse(
            any(abs(o[1] - 0.0) <= 40.0 for o in doubles),
            msg=f"적층 외여닫이가 양개로 묶임: {doubles}",
        )

        singles, _leaves, _sides = lib_llm_correct.find_single_door_openings(
            msp, doubles
        )
        vertical = [o for o in singles if o[0] and abs(o[1] - 0.0) <= 40.0]
        self.assertGreaterEqual(len(vertical), 2, msg=f"싱글 개구 부족: {singles}")

        lib_llm_correct.correct_walls_around_doors(msp)

        def _wall_v_cover(x: float, y0: float, y1: float) -> float:
            cover = 0.0
            for e in msp:
                if e.dxftype() != "LINE" or e.dxf.layer != "WALL":
                    continue
                a0, b0 = e.dxf.start.x, e.dxf.start.y
                a1, b1 = e.dxf.end.x, e.dxf.end.y
                if abs(a0 - a1) > 30.0 or abs((a0 + a1) * 0.5 - x) > 40.0:
                    continue
                lo, hi = min(b0, b1), max(b0, b1)
                cover += max(0.0, min(hi, y1) - max(lo, y0))
            return cover

        # 문 옆(바깥면 x=150)이 WALL
        self.assertGreaterEqual(_wall_v_cover(150.0, 0.0, 940.0), 700.0)
        self.assertGreaterEqual(_wall_v_cover(150.0, 1550.0, 2490.0), 700.0)


if __name__ == "__main__":
    unittest.main()
