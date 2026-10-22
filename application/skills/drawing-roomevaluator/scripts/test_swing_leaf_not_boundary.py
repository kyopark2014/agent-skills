"""스윙 문짝 LINE은 close 모드에서 실 경계를 가르지 않는다."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

import ezdxf

sys.path.insert(0, str(Path(__file__).resolve().parent))

import evaluate_room as room


class SwingLeafNotBoundaryTests(unittest.TestCase):
    def test_protruding_leaf_excluded_wall_chord_kept(self):
        doc = ezdxf.new()
        msp = doc.modelspace()
        # 방: (0,0)-(6000,4000). 아래벽 y=0 에 양개문 힌지 2000, 3710 (r=855).
        msp.add_line((0, 0), (2000, 0), dxfattribs={"layer": "WALL"})
        msp.add_line((3710, 0), (6000, 0), dxfattribs={"layer": "WALL"})
        msp.add_line((0, 0), (0, 4000), dxfattribs={"layer": "WALL"})
        msp.add_line((6000, 0), (6000, 4000), dxfattribs={"layer": "WALL"})
        msp.add_line((0, 4000), (6000, 4000), dxfattribs={"layer": "WALL"})
        # 개구를 막는 벽 현 (힌지~힌지).
        chord = msp.add_line((2000, 0), (3710, 0), dxfattribs={"layer": "WALL"})
        # 실 안으로 튀어나온 문짝 + 스윙.
        leaf = msp.add_line((2000, 0), (2000, 855), dxfattribs={"layer": "DOOR"})
        wall_leaf = msp.add_line((3710, 0), (3710, 855), dxfattribs={"layer": "WALL"})
        msp.add_arc((2000, 0), 855, 0, 90, dxfattribs={"layer": "DOOR"})
        msp.add_arc((3710, 0), 855, 90, 180, dxfattribs={"layer": "DOOR"})

        segs = room.wall_segments(msp, [])
        self.assertFalse(
            any(
                abs(s[0] - 2000) < 1 and abs(s[2] - 2000) < 1 and max(s[1], s[3]) > 400
                for s in segs
            ),
            "DOOR leaf into room must not be a boundary",
        )
        self.assertFalse(
            any(
                abs(s[0] - 3710) < 1 and abs(s[2] - 3710) < 1 and max(s[1], s[3]) > 400
                for s in segs
            ),
            "WALL-mispromoted leaf into room must not be a boundary",
        )
        self.assertTrue(
            any(
                min(s[0], s[2]) <= 2000 + 1
                and max(s[0], s[2]) >= 3710 - 1
                and abs((s[1] + s[3]) * 0.5) <= 1
                for s in segs
            ),
            "wall chord across opening must remain",
        )
        _ = leaf, wall_leaf, chord

        face, *_rest = room._room_lines(msp, segs, (3000, 2000), [], [])
        self.assertIsNotNone(face)
        # 문짝에서 잘리지 않고 아래 벽(y=0)까지 닫힌 면.
        self.assertLessEqual(face.bounds[1], 50.0)
        self.assertGreater(face.area / 1e6, 20.0)


if __name__ == "__main__":
    unittest.main()
