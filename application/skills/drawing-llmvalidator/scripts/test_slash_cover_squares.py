"""Rectangle with a single diagonal slash (/) becomes walls. Full X does not use this path."""

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


class SlashCoverSquareTests(unittest.TestCase):
    def test_rect_with_single_slash_becomes_wall(self):
        """네모 + 대각 / 하나면 대각·네모가 WALL."""
        doc = ezdxf.new()
        msp = doc.modelspace()
        cover = _rect(msp, 0, 0, 1200, 3000)
        slash = msp.add_line((0, 0), (1200, 3000), dxfattribs={"layer": "BASE"})
        far = _rect(msp, 5000, 5000, 6200, 8000)

        promoted = lib_llm_correct.promote_slash_cover_squares(msp)

        self.assertGreaterEqual(promoted, 2)
        self.assertEqual(slash.dxf.layer, "WALL")
        self.assertEqual(cover.dxf.layer, "WALL")
        self.assertEqual(far.dxf.layer, "BASE")

    def test_bare_three_sides_plus_slash_gets_missing_side(self):
        """변 3개 + / 이면 빠진 한 변을 가상으로 긋는다."""
        doc = ezdxf.new()
        msp = doc.modelspace()
        bot = msp.add_line((0, 0), (1000, 0), dxfattribs={"layer": "BASE"})
        right = msp.add_line((1000, 0), (1000, 2500), dxfattribs={"layer": "BASE"})
        top = msp.add_line((1000, 2500), (0, 2500), dxfattribs={"layer": "BASE"})
        # left missing
        slash = msp.add_line((0, 0), (1000, 2500), dxfattribs={"layer": "BASE"})

        promoted = lib_llm_correct.promote_slash_cover_squares(msp)

        self.assertGreaterEqual(promoted, 4)
        for entity in (bot, right, top, slash):
            self.assertEqual(entity.dxf.layer, "WALL")
        lefts = [
            entity
            for entity in msp
            if entity.dxftype() == "LINE"
            and entity.dxf.layer == "WALL"
            and abs(entity.dxf.start.x - entity.dxf.end.x) < 1.0
            and abs((entity.dxf.start.x + entity.dxf.end.x) * 0.5) < 1.0
        ]
        self.assertGreaterEqual(len(lefts), 1)

    def test_full_x_is_not_handled_here(self):
        """교차 X 는 slash 경로에서 올리지 않는다(x_cover 담당)."""
        doc = ezdxf.new()
        msp = doc.modelspace()
        _rect(msp, 0, 0, 900, 800)
        msp.add_line((0, 0), (900, 800), dxfattribs={"layer": "BASE"})
        msp.add_line((900, 0), (0, 800), dxfattribs={"layer": "BASE"})

        promoted = lib_llm_correct.promote_slash_cover_squares(msp)

        self.assertEqual(promoted, 0)

    def test_door_thickness_slash_stays_base(self):
        doc = ezdxf.new()
        msp = doc.modelspace()
        cover = _rect(msp, 0, 0, 900, 150)
        slash = msp.add_line((0, 0), (900, 150), dxfattribs={"layer": "BASE"})

        promoted = lib_llm_correct.promote_slash_cover_squares(msp)

        self.assertEqual(promoted, 0)
        self.assertEqual(cover.dxf.layer, "BASE")
        self.assertEqual(slash.dxf.layer, "BASE")

    def test_stray_diagonal_without_box_stays_base(self):
        """네모 증거 없는 홀로 대각선은 올리지 않는다."""
        doc = ezdxf.new()
        msp = doc.modelspace()
        slash = msp.add_line((0, 0), (2000, 3000), dxfattribs={"layer": "BASE"})

        promoted = lib_llm_correct.promote_slash_cover_squares(msp)

        self.assertEqual(promoted, 0)
        self.assertEqual(slash.dxf.layer, "BASE")


if __name__ == "__main__":
    unittest.main()
