"""짧은 호(약 30°) + 방사 문짝 스윙을 DOOR로 내리는지 확인한다."""

from __future__ import annotations

import math
import sys
import unittest
from pathlib import Path

import ezdxf

sys.path.insert(0, str(Path(__file__).resolve().parent))

import lib_llm_correct


def _partial_arc_swing(msp, hx: float, hy: float, radius: float = 850.0) -> None:
    """힌지에서 남쪽으로 닫힌 문짝 + 서쪽으로 60° 열린 문짝 + 30° 호."""
    base = {"layer": "BASE", "color": 8}
    wall = {"layer": "WALL", "color": 1}
    # 개구 양옆 긴 면은 BASE로 두고 승격되는지 본다.
    msp.add_line((hx, hy - radius - 2000.0), (hx, hy - radius - 20.0), dxfattribs=base)
    msp.add_line((hx, hy + 20.0), (hx, hy + 2000.0), dxfattribs=base)
    msp.add_line((hx - 200.0, hy - radius - 2000.0), (hx - 200.0, hy - radius - 20.0), dxfattribs=base)
    msp.add_line((hx - 200.0, hy + 20.0), (hx - 200.0, hy + 2000.0), dxfattribs=base)
    # 개구 끝 두께 리턴
    msp.add_line((hx - 200.0, hy - radius), (hx, hy - radius), dxfattribs=wall)
    msp.add_line((hx - 200.0, hy), (hx, hy), dxfattribs=wall)
    # 축정렬 닫힘 문짝
    msp.add_line((hx, hy), (hx, hy - radius), dxfattribs=base)
    # 사선 열림 문짝 (60°)
    rad = math.radians(60.0)
    msp.add_line(
        (hx, hy),
        (hx + radius * math.cos(rad), hy + radius * math.sin(rad)),
        dxfattribs=base,
    )
    # 30° 호
    msp.add_arc((hx, hy), radius, 240.0, 270.0, dxfattribs=base)


class PartialArcRadialSwingTests(unittest.TestCase):
    def test_paints_door_and_closes_opening(self):
        doc = ezdxf.new()
        msp = doc.modelspace()
        hx, hy, r = 10000.0, 20000.0, 850.0
        _partial_arc_swing(msp, hx, hy, r)

        n_demote, n_promote = lib_llm_correct.separate_partial_arc_radial_swing_doors(msp)
        self.assertGreaterEqual(n_demote, 1)
        self.assertGreaterEqual(n_promote, 1)

        arcs = [e for e in msp.query("ARC") if e.dxf.layer == "DOOR"]
        self.assertEqual(len(arcs), 1)
        diag_doors = [
            e
            for e in msp.query("LINE")
            if e.dxf.layer == "DOOR"
            and abs(float(e.dxf.start.x) - float(e.dxf.end.x)) > 12
            and abs(float(e.dxf.start.y) - float(e.dxf.end.y)) > 12
        ]
        self.assertGreaterEqual(len(diag_doors), 1)

        # 힌지면 개구가 WALL 로 막혀야 한다.
        covered = 0.0
        lo, hi = hy - r, hy
        for e in msp.query("LINE"):
            if e.dxf.layer != "WALL":
                continue
            a, b = e.dxf.start, e.dxf.end
            if abs(float(a.x) - hx) > 10 or abs(float(b.x) - hx) > 10:
                continue
            wlo, whi = sorted((float(a.y), float(b.y)))
            covered += max(0.0, min(hi, whi) - max(lo, wlo))
        self.assertGreaterEqual(covered / r, 0.85)

        # 개구 양옆 긴 BASE 면이 WALL 로 올라가야 한다.
        flank_walls = [
            e
            for e in msp.query("LINE")
            if e.dxf.layer == "WALL"
            and abs(float(e.dxf.start.x) - hx) <= 10
            and abs(float(e.dxf.end.x) - hx) <= 10
            and abs(float(e.dxf.start.y) - float(e.dxf.end.y)) >= 1500.0
        ]
        self.assertGreaterEqual(len(flank_walls), 2)


if __name__ == "__main__":
    unittest.main()
