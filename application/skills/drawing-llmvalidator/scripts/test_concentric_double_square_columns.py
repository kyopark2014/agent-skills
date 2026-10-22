"""평행 이중선(동심 사각) 기둥 승격."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

import ezdxf

sys.path.insert(0, str(Path(__file__).resolve().parent))

import lib_llm_correct


def _h(msp, x0, x1, y, layer="BASE", color=8):
    return msp.add_line(
        (x0, y), (x1, y), dxfattribs={"layer": layer, "color": color}
    )


def _v(msp, x, y0, y1, layer="BASE", color=8):
    return msp.add_line(
        (x, y0), (x, y1), dxfattribs={"layer": layer, "color": color}
    )


def _add_concentric(msp, x0, y0, outer_w, outer_h, shell):
    """바깥·안쪽 동심 사각 8선. shell=한 쪽 두께."""
    x1, y1 = x0 + outer_w, y0 + outer_h
    ix0, iy0 = x0 + shell, y0 + shell
    ix1, iy1 = x1 - shell, y1 - shell
    outer = [
        _h(msp, x0, x1, y0),
        _h(msp, x0, x1, y1),
        _v(msp, x0, y0, y1),
        _v(msp, x1, y0, y1),
    ]
    inner = [
        _h(msp, ix0, ix1, iy0),
        _h(msp, ix0, ix1, iy1),
        _v(msp, ix0, iy0, iy1),
        _v(msp, ix1, iy0, iy1),
    ]
    return outer, inner, (x0, y0, x1, y1)


class ConcentricDoubleSquareColumnTests(unittest.TestCase):
    def test_concentric_with_wall_stubs_promotes_to_column(self):
        """동심 이중 사각 + 변에 붙은 이중 WALL → WALL → COLUMN."""
        doc = ezdxf.new()
        msp = doc.modelspace()
        outer, inner, (x0, y0, x1, y1) = _add_concentric(
            msp, 1000.0, 2000.0, 1300.0, 1500.0, 150.0
        )
        # 오른쪽·위쪽 이중벽이 바깥 사각에서 이어짐
        _h(msp, x1, x1 + 4000.0, y0 + 500.0, layer="WALL", color=1)
        _h(msp, x1, x1 + 4000.0, y0 + 700.0, layer="WALL", color=1)
        _v(msp, x0 + 200.0, y1, y1 + 4000.0, layer="WALL", color=1)
        _v(msp, x0 + 400.0, y1, y1 + 4000.0, layer="WALL", color=1)

        n = lib_llm_correct.promote_concentric_double_square_columns(msp)
        self.assertEqual(n, 8)
        for ent in outer + inner:
            self.assertEqual(ent.dxf.layer, "WALL")

        marked = lib_llm_correct.mark_doors_and_columns(msp)
        self.assertGreaterEqual(marked["n_column"], 1)
        self.assertTrue(
            any(
                abs(c["bbox_mm"][0] - x0) < 1.0 and abs(c["bbox_mm"][1] - y0) < 1.0
                for c in marked["columns"]
            )
        )
        for ent in outer + inner:
            self.assertEqual(ent.dxf.layer, "COLUMN")

    def test_furniture_double_box_without_wall_stays_base(self):
        """벽에 안 붙은 동심 이중 사각(가구·설비)은 올리지 않는다."""
        doc = ezdxf.new()
        msp = doc.modelspace()
        outer, inner, _box = _add_concentric(msp, 0.0, 0.0, 1200.0, 1200.0, 150.0)
        _h(msp, 5000, 8000, 5000, layer="WALL", color=1)

        n = lib_llm_correct.promote_concentric_double_square_columns(msp)
        self.assertEqual(n, 0)
        for ent in outer + inner:
            self.assertEqual(ent.dxf.layer, "BASE")

    def test_single_empty_square_not_handled_here(self):
        """안쪽 없는 단일 사각은 이 규칙 대상이 아니다."""
        doc = ezdxf.new()
        msp = doc.modelspace()
        x0, y0, side = 1000.0, 2000.0, 600.0
        x1, y1 = x0 + side, y0 + side
        ents = [
            _h(msp, x0, x1, y0),
            _h(msp, x0, x1, y1),
            _v(msp, x0, y0, y1),
            _v(msp, x1, y0, y1),
        ]
        _h(msp, x1, x1 + 200.0, y1, layer="WALL", color=1)
        _h(msp, x1, x1 + 200.0, y1 - 200.0, layer="WALL", color=1)

        n = lib_llm_correct.promote_concentric_double_square_columns(msp)
        self.assertEqual(n, 0)
        for ent in ents:
            self.assertEqual(ent.dxf.layer, "BASE")


if __name__ == "__main__":
    unittest.main()
