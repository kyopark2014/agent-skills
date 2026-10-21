"""이중벽 바깥 평행면도 스윙문 개구를 가상 벽으로 닫는지 확인한다."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

import ezdxf

sys.path.insert(0, str(Path(__file__).resolve().parent))

import lib_llm_correct


def _double_wall_swing(msp) -> None:
    """힌지면·중간면은 문틀이 있고, 복도쪽 바깥면은 개구 전 구간이 비어 있다."""
    wall = {"layer": "WALL", "color": 1}
    # 힌지면 x=200 (문 개구 291475→ 상대 0..1075)
    msp.add_line((200.0, -100.0), (200.0, 0.0), dxfattribs=wall)
    msp.add_line((200.0, 1075.0), (200.0, 2200.0), dxfattribs=wall)
    # 중간면 x=100 — 개구는 비움, 양끝만
    msp.add_line((100.0, -100.0), (100.0, 15.0), dxfattribs=wall)
    msp.add_line((100.0, 1060.0), (100.0, 2200.0), dxfattribs=wall)
    # 복도 바깥면 x=0 — 개구를 포함한 긴 갭
    msp.add_line((0.0, -150.0), (0.0, -25.0), dxfattribs=wall)
    msp.add_line((0.0, 2175.0), (0.0, 2300.0), dxfattribs=wall)
    # 외여닫이: 힌지 (200,0), 개구 +Y, 스윙 +X
    msp.add_arc((200.0, 0.0), 1075.0, 0.0, 90.0, dxfattribs={"layer": "DOOR"})


def _covered_frac(msp, x: float, y0: float, y1: float) -> float:
    lo, hi = sorted((y0, y1))
    span = hi - lo
    intervals: list[tuple[float, float]] = []
    for ent in msp.query("LINE"):
        if ent.dxf.layer != "WALL":
            continue
        a, b = ent.dxf.start, ent.dxf.end
        if abs(a.x - x) > 8.0 or abs(b.x - x) > 8.0:
            continue
        wlo, whi = sorted((float(a.y), float(b.y)))
        intervals.append((max(lo, wlo), min(hi, whi)))
    intervals.sort()
    covered_len = 0.0
    cursor = lo
    for a, b in intervals:
        if b <= a:
            continue
        if a > cursor:
            cursor = a
        if b > cursor:
            covered_len += b - cursor
            cursor = b
    return covered_len / span if span > 1.0 else 1.0


class ClosedDoorParallelPackTests(unittest.TestCase):
    def test_closes_outer_pack_face(self):
        doc = ezdxf.new()
        msp = doc.modelspace()
        _double_wall_swing(msp)

        added = lib_llm_correct.add_closed_door_wall_lines(msp)
        self.assertGreaterEqual(added, 1)

        # 힌지면·바깥면 모두 개구가 대부분 막혀야 한다.
        self.assertGreaterEqual(_covered_frac(msp, 200.0, 0.0, 1075.0), 0.85)
        self.assertGreaterEqual(_covered_frac(msp, 0.0, 0.0, 1075.0), 0.85)

    def test_closes_when_base_spans_two_doors(self):
        """힌지·중간면 BASE 한 줄이 문 두 칸을 지나도 복도 바깥면을 닫는다."""
        doc = ezdxf.new()
        msp = doc.modelspace()
        wall = {"layer": "WALL", "color": 1}
        base = {"layer": "BASE", "color": 8}
        # 힌지면·중간면: 두 문 개구를 한 BASE 로 관통 (sheet_10 패턴)
        msp.add_line((200.0, -100.0), (200.0, 0.0), dxfattribs=wall)
        msp.add_line((200.0, 0.0), (200.0, 2150.0), dxfattribs=base)
        msp.add_line((200.0, 2150.0), (200.0, 2300.0), dxfattribs=wall)
        msp.add_line((100.0, -100.0), (100.0, 15.0), dxfattribs=wall)
        msp.add_line((100.0, 15.0), (100.0, 2135.0), dxfattribs=base)
        msp.add_line((100.0, 2135.0), (100.0, 2300.0), dxfattribs=wall)
        # 복도 바깥면: 개구 전 구간 갭
        msp.add_line((0.0, -150.0), (0.0, -25.0), dxfattribs=wall)
        msp.add_line((0.0, 2175.0), (0.0, 2300.0), dxfattribs=wall)
        msp.add_arc((200.0, 0.0), 1075.0, 0.0, 90.0, dxfattribs={"layer": "DOOR"})
        msp.add_arc((200.0, 2150.0), 1075.0, 270.0, 360.0, dxfattribs={"layer": "DOOR"})

        added = lib_llm_correct.add_closed_door_wall_lines(msp)
        self.assertGreaterEqual(added, 1)
        self.assertGreaterEqual(_covered_frac(msp, 0.0, 0.0, 1075.0), 0.85)

    def test_closes_near_intermediate_pack_face(self):
        """닫힌 힌지면에서 20 mm 떨어진 중간면 갭도 잇는다 (sheet_10 우측 스윙)."""
        doc = ezdxf.new()
        msp = doc.modelspace()
        wall = {"layer": "WALL", "color": 1}
        # 힌지면 x=200 — 개구 양끝만
        msp.add_line((200.0, -100.0), (200.0, 0.0), dxfattribs=wall)
        msp.add_line((200.0, 1075.0), (200.0, 2200.0), dxfattribs=wall)
        # 중간면 x=180 (20 mm) — 동일 갭
        msp.add_line((180.0, -100.0), (180.0, -20.0), dxfattribs=wall)
        msp.add_line((180.0, 1100.0), (180.0, 2200.0), dxfattribs=wall)
        # 바깥면 x=0
        msp.add_line((0.0, -150.0), (0.0, -25.0), dxfattribs=wall)
        msp.add_line((0.0, 2175.0), (0.0, 2300.0), dxfattribs=wall)
        msp.add_arc((200.0, 0.0), 1075.0, 0.0, 90.0, dxfattribs={"layer": "DOOR"})

        added = lib_llm_correct.add_closed_door_wall_lines(msp)
        self.assertGreaterEqual(added, 1)
        self.assertGreaterEqual(_covered_frac(msp, 200.0, 0.0, 1075.0), 0.85)
        self.assertGreaterEqual(_covered_frac(msp, 180.0, 0.0, 1075.0), 0.85)
        self.assertGreaterEqual(_covered_frac(msp, 0.0, 0.0, 1075.0), 0.85)


if __name__ == "__main__":
    unittest.main()
