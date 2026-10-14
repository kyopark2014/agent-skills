"""Two meeting swings close the opening with one virtual wall."""

from __future__ import annotations

import math
import sys
import unittest
from pathlib import Path

import ezdxf

sys.path.insert(0, str(Path(__file__).resolve().parent))

import lib_llm_correct


def _meeting_arcs(msp, offset: float = 30.0):
    """Hinges 1800 mm apart. The arcs meet near the middle, off the hinge line."""
    radius = 900.0
    meet = (offset, 900.0)
    upper = (0.0, 1800.0)
    lower = (0.0, 0.0)
    upper_meet = math.degrees(math.atan2(meet[1] - upper[1], meet[0] - upper[0])) % 360.0
    lower_meet = math.degrees(math.atan2(meet[1] - lower[1], meet[0] - lower[0])) % 360.0
    msp.add_arc(upper, radius, upper_meet, 0.0, dxfattribs={"layer": "DOOR"})
    msp.add_arc(lower, radius, 0.0, lower_meet, dxfattribs={"layer": "DOOR"})


class PairedSwingWallTests(unittest.TestCase):
    def test_diagonal_pair_closes_between_hinges(self):
        doc = ezdxf.new()
        msp = doc.modelspace()
        _meeting_arcs(msp)

        added = lib_llm_correct.add_paired_swing_walls(msp)

        walls = [
            entity for entity in msp
            if entity.dxftype() == "LINE" and entity.dxf.layer == "WALL"
        ]
        self.assertEqual(added, 1)
        self.assertEqual(len(walls), 1)
        ends = {
            (round(walls[0].dxf.start.x), round(walls[0].dxf.start.y)),
            (round(walls[0].dxf.end.x), round(walls[0].dxf.end.y)),
        }
        self.assertEqual(ends, {(0, 0), (0, 1800)})

    def test_single_swing_stays_open(self):
        doc = ezdxf.new()
        msp = doc.modelspace()
        msp.add_arc((0.0, 0.0), 900.0, 0.0, 90.0, dxfattribs={"layer": "DOOR"})

        added = lib_llm_correct.add_paired_swing_walls(msp)

        self.assertEqual(added, 0)

    def test_existing_wall_is_not_drawn_again(self):
        doc = ezdxf.new()
        msp = doc.modelspace()
        _meeting_arcs(msp)
        msp.add_line((0.0, 0.0), (0.0, 1800.0), dxfattribs={"layer": "WALL", "color": 1})

        added = lib_llm_correct.add_paired_swing_walls(msp)

        self.assertEqual(added, 0)

    def test_swings_that_do_not_meet_stay_open(self):
        doc = ezdxf.new()
        msp = doc.modelspace()
        msp.add_arc((0.0, 1800.0), 900.0, 90.0, 180.0, dxfattribs={"layer": "DOOR"})
        msp.add_arc((0.0, 0.0), 900.0, 270.0, 360.0, dxfattribs={"layer": "DOOR"})

        added = lib_llm_correct.add_paired_swing_walls(msp)

        self.assertEqual(added, 0)


if __name__ == "__main__":
    unittest.main()
