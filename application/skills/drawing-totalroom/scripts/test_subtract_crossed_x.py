"""기명 실 면에서 교차 X 침범분을 빼는지 확인한다."""

import unittest

import ezdxf
from shapely.geometry import Polygon, box

import detect_labels as labels


class SubtractCrossedXTest(unittest.TestCase):
    def test_x_overlap_is_cut_from_named_room(self):
        doc = ezdxf.new()
        msp = doc.modelspace()
        # ST 칸 X: 600 x 950 mm (문 X 제외 구간, thick>=499).
        msp.add_line((0.0, 0.0), (600.0, 950.0), dxfattribs={"layer": "WALL"})
        msp.add_line((0.0, 950.0), (600.0, 0.0), dxfattribs={"layer": "WALL"})
        # 전실 면이 X 칸(0~600, 0~950) 오른쪽으로 새어 들어옴.
        room_poly = box(200.0, 200.0, 2000.0, 900.0)
        room = {
            "text": "전실#1",
            "x": 1200.0,
            "y": 500.0,
            "name": "전실#1",
            "pts": list(room_poly.exterior.coords[:-1]),
            "holes": [],
            "area_m2": room_poly.area / 1_000_000.0,
            "width_m": 1.8,
            "height_m": 0.7,
        }
        before = room["area_m2"]
        x_box = box(0.0, 0.0, 600.0, 950.0)
        self.assertGreater(room_poly.intersection(x_box).area, 50_000.0)

        labels._subtract_crossed_x_regions(msp, [room])
        after = Polygon(room["pts"])
        self.assertLess(room["area_m2"], before)
        self.assertLess(after.intersection(x_box).area, 5_000.0)
        self.assertTrue(labels.room.point_in_poly(1200.0, 500.0, room["pts"]))

    def test_skips_when_label_is_inside_x(self):
        doc = ezdxf.new()
        msp = doc.modelspace()
        msp.add_line((0.0, 0.0), (600.0, 950.0), dxfattribs={"layer": "WALL"})
        msp.add_line((0.0, 950.0), (600.0, 0.0), dxfattribs={"layer": "WALL"})
        room = {
            "text": "홀",
            "x": 300.0,
            "y": 475.0,
            "name": "홀",
            "pts": [(0.0, 0.0), (2000.0, 0.0), (2000.0, 2000.0), (0.0, 2000.0)],
            "holes": [],
            "area_m2": 4.0,
            "width_m": 2.0,
            "height_m": 2.0,
        }
        labels._subtract_crossed_x_regions(msp, [room])
        self.assertAlmostEqual(room["area_m2"], 4.0)


if __name__ == "__main__":
    unittest.main()
