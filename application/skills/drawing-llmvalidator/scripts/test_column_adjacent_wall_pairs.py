"""기둥 인접 이중선 승격: 다변 열린 LWPOLYLINE 가구 윤곽은 올리지 않는다."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

import ezdxf

sys.path.insert(0, str(Path(__file__).resolve().parent))

import lib_llm_correct


class ColumnAdjacentWallPairsTests(unittest.TestCase):
    def test_skips_open_multi_vertex_furniture_polyline(self):
        """E자 단일선 가구 윤곽이 이중 WALL과 200 mm로 겹쳐도 가구는 BASE 유지."""
        doc = ezdxf.new()
        msp = doc.modelspace()
        # 303호 옷장/단차와 비슷한 열린 E자 LWPOLYLINE (단일선, 평행 이중 아님)
        furn = msp.add_lwpolyline(
            [
                (0.0, 4000.0),
                (0.0, 3300.0),
                (1750.0, 3300.0),
                (1750.0, 2300.0),
                (0.0, 2300.0),
                (0.0, 1770.0),
                (1750.0, 1770.0),
                (1750.0, 770.0),
                (8.0, 770.0),
                (8.0, 70.0),
            ],
            dxfattribs={"layer": "BASE", "color": 8},
        )
        furn.closed = False
        # 가구 오른쪽 세로변(x=1750)과 간격 200 mm인 WALL (이전 오승격 짝)
        msp.add_line(
            (1950.0, 500.0),
            (1950.0, 3500.0),
            dxfattribs={"layer": "WALL", "color": 1},
        )
        col_box = (1700.0, 1400.0, 2300.0, 2000.0)
        n = lib_llm_correct.promote_column_adjacent_wall_pairs(msp, [col_box])
        self.assertEqual(furn.dxf.layer, "BASE")
        self.assertEqual(n, 0)

    def test_promotes_line_double_pair_near_column(self):
        """기둥에 닿는 LINE 이중 평행선은 여전히 WALL로 올린다."""
        doc = ezdxf.new()
        msp = doc.modelspace()
        a = msp.add_line(
            (0.0, 0.0),
            (0.0, 1500.0),
            dxfattribs={"layer": "BASE", "color": 8},
        )
        b = msp.add_line(
            (200.0, 0.0),
            (200.0, 1500.0),
            dxfattribs={"layer": "BASE", "color": 8},
        )
        col_box = (-100.0, 400.0, 500.0, 1000.0)
        n = lib_llm_correct.promote_column_adjacent_wall_pairs(msp, [col_box])
        self.assertEqual(n, 2)
        self.assertEqual(a.dxf.layer, "WALL")
        self.assertEqual(b.dxf.layer, "WALL")

    def test_skips_closed_furniture_box_near_column(self):
        """닫힌 가구 사각은 기둥 이중벽에 한 변이 닿아도 BASE 유지."""
        doc = ezdxf.new()
        msp = doc.modelspace()
        furn = msp.add_lwpolyline(
            [
                (0.0, 0.0),
                (1500.0, 0.0),
                (1500.0, 2000.0),
                (0.0, 2000.0),
            ],
            close=True,
            dxfattribs={"layer": "BASE", "color": 8},
        )
        # 가구 오른쪽과 200 mm 간격 WALL + 기둥 박스
        msp.add_line(
            (1700.0, -200.0),
            (1700.0, 2200.0),
            dxfattribs={"layer": "WALL", "color": 1},
        )
        col_box = (1450.0, 800.0, 1950.0, 1400.0)
        n = lib_llm_correct.promote_column_adjacent_wall_pairs(msp, [col_box])
        self.assertEqual(furn.dxf.layer, "BASE")
        self.assertEqual(n, 0)

    def test_promotes_double_between_round_columns(self):
        """원형 기둥 사이를 잇는 이중 세로면도 WALL로 올린다."""
        doc = ezdxf.new()
        msp = doc.modelspace()
        # 위·아래 원형 기둥 — 인식용 이중 WALL은 가로로만 붙여 세로면과 겹치지 않게 한다.
        for cy in (0.0, 2000.0):
            msp.add_circle((0.0, cy), 225.0, dxfattribs={"layer": "BASE", "color": 8})
            msp.add_circle((0.0, cy), 325.0, dxfattribs={"layer": "BASE", "color": 8})
            msp.add_line(
                (325.0, cy - 100.0),
                (4000.0, cy - 100.0),
                dxfattribs={"layer": "WALL", "color": 1},
            )
            msp.add_line(
                (325.0, cy + 100.0),
                (4000.0, cy + 100.0),
                dxfattribs={"layer": "WALL", "color": 1},
            )
        # 기둥 사이를 잇는 이중 세로면 (방풍실 우측)
        a = msp.add_line(
            (-75.0, 325.0),
            (-75.0, 1675.0),
            dxfattribs={"layer": "BASE", "color": 8},
        )
        b = msp.add_line(
            (75.0, 325.0),
            (75.0, 1675.0),
            dxfattribs={"layer": "BASE", "color": 8},
        )
        lib_llm_correct.promote_round_columns(msp)
        round_boxes = [
            (cx - r_out, cy - r_out, cx + r_out, cy + r_out)
            for cx, cy, _ri, r_out, _ents in lib_llm_correct.find_round_column_rings(
                msp
            )
        ]
        self.assertGreaterEqual(len(round_boxes), 2)
        n = lib_llm_correct.promote_column_adjacent_wall_pairs(msp, round_boxes)
        self.assertGreaterEqual(n, 2)
        self.assertEqual(a.dxf.layer, "WALL")
        self.assertEqual(b.dxf.layer, "WALL")


if __name__ == "__main__":
    unittest.main()
