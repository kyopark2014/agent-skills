"""Squares covering an X become walls. A thin door X does not."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

import ezdxf

sys.path.insert(0, str(Path(__file__).resolve().parent))

import lib_llm_correct


def _rect(msp, x0, y0, x1, y1, *, closed=True):
    pts = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
    if not closed:
        pts.append((x0, y0))
    entity = msp.add_lwpolyline(pts, dxfattribs={"layer": "BASE"})
    entity.closed = closed
    return entity


class XCoverSquareTests(unittest.TestCase):
    def test_inner_and_outer_squares_become_wall(self):
        doc = ezdxf.new()
        msp = doc.modelspace()
        left = msp.add_line((0, 0), (900, 800), dxfattribs={"layer": "DOOR"})
        right = msp.add_line((900, 0), (0, 800), dxfattribs={"layer": "DOOR"})
        inner = _rect(msp, 0, 0, 900, 800)
        outer = _rect(msp, -100, -100, 1000, 900, closed=False)
        far = _rect(msp, 5000, 5000, 5900, 5800)

        promoted = lib_llm_correct.promote_x_cover_squares(msp)

        self.assertEqual(promoted, 4)
        self.assertEqual(inner.dxf.layer, "WALL")
        self.assertEqual(outer.dxf.layer, "WALL")
        self.assertEqual(far.dxf.layer, "BASE")
        self.assertEqual(left.dxf.layer, "WALL")
        self.assertEqual(right.dxf.layer, "WALL")

    def test_door_thickness_x_square_stays_base(self):
        doc = ezdxf.new()
        msp = doc.modelspace()
        msp.add_line((0, 0), (900, 150), dxfattribs={"layer": "DOOR"})
        msp.add_line((900, 0), (0, 150), dxfattribs={"layer": "DOOR"})
        cover = _rect(msp, 0, 0, 900, 150)

        promoted = lib_llm_correct.promote_x_cover_squares(msp)

        self.assertEqual(promoted, 0)
        self.assertEqual(cover.dxf.layer, "BASE")
        walls = [entity for entity in msp if entity.dxftype() == "LINE" and entity.dxf.layer == "WALL"]
        self.assertEqual(walls, [])

    def test_bare_x_gets_a_virtual_square(self):
        doc = ezdxf.new()
        msp = doc.modelspace()
        left = msp.add_line((0, 0), (900, 800), dxfattribs={"layer": "DOOR"})
        right = msp.add_line((900, 0), (0, 800), dxfattribs={"layer": "DOOR"})

        promoted = lib_llm_correct.promote_x_cover_squares(msp)

        self.assertEqual(promoted, 6)
        self.assertEqual(left.dxf.layer, "WALL")
        self.assertEqual(right.dxf.layer, "WALL")
        walls = [
            (
                (round(entity.dxf.start.x), round(entity.dxf.start.y)),
                (round(entity.dxf.end.x), round(entity.dxf.end.y)),
            )
            for entity in msp
            if entity.dxftype() == "LINE" and entity.dxf.layer == "WALL"
        ]
        self.assertIn(((0, 0), (900, 800)), walls)
        self.assertIn(((900, 0), (0, 800)), walls)
        self.assertIn(((0, 0), (900, 0)), walls)
        self.assertIn(((900, 0), (900, 800)), walls)
        self.assertIn(((900, 800), (0, 800)), walls)
        self.assertIn(((0, 800), (0, 0)), walls)

    def test_long_duct_x_line_box_becomes_wall(self):
        """DA 급배기덕트처럼 긴 X(~5.5 m)와 선 네 개 사각도 올린다."""
        doc = ezdxf.new()
        msp = doc.modelspace()
        diag_a = msp.add_line((0, 0), (5500, 1000), dxfattribs={"layer": "BASE"})
        diag_b = msp.add_line((5500, 0), (0, 1000), dxfattribs={"layer": "BASE"})
        bot = msp.add_line((0, 0), (5500, 0), dxfattribs={"layer": "BASE"})
        top = msp.add_line((0, 1000), (5500, 1000), dxfattribs={"layer": "BASE"})
        left = msp.add_line((0, 0), (0, 1000), dxfattribs={"layer": "BASE"})
        right = msp.add_line((5500, 0), (5500, 1000), dxfattribs={"layer": "BASE"})

        promoted = lib_llm_correct.promote_x_cover_squares(msp)

        self.assertEqual(promoted, 6)
        for entity in (diag_a, diag_b, bot, top, left, right):
            self.assertEqual(entity.dxf.layer, "WALL")

    def test_tall_elev_x_with_float_width_and_poly_edge(self):
        """승강기 옆처럼 짧은 변 ~500 mm(좌표 오차) X와 2점 폴리 변도 올린다."""
        doc = ezdxf.new()
        msp = doc.modelspace()
        # 실제 DXF처럼 짧은 변이 499.999…
        x0, x1, y0, y1 = 0.0, 499.99987, 0.0, 2050.00044
        diag_a = msp.add_line((x0, y0), (x1, y1), dxfattribs={"layer": "BASE"})
        diag_b = msp.add_line((x1, y0), (x0, y1), dxfattribs={"layer": "BASE"})
        bot = msp.add_line((x0, y0), (x1, y0), dxfattribs={"layer": "BASE"})
        top = msp.add_line((x0, y1), (x1, y1), dxfattribs={"layer": "BASE"})
        left = msp.add_line((x0, y0), (x0, y1), dxfattribs={"layer": "BASE"})
        right = msp.add_lwpolyline([(x1, y0), (x1, y1)], dxfattribs={"layer": "BASE"})

        promoted = lib_llm_correct.promote_x_cover_squares(msp)

        self.assertGreaterEqual(promoted, 6)
        for entity in (diag_a, diag_b, bot, top, left, right):
            self.assertEqual(entity.dxf.layer, "WALL")

    def test_zigzag_polyline_x_square_becomes_wall(self):
        """오송클리닉처럼 지그재그 LWPOLYLINE X와 감싼 네모도 올린다."""
        doc = ezdxf.new()
        msp = doc.modelspace()
        # 두 칸 옆 샤프트를 한 폴리선으로 이은 X (실제 sheet_01 형태)
        zig_a = msp.add_lwpolyline(
            [(0, 100), (600, 700), (1200, 100), (1800, 700)],
            dxfattribs={"layer": "BASE"},
        )
        zig_b = msp.add_lwpolyline(
            [(600, 100), (1200, 700), (1800, 100)],
            dxfattribs={"layer": "BASE"},
        )
        left = _rect(msp, 600, 100, 1200, 700)
        right = _rect(msp, 1200, 100, 1800, 700)

        promoted = lib_llm_correct.promote_x_cover_squares(msp)

        self.assertGreaterEqual(promoted, 4)
        self.assertEqual(zig_a.dxf.layer, "WALL")
        self.assertEqual(zig_b.dxf.layer, "WALL")
        self.assertEqual(left.dxf.layer, "WALL")
        self.assertEqual(right.dxf.layer, "WALL")


if __name__ == "__main__":
    unittest.main()
