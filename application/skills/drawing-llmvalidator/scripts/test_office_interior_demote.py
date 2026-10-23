"""사무실 글자 안쪽 의자만 demote, 칸막이·외곽 WALL은 유지."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

import ezdxf

sys.path.insert(0, str(Path(__file__).resolve().parent))

import lib_llm_correct


def _office_room(msp):
    wall = {"layer": "WALL"}
    outer = [
        msp.add_line((0, 0), (10000, 0), dxfattribs=wall),
        msp.add_line((10000, 0), (10000, 8000), dxfattribs=wall),
        msp.add_line((10000, 8000), (0, 8000), dxfattribs=wall),
        msp.add_line((0, 8000), (0, 0), dxfattribs=wall),
    ]
    # 미팅룸 바닥처럼 상자 안쪽에 있는 칸막이 — 지우면 안 됨.
    partition = msp.add_line((2000, 6000), (8000, 6000), dxfattribs=wall)
    chair = msp.add_circle((4000, 3500), 200.0, dxfattribs=wall)
    arc = msp.add_arc((6000, 3500), 200.0, 110.0, -110.0, dxfattribs=wall)
    msp.add_text("사무실", dxfattribs={"height": 300, "insert": (5000, 4000)})
    return outer, partition, chair, arc


class OfficeInteriorDemoteTests(unittest.TestCase):
    def test_office_demotes_chairs_keeps_outline_and_partition(self):
        doc = ezdxf.new()
        outer, partition, chair, arc = _office_room(doc.modelspace())
        segs = lib_llm_correct.iter_axis_segs(doc.modelspace(), min_len_mm=200.0)
        demoted = lib_llm_correct.demote_office_interiors(doc.modelspace(), segs)
        self.assertIn(id(chair), demoted)
        self.assertIn(id(arc), demoted)
        self.assertNotIn(id(partition), demoted)
        self.assertTrue(all(id(line) not in demoted for line in outer))

    def test_non_office_label_keeps_chairs(self):
        doc = ezdxf.new()
        msp = doc.modelspace()
        wall = {"layer": "WALL"}
        for a, b in (
            ((0, 0), (10000, 0)),
            ((10000, 0), (10000, 8000)),
            ((10000, 8000), (0, 8000)),
            ((0, 8000), (0, 0)),
        ):
            msp.add_line(a, b, dxfattribs=wall)
        chair = msp.add_circle((4000, 3500), 200.0, dxfattribs=wall)
        msp.add_text("창고", dxfattribs={"height": 300, "insert": (5000, 4000)})
        segs = lib_llm_correct.iter_axis_segs(msp, min_len_mm=200.0)
        demoted = lib_llm_correct.demote_office_interiors(msp, segs)
        self.assertNotIn(id(chair), demoted)

    def test_column_circle_too_large_is_kept(self):
        """기둥급 큰 CIRCLE은 의자 범위 밖이라 남긴다."""
        doc = ezdxf.new()
        outer, partition, chair, _arc = _office_room(doc.modelspace())
        msp = doc.modelspace()
        fat = msp.add_circle((7000, 2000), 400.0, dxfattribs={"layer": "WALL"})
        segs = lib_llm_correct.iter_axis_segs(msp, min_len_mm=200.0)
        demoted = lib_llm_correct.demote_office_interiors(msp, segs)
        self.assertIn(id(chair), demoted)
        self.assertNotIn(id(fat), demoted)
        self.assertNotIn(id(partition), demoted)
        self.assertTrue(all(id(line) not in demoted for line in outer))


if __name__ == "__main__":
    unittest.main()
