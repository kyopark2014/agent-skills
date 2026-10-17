"""닫힌 빈 정사각 폴리라인 기둥(클럽하우스 주방 모서리) 승격."""

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


class ClosedSquareColumnTests(unittest.TestCase):
    def test_empty_closed_square_with_wall_stubs_promotes(self):
        """속이 빈 600 mm 닫힌 사각 + 변에 붙은 짧은 이중 WALL → WALL."""
        doc = ezdxf.new()
        msp = doc.modelspace()
        x0, y0, side = 1000.0, 2000.0, 600.0
        x1, y1 = x0 + side, y0 + side
        outline = msp.add_lwpolyline(
            [(x0, y0), (x1, y0), (x1, y1), (x0, y1)],
            close=True,
            dxfattribs={"layer": "BASE", "color": 8},
        )
        # 오른쪽 변에서 밖으로 나가는 이중 스터브(클럽하우스 주방)
        _h(msp, x1, x1 + 200.0, y1)
        _h(msp, x1, x1 + 200.0, y1 - 200.0)
        # 왼쪽 변에서 위로 이어지는 WALL
        _v(msp, x0, y1, y1 + 700.0)

        n = lib_llm_correct.promote_closed_square_columns(msp)
        self.assertEqual(n, 1)
        self.assertEqual(outline.dxf.layer, "WALL")

        marked = lib_llm_correct.mark_doors_and_columns(msp)
        self.assertEqual(outline.dxf.layer, "COLUMN")
        self.assertGreaterEqual(marked["n_column"], 1)
        self.assertTrue(
            any(
                abs(c["bbox_mm"][0] - x0) < 1.0 and abs(c["bbox_mm"][1] - y0) < 1.0
                for c in marked["columns"]
            )
        )

    def test_nearly_closed_polyline_promotes(self):
        """닫힘 플래그 없이 첫·끝점이 같은 사각도 올린다."""
        doc = ezdxf.new()
        msp = doc.modelspace()
        x0, y0, side = 3000.0, 4000.0, 600.0
        x1, y1 = x0 + side, y0 + side
        outline = msp.add_lwpolyline(
            [(x1, y0), (x1, y1), (x0, y1), (x0, y0), (x1, y0)],
            close=False,
            dxfattribs={"layer": "BASE", "color": 8},
        )
        _v(msp, x0 + 200.0, y0 - 3000.0, y0)
        _h(msp, x1, x1 + 200.0, y1)
        _h(msp, x1, x1 + 200.0, y0 + 200.0)

        n = lib_llm_correct.promote_closed_square_columns(msp)
        self.assertEqual(n, 1)
        self.assertEqual(outline.dxf.layer, "WALL")

    def test_furniture_square_without_wall_abutment_stays_base(self):
        """벽에 안 붙은 닫힌 사각(가구)은 올리지 않는다."""
        doc = ezdxf.new()
        msp = doc.modelspace()
        outline = msp.add_lwpolyline(
            [(0, 0), (600, 0), (600, 600), (0, 600)],
            close=True,
            dxfattribs={"layer": "BASE", "color": 8},
        )
        _h(msp, 2000, 5000, 2000)

        n = lib_llm_correct.promote_closed_square_columns(msp)
        self.assertEqual(n, 0)
        self.assertEqual(outline.dxf.layer, "BASE")


if __name__ == "__main__":
    unittest.main()
