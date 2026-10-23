"""축정렬 DOOR 선 문짝 개구·창 베이 끝 두께 피어 → WALL."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

import ezdxf

sys.path.insert(0, str(Path(__file__).resolve().parent))

import lib_llm_correct


def _rect(msp, x0, y0, x1, y1, *, layer="BASE"):
    return msp.add_lwpolyline(
        [(x0, y0), (x1, y0), (x1, y1), (x0, y1), (x0, y0)],
        close=False,
        dxfattribs={"layer": layer},
    )


class AxisDoorPackEndPierTests(unittest.TestCase):
    def test_far_jamb_after_wall_opening_faces_is_wall(self):
        """바로유 sheet_01형: DOOR 선 + 개구 WALL 면 끝 피어 → WALL."""
        doc = ezdxf.new()
        msp = doc.modelspace()
        y0, y1 = 67013.0, 67216.0
        # 왼쪽 잼(이미 WALL) · 문짝 선 · 개구 면 · 오른쪽 BASE 피어
        _rect(msp, 36396.0, y0, 36459.0, y1, layer="WALL")
        msp.add_line((36459.0, 67064.0), (36459.0, 67216.0), dxfattribs={"layer": "DOOR"})
        msp.add_line((36459.0, y0), (36986.0, y0), dxfattribs={"layer": "WALL"})
        msp.add_line((36459.0, y1), (36986.0, y1), dxfattribs={"layer": "WALL"})
        far = _rect(msp, 36986.0, y0, 37043.0, y1, layer="BASE")
        stub = msp.add_lwpolyline(
            [(36986.0, 67064.0), (36986.0, 67216.0)],
            dxfattribs={"layer": "BASE"},
        )
        # 문과 무관한 피어
        lonely = _rect(msp, 40000.0, y0, 40057.0, y1, layer="BASE")

        n = lib_llm_correct.promote_axis_door_pack_end_piers(msp)

        self.assertGreaterEqual(n, 1)
        self.assertEqual(far.dxf.layer, "WALL")
        self.assertEqual(stub.dxf.layer, "WALL")
        self.assertEqual(lonely.dxf.layer, "BASE")

    def test_window_bay_outer_pier_next_to_door_is_wall(self):
        """문 옆 창 베이 바깥쪽 피어도 WALL."""
        doc = ezdxf.new()
        msp = doc.modelspace()
        y0, y1 = 67013.0, 67216.0
        outer = _rect(msp, 32256.0, y0, 32319.0, y1, layer="BASE")
        msp.add_line((32319.0, y0), (33246.0, y0), dxfattribs={"layer": "WINDOW"})
        msp.add_line((32319.0, y1), (33246.0, y1), dxfattribs={"layer": "WINDOW"})
        _rect(msp, 33246.0, y0, 33310.0, y1, layer="WALL")
        msp.add_line((33310.0, 67064.0), (33310.0, 67216.0), dxfattribs={"layer": "DOOR"})

        n = lib_llm_correct.promote_axis_door_pack_end_piers(msp)

        self.assertGreaterEqual(n, 1)
        self.assertEqual(outer.dxf.layer, "WALL")

    def test_nearby_wall_bay_end_pier_is_wall(self):
        """문 개구 다음 WALL 베이 끝 피어도 WALL."""
        doc = ezdxf.new()
        msp = doc.modelspace()
        y0, y1 = 67013.0, 67216.0
        msp.add_line((36459.0, 67064.0), (36459.0, 67216.0), dxfattribs={"layer": "DOOR"})
        msp.add_line((36459.0, y0), (36986.0, y0), dxfattribs={"layer": "WALL"})
        msp.add_line((36459.0, y1), (36986.0, y1), dxfattribs={"layer": "WALL"})
        _rect(msp, 36986.0, y0, 37043.0, y1, layer="WALL")
        msp.add_line((37043.0, y0), (37450.0, y0), dxfattribs={"layer": "WALL"})
        msp.add_line((37043.0, y1), (37450.0, y1), dxfattribs={"layer": "WALL"})
        end = _rect(msp, 37450.0, y0, 37507.0, y1, layer="BASE")

        n = lib_llm_correct.promote_axis_door_pack_end_piers(msp)

        self.assertGreaterEqual(n, 1)
        self.assertEqual(end.dxf.layer, "WALL")


if __name__ == "__main__":
    unittest.main()
