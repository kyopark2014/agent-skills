"""Vision outer-wall strips must not split long void X diagonals."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

import ezdxf

sys.path.insert(0, str(Path(__file__).resolve().parent))

import lib_llm_correct


class VisionOuterSkipsVoidXTests(unittest.TestCase):
    def test_long_void_diagonal_not_split_by_outer_strip(self):
        doc = ezdxf.new()
        msp = doc.modelspace()
        # ~23×4.5 m void X (지에스이 조경 상부오픈)
        w, h = 23100.0, 4500.0
        d1 = msp.add_line((0, 0), (w, h), dxfattribs={"layer": "BASE"})
        d2 = msp.add_line((w, 0), (0, h), dxfattribs={"layer": "BASE"})
        # thin outer-wall strip overlapping one end of the diagonal
        boxes = [
            {
                "xmin": -500.0,
                "ymin": -500.0,
                "xmax": 2000.0,
                "ymax": 500.0,
                "label": "외벽",
            }
        ]

        n = lib_llm_correct.promote_vision_outer_walls(msp, boxes)

        self.assertEqual(n, 0)
        self.assertEqual(d1.dxf.layer, "BASE")
        self.assertEqual(d2.dxf.layer, "BASE")
        # original entities still full length
        self.assertAlmostEqual(
            ((d1.dxf.end.x - d1.dxf.start.x) ** 2 + (d1.dxf.end.y - d1.dxf.start.y) ** 2) ** 0.5,
            (w * w + h * h) ** 0.5,
            places=3,
        )

    def test_void_x_frame_not_split_by_mislabeled_top_outer_strip(self):
        """Vision이 X 윗띠를 외벽으로 잡아도 감싼 네모 변은 쪼개지 않는다.

        지에스이 신사옥 1F 조경 상부오픈: X 상자가 위쪽에서 짧고 그 띠를
        외벽으로 올리면 왼쪽 세로 변이 잘려 네모가 깨졌다.
        """
        doc = ezdxf.new()
        msp = doc.modelspace()
        w, h = 23100.0, 4500.0
        msp.add_line((0, 0), (w, h), dxfattribs={"layer": "BASE"})
        msp.add_line((w, 0), (0, h), dxfattribs={"layer": "BASE"})
        left = msp.add_line((0, 0), (0, h), dxfattribs={"layer": "BASE"})
        top = msp.add_line((0, h), (w, h), dxfattribs={"layer": "BASE"})
        bottom = msp.add_line((0, 0), (w, 0), dxfattribs={"layer": "BASE"})
        right = msp.add_line((w, 0), (w, h), dxfattribs={"layer": "BASE"})
        # Vision X 상자보다 짧게 잡힌 윗띠를 외벽으로 오인
        strip = {
            "label": "외벽",
            "xmin": -300.0,
            "ymin": h - 650.0,
            "xmax": w - 800.0,
            "ymax": h + 100.0,
        }

        n = lib_llm_correct.promote_vision_outer_walls(msp, [strip])

        self.assertEqual(n, 0)
        for edge in (left, top, bottom, right):
            self.assertEqual(edge.dxf.layer, "BASE")
            self.assertIs(edge in list(msp), True)
        # 왼쪽 변이 통째로 남아 있다(분할·부분 WALL 없음)
        left_segs = [
            e
            for e in msp
            if e.dxftype() == "LINE"
            and abs(e.dxf.start.x) <= 1.0
            and abs(e.dxf.end.x) <= 1.0
        ]
        self.assertEqual(len(left_segs), 1)
        self.assertAlmostEqual(
            abs(left_segs[0].dxf.end.y - left_segs[0].dxf.start.y),
            h,
            places=3,
        )


if __name__ == "__main__":
    unittest.main()
