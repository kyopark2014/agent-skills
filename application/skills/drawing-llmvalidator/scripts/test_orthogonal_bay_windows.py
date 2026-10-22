"""Orthogonal bay returns + outer face close the room boundary."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

import ezdxf

sys.path.insert(0, str(Path(__file__).resolve().parent))

import lib_llm_correct


class OrthogonalBayWindowTests(unittest.TestCase):
    def test_promotes_returns_and_outer_face(self):
        doc = ezdxf.new()
        msp = doc.modelspace()
        # Wall line y=0 with flanking windows; bay depth 600 to y=600.
        msp.add_line((0.0, 0.0), (1900.0, 0.0), dxfattribs={"layer": "WINDOW", "color": 4})
        msp.add_line((5000.0, 0.0), (6900.0, 0.0), dxfattribs={"layer": "WINDOW", "color": 4})
        left_return = msp.add_line(
            (1900.0, 0.0), (1900.0, 600.0), dxfattribs={"layer": "BASE", "color": 8}
        )
        right_return = msp.add_line(
            (5000.0, 0.0), (5000.0, 600.0), dxfattribs={"layer": "BASE", "color": 8}
        )
        # Double-wall mates (150 mm).
        left_mate = msp.add_line(
            (2050.0, 0.0), (2050.0, 600.0), dxfattribs={"layer": "BASE", "color": 8}
        )
        right_mate = msp.add_line(
            (4850.0, 0.0), (4850.0, 600.0), dxfattribs={"layer": "BASE", "color": 8}
        )
        outer_a = msp.add_line(
            (2050.0, 600.0), (3400.0, 600.0), dxfattribs={"layer": "BASE", "color": 8}
        )
        outer_b = msp.add_line(
            (3500.0, 600.0), (4850.0, 600.0), dxfattribs={"layer": "BASE", "color": 8}
        )
        # Unrelated base elsewhere must stay BASE.
        other = msp.add_line(
            (8000.0, 0.0), (8000.0, 600.0), dxfattribs={"layer": "BASE", "color": 8}
        )

        n = lib_llm_correct.promote_orthogonal_bay_windows(msp)
        self.assertGreaterEqual(n, 6)
        for ent in (left_return, right_return, left_mate, right_mate, outer_a, outer_b):
            self.assertEqual(ent.dxf.layer, "WALL")
        self.assertEqual(other.dxf.layer, "BASE")

    def test_skips_when_outer_face_missing(self):
        doc = ezdxf.new()
        msp = doc.modelspace()
        msp.add_line((0.0, 0.0), (1900.0, 0.0), dxfattribs={"layer": "WINDOW", "color": 4})
        msp.add_line((5000.0, 0.0), (6900.0, 0.0), dxfattribs={"layer": "WINDOW", "color": 4})
        left = msp.add_line(
            (1900.0, 0.0), (1900.0, 600.0), dxfattribs={"layer": "BASE", "color": 8}
        )
        right = msp.add_line(
            (5000.0, 0.0), (5000.0, 600.0), dxfattribs={"layer": "BASE", "color": 8}
        )
        n = lib_llm_correct.promote_orthogonal_bay_windows(msp)
        self.assertEqual(n, 0)
        self.assertEqual(left.dxf.layer, "BASE")
        self.assertEqual(right.dxf.layer, "BASE")


if __name__ == "__main__":
    unittest.main()
