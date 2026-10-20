"""원형 WALL ARC·CIRCLE 경계와 원호 개구 잇기."""

from __future__ import annotations

import math
import sys
import unittest
from pathlib import Path

import ezdxf

sys.path.insert(0, str(Path(__file__).resolve().parent))

import evaluate_room as room


def _add_arc(msp, cx, cy, r, start_deg, end_deg, layer="WALL"):
    msp.add_arc((cx, cy), r, start_deg, end_deg, dxfattribs={"layer": layer})


class CurvePartsTests(unittest.TestCase):
    def test_circle_splits_into_chords(self):
        doc = ezdxf.new()
        msp = doc.modelspace()
        msp.add_circle((0, 0), 5000, dxfattribs={"layer": "WALL"})
        e = list(msp.query("CIRCLE"))[0]
        parts = room._curve_parts(e)
        self.assertGreaterEqual(len(parts), 12)
        # 닫힌 고리: 끝점이 이어짐
        ends = {(round(p[2], 1), round(p[3], 1)) for p in parts}
        starts = {(round(p[0], 1), round(p[1], 1)) for p in parts}
        self.assertEqual(starts, ends)

    def test_tiny_circle_is_ignored(self):
        doc = ezdxf.new()
        msp = doc.modelspace()
        msp.add_circle((0, 0), 50, dxfattribs={"layer": "WALL"})
        e = list(msp.query("CIRCLE"))[0]
        self.assertEqual(room._curve_parts(e), [])


class CircularWallRoomTests(unittest.TestCase):
    def test_arc_ring_with_door_gap_closes(self):
        """반지름 5 m 원. 10°(~0.87 m) 개구만 비우고 ARC로 둘러싼다."""
        doc = ezdxf.new()
        msp = doc.modelspace()
        cx, cy, r = 0.0, 0.0, 5000.0
        # 10°..360° 덮개 → 0°..10° 틈 (원호 ~873 mm < 2.4 m)
        _add_arc(msp, cx, cy, r, 10.0, 360.0)
        msp.add_text("원형실", dxfattribs={"layer": "0", "height": 300}).set_placement(
            (0, 0)
        )
        segs = room.wall_segments(msp, [])
        face, _x, _y, _swings = room._room_lines(msp, segs, (0.0, 0.0), [], [])
        self.assertIsNotNone(face)
        expected = math.pi * (r / 1000.0) ** 2
        self.assertAlmostEqual(face.area / 1_000_000.0, expected, delta=8.0)

    def test_wide_arc_gap_stays_open(self):
        """90°(~7.85 m) 개구는 2.4 m를 넘어 닫지 않는다."""
        doc = ezdxf.new()
        msp = doc.modelspace()
        cx, cy, r = 0.0, 0.0, 5000.0
        _add_arc(msp, cx, cy, r, 90.0, 360.0)
        segs = room.wall_segments(msp, [])
        face, _x, _y, _swings = room._room_lines(msp, segs, (0.0, 0.0), [], [])
        self.assertIsNone(face)

    def test_door_swing_arc_is_not_a_wall(self):
        doc = ezdxf.new()
        msp = doc.modelspace()
        # 닫히지 않는 직선 방 + DOOR 스윙만
        msp.add_line((0, 0), (8000, 0), dxfattribs={"layer": "WALL"})
        msp.add_line((8000, 0), (8000, 6000), dxfattribs={"layer": "WALL"})
        msp.add_line((8000, 6000), (0, 6000), dxfattribs={"layer": "WALL"})
        msp.add_line((0, 6000), (0, 0), dxfattribs={"layer": "WALL"})
        before = len(room.wall_segments(msp, []))
        msp.add_arc((2000, 0), 900, 0, 90, dxfattribs={"layer": "DOOR"})
        after = len(room.wall_segments(msp, []))
        self.assertEqual(before, after)


class ArcGapBridgeTests(unittest.TestCase):
    def test_bridge_adds_chord_for_small_gap(self):
        # 20→180 과 190→20(랩). 틈 180→190 (10°, ~873 mm)
        spans = [
            (0.0, 0.0, 5000.0, 20.0, 180.0),
            (0.0, 0.0, 5000.0, 190.0, 20.0),
        ]
        chords = room._bridged_arc_gap_chords(spans)
        self.assertGreaterEqual(len(chords), 1)
        x0, y0, x1, y1 = chords[0]
        self.assertLess(math.hypot(x1 - x0, y1 - y0), 1200.0)


if __name__ == "__main__":
    unittest.main()
