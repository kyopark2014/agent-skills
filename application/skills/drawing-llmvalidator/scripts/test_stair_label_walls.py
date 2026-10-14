"""Stair-room surroundings are walls. The treads stay put."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

import ezdxf

sys.path.insert(0, str(Path(__file__).resolve().parent))

import lib_llm_correct


def _h(msp, x0, x1, y, layer="BASE"):
    return msp.add_line((x0, y), (x1, y), dxfattribs={"layer": layer})


def _v(msp, x, y0, y1, layer="BASE"):
    return msp.add_line((x, y0), (x, y1), dxfattribs={"layer": layer})


class StairLabelWallTests(unittest.TestCase):
    def test_enclosure_becomes_wall_and_treads_stay(self):
        doc = ezdxf.new()
        msp = doc.modelspace()
        msp.add_text("계단실-1", dxfattribs={"insert": (1000, 600), "height": 300, "layer": "BASE"})
        treads = [_h(msp, 0, 2000, y) for y in (0, 300, 600, 900, 1200)]
        ends = [
            _v(msp, 0, 0, 1200),
            _v(msp, -200, 0, 1200),
            _v(msp, 2000, 0, 1200),
            _v(msp, 2200, 0, 1200),
        ]
        outer = [_h(msp, 0, 2000, 1800), _h(msp, 0, 2000, 2000)]
        far = _h(msp, 20000, 22000, 0)

        promoted = lib_llm_correct.promote_stair_label_walls(msp)

        self.assertGreaterEqual(promoted, 6)
        self.assertTrue(all(line.dxf.layer == "BASE" for line in treads))
        self.assertTrue(all(line.dxf.layer == "WALL" for line in ends + outer))
        self.assertEqual(far.dxf.layer, "BASE")


class UpDownStairTests(unittest.TestCase):
    def test_landing_labels_promote_the_flight_and_its_edge(self):
        doc = ezdxf.new()
        msp = doc.modelspace()
        msp.add_text("UP", dxfattribs={"insert": (200, 0), "height": 200, "layer": "BASE"})
        msp.add_text("DN", dxfattribs={"insert": (800, 0), "height": 200, "layer": "BASE"})
        treads = [
            _h(msp, 0, 1600, y)
            for y in range(3200, 3200 + 290 * 8, 290)
        ]
        edge = _h(msp, 0, 2000, 2900)
        far = _h(msp, 20000, 22000, 3200)
        up_only = ezdxf.new()
        up_msp = up_only.modelspace()
        up_msp.add_text("UP", dxfattribs={"insert": (200, 0), "height": 200, "layer": "BASE"})
        up_treads = [_h(up_msp, 0, 1600, y) for y in range(3200, 3200 + 290 * 8, 290)]

        segs = lib_llm_correct._core_segments(msp)
        promoted = lib_llm_correct._promote_stair_runs(msp, segs)
        up_segs = lib_llm_correct._core_segments(up_msp)
        up_promoted = lib_llm_correct._promote_stair_runs(up_msp, up_segs)

        self.assertGreaterEqual(promoted, len(treads))
        self.assertTrue(all(line.dxf.layer == "WALL" for line in treads))
        self.assertEqual(edge.dxf.layer, "WALL")
        self.assertEqual(far.dxf.layer, "BASE")
        self.assertEqual(up_promoted, 0)
        self.assertTrue(all(line.dxf.layer == "BASE" for line in up_treads))


if __name__ == "__main__":
    unittest.main()
