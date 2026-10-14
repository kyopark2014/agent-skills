"""방풍실처럼 면적에서 빠지는 실의 기울어진 외곽은 벽이다."""

from __future__ import annotations

import math
import sys
import unittest
from pathlib import Path

import ezdxf

sys.path.insert(0, str(Path(__file__).resolve().parent))

import lib_llm_correct


def _rect(msp, angle, width, height, gap, layer):
    ux, uy = math.cos(angle), math.sin(angle)
    nx, ny = -uy, ux
    hw, hh = width * 0.5, height * 0.5
    corners = [
        (-hw * ux - hh * nx, -hw * uy - hh * ny),
        (hw * ux - hh * nx, hw * uy - hh * ny),
        (hw * ux + hh * nx, hw * uy + hh * ny),
        (-hw * ux + hh * nx, -hw * uy + hh * ny),
    ]
    lines = []
    for start, end in zip(corners, corners[1:] + corners[:1]):
        lines.append(msp.add_line(start, end, dxfattribs={"layer": layer}))
    lines.append(msp.add_line(
        (corners[0][0] + nx * gap, corners[0][1] + ny * gap),
        (corners[1][0] + nx * gap, corners[1][1] + ny * gap),
        dxfattribs={"layer": layer},
    ))
    return lines


class ExcludedAreaWallTests(unittest.TestCase):
    def test_tilted_vestibule_outline_becomes_wall(self):
        doc = ezdxf.new()
        msp = doc.modelspace()
        room_lines = _rect(msp, math.radians(10.0), 3000.0, 2500.0, 150.0, "BASE")
        msp.add_text("방풍실-1", dxfattribs={"insert": (0.0, 0.0), "height": 300.0, "layer": "BASE"})
        other = msp.add_line((8000.0, -1000.0), (11000.0, -1000.0), dxfattribs={"layer": "BASE"})
        msp.add_text("교실", dxfattribs={"insert": (9500.0, 0.0), "height": 300.0, "layer": "BASE"})

        promoted = lib_llm_correct.promote_nonarea_enclosures(msp)

        self.assertGreaterEqual(promoted, 5)
        self.assertTrue(all(line.dxf.layer == "WALL" for line in room_lines))
        self.assertEqual(other.dxf.layer, "BASE")


if __name__ == "__main__":
    unittest.main()
