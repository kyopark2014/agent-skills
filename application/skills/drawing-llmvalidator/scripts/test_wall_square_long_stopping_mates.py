"""모서리 회색 사각 + 긴(>4.5 m) 이중벽 끊김 → 벽면 정사각 기둥."""

from __future__ import annotations

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


class WallSquareLongStoppingMatesTests(unittest.TestCase):
    def test_corner_square_with_long_double_wall_promotes(self):
        """GSE 전산/통신실 좌상단: 600 사각 + 5 m/4.8 m 이중면이 하단에서 끊김."""
        doc = ezdxf.new()
        msp = doc.modelspace()
        x0, y0, side = 1000.0, 5000.0, 600.0
        x1, y1 = x0 + side, y0 + side
        # 회색 정사각 네 변
        bottom = _h(msp, x0, x1, y0, layer="BASE", color=8)
        top = _h(msp, x0, x1, y1, layer="BASE", color=8)
        left = _v(msp, x0, y0, y1, layer="BASE", color=8)
        right = _v(msp, x1, y0, y1, layer="BASE", color=8)
        # 바깥면 BASE 5 m, 안쪽면 WALL 4.8 m — 예전 4500 mm 상한에 걸렸던 패턴
        _v(msp, x0, y0 - 5000.0, y0, layer="BASE", color=8)
        _v(msp, x0 + 200.0, y0 - 4800.0, y0, layer="WALL", color=1)

        n = lib_llm_correct.promote_wall_square_columns(msp)
        self.assertGreaterEqual(n, 4)
        for ent in (bottom, top, left, right):
            self.assertEqual(ent.dxf.layer, "WALL")

        marked = lib_llm_correct.mark_doors_and_columns(msp)
        self.assertTrue(
            any(
                abs(c["bbox_mm"][0] - x0) < 1.0 and abs(c["bbox_mm"][1] - y0) < 1.0
                for c in marked["columns"]
            ),
            marked["columns"],
        )
        for ent in (bottom, top, left, right):
            self.assertEqual(ent.dxf.layer, "COLUMN")


if __name__ == "__main__":
    unittest.main()
