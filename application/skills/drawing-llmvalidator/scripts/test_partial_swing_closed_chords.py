"""Partially open paired swings close with virtual walls on the closed leaves."""

from __future__ import annotations

import math
import sys
import unittest
from pathlib import Path

import ezdxf

sys.path.insert(0, str(Path(__file__).resolve().parent))

import lib_llm_correct


def _partial_double_door(msp, x: float = 0.0, y_mid: float = 0.0, radius: float = 700.0):
    """Two leaves drawn ~52° open. Closed leaves lie on the wall; open leaves angle out.

    Hinges sit 200 mm apart near the meeting stile. Closed chords run outward to the jambs.
    """
    gap = 200.0
    upper = (x, y_mid + gap * 0.5)
    lower = (x, y_mid - gap * 0.5)
    # Upper: closed up (90°), open ~141.9°
    msp.add_arc(upper, radius, 90.0, 141.9, dxfattribs={"layer": "BASE"})
    msp.add_line(upper, (x, upper[1] + radius), dxfattribs={"layer": "BASE"})
    open_ux = math.cos(math.radians(141.9))
    open_uy = math.sin(math.radians(141.9))
    msp.add_line(
        upper,
        (upper[0] + radius * open_ux, upper[1] + radius * open_uy),
        dxfattribs={"layer": "BASE"},
    )
    # Lower: closed down (-90°), open ~-141.9°
    msp.add_arc(lower, radius, -141.9, -90.0, dxfattribs={"layer": "BASE"})
    msp.add_line(lower, (x, lower[1] - radius), dxfattribs={"layer": "BASE"})
    open_ux = math.cos(math.radians(-141.9))
    open_uy = math.sin(math.radians(-141.9))
    msp.add_line(
        lower,
        (lower[0] + radius * open_ux, lower[1] + radius * open_uy),
        dxfattribs={"layer": "BASE"},
    )
    return upper, lower


class PartialSwingClosedChordTests(unittest.TestCase):
    def test_partial_double_door_promotes_closed_leaves_and_gap(self):
        doc = ezdxf.new()
        msp = doc.modelspace()
        upper, lower = _partial_double_door(msp)

        added = lib_llm_correct.add_swing_closed_chords(msp)

        walls = [
            entity
            for entity in msp
            if entity.dxftype() == "LINE" and entity.dxf.layer == "WALL"
        ]
        self.assertGreaterEqual(added, 3)
        self.assertGreaterEqual(len(walls), 3)

        def _coverage(y0: float, y1: float) -> float:
            lo, hi = sorted((y0, y1))
            span = hi - lo
            if span < 1.0:
                return 1.0
            intervals: list[tuple[float, float]] = []
            for wall in walls:
                a = wall.dxf.start
                b = wall.dxf.end
                if abs(a.x) > 1.0 or abs(b.x) > 1.0:
                    continue
                wlo, whi = sorted((a.y, b.y))
                intervals.append((max(lo, wlo), min(hi, whi)))
            intervals.sort()
            covered = 0.0
            cursor = lo
            for a, b in intervals:
                if b <= cursor:
                    continue
                covered += b - max(a, cursor)
                cursor = max(cursor, b)
            return covered / span

        self.assertGreaterEqual(_coverage(upper[1], upper[1] + 700.0), 0.95)
        self.assertGreaterEqual(_coverage(lower[1] - 700.0, lower[1]), 0.95)
        self.assertGreaterEqual(_coverage(lower[1], upper[1]), 0.95)

    def test_partial_arc_without_leaves_stays_open(self):
        doc = ezdxf.new()
        msp = doc.modelspace()
        msp.add_arc((0.0, 0.0), 700.0, 90.0, 141.9, dxfattribs={"layer": "BASE"})

        added = lib_llm_correct.add_swing_closed_chords(msp)

        self.assertEqual(added, 0)
        walls = [
            entity
            for entity in msp
            if entity.dxftype() == "LINE" and entity.dxf.layer == "WALL"
        ]
        self.assertEqual(walls, [])

    def test_quarter_swing_still_adds_closed_chord(self):
        doc = ezdxf.new()
        msp = doc.modelspace()
        msp.add_arc((0.0, 0.0), 900.0, 0.0, 90.0, dxfattribs={"layer": "BASE"})
        # Closed along +x: jamb at the strike, wall mass on that axis.
        msp.add_line((900.0, -120.0), (900.0, 120.0), dxfattribs={"layer": "BASE"})
        msp.add_line((-2000.0, 0.0), (-80.0, 0.0), dxfattribs={"layer": "WALL", "color": 1})
        msp.add_line((980.0, 0.0), (3000.0, 0.0), dxfattribs={"layer": "WALL", "color": 1})

        added = lib_llm_correct.add_swing_closed_chords(msp)

        self.assertGreaterEqual(added, 1)
        walls = [
            entity
            for entity in msp
            if entity.dxftype() == "LINE" and entity.dxf.layer == "WALL"
        ]
        closed = [
            wall
            for wall in walls
            if abs(wall.dxf.start.y) < 1.0
            and abs(wall.dxf.end.y) < 1.0
            and min(wall.dxf.start.x, wall.dxf.end.x) <= 10.0
            and max(wall.dxf.start.x, wall.dxf.end.x) >= 850.0
        ]
        self.assertTrue(closed)


if __name__ == "__main__":
    unittest.main()
