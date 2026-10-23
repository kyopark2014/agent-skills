"""A bathroom box from vision promotes every wall and fixture inside it."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

import ezdxf

sys.path.insert(0, str(Path(__file__).resolve().parent))

import lib_llm_correct


class VisionBathroomTests(unittest.TestCase):
    def test_labeled_bathroom_promotes_stalls_and_fixtures(self):
        doc = ezdxf.new()
        msp = doc.modelspace()
        stall = msp.add_line((1000, 0), (1000, 1800), dxfattribs={"layer": "BASE"})
        wall = msp.add_line((0, 2000), (3000, 2000), dxfattribs={"layer": "BASE"})
        bowl = msp.add_circle((800, 600), 220, dxfattribs={"layer": "BASE"})
        swing = msp.add_arc(
            (200, 200),
            900,
            0,
            90,
            dxfattribs={"layer": "BASE"},
        )
        door_x = msp.add_line((400, 200), (1600, 1400), dxfattribs={"layer": "BASE"})
        outside = msp.add_line((5000, 0), (5000, 1800), dxfattribs={"layer": "BASE"})

        promoted = lib_llm_correct.promote_vision_bathrooms(
            msp,
            [{"label": "화장실", "xmin": 0, "ymin": 0, "xmax": 3200, "ymax": 2200}],
        )

        self.assertEqual(promoted, 3)
        self.assertEqual(stall.dxf.layer, "WALL")
        self.assertEqual(wall.dxf.layer, "WALL")
        self.assertEqual(bowl.dxf.layer, "WALL")
        self.assertEqual(swing.dxf.layer, "BASE")
        self.assertEqual(door_x.dxf.layer, "BASE")
        self.assertEqual(outside.dxf.layer, "BASE")

    def test_other_labels_stay_base(self):
        doc = ezdxf.new()
        msp = doc.modelspace()
        line = msp.add_line((0, 0), (2000, 0), dxfattribs={"layer": "BASE"})

        promoted = lib_llm_correct.promote_vision_bathrooms(
            msp,
            [{"label": "가구", "xmin": 0, "ymin": -100, "xmax": 2200, "ymax": 100}],
        )

        self.assertEqual(promoted, 0)
        self.assertEqual(line.dxf.layer, "BASE")

    def test_oversized_bathroom_bbox_is_skipped(self):
        """객실까지 덮는 과대 화장실 상자는 버리고 가구를 WALL로 올리지 않는다."""
        doc = ezdxf.new()
        msp = doc.modelspace()
        furniture = msp.add_lwpolyline(
            [
                (97560.0, 25187.5),
                (97160.0, 24887.5),
                (97160.0, 23367.5),
                (97110.0, 23317.5),
                (97060.0, 23267.5),
                (97060.0, 21937.5),
                (97310.0, 21687.5),
                (97560.0, 21687.5),
            ],
            close=True,
            dxfattribs={"layer": "BASE", "color": 8},
        )
        stall = msp.add_line(
            (94000.0, 27000.0),
            (94000.0, 28500.0),
            dxfattribs={"layer": "BASE"},
        )

        promoted = lib_llm_correct.promote_vision_bathrooms(
            msp,
            [
                {
                    "label": "화장실",
                    "xmin": 93103.4,
                    "ymin": 22953.8,
                    "xmax": 107409.9,
                    "ymax": 28958.9,
                }
            ],
        )

        self.assertEqual(promoted, 0)
        self.assertEqual(furniture.dxf.layer, "BASE")
        self.assertEqual(stall.dxf.layer, "BASE")

    def test_closet_outline_inside_oversized_bathroom_bbox_stays_base(self):
        """화장실 Vision 상자가 객실까지 커져도 옷장 윤곽은 WALL로 올리지 않는다."""
        doc = ezdxf.new()
        msp = doc.modelspace()
        stall = msp.add_line((1000, 0), (1000, 1800), dxfattribs={"layer": "BASE"})
        closet = msp.add_lwpolyline(
            [
                (1000.0, 2500.0),
                (2700.0, 2500.0),
                (2700.0, 1900.0),
                (1000.0, 1900.0),
                (1000.0, 2550.0),
                (2000.0, 2550.0),
            ],
            close=True,
            dxfattribs={"layer": "BASE", "color": 8},
        )
        msp.add_text(
            "옷장",
            dxfattribs={"layer": "BASE", "insert": (1850.0, 2200.0), "height": 100.0},
        )

        promoted = lib_llm_correct.promote_vision_bathrooms(
            msp,
            [{"label": "화장실", "xmin": 0, "ymin": 0, "xmax": 3200, "ymax": 2800}],
        )

        self.assertEqual(stall.dxf.layer, "WALL")
        self.assertEqual(closet.dxf.layer, "BASE")
        self.assertGreaterEqual(promoted, 1)

    def test_line_mostly_outside_bathroom_stays_base(self):
        """중점만 화장실 상자에 걸치고 밖으로 길게 빠진 선은 벽이 아니다."""
        doc = ezdxf.new()
        msp = doc.modelspace()
        # 상자 폭 2.36 m, 선 길이 4.9 m → 상자 안 비율 ≈ 48%
        long_hall = msp.add_line(
            (7781525, 693830),
            (7786425, 693830),
            dxfattribs={"layer": "BASE"},
        )
        # 커버 ≈ 71%여도 밖으로 1.4 m 나가면 벽이 아니다.
        long_partial = msp.add_line(
            (7781525, 693200),
            (7786425, 693200),
            dxfattribs={"layer": "BASE"},
        )
        inside = msp.add_line(
            (7782800, 693640),
            (7784800, 693640),
            dxfattribs={"layer": "BASE"},
        )

        promoted = lib_llm_correct.promote_vision_bathrooms(
            msp,
            [
                {
                    "label": "화장실",
                    "xmin": 7782655,
                    "ymin": 690805,
                    "xmax": 7785015,
                    "ymax": 694425,
                },
                {
                    "label": "화장실",
                    "xmin": 7781082,
                    "ymin": 685634,
                    "xmax": 7785015,
                    "ymax": 694425,
                },
            ],
        )

        self.assertEqual(promoted, 1)
        self.assertEqual(long_hall.dxf.layer, "BASE")
        self.assertEqual(long_partial.dxf.layer, "BASE")
        self.assertEqual(inside.dxf.layer, "WALL")

    def test_room_row_divider_mostly_outside_bbox_stays_base(self):
        doc = ezdxf.new()
        msp = doc.modelspace()
        # 이웃 WALL 두 줄 + 긴 BASE 이중선 (중점만 bbox 안)
        msp.add_line((7781500, 686835), (7786500, 686835), dxfattribs={"layer": "WALL"})
        msp.add_line((7781500, 689945), (7786500, 689945), dxfattribs={"layer": "WALL"})
        long_hall = msp.add_line(
            (7781525, 693830),
            (7786425, 693830),
            dxfattribs={"layer": "BASE"},
        )
        msp.add_line(
            (7782235, 693640),
            (7784305, 693640),
            dxfattribs={"layer": "BASE"},
        )
        segs = lib_llm_correct.iter_axis_segs(msp, min_len_mm=70.0)
        promoted = lib_llm_correct.promote_room_row_dividers(
            segs,
            [
                {
                    "label": "화장실",
                    "xmin": 7782655,
                    "ymin": 690805,
                    "xmax": 7785015,
                    "ymax": 694425,
                }
            ],
        )
        handles = {getattr(s.entity.dxf, "handle", None) for s in promoted}
        self.assertNotIn(long_hall.dxf.handle, handles)

    def test_stair_and_elevator_boxes_promote_their_walls(self):
        doc = ezdxf.new()
        msp = doc.modelspace()
        tread = msp.add_line((0, 400), (1800, 400), dxfattribs={"layer": "BASE"})
        stair_wall = msp.add_line((0, 0), (0, 2400), dxfattribs={"layer": "BASE"})
        cut = msp.add_line((200, 200), (1600, 2000), dxfattribs={"layer": "BASE"})
        car = msp.add_line((4000, 0), (4000, 2200), dxfattribs={"layer": "BASE"})
        shaft = msp.add_line((4200, 100), (5800, 2100), dxfattribs={"layer": "BASE"})

        promoted = lib_llm_correct.promote_vision_bathrooms(
            msp,
            [
                {"label": "계단", "xmin": -100, "ymin": -100, "xmax": 2000, "ymax": 2600},
                {"label": "승강기", "xmin": 3800, "ymin": -100, "xmax": 6200, "ymax": 2600},
            ],
        )

        self.assertEqual(promoted, 3)
        self.assertEqual(tread.dxf.layer, "WALL")
        self.assertEqual(stair_wall.dxf.layer, "WALL")
        self.assertEqual(car.dxf.layer, "WALL")
        self.assertEqual(cut.dxf.layer, "BASE")
        self.assertEqual(shaft.dxf.layer, "BASE")

    def test_building_wall_strip_is_promoted(self):
        doc = ezdxf.new()
        msp = doc.modelspace()
        outer = msp.add_line((0, 20000), (30000, 20000), dxfattribs={"layer": "BASE"})
        side = msp.add_line((0, 0), (0, 20000), dxfattribs={"layer": "BASE"})
        inner = msp.add_line((10000, 4000), (10000, 16000), dxfattribs={"layer": "BASE"})
        swing = msp.add_arc((500, 19500), 900, 0, 90, dxfattribs={"layer": "BASE"})

        promoted = lib_llm_correct.promote_vision_outer_walls(
            msp,
            [
                {"label": "외벽", "xmin": -200, "ymin": 0, "xmax": 400, "ymax": 20000},
                {"label": "외벽", "xmin": 0, "ymin": 19600, "xmax": 30000, "ymax": 20400},
            ],
        )

        self.assertEqual(promoted, 2)
        self.assertEqual(outer.dxf.layer, "WALL")
        self.assertEqual(side.dxf.layer, "WALL")
        self.assertEqual(inner.dxf.layer, "BASE")
        self.assertEqual(swing.dxf.layer, "BASE")

    def test_large_drawing_box_is_not_an_exterior_wall(self):
        doc = ezdxf.new()
        msp = doc.modelspace()
        edge = msp.add_line((0, 20000), (30000, 20000), dxfattribs={"layer": "BASE"})

        promoted = lib_llm_correct.promote_vision_outer_walls(
            msp,
            [{"label": "외벽", "xmin": 0, "ymin": 0, "xmax": 30000, "ymax": 20000}],
        )

        self.assertEqual(promoted, 0)
        self.assertEqual(edge.dxf.layer, "BASE")

    def test_line_that_enters_the_building_keeps_only_the_outer_part(self):
        doc = ezdxf.new()
        msp = doc.modelspace()
        msp.add_line((0, 10000), (15000, 10000), dxfattribs={"layer": "BASE"})
        msp.add_lwpolyline(
            [(0, 20000), (8000, 20000), (8000, 5000)],
            dxfattribs={"layer": "BASE"},
        )

        lib_llm_correct.promote_vision_outer_walls(
            msp,
            [
                {"label": "외벽", "xmin": 0, "ymin": 9000, "xmax": 2000, "ymax": 11000},
                {"label": "외벽", "xmin": 0, "ymin": 18000, "xmax": 9000, "ymax": 20500},
            ],
        )

        walls = _segments(msp, "WALL")
        base = _segments(msp, "BASE")
        self.assertIn(((0, 10000), (2000, 10000)), walls)
        self.assertIn(((2000, 10000), (15000, 10000)), base)
        self.assertIn(((0, 20000), (8000, 20000)), walls)
        self.assertIn(((8000, 18000), (8000, 20000)), walls)
        self.assertIn(((8000, 5000), (8000, 18000)), base)
        self.assertNotIn(((8000, 5000), (8000, 20000)), walls)


def _segments(msp, layer: str) -> set[tuple[tuple[int, int], tuple[int, int]]]:
    found = set()
    for entity in msp:
        if entity.dxftype() != "LINE" or entity.dxf.layer != layer:
            continue
        start = (round(entity.dxf.start.x), round(entity.dxf.start.y))
        end = (round(entity.dxf.end.x), round(entity.dxf.end.y))
        found.add(tuple(sorted((start, end))))
    return found


if __name__ == "__main__":
    unittest.main()
