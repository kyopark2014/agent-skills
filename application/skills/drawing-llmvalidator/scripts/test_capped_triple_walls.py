"""A closed thin rectangle with a center line is a wall."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

import ezdxf

sys.path.insert(0, str(Path(__file__).resolve().parent))

import lib_llm_correct


class CappedTripleWallTests(unittest.TestCase):
    def test_closed_triple_becomes_wall(self):
        doc = ezdxf.new()
        msp = doc.modelspace()
        shell = msp.add_lwpolyline(
            [(0, 0), (1200, 0), (1200, 100), (0, 100)],
            close=True,
            dxfattribs={"layer": "BASE"},
        )
        center = msp.add_line((0, 50), (1200, 50), dxfattribs={"layer": "BASE"})
        plain = msp.add_lwpolyline(
            [(0, 400), (1200, 400), (1200, 500), (0, 500)],
            close=True,
            dxfattribs={"layer": "BASE"},
        )

        promoted = lib_llm_correct.promote_capped_triple_walls(msp)

        self.assertEqual(promoted, 2)
        self.assertEqual(shell.dxf.layer, "WALL")
        self.assertEqual(center.dxf.layer, "WALL")
        self.assertEqual(plain.dxf.layer, "BASE")

    def test_window_center_stays_window(self):
        doc = ezdxf.new()
        msp = doc.modelspace()
        shell = msp.add_lwpolyline(
            [(0, 0), (1000, 0), (1000, 100), (0, 100)],
            close=True,
            dxfattribs={"layer": "BASE"},
        )
        center = msp.add_line((0, 50), (1000, 50), dxfattribs={"layer": "WINDOW"})

        lib_llm_correct.promote_capped_triple_walls(msp)

        self.assertEqual(shell.dxf.layer, "WALL")
        self.assertEqual(center.dxf.layer, "WINDOW")

    def test_polyline_double_rail_center_becomes_wall(self):
        """오송클리닉처럼 2점 폴리 이중 레일도 가운데 선으로 보고 올린다."""
        doc = ezdxf.new()
        msp = doc.modelspace()
        shell = msp.add_lwpolyline(
            [(0, 0), (1633, 0), (1633, 102), (0, 102)],
            close=True,
            dxfattribs={"layer": "BASE"},
        )
        rail_a = msp.add_lwpolyline([(0, 44), (1601, 44)], dxfattribs={"layer": "BASE"})
        rail_b = msp.add_lwpolyline([(0, 63), (1601, 63)], dxfattribs={"layer": "BASE"})

        promoted = lib_llm_correct.promote_capped_triple_walls(msp)

        self.assertGreaterEqual(promoted, 3)
        self.assertEqual(shell.dxf.layer, "WALL")
        self.assertEqual(rail_a.dxf.layer, "WALL")
        self.assertEqual(rail_b.dxf.layer, "WALL")


if __name__ == "__main__":
    unittest.main()
