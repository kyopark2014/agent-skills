"""선으로 그린 작은 H형 기둥을 WALL/COLUMN으로 올리는지 확인한다."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

import ezdxf

sys.path.insert(0, str(Path(__file__).resolve().parent))

import lib_llm_correct


def _line_h_column(msp, x0: float, y0: float, side: float = 400.0) -> None:
    """웹 두 줄 + 가운데 플랜지 H (sheet_10 우상단 패턴)."""
    base = {"layer": "BASE", "color": 8}
    x1 = x0 + side
    y1 = y0 + side
    # 세로 웹
    msp.add_line((x0, y0), (x0, y1), dxfattribs=base)
    msp.add_line((x1, y0), (x1, y1), dxfattribs=base)
    # 가운데 플랜지
    mid = (y0 + y1) * 0.5
    msp.add_line((x0 + 20.0, mid), (x1 - 20.0, mid), dxfattribs=base)
    # 상·하 플랜지 조각
    msp.add_line((x0, y1), (x0 + 20.0, y1), dxfattribs=base)
    msp.add_line((x1 - 20.0, y1), (x1, y1), dxfattribs=base)
    msp.add_line((x0, y0), (x0 + 20.0, y0), dxfattribs=base)
    msp.add_line((x1 - 20.0, y0), (x1, y0), dxfattribs=base)


def _line_rect_sleeve(msp, x0: float, y0: float, width: float = 300.0, height: float = 1140.0) -> None:
    """양쪽 끝 플랜지가 있는 사각 슬리브 (sheet_10 하단 문옆 기둥)."""
    base = {"layer": "BASE", "color": 8}
    x1 = x0 + width
    y1 = y0 + height
    msp.add_line((x0, y0), (x0, y1), dxfattribs=base)
    msp.add_line((x1, y0), (x1, y1), dxfattribs=base)
    msp.add_line((x0, y0), (x1, y0), dxfattribs=base)
    msp.add_line((x0, y1), (x1, y1), dxfattribs=base)


class LineHSymbolColumnTests(unittest.TestCase):
    def test_promotes_line_h_to_wall_then_column(self):
        doc = ezdxf.new()
        msp = doc.modelspace()
        x0, y0, side = 77000.0, 331350.0, 400.0
        _line_h_column(msp, x0, y0, side)

        ids, boxes = lib_llm_correct.find_line_h_symbol_column_entities(msp)
        self.assertGreaterEqual(len(ids), 2)
        self.assertGreaterEqual(len(boxes), 1)

        n = lib_llm_correct.promote_hbeam_columns(msp)
        self.assertGreaterEqual(n, 2)
        webs = [
            e
            for e in msp.query("LINE")
            if abs(float(e.dxf.start.x) - float(e.dxf.end.x)) <= 12
            and abs(float(e.dxf.start.y) - float(e.dxf.end.y)) >= 350
        ]
        self.assertTrue(all(e.dxf.layer == "WALL" for e in webs))

        marked = lib_llm_correct.mark_doors_and_columns(msp)
        self.assertGreaterEqual(marked["n_column"], 1)
        self.assertTrue(any(e.dxf.layer == "COLUMN" for e in webs))
        self.assertTrue(
            any(
                abs(c["bbox_mm"][0] - (x0 - 20.0)) < 5.0
                or abs(c["bbox_mm"][0] - x0) < 30.0
                for c in marked["columns"]
            )
        )

    def test_promotes_short_web_h(self):
        """웹 길이 281 mm (기존 300 하한 미만) H도 잡는다."""
        doc = ezdxf.new()
        msp = doc.modelspace()
        x0, y0, side = 71750.0, 301970.0, 270.0
        height = 281.0
        base = {"layer": "BASE", "color": 8}
        msp.add_line((x0, y0), (x0, y0 + height), dxfattribs=base)
        msp.add_line((x0 + side, y0), (x0 + side, y0 + height), dxfattribs=base)
        msp.add_line((x0, y0), (x0 + side, y0), dxfattribs=base)

        ids, boxes = lib_llm_correct.find_line_h_symbol_column_entities(msp)
        self.assertGreaterEqual(len(boxes), 1)
        self.assertGreaterEqual(len(ids), 2)

    def test_promotes_rect_sleeve_column(self):
        """양쪽 플랜지 사각 슬리브(문옆 기둥)를 COLUMN으로 올린다."""
        doc = ezdxf.new()
        msp = doc.modelspace()
        x0, y0 = 70950.0, 300060.0
        _line_rect_sleeve(msp, x0, y0, width=300.0, height=1140.0)

        ids, boxes = lib_llm_correct.find_line_h_symbol_column_entities(msp)
        self.assertGreaterEqual(len(boxes), 1)
        self.assertTrue(
            any(abs(b[0] - (x0 - 20.0)) < 5.0 or abs(b[0] - x0) < 30.0 for b in boxes)
        )

        n = lib_llm_correct.promote_hbeam_columns(msp)
        self.assertGreaterEqual(n, 4)
        marked = lib_llm_correct.mark_doors_and_columns(msp)
        self.assertGreaterEqual(marked["n_column"], 1)
        sides = [
            e
            for e in msp.query("LINE")
            if abs(float(e.dxf.start.x) - float(e.dxf.end.x)) <= 12
            and abs(float(e.dxf.start.y) - float(e.dxf.end.y)) >= 1000
        ]
        self.assertTrue(all(e.dxf.layer == "COLUMN" for e in sides))

    def test_sofa_seat_rectangle_not_sleeve_column(self):
        """소파·의자 좌판(낮고 넓은 사각)은 사각 슬리브 기둥으로 보지 않는다."""
        doc = ezdxf.new()
        msp = doc.modelspace()
        # 의원실 소파 좌판 ≈ 600×250 mm
        _line_rect_sleeve(msp, 0.0, 0.0, width=600.0, height=250.0)
        # 옆 등받이 사각 ≈ 700×750 (거의 정사각)도 슬리브가 아니다.
        _line_rect_sleeve(msp, 800.0, 0.0, width=700.0, height=750.0)

        ids, boxes = lib_llm_correct.find_line_h_symbol_column_entities(msp)
        self.assertEqual(len(boxes), 0)
        self.assertEqual(len(ids), 0)

        n = lib_llm_correct.promote_hbeam_columns(msp)
        self.assertEqual(n, 0)
        for e in msp.query("LINE"):
            self.assertEqual(e.dxf.layer, "BASE")

    def test_chair_grid_not_line_h_columns(self):
        """접객실 의자 격자(H형으로 보이는 사각 열)는 기둥이 아니다."""
        doc = ezdxf.new()
        msp = doc.modelspace()
        base = {"layer": "BASE", "color": 8}
        # 380×360 의자 2×3, 열 간격 500
        for col in range(2):
            for row in range(3):
                x0 = col * 500.0
                y0 = row * 500.0
                x1, y1 = x0 + 380.0, y0 + 360.0
                msp.add_line((x0, y0), (x0, y1), dxfattribs=base)
                msp.add_line((x1, y0), (x1, y1), dxfattribs=base)
                msp.add_line((x0, y0), (x1, y0), dxfattribs=base)
                msp.add_line((x0, y1), (x1, y1), dxfattribs=base)

        ids, boxes = lib_llm_correct.find_line_h_symbol_column_entities(msp)
        self.assertEqual(len(boxes), 0)
        self.assertEqual(len(ids), 0)


if __name__ == "__main__":
    unittest.main()
