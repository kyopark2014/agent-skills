"""식탁 옆 의자·방석 LINE 격자는 WALL이 아니다."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

import ezdxf

sys.path.insert(0, str(Path(__file__).resolve().parent))

from correct import _rules as R


def _h(msp, x0, x1, y, layer="WALL"):
    return msp.add_line((x0, y), (x1, y), dxfattribs={"layer": layer})


def _v(msp, x, y0, y1, layer="WALL"):
    return msp.add_line((x, y0), (x, y1), dxfattribs={"layer": layer})


class SeatCushionDemoteTests(unittest.TestCase):
    def test_chair_grid_demoted(self):
        doc = ezdxf.new()
        msp = doc.modelspace()
        # 380×400 의자 4개, 열 간격 100 → 동종 변 간격 500
        chairs = []
        for i in range(4):
            y0 = i * 500
            chairs.extend(
                [
                    _h(msp, 0, 380, y0),
                    _h(msp, 0, 380, y0 + 400),
                    _v(msp, 0, y0, y0 + 400),
                    _v(msp, 380, y0, y0 + 400),
                ]
            )
        wall = _h(msp, -2000, 5000, -500)  # 긴 벽은 유지

        demoted = R.demote_seat_cushion_line_boxes(msp)

        self.assertGreaterEqual(len(demoted), len(chairs))
        self.assertTrue(all(id(c) in demoted for c in chairs))
        self.assertNotIn(id(wall), demoted)

    def test_meeting_label_matches_jeopgaeksil(self):
        self.assertTrue(R._MEETING_RE.search("접객실(입식)"))
        self.assertTrue(R._MEETING_RE.search("접견실"))
        self.assertIsNone(R._MEETING_RE.search("회의실"))


if __name__ == "__main__":
    unittest.main()
