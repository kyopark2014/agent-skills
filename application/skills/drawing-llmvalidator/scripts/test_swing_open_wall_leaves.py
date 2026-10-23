"""후반 promote 가 다시 올린 스윙 열린 문짝 WALL → DOOR, 닫힌 현은 유지."""

from __future__ import annotations

import math
import sys
import unittest
from pathlib import Path

import ezdxf

sys.path.insert(0, str(Path(__file__).resolve().parent))

import lib_llm_correct


class SwingOpenWallLeafTests(unittest.TestCase):
    def test_demotes_open_wall_leaf_keeps_closed_chord(self):
        doc = ezdxf.new()
        msp = doc.modelspace()
        hx, hy, r = 1880158.0, -2195627.0, 820.0
        door = {"layer": "DOOR", "color": 3}
        wall = {"layer": "WALL", "color": 1}
        # 1/4 스윙 (180→270). 열린 쪽=아래, 닫힌 쪽=왼쪽.
        msp.add_arc((hx, hy), r, 180.0, 270.0, dxfattribs=door)
        # 열린 문짝: 축 위 WALL + 오프셋 DOOR (후반 promote 가 WALL 을 다시 올린 상태)
        msp.add_line((hx, hy), (hx, hy - r), dxfattribs=wall)
        msp.add_line((hx - 40.0, hy), (hx - 40.0, hy - r), dxfattribs=door)
        # 닫힌 현 WALL
        msp.add_line((hx, hy), (hx - r, hy), dxfattribs=wall)

        n = lib_llm_correct.demote_swing_open_wall_leaves(msp)
        self.assertGreaterEqual(n, 1)

        layers = []
        for e in msp.query("LINE"):
            x0, y0 = float(e.dxf.start.x), float(e.dxf.start.y)
            x1, y1 = float(e.dxf.end.x), float(e.dxf.end.y)
            length = math.hypot(x1 - x0, y1 - y0)
            if abs(length - r) > 5.0:
                continue
            layers.append((e.dxf.layer, round(x0), round(y0), round(x1), round(y1)))

        open_layers = [
            layer for layer, x0, y0, x1, y1 in layers
            if abs(x0 - hx) <= 50 and abs(x1 - hx) <= 50
        ]
        closed = [
            layer for layer, x0, y0, x1, y1 in layers
            if abs(y0 - hy) <= 5 and abs(y1 - hy) <= 5
        ]
        self.assertTrue(open_layers)
        self.assertTrue(all(layer == "DOOR" for layer in open_layers))
        self.assertEqual(closed, ["WALL"])

    def test_all_wall_open_pair_demoted(self):
        """열린 쪽 문짝이 둘 다 WALL 이어도 내린다. 닫힌 현은 남긴다."""
        doc = ezdxf.new()
        msp = doc.modelspace()
        hx, hy, r = 1855084.0, -2189881.0, 960.0
        door = {"layer": "DOOR", "color": 3}
        wall = {"layer": "WALL", "color": 1}
        msp.add_arc((hx, hy), r, 180.0, 269.0, dxfattribs=door)
        msp.add_line((hx - 10.0, hy), (hx - 10.0, hy - r), dxfattribs=wall)
        msp.add_line((hx - 40.0, hy), (hx - 40.0, hy - r), dxfattribs=wall)
        msp.add_line((hx, hy + 24.0), (hx - r, hy + 24.0), dxfattribs=wall)

        n = lib_llm_correct.demote_swing_open_wall_leaves(msp)
        self.assertGreaterEqual(n, 2)
        vertical = []
        horizontal = []
        for e in msp.query("LINE"):
            x0, y0 = float(e.dxf.start.x), float(e.dxf.start.y)
            x1, y1 = float(e.dxf.end.x), float(e.dxf.end.y)
            if abs(x0 - x1) < 1.0:
                vertical.append(e.dxf.layer)
            elif abs(y0 - y1) < 1.0:
                horizontal.append(e.dxf.layer)
        self.assertTrue(all(layer == "DOOR" for layer in vertical))
        self.assertEqual(horizontal, ["WALL"])


if __name__ == "__main__":
    unittest.main()
