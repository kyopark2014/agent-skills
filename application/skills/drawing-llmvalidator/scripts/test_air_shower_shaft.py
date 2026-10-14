"""AIR SHOWER X는 엘리베이터 샤프트가 아니다. 걸린 긴 벽은 남긴다."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

import ezdxf

sys.path.insert(0, str(Path(__file__).resolve().parent))

import lib_llm_correct


def _air_shower_x(msp, text: str | None):
    # 대각선 약 2.8 m. 문면(아래)은 y=-150.
    msp.add_line((0.0, 0.0), (2000.0, 2000.0), dxfattribs={"layer": "BASE"})
    msp.add_line((0.0, 2000.0), (2000.0, 0.0), dxfattribs={"layer": "BASE"})
    wall = msp.add_line((-8000.0, -150.0), (14000.0, -150.0), dxfattribs={"layer": "WALL"})
    if text:
        msp.add_text(text, dxfattribs={"insert": (1000.0, 1000.0), "height": 200.0, "layer": "BASE"})
    return wall


class AirShowerShaftTests(unittest.TestCase):
    def test_air_shower_x_is_not_a_shaft_and_keeps_the_long_wall(self):
        doc = ezdxf.new()
        msp = doc.modelspace()
        wall = _air_shower_x(msp, "AIR SHOWER")

        self.assertEqual(lib_llm_correct.find_elevator_shafts(msp), [])
        segs = lib_llm_correct.iter_axis_segs(msp, min_len_mm=70.0)
        demoted = lib_llm_correct.demote_elevator_door_back_faces(msp, segs)
        self.assertNotIn(id(wall), demoted)
        self.assertEqual(wall.dxf.layer, "WALL")

    def test_korean_air_shower_label_is_excluded(self):
        doc = ezdxf.new()
        msp = doc.modelspace()
        _air_shower_x(msp, "에어샤워")
        self.assertEqual(lib_llm_correct.find_elevator_shafts(msp), [])

    def test_unlabeled_x_is_still_an_elevator_shaft(self):
        doc = ezdxf.new()
        msp = doc.modelspace()
        wall = _air_shower_x(msp, None)

        shafts = lib_llm_correct.find_elevator_shafts(msp)
        self.assertEqual(len(shafts), 1)
        segs = lib_llm_correct.iter_axis_segs(msp, min_len_mm=70.0)
        demoted = lib_llm_correct.demote_elevator_door_back_faces(msp, segs)
        self.assertIn(id(wall), demoted)


if __name__ == "__main__":
    unittest.main()
