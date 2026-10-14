"""A thin return between two wall ends is a wall. A dimension line across the room is not."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

import ezdxf

sys.path.insert(0, str(Path(__file__).resolve().parent))

import lib_llm_correct


class WallEndReturnTests(unittest.TestCase):
    def test_return_between_wall_ends_becomes_wall(self):
        doc = ezdxf.new()
        msp = doc.modelspace()
        msp.add_line((0, 0), (3000, 0), dxfattribs={"layer": "WALL"})
        msp.add_line((0, 2100), (3000, 2100), dxfattribs={"layer": "WALL"})
        msp.add_line((0, 0), (0, 2100), dxfattribs={"layer": "WALL"})
        shell = msp.add_lwpolyline(
            [(3000, 100), (3100, 100), (3100, 840), (3000, 840)],
            close=True,
            dxfattribs={"layer": "BASE"},
        )
        filler = msp.add_line((3000, 870), (3000, 2100), dxfattribs={"layer": "BASE"})
        dimension = msp.add_line((0, 1000), (3000, 1000), dxfattribs={"layer": "BASE"})

        promoted = lib_llm_correct.promote_wall_end_returns(msp)

        self.assertEqual(promoted, 2)
        self.assertEqual(shell.dxf.layer, "WALL")
        self.assertEqual(filler.dxf.layer, "WALL")
        self.assertEqual(dimension.dxf.layer, "BASE")

    def test_line_alone_does_not_close_the_gap(self):
        doc = ezdxf.new()
        msp = doc.modelspace()
        msp.add_line((0, 0), (3000, 0), dxfattribs={"layer": "WALL"})
        msp.add_line((0, 2100), (3000, 2100), dxfattribs={"layer": "WALL"})
        filler = msp.add_line((3000, 0), (3000, 2100), dxfattribs={"layer": "BASE"})

        promoted = lib_llm_correct.promote_wall_end_returns(msp)

        self.assertEqual(promoted, 0)
        self.assertEqual(filler.dxf.layer, "BASE")

    def test_full_span_dimension_band_stays_base(self):
        doc = ezdxf.new()
        msp = doc.modelspace()
        msp.add_line((0, 0), (0, 2100), dxfattribs={"layer": "WALL"})
        msp.add_line((2100, 0), (2100, 2100), dxfattribs={"layer": "WALL"})
        band = msp.add_lwpolyline(
            [(0, 2100), (2100, 2100), (2100, 2170), (0, 2170)],
            close=True,
            dxfattribs={"layer": "BASE"},
        )

        promoted = lib_llm_correct.promote_wall_end_returns(msp)

        self.assertEqual(promoted, 0)
        self.assertEqual(band.dxf.layer, "BASE")


if __name__ == "__main__":
    unittest.main()
