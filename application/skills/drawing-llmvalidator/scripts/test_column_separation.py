"""기둥 간 최소 거리 — 맞닿은·가까운 사각은 COLUMN 제외."""

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


def _square(msp, x0, y0, w, h, layer="WALL"):
    x1, y1 = x0 + w, y0 + h
    return [
        _h(msp, x0, x1, y0, layer=layer),
        _h(msp, x0, x1, y1, layer=layer),
        _v(msp, x0, y0, y1, layer=layer),
        _v(msp, x1, y0, y1, layer=layer),
    ]


class ColumnSeparationTests(unittest.TestCase):
    def test_touching_equal_squares_both_demoted(self):
        """맞닿은 같은 크기 사각 둘은 둘 다 COLUMN이 아니다."""
        doc = ezdxf.new()
        msp = doc.modelspace()
        w = h = 600.0
        _square(msp, 0.0, 0.0, w, h)
        _square(msp, 0.0, h, w, h)  # 맞닿음, 중심 거리 600 mm
        # 멀리 떨어진 외벽(승격 조건용은 아님 — 이미 WALL)
        _h(msp, 10000, 15000, 0, layer="WALL")

        marked = lib_llm_correct.mark_doors_and_columns(msp)
        self.assertEqual(marked["n_column"], 0)
        for ent in msp:
            if ent.dxftype() == "LINE" and ent.dxf.layer == "COLUMN":
                self.fail("close squares must not stay COLUMN")

    def test_isolated_square_stays_column(self):
        """혼자 떨어진 정사각 WALL 윤곽은 COLUMN으로 남는다."""
        doc = ezdxf.new()
        msp = doc.modelspace()
        ents = _square(msp, 1000.0, 2000.0, 600.0, 600.0)
        marked = lib_llm_correct.mark_doors_and_columns(msp)
        self.assertGreaterEqual(marked["n_column"], 1)
        self.assertTrue(any(e.dxf.layer == "COLUMN" for e in ents))

    def test_well_spaced_pair_both_kept(self):
        """8 m 떨어진 같은 크기 사각 둘은 둘 다 COLUMN."""
        doc = ezdxf.new()
        msp = doc.modelspace()
        a = _square(msp, 0.0, 0.0, 600.0, 600.0)
        b = _square(msp, 8000.0, 0.0, 600.0, 600.0)
        marked = lib_llm_correct.mark_doors_and_columns(msp)
        self.assertGreaterEqual(marked["n_column"], 2)
        self.assertTrue(all(e.dxf.layer == "COLUMN" for e in a + b))

    def test_filter_helper_demotes_smaller(self):
        cols = [
            {"bbox_mm": [0.0, 0.0, 600.0, 600.0]},
            {"bbox_mm": [700.0, 0.0, 1000.0, 300.0]},  # 더 작고 가까움
        ]
        kept, dropped = lib_llm_correct._filter_columns_by_separation(cols)
        self.assertEqual(len(kept), 1)
        self.assertEqual(kept[0]["bbox_mm"][2], 600.0)
        self.assertEqual(len(dropped), 1)

    def test_reconcile_removes_orphan_column_lines(self):
        """목록에 없는 COLUMN 잔여 선은 WALL로 돌아간다."""
        doc = ezdxf.new()
        msp = doc.modelspace()
        keep = _square(msp, 0.0, 0.0, 600.0, 600.0)
        orphan = _square(msp, 5000.0, 5000.0, 300.0, 300.0)
        for e in keep + orphan:
            e.dxf.layer = "COLUMN"
            e.dxf.color = 5
        # keep 만 목록에 있음
        n = lib_llm_correct._reconcile_column_layer(
            msp, [{"bbox_mm": [0.0, 0.0, 600.0, 600.0]}]
        )
        self.assertGreaterEqual(n, 4)
        self.assertTrue(all(e.dxf.layer == "COLUMN" for e in keep))
        self.assertTrue(all(e.dxf.layer == "WALL" for e in orphan))


if __name__ == "__main__":
    unittest.main()
