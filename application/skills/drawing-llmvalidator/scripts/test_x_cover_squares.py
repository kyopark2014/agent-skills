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

        self.assertEqual(promoted, 2)
        self.assertEqual(inner.dxf.layer, "WALL")
        self.assertEqual(outer.dxf.layer, "WALL")
        self.assertEqual(far.dxf.layer, "BASE")
        self.assertEqual(left.dxf.layer, "DOOR")
        self.assertEqual(right.dxf.layer, "DOOR")

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

        self.assertEqual(promoted, 4)
        self.assertEqual(left.dxf.layer, "DOOR")
        self.assertEqual(right.dxf.layer, "DOOR")
        walls = [
            (
                (round(entity.dxf.start.x), round(entity.dxf.start.y)),
                (round(entity.dxf.end.x), round(entity.dxf.end.y)),
            )
            for entity in msp
            if entity.dxftype() == "LINE" and entity.dxf.layer == "WALL"
        ]
        self.assertEqual(
            walls,
            [
                ((0, 0), (900, 0)),
                ((900, 0), (900, 800)),
                ((900, 800), (0, 800)),
                ((0, 800), (0, 0)),
            ],
        )


if __name__ == "__main__":
    unittest.main()
