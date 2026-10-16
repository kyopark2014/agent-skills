"""실간 격벽을 가로지르는 문 틈 브리지는 하지 않는다."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

import ezdxf

sys.path.insert(0, str(Path(__file__).resolve().parent))

import evaluate_room as room


class PartitionBridgeTests(unittest.TestCase):
    def test_neighbor_bathroom_walls_do_not_bridge_across_partition(self):
        """512·505처럼 이웃 욕실 하벽 틈(1.7 m)에 실간 벽이 있으면 잇지 않는다."""
        doc = ezdxf.new()
        msp = doc.modelspace()
        wall = {"layer": "WALL"}
        # 두 욕실 하벽 (같은 y), 사이 1665 mm, 가운데 실간 세로벽
        msp.add_line((0, 5000), (1485, 5000), dxfattribs=wall)
        msp.add_line((3150, 5000), (4635, 5000), dxfattribs=wall)
        msp.add_line((2000, 0), (2000, 8000), dxfattribs=wall)
        segs = room.wall_segments(msp, [])

        h_final, _v, _d, _xa, _ya = room.bridged_runs(
            segs, (3500.0, 3000.0), 20000.0, door="close"
        )

        at_y = None
        for y, ivs in h_final.items():
            if abs(y - 5000.0) <= 20.0:
                at_y = ivs
                break
        self.assertIsNotNone(at_y)
        self.assertEqual(len(at_y), 2, msg=f"bridged across partition: {at_y}")
        self.assertFalse(any(a <= 2500.0 <= b for a, b in at_y))

    def test_plain_door_gap_still_bridges_without_partition(self):
        """실간 벽이 없으면 2.4 m 이하 문 틈은 그대로 잇는다."""
        doc = ezdxf.new()
        msp = doc.modelspace()
        wall = {"layer": "WALL"}
        msp.add_line((0, 5000), (1000, 5000), dxfattribs=wall)
        msp.add_line((2500, 5000), (4000, 5000), dxfattribs=wall)
        segs = room.wall_segments(msp, [])

        h_final, _v, _d, _xa, _ya = room.bridged_runs(
            segs, (2000.0, 3000.0), 20000.0, door="close"
        )

        at_y = None
        for y, ivs in h_final.items():
            if abs(y - 5000.0) <= 20.0:
                at_y = ivs
                break
        self.assertIsNotNone(at_y)
        self.assertEqual(len(at_y), 1)
        self.assertAlmostEqual(at_y[0][0], 0.0, delta=1.0)
        self.assertAlmostEqual(at_y[0][1], 4000.0, delta=1.0)


if __name__ == "__main__":
    unittest.main()
