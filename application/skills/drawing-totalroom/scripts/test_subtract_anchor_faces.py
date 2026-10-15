"""화장실 칸을 잡아 들어 있는 실에서 빼는지 확인한다."""

import unittest

import ezdxf

import detect_labels as labels


def _hall(area=60.0):
    return {
        "text": "홀",
        "x": 2000.0,
        "y": 3000.0,
        "name": "홀",
        "pts": [(0.0, 0.0), (10000.0, 0.0), (10000.0, 6000.0), (0.0, 6000.0)],
        "holes": [],
        "area_m2": area,
        "width_m": 10.0,
        "height_m": 6.0,
    }


def _bathroom_walls():
    return [
        (0.0, 0.0, 10000.0, 0.0),
        (10000.0, 0.0, 10000.0, 6000.0),
        (10000.0, 6000.0, 0.0, 6000.0),
        (0.0, 6000.0, 0.0, 0.0),
        (7000.0, 1000.0, 9500.0, 1000.0),
        (9500.0, 1000.0, 9500.0, 4000.0),
        (9500.0, 4000.0, 7000.0, 4000.0),
        (7000.0, 1000.0, 7000.0, 1800.0),
        (7000.0, 2800.0, 7000.0, 4000.0),
    ]


class SubtractAnchorFacesTest(unittest.TestCase):
    def test_gapped_bathroom_is_removed_from_hall(self):
        doc = ezdxf.new()
        msp = doc.modelspace()
        msp.add_text("홀", dxfattribs={"insert": (2000.0, 3000.0), "height": 300})
        msp.add_text("화장실", dxfattribs={"insert": (8200.0, 2500.0), "height": 200})
        hall = _hall()
        labels._subtract_anchor_faces(msp, _bathroom_walls(), [hall])
        self.assertLess(hall["area_m2"], 55.0)
        self.assertGreater(hall["area_m2"], 45.0)
        self.assertTrue(labels.room.point_in_poly(2000.0, 3000.0, hall["pts"]))
        in_hole = any(
            labels.room.point_in_poly(8200.0, 2500.0, hole) for hole in hall["holes"]
        )
        outside = not labels.room.point_in_poly(8200.0, 2500.0, hall["pts"])
        self.assertTrue(in_hole or outside)

    def test_open_room_is_not_cut_when_the_face_swallows_the_hall(self):
        doc = ezdxf.new()
        msp = doc.modelspace()
        msp.add_text("홀", dxfattribs={"insert": (2000.0, 3000.0), "height": 300})
        msp.add_text("화장실", dxfattribs={"insert": (8200.0, 2500.0), "height": 200})
        hall = _hall()
        walls = [
            (0.0, 0.0, 10000.0, 0.0),
            (10000.0, 0.0, 10000.0, 6000.0),
            (10000.0, 6000.0, 0.0, 6000.0),
            (0.0, 6000.0, 0.0, 0.0),
        ]
        labels._subtract_anchor_faces(msp, walls, [hall])
        self.assertAlmostEqual(hall["area_m2"], 60.0)


if __name__ == "__main__":
    unittest.main()
