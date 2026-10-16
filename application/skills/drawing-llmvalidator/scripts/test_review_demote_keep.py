"""Vision demote: 창문·기둥·문 양옆은 내리지 않는다."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

import ezdxf

sys.path.insert(0, str(Path(__file__).resolve().parent))

import lib_llm_correct


def _h(msp, x0, x1, y, layer="WALL", color=1):
    return msp.add_line(
        (x0, y), (x1, y), dxfattribs={"layer": layer, "color": color}
    )


def _v(msp, x, y0, y1, layer="WALL", color=1):
    return msp.add_line(
        (x, y0), (x, y1), dxfattribs={"layer": layer, "color": color}
    )


def _hbeam_column(msp, x0, y0, side=600.0):
    """정사각 + 중앙 '_' 대시 — find_hbeam_column_entities 후보."""
    x1, y1 = x0 + side, y0 + side
    outline = msp.add_lwpolyline(
        [(x0, y0), (x1, y0), (x1, y1), (x0, y1)],
        close=True,
        dxfattribs={"layer": "WALL", "color": 1},
    )
    cx, cy = (x0 + x1) * 0.5, (y0 + y1) * 0.5
    dash = msp.add_line(
        (cx - side * 0.2, cy),
        (cx + side * 0.2, cy),
        dxfattribs={"layer": "WALL", "color": 1},
    )
    return outline, dash


class ReviewDemoteKeepTests(unittest.TestCase):
    def test_column_abutting_wall_survives_bed_demote(self):
        """H-Beam 기둥에 맞닿은 짧은 외벽은 침대 demote 상자에 있어도 남는다."""
        doc = ezdxf.new()
        msp = doc.modelspace()
        _hbeam_column(msp, 1000, 0, side=600.0)
        # 기둥 왼쪽 모서리(1000,0)에 맞닿은 이중벽 — 중점은 demote 상자 안
        face0 = _h(msp, 300, 1000, 0)
        face1 = _h(msp, 300, 1000, 200)
        # 상자 안 가구 선(기둥에 안 닿음) — demote 대상
        furniture = _h(msp, 400, 800, 400)

        columns = lib_llm_correct.find_hbeam_column_entities(msp)
        self.assertTrue(columns)

        boxes = [{"xmin": 200, "ymin": -50, "xmax": 900, "ymax": 500, "label": "침대"}]
        demote = lib_llm_correct.demote_in_bboxes(msp, boxes)
        keep = lib_llm_correct.review_demote_keep_ids(msp, boxes, column_ids=columns)
        demote -= keep

        self.assertIn(id(face0), keep)
        self.assertIn(id(face1), keep)
        self.assertNotIn(id(face0), demote)
        self.assertNotIn(id(face1), demote)
        self.assertIn(id(furniture), demote)

    def test_line_square_column_abutting_wall_survives(self):
        """원광대 502처럼 선 네 개 정사각 기둥에 맞닿은 외벽도 남긴다."""
        doc = ezdxf.new()
        msp = doc.modelspace()
        x0, y0, side = 1000.0, 0.0, 600.0
        _h(msp, x0, x0 + side, y0)
        _h(msp, x0, x0 + side, y0 + side)
        _v(msp, x0, y0, y0 + side)
        _v(msp, x0 + side, y0, y0 + side)
        face0 = _h(msp, 300, x0, y0)
        face1 = _h(msp, 300, x0, y0 + 200)
        furniture = _h(msp, 400, 800, 400)

        boxes = [{"xmin": 200, "ymin": -50, "xmax": 900, "ymax": 500, "label": "침대"}]
        demote = lib_llm_correct.demote_in_bboxes(msp, boxes)
        keep = lib_llm_correct.review_demote_keep_ids(msp, boxes, column_ids=set())
        demote -= keep

        self.assertIn(id(face0), keep)
        self.assertIn(id(face1), keep)
        self.assertNotIn(id(face0), demote)
        self.assertIn(id(furniture), demote)

    def test_window_faces_survive_demote(self):
        """안쪽 창짝이 있는 이중 창면은 demote 상자 안이어도 남는다."""
        doc = ezdxf.new()
        msp = doc.modelspace()
        face0 = _h(msp, 0, 2000, 0)
        face1 = _h(msp, 0, 2000, 200)
        _h(msp, 100, 1900, 80, layer="BASE", color=8)
        _h(msp, 100, 1900, 120, layer="BASE", color=8)
        clutter = _h(msp, 500, 900, 500)

        boxes = [{"xmin": -50, "ymin": -50, "xmax": 2100, "ymax": 600}]
        demote = lib_llm_correct.demote_in_bboxes(msp, boxes)
        keep = lib_llm_correct.review_demote_keep_ids(msp, boxes, column_ids=set())
        demote -= keep

        self.assertIn(id(face0), keep)
        self.assertIn(id(face1), keep)
        self.assertNotIn(id(face0), demote)
        self.assertIn(id(clutter), demote)

    def test_door_side_walls_still_kept(self):
        """기존 문 양옆 보호도 keep 집합에 들어간다."""
        doc = ezdxf.new()
        msp = doc.modelspace()
        # 스윙 ARC + 양옆 이중벽
        left0 = _v(msp, 0, 0, 400)
        left1 = _v(msp, 150, 0, 400)
        right0 = _v(msp, 1000, 0, 400)
        right1 = _v(msp, 1150, 0, 400)
        msp.add_arc(
            center=(150, 0),
            radius=850,
            start_angle=0.0,
            end_angle=90.0,
            dxfattribs={"layer": "BASE", "color": 8},
        )
        leaf = _h(msp, 200, 800, 200)

        boxes = [{"xmin": -50, "ymin": -50, "xmax": 1200, "ymax": 900}]
        keep = lib_llm_correct.review_demote_keep_ids(msp, boxes, column_ids=set())
        demote = lib_llm_correct.demote_in_bboxes(msp, boxes) - keep

        self.assertTrue(
            {id(left0), id(left1)} & keep or {id(right0), id(right1)} & keep,
            msg="문 양옆이 keep에 없음",
        )
        self.assertIn(id(leaf), demote)

    def test_opening_end_partition_survives_bed_demote(self):
        """위·아래가 문·창 이중선인 긴 실간 격벽은 침대 demote에 안 지워진다."""
        doc = ezdxf.new()
        msp = doc.modelspace()
        # 실간 이중 격벽 (6 m) — 중점은 침대 bbox 안
        face0 = _v(msp, 1000, 0, 6000)
        face1 = _v(msp, 1180, 0, 6000)
        # 아래: pier + 옆 창 이중선
        _h(msp, 900, 1280, 0)
        _h(msp, 200, 900, 40)
        _h(msp, 200, 900, 160)
        # 위: T접합 복도벽 + 옆 문 이중선
        _h(msp, 200, 1000, 6000)
        _h(msp, 1180, 2000, 6000)
        _h(msp, 200, 900, 6100)
        _h(msp, 200, 900, 6250)
        # bbox 안 가구 선
        furniture = _h(msp, 1300, 1800, 3000)

        boxes = [{"xmin": 900, "ymin": 500, "xmax": 2000, "ymax": 4500, "label": "침대"}]
        demote = lib_llm_correct.demote_in_bboxes(msp, boxes)
        keep = lib_llm_correct.review_demote_keep_ids(msp, boxes, column_ids=set())
        demote -= keep

        self.assertIn(id(face0), keep)
        self.assertIn(id(face1), keep)
        self.assertNotIn(id(face0), demote)
        self.assertNotIn(id(face1), demote)
        self.assertIn(id(furniture), demote)

    def test_short_wall_without_openings_still_demoted(self):
        """문·창 끝이 없는 짧은 선은 침대 demote에 그대로 내려간다."""
        doc = ezdxf.new()
        msp = doc.modelspace()
        short0 = _v(msp, 1000, 0, 900)
        short1 = _v(msp, 1180, 0, 900)

        boxes = [{"xmin": 900, "ymin": -50, "xmax": 1300, "ymax": 1000, "label": "침대"}]
        keep = lib_llm_correct.review_demote_keep_ids(msp, boxes, column_ids=set())
        demote = lib_llm_correct.demote_in_bboxes(msp, boxes) - keep

        self.assertNotIn(id(short0), keep)
        self.assertIn(id(short0), demote)
        self.assertIn(id(short1), demote)


if __name__ == "__main__":
    unittest.main()
