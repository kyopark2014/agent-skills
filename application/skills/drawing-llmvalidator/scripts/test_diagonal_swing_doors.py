"""대각 1/4 스윙: 문짝 DOOR + 열린 반대 방향 닫힌 현 WALL."""

from __future__ import annotations

import math
import sys
import unittest
from pathlib import Path

import ezdxf

sys.path.insert(0, str(Path(__file__).resolve().parent))

import lib_llm_correct


def _diagonal_quarter_swing(
    msp,
    hx: float,
    hy: float,
    radius: float = 1050.0,
    *,
    open_angle: float = 45.0,
    closed_angle: float = 135.0,
) -> None:
    """전남대 예대 CNC실 단열도어형: 호 끝 둘 다 대각, 문짝은 열린 쪽만."""
    base = {"layer": "BASE", "color": 8}
    wall = {"layer": "WALL", "color": 1}
    oux = math.cos(math.radians(open_angle))
    ouy = math.sin(math.radians(open_angle))
    cux = math.cos(math.radians(closed_angle))
    cuy = math.sin(math.radians(closed_angle))
    # 열린 문짝 (이중)
    msp.add_line((hx, hy), (hx + radius * oux, hy + radius * ouy), dxfattribs=base)
    msp.add_line(
        (hx - 30.0 * cuy, hy + 30.0 * cux),
        (hx + radius * oux - 30.0 * cuy, hy + radius * ouy + 30.0 * cux),
        dxfattribs=base,
    )
    # 힌지 뒤·옆 짧은 벽
    msp.add_line(
        (hx - 250.0 * oux, hy - 250.0 * ouy),
        (hx - 20.0 * oux, hy - 20.0 * ouy),
        dxfattribs=wall,
    )
    msp.add_line(
        (hx - 110.0 * cux, hy - 110.0 * cuy),
        (hx - 20.0 * cux, hy - 20.0 * cuy),
        dxfattribs=wall,
    )
    # 문 밖 이어진 경사 벽면 (열린 쪽 끝 너머)
    far = radius + 400.0
    msp.add_line(
        (hx + (radius + 40.0) * oux, hy + (radius + 40.0) * ouy),
        (hx + far * oux, hy + far * ouy),
        dxfattribs=base,
    )
    # 닫힌 쪽 스트라이크 짧은 스터브만 (현은 없음)
    msp.add_line(
        (hx + radius * cux, hy + radius * cuy),
        (hx + radius * cux + 25.0 * cux, hy + radius * cuy + 25.0 * cuy),
        dxfattribs=base,
    )
    msp.add_arc((hx, hy), radius, open_angle, closed_angle, dxfattribs=base)


class DiagonalSwingDoorTests(unittest.TestCase):
    def test_paints_door_and_closes_diagonal_opening(self):
        doc = ezdxf.new()
        msp = doc.modelspace()
        hx, hy, r = 1615430.6, -2187861.0, 1050.0
        _diagonal_quarter_swing(msp, hx, hy, r)

        found = lib_llm_correct._find_diagonal_swing_doors(msp)
        self.assertEqual(len(found), 1)
        self.assertIsNotNone(found[0]["close"])
        cx0, cy0, cx1, cy1 = found[0]["close"]
        self.assertAlmostEqual(cx0, hx, delta=1.0)
        self.assertAlmostEqual(cy0, hy, delta=1.0)
        # 닫힌 현은 135° 방향
        cux = math.cos(math.radians(135.0))
        cuy = math.sin(math.radians(135.0))
        self.assertAlmostEqual(cx1, hx + r * cux, delta=2.0)
        self.assertAlmostEqual(cy1, hy + r * cuy, delta=2.0)

        n_demote, n_promote = lib_llm_correct.separate_diagonal_swing_doors(msp)
        self.assertGreaterEqual(n_demote, 1)
        self.assertGreaterEqual(n_promote, 1)

        arcs = [e for e in msp.query("ARC") if e.dxf.layer == "DOOR"]
        self.assertEqual(len(arcs), 1)
        leaf_doors = [
            e
            for e in msp.query("LINE")
            if e.dxf.layer == "DOOR"
            and abs(math.hypot(float(e.dxf.end.x) - float(e.dxf.start.x), float(e.dxf.end.y) - float(e.dxf.start.y)) - r)
            <= 5.0
        ]
        self.assertGreaterEqual(len(leaf_doors), 1)

        # 힌지→닫힌 끝이 WALL 로 막혀야 한다.
        covered = 0.0
        for e in msp.query("LINE"):
            if e.dxf.layer != "WALL":
                continue
            a, b = e.dxf.start, e.dxf.end
            ax, ay, bx, by = float(a.x), float(a.y), float(b.x), float(b.y)
            # 닫힌 축에 투영
            along0 = (ax - hx) * cux + (ay - hy) * cuy
            along1 = (bx - hx) * cux + (by - hy) * cuy
            off0 = (ax - hx) * (-cuy) + (ay - hy) * cux
            off1 = (bx - hx) * (-cuy) + (by - hy) * cux
            if abs(off0) > 12.0 or abs(off1) > 12.0:
                continue
            if abs(((bx - ax) / max(math.hypot(bx - ax, by - ay), 1.0)) * cux + ((by - ay) / max(math.hypot(bx - ax, by - ay), 1.0)) * cuy) < 0.98:
                continue
            covered += max(0.0, min(r, max(along0, along1)) - max(0.0, min(along0, along1)))
        self.assertGreaterEqual(covered / r, 0.85)

    def test_axis_aligned_quarter_still_skipped_by_finder(self):
        """호 끝이 둘 다 축정렬이면 대각 스윙 대상이 아니다."""
        doc = ezdxf.new()
        msp = doc.modelspace()
        hx, hy, r = 10000.0, 20000.0, 900.0
        base = {"layer": "BASE", "color": 8}
        msp.add_line((hx, hy), (hx + r, hy), dxfattribs=base)
        msp.add_arc((hx, hy), r, 0.0, 90.0, dxfattribs=base)
        self.assertEqual(lib_llm_correct._find_diagonal_swing_doors(msp), [])

    def test_polyline_l_swing_on_slanted_double_wall(self):
        """ARC 없는 3점 L자 스윙: 문짝 DOOR, 이중면 양옆 WALL.

        바로유 sheet_01 해칭 외벽처럼, 문 개구에서 평행 짝 overlap 이
        음수여도 근처 이중면이면 벽으로 올린다.
        """
        doc = ezdxf.new()
        msp = doc.modelspace()
        base = {"layer": "BASE", "color": 8}
        # 30° 이중면. 문 개구 기준으로 왼쪽·오른쪽 면이 어긋나 overlap 없음.
        ang = math.radians(30.0)
        ux, uy = math.cos(ang), math.sin(ang)
        nx, ny = -uy, ux
        gap = 199.0
        # host face (오른쪽, 문 힌지 쪽)
        hx0, hy0 = 40000.0, 53000.0
        msp.add_line(
            (hx0, hy0),
            (hx0 + 953.0 * ux, hy0 + 953.0 * uy),
            dxfattribs=base,
        )
        # mate face (왼쪽, 개구 너머 — overlap 음수)
        mx0 = hx0 - 200.0 * ux + gap * nx
        my0 = hy0 - 200.0 * uy + gap * ny
        msp.add_line(
            (mx0, my0),
            (mx0 + 748.0 * ux, my0 + 748.0 * uy),
            dxfattribs=base,
        )
        # 문 힌지가 붙는 벽 끝
        wall_end = (hx0 + 20.0 * ux, hy0 + 20.0 * uy)
        # 3점 L: 벽끝 → 열린 tip(≈90°) → 짧은 리턴
        tip = (
            wall_end[0] - 489.0 * math.cos(ang + math.pi / 2.0),
            wall_end[1] - 489.0 * math.sin(ang + math.pi / 2.0),
        )
        ret = (
            tip[0] + 205.0 * math.cos(ang + math.pi),
            tip[1] + 205.0 * math.sin(ang + math.pi),
        )
        leaf = msp.add_lwpolyline(
            [wall_end, tip, ret],
            dxfattribs=base,
        )
        leaf.closed = False

        found = lib_llm_correct._find_polyline_l_swing_doors(msp)
        self.assertGreaterEqual(len(found), 1)
        self.assertTrue(any(abs(s["hx"] - tip[0]) < 5.0 and abs(s["hy"] - tip[1]) < 5.0 for s in found))

        n_demote, n_promote = lib_llm_correct.separate_diagonal_swing_doors(msp)
        self.assertGreaterEqual(n_demote, 1)
        self.assertGreaterEqual(n_promote, 2)
        self.assertEqual(leaf.dxf.layer, "DOOR")
        wall_lines = [e for e in msp.query("LINE") if e.dxf.layer == "WALL"]
        self.assertGreaterEqual(len(wall_lines), 2)

        # 분절 스윙이 WALL LWPOLYLINE 문옆 면을 해칭 가짜 호로 DOOR 덮지 않는다.
        mate_poly = msp.add_lwpolyline(
            [
                (mx0, my0),
                (mx0 + 748.0 * ux, my0 + 748.0 * uy),
            ],
            dxfattribs={"layer": "WALL", "color": 1},
        )
        mate_poly.closed = False
        # 원주 위 짧은 해칭처럼 보이는 선
        for k in range(6):
            ang_h = ang + math.radians(20.0 + 10.0 * k)
            rr = 748.0
            cx = mx0 + rr * math.cos(ang_h)
            cy = my0 + rr * math.sin(ang_h)
            tx, ty = -math.sin(ang_h), math.cos(ang_h)
            msp.add_line(
                (cx - 40.0 * tx, cy - 40.0 * ty),
                (cx + 40.0 * tx, cy + 40.0 * ty),
                dxfattribs=base,
            )
        lib_llm_correct.separate_segmented_swing_doors(msp)
        self.assertEqual(mate_poly.dxf.layer, "WALL")


if __name__ == "__main__":
    unittest.main()
