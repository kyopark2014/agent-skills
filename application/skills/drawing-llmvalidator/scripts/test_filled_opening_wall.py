"""A long double face without door jambs is a wall, not a filled opening."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

import ezdxf

sys.path.insert(0, str(Path(__file__).resolve().parent))

import lib_llm_correct


def _h(msp, x0, x1, y, layer):
    return msp.add_line((x0, y), (x1, y), dxfattribs={"layer": layer})


class FilledOpeningWallTests(unittest.TestCase):
    def test_long_face_without_jambs_stays_wall(self):
        doc = ezdxf.new()
        msp = doc.modelspace()
        outer = _h(msp, 0, 3600, 0, "BASE")
        inner = _h(msp, 0, 3600, 150, "BASE")
        for start in (0, 900, 1800, 2700):
            _h(msp, start, start + 850, 70, "BASE")

        demoted, promoted = lib_llm_correct.correct_filled_opening_doors(msp)

        self.assertEqual(demoted, 0)
        self.assertEqual(promoted, 2)
        self.assertEqual(outer.dxf.layer, "WALL")
        self.assertEqual(inner.dxf.layer, "WALL")

    def test_opening_with_jambs_is_still_a_door(self):
        doc = ezdxf.new()
        msp = doc.modelspace()
        outer = _h(msp, 0, 2000, 0, "WALL")
        inner = _h(msp, 0, 2000, 150, "WALL")
        for start in (0, 500, 1000, 1500):
            _h(msp, start, start + 700, 70, "BASE")
        _h(msp, -800, 0, 30, "BASE")
        _h(msp, -800, 0, 180, "BASE")
        _h(msp, 2000, 2800, 30, "BASE")
        _h(msp, 2000, 2800, 180, "BASE")

        demoted, _promoted = lib_llm_correct.correct_filled_opening_doors(msp)

        self.assertGreaterEqual(demoted, 2)
        self.assertEqual(outer.dxf.layer, "BASE")
        self.assertEqual(inner.dxf.layer, "BASE")


if __name__ == "__main__":
    unittest.main()
