"""2인실#1처럼 인실# 글자 안쪽 선은 벽이 아니다."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

import ezdxf

sys.path.insert(0, str(Path(__file__).resolve().parent))

import lib_llm_correct


def _room(msp, label: str):
    wall = {"layer": "WALL"}
    outer = [
        msp.add_line((0, 0), (8000, 0), dxfattribs=wall),
        msp.add_line((8000, 0), (8000, 6000), dxfattribs=wall),
        msp.add_line((8000, 6000), (0, 6000), dxfattribs=wall),
        msp.add_line((0, 6000), (0, 0), dxfattribs=wall),
    ]
    inner = msp.add_line((2000, 3000), (4000, 3000), dxfattribs=wall)
    msp.add_text(label, dxfattribs={"height": 300, "insert": (4000, 3000)})
    return outer, inner


class BedWardInteriorTests(unittest.TestCase):
    def test_hash_label_demotes_interior_and_keeps_outline(self):
        doc = ezdxf.new()
        outer, inner = _room(doc.modelspace(), "2인실#1")
        segs = lib_llm_correct.iter_axis_segs(doc.modelspace(), min_len_mm=200.0)
        demoted = lib_llm_correct.demote_bed_ward_interiors(doc.modelspace(), segs)
        self.assertIn(id(inner), demoted)
        self.assertTrue(all(id(line) not in demoted for line in outer))

    def test_fullwidth_hash_and_spaces(self):
        doc = ezdxf.new()
        _, inner = _room(doc.modelspace(), "4인실 ＃ 2")
        segs = lib_llm_correct.iter_axis_segs(doc.modelspace(), min_len_mm=200.0)
        demoted = lib_llm_correct.demote_bed_ward_interiors(doc.modelspace(), segs)
        self.assertIn(id(inner), demoted)

    def test_plain_bed_count_is_not_demoted(self):
        doc = ezdxf.new()
        _, inner = _room(doc.modelspace(), "2인실")
        segs = lib_llm_correct.iter_axis_segs(doc.modelspace(), min_len_mm=200.0)
        demoted = lib_llm_correct.demote_bed_ward_interiors(doc.modelspace(), segs)
        self.assertNotIn(id(inner), demoted)


if __name__ == "__main__":
    unittest.main()
