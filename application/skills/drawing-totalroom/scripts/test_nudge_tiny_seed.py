"""라벨이 1 m² 이하 작은 칸에 있으면 씨앗을 옮겨 넓은 실을 잡는지 확인한다."""

from __future__ import annotations

import unittest

import ezdxf

import detect_labels as labels


def _wall_room(msp, points: list[tuple[float, float]]) -> None:
    wall = {"layer": "WALL"}
    for index, start in enumerate(points):
        end = points[(index + 1) % len(points)]
        msp.add_line(start, end, dxfattribs=wall)


class NudgeTinySeedTest(unittest.TestCase):
    def test_closet_seed_moves_into_large_room(self):
        doc = ezdxf.new()
        msp = doc.modelspace()
        # 0.64 m² 작은 칸과 오른쪽에 붙은 큰 실.
        _wall_room(msp, [(0.0, 0.0), (800.0, 0.0), (800.0, 800.0), (0.0, 800.0)])
        _wall_room(
            msp,
            [
                (800.0, 0.0),
                (10800.0, 0.0),
                (10800.0, 8000.0),
                (800.0, 8000.0),
            ],
        )
        boxes = labels.room.column_boxes(msp)
        segs = labels.room.wall_segments(msp, boxes)

        tiny, tiny_reason = labels.detect_one(msp, boxes, segs, 400.0, 400.0)
        self.assertIsNone(tiny_reason)
        self.assertIsNotNone(tiny)
        assert tiny is not None
        self.assertLessEqual(tiny["area_m2"], 1.0)

        found, reason, seed_x, seed_y = labels.detect_one_with_nudge(
            msp, boxes, segs, 400.0, 400.0
        )
        self.assertIsNone(reason)
        self.assertIsNotNone(found)
        assert found is not None
        self.assertGreater(found["area_m2"], 1.0)
        self.assertGreater(seed_x, 400.0)
        self.assertAlmostEqual(seed_y, 400.0)

    def test_large_room_seed_is_not_moved(self):
        doc = ezdxf.new()
        msp = doc.modelspace()
        _wall_room(
            msp,
            [
                (0.0, 0.0),
                (10000.0, 0.0),
                (10000.0, 8000.0),
                (0.0, 8000.0),
            ],
        )
        boxes = labels.room.column_boxes(msp)
        segs = labels.room.wall_segments(msp, boxes)
        found, reason, seed_x, seed_y = labels.detect_one_with_nudge(
            msp, boxes, segs, 4000.0, 4000.0
        )
        self.assertIsNone(reason)
        self.assertIsNotNone(found)
        assert found is not None
        self.assertGreater(found["area_m2"], 1.0)
        self.assertEqual((seed_x, seed_y), (4000.0, 4000.0))


if __name__ == "__main__":
    unittest.main()
