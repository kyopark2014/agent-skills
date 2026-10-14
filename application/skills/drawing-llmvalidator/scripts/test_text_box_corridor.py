"""A long line beside a text box is not a corridor wall."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

import ezdxf

sys.path.insert(0, str(Path(__file__).resolve().parent))

import lib_llm_correct as correct


def _promoted(msp):
    segs = correct.iter_axis_segs(msp, min_len_mm=70.0)
    return correct.promote_corridor_walls(segs)


class TextBoxCorridorTests(unittest.TestCase):
    def test_centerline_beside_text_box_stays_base(self):
        doc = ezdxf.new()
        msp = doc.modelspace()
        msp.add_line((0, 0), (8700, 0), dxfattribs={"layer": "BASE"})
        msp.add_lwpolyline(
            [(4000, -415), (7629, -415), (7629, -1215), (4000, -1215)],
            close=True,
            dxfattribs={"layer": "BASE"},
        )
        self.assertEqual(_promoted(msp), [])

    def test_long_double_line_is_a_corridor_wall(self):
        doc = ezdxf.new()
        msp = doc.modelspace()
        msp.add_line((0, 0), (8000, 0), dxfattribs={"layer": "BASE"})
        msp.add_line((0, 200), (8000, 200), dxfattribs={"layer": "BASE"})
        promoted = _promoted(msp)
        self.assertEqual(len(promoted), 2)

    def test_centerline_beside_column_grid_stays_base(self):
        doc = ezdxf.new()
        msp = doc.modelspace()
        msp.add_line((32425, 0), (32425, 10690), dxfattribs={"layer": "BASE"})
        msp.add_lwpolyline(
            [(32305, -2000), (83195, -2000), (83195, 29865), (32305, 29865)],
            close=True,
            dxfattribs={"layer": "BASE"},
        )
        self.assertEqual(_promoted(msp), [])

    def test_grid_does_not_extend_a_room_wall(self):
        doc = ezdxf.new()
        msp = doc.modelspace()
        msp.add_line((32425, -119822), (32425, -109992), dxfattribs={"layer": "WALL"})
        msp.add_line((32425, -109992), (32425, -99302), dxfattribs={"layer": "BASE"})
        msp.add_lwpolyline(
            [(32305, -132367), (83195, -132367), (83195, -100502), (32305, -100502)],
            close=True,
            dxfattribs={"layer": "BASE"},
        )
        segs = correct.iter_axis_segs(msp, min_len_mm=70.0)
        promoted = correct.promote_collinear_room_walls(segs)
        self.assertEqual(promoted, [])


if __name__ == "__main__":
    unittest.main()
