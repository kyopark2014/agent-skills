"""동심 원/호(평행 이중 원) 둥근 기둥 승격."""

from __future__ import annotations

import math
import sys
import unittest
from pathlib import Path

import ezdxf

sys.path.insert(0, str(Path(__file__).resolve().parent))

import lib_llm_correct


def _h(msp, x0, x1, y, layer="WALL", color=1):
    return msp.add_line(
        (x0, y), (x1, y), dxfattribs={"layer": layer, "color": color}
    )


def _v(msp, x, y0, y1, layer="WALL", color=1):
    return msp.add_line(
        (x, y0), (x, y1), dxfattribs={"layer": layer, "color": color}
    )


class RoundColumnTests(unittest.TestCase):
    def test_concentric_circles_with_wall_promote_to_column(self):
        """동심 CIRCLE + 원주에 닿는 이중 WALL → WALL → COLUMN."""
        doc = ezdxf.new()
        msp = doc.modelspace()
        cx, cy, ri, ro = 5000.0, 6000.0, 300.0, 375.0
        inner = msp.add_circle(
            (cx, cy), ri, dxfattribs={"layer": "BASE", "color": 8}
        )
        outer = msp.add_circle(
            (cx, cy), ro, dxfattribs={"layer": "BASE", "color": 8}
        )
        # 아래에서 올라오는 이중 수직벽 + 오른쪽 수평벽 (끝점이 원주 근처)
        _v(msp, cx - 100.0, cy - 5000.0, cy - ro)
        _v(msp, cx + 100.0, cy - 5000.0, cy - ro)
        _h(msp, cx + ro, cx + 4000.0, cy - 100.0)
        _h(msp, cx + ro, cx + 4000.0, cy + 100.0)

        n = lib_llm_correct.promote_round_columns(msp)
        self.assertGreaterEqual(n, 2)
        self.assertEqual(inner.dxf.layer, "WALL")
        self.assertEqual(outer.dxf.layer, "WALL")

        marked = lib_llm_correct.mark_doors_and_columns(msp)
        self.assertGreaterEqual(marked["n_column"], 1)
        self.assertEqual(inner.dxf.layer, "COLUMN")
        self.assertEqual(outer.dxf.layer, "COLUMN")
        self.assertTrue(
            any(
                abs((c["bbox_mm"][0] + c["bbox_mm"][2]) * 0.5 - cx) < 1.0
                and abs((c["bbox_mm"][1] + c["bbox_mm"][3]) * 0.5 - cy) < 1.0
                for c in marked["columns"]
            )
        )

    def test_concentric_arcs_covering_circle_promote(self):
        """거의 한 바퀴인 동심 ARC 쌍도 둥근 기둥이다."""
        doc = ezdxf.new()
        msp = doc.modelspace()
        cx, cy, ri, ro = 1000.0, 2000.0, 345.0, 375.0
        arcs = [
            msp.add_arc(
                (cx, cy), ro, 258.0, 348.0, dxfattribs={"layer": "BASE", "color": 8}
            ),
            msp.add_arc(
                (cx, cy), ro, 350.0, 258.0, dxfattribs={"layer": "BASE", "color": 8}
            ),
            msp.add_arc(
                (cx, cy), ri, 349.0, 256.0, dxfattribs={"layer": "BASE", "color": 8}
            ),
        ]
        _v(msp, cx - 100.0, cy - 6000.0, cy - ro + 30.0)
        _v(msp, cx + 100.0, cy - 6000.0, cy - ro + 30.0)
        _h(msp, cx + ro - 30.0, cx + 3000.0, cy)

        n = lib_llm_correct.promote_round_columns(msp)
        self.assertGreaterEqual(n, 2)
        for a in arcs:
            self.assertEqual(a.dxf.layer, "WALL")

        marked = lib_llm_correct.mark_doors_and_columns(msp)
        self.assertGreaterEqual(marked["n_column"], 1)
        for a in arcs:
            self.assertEqual(a.dxf.layer, "COLUMN")

    def test_furniture_circle_without_wall_stays_base(self):
        """벽에 안 붙은 동심 원은 올리지 않는다."""
        doc = ezdxf.new()
        msp = doc.modelspace()
        inner = msp.add_circle((0, 0), 220.0, dxfattribs={"layer": "BASE", "color": 8})
        outer = msp.add_circle((0, 0), 240.0, dxfattribs={"layer": "BASE", "color": 8})
        _h(msp, 5000, 8000, 5000)

        n = lib_llm_correct.promote_round_columns(msp)
        self.assertEqual(n, 0)
        self.assertEqual(inner.dxf.layer, "BASE")
        self.assertEqual(outer.dxf.layer, "BASE")

    def test_close_round_pair_demoted_by_separation(self):
        """너무 가까운 둥근 기둥 후보는 승격·COLUMN 모두에서 빠진다."""
        doc = ezdxf.new()
        msp = doc.modelspace()
        circles = []
        for cx in (0.0, 600.0):
            circles.append(
                msp.add_circle((cx, 0.0), 220.0, dxfattribs={"layer": "BASE", "color": 8})
            )
            circles.append(
                msp.add_circle((cx, 0.0), 240.0, dxfattribs={"layer": "BASE", "color": 8})
            )
            _v(msp, cx - 50.0, -3000.0, -240.0)
            _v(msp, cx + 50.0, -3000.0, -240.0)
        # 중심 거리 600 mm << 2000 mm → promote 단계부터 제외
        n = lib_llm_correct.promote_round_columns(msp)
        self.assertEqual(n, 0)
        for c in circles:
            self.assertEqual(c.dxf.layer, "BASE")
        marked = lib_llm_correct.mark_doors_and_columns(msp)
        round_cols = [
            c
            for c in marked["columns"]
            if abs((c["bbox_mm"][2] - c["bbox_mm"][0]) - 480.0) < 5.0
        ]
        self.assertEqual(len(round_cols), 0)
        for c in circles:
            self.assertEqual(c.dxf.layer, "BASE")

    def test_triple_circle_with_corner_stubs_promotes(self):
        """삼중 동심 CIRCLE + 바깥 사각 stub 2변 → 기둥."""
        doc = ezdxf.new()
        msp = doc.modelspace()
        cx, cy = 10000.0, 20000.0
        circles = [
            msp.add_circle((cx, cy), r, dxfattribs={"layer": "BASE", "color": 8})
            for r in (225.0, 295.0, 325.0)
        ]
        ro = 325.0
        # 오른쪽·아래 stub (닫힌 사각 없이 모서리만)
        _v(msp, cx + ro, cy - 75.0, cy + 75.0, layer="BASE", color=8)
        _h(msp, cx - 75.0, cx + 75.0, cy - ro, layer="BASE", color=8)
        # 아래에서 끊기는 이중벽
        _v(msp, cx - 80.0, cy - ro - 4000.0, cy - ro, layer="WALL", color=1)
        _v(msp, cx + 80.0, cy - ro - 4000.0, cy - ro, layer="WALL", color=1)

        n = lib_llm_correct.promote_round_columns(msp)
        self.assertGreaterEqual(n, 3)
        for c in circles:
            self.assertEqual(c.dxf.layer, "WALL")
        marked = lib_llm_correct.mark_doors_and_columns(msp)
        for c in circles:
            self.assertEqual(c.dxf.layer, "COLUMN")
        self.assertGreaterEqual(marked["n_column"], 1)

    def test_wall_through_round_column_is_trimmed(self):
        """둥근 기둥을 관통하는 WALL은 원주에서 끊긴다."""
        doc = ezdxf.new()
        msp = doc.modelspace()
        cx, cy, ri, ro = 0.0, 0.0, 300.0, 375.0
        msp.add_circle((cx, cy), ri, dxfattribs={"layer": "BASE", "color": 8})
        msp.add_circle((cx, cy), ro, dxfattribs={"layer": "BASE", "color": 8})
        _v(msp, cx - 100.0, cy - 5000.0, cy - ro)
        _v(msp, cx + 100.0, cy - 5000.0, cy - ro)
        through = _v(msp, cx + 50.0, cy - 3000.0, cy + 3000.0, layer="WALL", color=1)

        lib_llm_correct.promote_round_columns(msp)
        # 관통선이 잘렸거나 BASE로 내려감
        y0 = min(float(through.dxf.start.y), float(through.dxf.end.y))
        y1 = max(float(through.dxf.start.y), float(through.dxf.end.y))
        if through.dxf.layer == "BASE":
            return
        self.assertEqual(through.dxf.layer, "WALL")
        # 한쪽 끝이 원 밖·원주 근처로 줄어듦 (전체 ±3000 유지 금지)
        self.assertLess(y1 - y0, 5500.0)

    def test_circle_inside_square_frame_promotes(self):
        """가운데 동심 원 + 바깥 네모(WALL 접촉 없음) → 기둥."""
        doc = ezdxf.new()
        msp = doc.modelspace()
        cx, cy, ri, ro = 5000.0, 6000.0, 345.0, 375.0
        half = 450.0  # 원과 사각 틈 ~75 mm
        circles = [
            msp.add_circle((cx, cy), ri, dxfattribs={"layer": "BASE", "color": 8}),
            msp.add_circle((cx, cy), ro, dxfattribs={"layer": "BASE", "color": 8}),
        ]
        frame = [
            _v(msp, cx - half, cy - half, cy + half, layer="BASE", color=8),
            _v(msp, cx + half, cy - half, cy + half, layer="BASE", color=8),
            _h(msp, cx - half, cx + half, cy - half, layer="BASE", color=8),
            _h(msp, cx - half, cx + half, cy + half, layer="BASE", color=8),
        ]

        n = lib_llm_correct.promote_round_columns(msp)
        self.assertGreaterEqual(n, 2)
        for c in circles:
            self.assertEqual(c.dxf.layer, "WALL")
        for f in frame:
            self.assertEqual(f.dxf.layer, "WALL")
        marked = lib_llm_correct.mark_doors_and_columns(msp)
        for c in circles + frame:
            self.assertEqual(c.dxf.layer, "COLUMN")
        self.assertGreaterEqual(marked["n_column"], 1)


if __name__ == "__main__":
    unittest.main()
