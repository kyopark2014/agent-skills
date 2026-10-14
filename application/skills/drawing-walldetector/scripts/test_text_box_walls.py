"""A centerline beside a text box is not a wall. A real double line still is."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

import ezdxf

sys.path.insert(0, str(Path(__file__).resolve().parent))

from lib_walls import classify_entities


class TextBoxWallTests(unittest.TestCase):
    def test_centerline_beside_text_box_is_not_a_wall(self):
        doc = ezdxf.new()
        msp = doc.modelspace()
        center = msp.add_line(
            (0, 0),
            (8700, 0),
            dxfattribs={"layer": "A01-중심선-아파트"},
        )
        msp.add_lwpolyline(
            [(4000, -415), (7629, -415), (7629, -1215), (4000, -1215)],
            close=True,
            dxfattribs={"layer": "TBOX"},
        )
        face = msp.add_line((0, 10000), (4000, 10000))
        mate = msp.add_line((0, 10200), (4000, 10200))

        classified = classify_entities(list(msp))
        walls = set(classified["wall_entity_idxs"])
        entities = list(msp)
        self.assertNotIn(entities.index(center), walls)
        self.assertIn(entities.index(face), walls)
        self.assertIn(entities.index(mate), walls)

    def test_long_parallel_faces_stay_walls(self):
        doc = ezdxf.new()
        msp = doc.modelspace()
        face = msp.add_line((0, 0), (4000, 0))
        mate = msp.add_line((0, 415), (4000, 415))

        classified = classify_entities(list(msp))
        walls = set(classified["wall_entity_idxs"])
        entities = list(msp)
        self.assertIn(entities.index(face), walls)
        self.assertIn(entities.index(mate), walls)

    def test_column_grid_edge_is_not_a_wall(self):
        doc = ezdxf.new()
        msp = doc.modelspace()
        grid = msp.add_lwpolyline(
            [(0, 0), (50890, 0), (50890, 31865), (0, 31865)],
            close=True,
            dxfattribs={"layer": "XC"},
        )
        msp.add_line((32350, 10000), (32350, 17090), dxfattribs={"layer": "A-창호"})
        entities = list(msp)
        classified = classify_entities(entities)
        keys = classified["wall_keys"]
        self.assertFalse(any(key[0] == entities.index(grid) for key in keys))


if __name__ == "__main__":
    unittest.main()
