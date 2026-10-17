"""이중선 한가운데 중심선은 벽면이 아니다."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

import ezdxf

sys.path.insert(0, str(Path(__file__).resolve().parent))

from lib_walls import classify_entities


class WallCenterlineTests(unittest.TestCase):
    def test_midline_between_double_wall_is_not_a_wall(self):
        """두께 200 mm 이중선 + 중간 긴 중심선 → 바깥 두 면만 WALL."""
        doc = ezdxf.new()
        msp = doc.modelspace()
        face_l = msp.add_line((0, 0), (0, 2930), dxfattribs={"layer": "WALL"})
        face_r = msp.add_line((200, 0), (200, 2930), dxfattribs={"layer": "WALL"})
        # 실제 벽보다 길게 뻗은 CEN 축선 (조경 구간까지)
        center = msp.add_line(
            (100, -5000),
            (100, 15000),
            dxfattribs={"layer": "CEN"},
        )

        entities = list(msp)
        classified = classify_entities(entities)
        walls = set(classified["wall_entity_idxs"])
        self.assertIn(entities.index(face_l), walls)
        self.assertIn(entities.index(face_r), walls)
        self.assertNotIn(entities.index(center), walls)

    def test_horizontal_midline_between_double_wall_is_not_a_wall(self):
        doc = ezdxf.new()
        msp = doc.modelspace()
        face_b = msp.add_line((0, 0), (4000, 0))
        face_t = msp.add_line((0, 200), (4000, 200))
        center = msp.add_line((-2000, 100), (8000, 100), dxfattribs={"layer": "CEN"})

        entities = list(msp)
        classified = classify_entities(entities)
        walls = set(classified["wall_entity_idxs"])
        self.assertIn(entities.index(face_b), walls)
        self.assertIn(entities.index(face_t), walls)
        self.assertNotIn(entities.index(center), walls)

    def test_plain_double_wall_without_midline_stays_wall(self):
        doc = ezdxf.new()
        msp = doc.modelspace()
        face = msp.add_line((0, 0), (0, 3000))
        mate = msp.add_line((200, 0), (200, 3000))

        entities = list(msp)
        classified = classify_entities(entities)
        walls = set(classified["wall_entity_idxs"])
        self.assertIn(entities.index(face), walls)
        self.assertIn(entities.index(mate), walls)

    def test_parallel_grid_axes_are_not_walls(self):
        """10 m 이상 평행 축선이 축 피치(3 m)로 3줄 이상이면 벽이 아니다."""
        doc = ezdxf.new()
        msp = doc.modelspace()
        axes = []
        for x in (0, 3000, 6000, 9000):
            axes.append(
                msp.add_line(
                    (x, 0),
                    (x, 28500),
                    dxfattribs={"layer": "CEN"},
                )
            )
        # 실제 짧은 이중선 벽 — 축선과 떨어진 곳에 둔다
        face = msp.add_line((15000, 5000), (15000, 8000))
        mate = msp.add_line((15200, 5000), (15200, 8000))

        entities = list(msp)
        classified = classify_entities(entities)
        walls = set(classified["wall_entity_idxs"])
        for axis in axes:
            self.assertNotIn(entities.index(axis), walls)
        self.assertIn(entities.index(face), walls)
        self.assertIn(entities.index(mate), walls)

    def test_cen_thickness_pair_is_wall_not_grid(self):
        """CEN–CEN 벽 두께(250 mm) 짝은 격자가 아니라 이중벽이다."""
        doc = ezdxf.new()
        msp = doc.modelspace()
        a = msp.add_line((0, 0), (0, 20000), dxfattribs={"layer": "CEN"})
        b = msp.add_line((250, 0), (250, 20000), dxfattribs={"layer": "CEN"})
        # 먼 축 격자(피치 3 m) — 두께 짝을 격자로 끌어내리지 않는다
        for x in (5000, 8000, 11000):
            msp.add_line((x, 0), (x, 20000), dxfattribs={"layer": "CEN"})

        entities = list(msp)
        classified = classify_entities(entities)
        walls = set(classified["wall_entity_idxs"])
        self.assertIn(entities.index(a), walls)
        self.assertIn(entities.index(b), walls)

    def test_long_wall_thickness_double_is_not_grid(self):
        """10 m+ 이중선(간격 200 mm)은 먼 평행선이 있어도 벽이다.

        클럽하우스 소화수조 옆: 피치 제한 없으면 도곽 장축과 겹쳐 격자로 빠진다.
        """
        doc = ezdxf.new()
        msp = doc.modelspace()
        face = msp.add_line((0, 0), (24000, 0))
        mate = msp.add_line((0, 200), (14000, 200))
        # 건물 반대편·도곽처럼 먼 평행 장축 (피치 ≫ 12 m)
        far_a = msp.add_line((0, 50000), (24000, 50000))
        far_b = msp.add_line((0, 55000), (24000, 55000))
        far_c = msp.add_line((0, 60000), (24000, 60000))

        entities = list(msp)
        classified = classify_entities(entities)
        walls = set(classified["wall_entity_idxs"])
        self.assertIn(entities.index(face), walls)
        self.assertIn(entities.index(mate), walls)
        self.assertNotIn(entities.index(far_a), walls)
        self.assertNotIn(entities.index(far_b), walls)
        self.assertNotIn(entities.index(far_c), walls)


if __name__ == "__main__":
    unittest.main()
