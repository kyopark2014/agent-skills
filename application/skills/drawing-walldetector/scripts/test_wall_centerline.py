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
        """10 m 이상 평행 축선 3줄 이상(+ CEN–CEN 짝)은 벽이 아니다."""
        doc = ezdxf.new()
        msp = doc.modelspace()
        axes = []
        for x in (0, 250, 3000, 6000, 9000):
            axes.append(
                msp.add_line(
                    (x, 0),
                    (x, 28500),
                    dxfattribs={"layer": "CEN"},
                )
            )
        # 실제 짧은 이중선 벽 — 축선 옆에 있어도 벽으로 남는다
        face = msp.add_line((2980, 5000), (2980, 8000))
        mate = msp.add_line((3180, 5000), (3180, 8000))

        entities = list(msp)
        classified = classify_entities(entities)
        walls = set(classified["wall_entity_idxs"])
        for axis in axes:
            self.assertNotIn(entities.index(axis), walls)
        self.assertIn(entities.index(face), walls)
        self.assertIn(entities.index(mate), walls)


if __name__ == "__main__":
    unittest.main()
