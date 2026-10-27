"""긴 평행 축 격자 WALL demote."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

import ezdxf

sys.path.insert(0, str(Path(__file__).resolve().parent))

from lib_llm_correct import (  # noqa: E402
    BASE_LAYER,
    WALL_LAYER,
    demote_parallel_grid_axis_walls,
    filter_promote_away_from_grid_axes,
    iter_axis_segs,
)


class GridAxisWallTests(unittest.TestCase):
    def test_long_parallel_walls_are_demoted(self):
        doc = ezdxf.new()
        msp = doc.modelspace()
        axes = []
        # 기둥 축 피치(수 m). 벽 두께(수백 mm) 이중선은 넣지 않는다.
        for x in (0, 3000, 6000, 9000):
            axes.append(
                msp.add_line(
                    (x, 0),
                    (x, 28500),
                    dxfattribs={"layer": WALL_LAYER, "color": 1},
                )
            )
        # 짧은 실벽 — demote 대상 아님
        short = msp.add_line(
            (10000, 0),
            (10000, 3000),
            dxfattribs={"layer": WALL_LAYER, "color": 1},
        )
        segs = iter_axis_segs(msp, min_len_mm=70.0)
        demote = demote_parallel_grid_axis_walls(segs)
        for axis in axes:
            self.assertIn(id(axis), demote)
        self.assertNotIn(id(short), demote)

    def test_promote_skips_grid_base_axes(self):
        doc = ezdxf.new()
        msp = doc.modelspace()
        bases = []
        for x in (0, 4000, 8000, 12000):
            bases.append(
                msp.add_line(
                    (x, 0),
                    (x, 20000),
                    dxfattribs={"layer": BASE_LAYER, "color": 8},
                )
            )
        segs = iter_axis_segs(msp, min_len_mm=70.0)
        promote = [s for s in segs if s.layer == BASE_LAYER]
        filtered = filter_promote_away_from_grid_axes(promote, segs)
        self.assertEqual(filtered, [])

    def test_long_double_wall_is_not_grid_demoted(self):
        """벽 두께 이중선(+멀리 있는 평행 장축)은 기둥 축 격자가 아니다."""
        doc = ezdxf.new()
        msp = doc.modelspace()
        face_a = msp.add_line(
            (0, 0),
            (40000, 0),
            dxfattribs={"layer": WALL_LAYER, "color": 1},
        )
        face_b = msp.add_line(
            (0, 200),
            (40000, 200),
            dxfattribs={"layer": WALL_LAYER, "color": 1},
        )
        # 건물 반대편·다른 실 테두리 — 축 피치 밖
        msp.add_line(
            (0, 12500),
            (40000, 12500),
            dxfattribs={"layer": WALL_LAYER, "color": 1},
        )
        msp.add_line(
            (5000, 3100),
            (18000, 3100),
            dxfattribs={"layer": BASE_LAYER, "color": 8},
        )
        segs = iter_axis_segs(msp, min_len_mm=70.0)
        demote = demote_parallel_grid_axis_walls(segs)
        self.assertNotIn(id(face_a), demote)
        self.assertNotIn(id(face_b), demote)

    def test_450mm_double_wall_is_not_grid_demoted(self):
        """반포초처럼 450 mm 외벽 이중선도 벽 두께 짝으로 격자 demote에서 뺀다."""
        doc = ezdxf.new()
        msp = doc.modelspace()
        face_a = msp.add_line(
            (0, 0),
            (32400, 0),
            dxfattribs={"layer": WALL_LAYER, "color": 1},
        )
        face_b = msp.add_line(
            (0, 450),
            (32400, 450),
            dxfattribs={"layer": WALL_LAYER, "color": 1},
        )
        msp.add_line(
            (0, 12500),
            (32400, 12500),
            dxfattribs={"layer": WALL_LAYER, "color": 1},
        )
        segs = iter_axis_segs(msp, min_len_mm=70.0)
        demote = demote_parallel_grid_axis_walls(segs)
        self.assertNotIn(id(face_a), demote)
        self.assertNotIn(id(face_b), demote)


if __name__ == "__main__":
    unittest.main()
