"""두꺼운 벽의 맞은편 BASE 면은 WALL로 올린다."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

import ezdxf

sys.path.insert(0, str(Path(__file__).resolve().parent))

import lib_llm_correct


class ThickWallMateTests(unittest.TestCase):
    def test_outer_face_of_450mm_wall_promoted(self):
        """방풍실 옆처럼 안쪽만 WALL인 450 mm 벽의 바깥 면을 올린다."""
        doc = ezdxf.new()
        msp = doc.modelspace()
        # 안쪽 면 (이미 WALL), 바깥 면은 두 조각 BASE.
        inner = msp.add_line((0, 0), (0, 2700), dxfattribs={"layer": "WALL", "color": 1})
        mid = msp.add_line((200, 0), (200, 2600), dxfattribs={"layer": "WALL", "color": 1})
        outer_a = msp.add_line((450, 0), (450, 1950), dxfattribs={"layer": "BASE", "color": 8})
        outer_b = msp.add_line((450, 2150), (450, 2700), dxfattribs={"layer": "BASE", "color": 8})
        # 끝단
        cap = msp.add_line((0, 2700), (450, 2700), dxfattribs={"layer": "BASE", "color": 8})

        n = lib_llm_correct.promote_thick_wall_mates(msp)

        self.assertGreaterEqual(n, 2)
        self.assertEqual(outer_a.dxf.layer, "WALL")
        self.assertEqual(outer_b.dxf.layer, "WALL")
        self.assertEqual(inner.dxf.layer, "WALL")
        self.assertEqual(mid.dxf.layer, "WALL")
        _ = cap

    def test_sofa_inside_double_wall_not_promoted(self):
        """이중 WALL 안쪽 소파 BASE는 두꺼운 벽 맞은편으로 올리지 않는다."""
        doc = ezdxf.new()
        msp = doc.modelspace()
        # 100 mm 이중벽 (이미 양쪽 WALL)
        msp.add_line((0, 0), (0, 5800), dxfattribs={"layer": "WALL", "color": 1})
        msp.add_line((100, 0), (100, 5800), dxfattribs={"layer": "WALL", "color": 1})
        # 실 안 소파 장변 (벽에서 ~250–450 mm)
        sofa_a = msp.add_line((350, 800), (350, 2750), dxfattribs={"layer": "BASE", "color": 8})
        sofa_b = msp.add_line((550, 950), (550, 2600), dxfattribs={"layer": "BASE", "color": 8})

        n = lib_llm_correct.promote_thick_wall_mates(msp)

        self.assertEqual(n, 0)
        self.assertEqual(sofa_a.dxf.layer, "BASE")
        self.assertEqual(sofa_b.dxf.layer, "BASE")


if __name__ == "__main__":
    unittest.main()
