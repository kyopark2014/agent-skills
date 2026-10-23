"""스윙문 옆 복도 면 — LWPOLY BASE 이어짐도 WALL로 올린다."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

import ezdxf

sys.path.insert(0, str(Path(__file__).resolve().parent))

import lib_llm_correct


class SwingSideWallTests(unittest.TestCase):
    def test_lwpoly_corridor_stub_below_kitchen_swing(self):
        """clubhouse 캐디휴게실 우측: 주방 스윙 아래 600 mm LWPOLY BASE → WALL.

        긴 WALL 면에 맞닿고 힌지에서 ~2.1 m 떨어진 복도 바깥면도 올린다.
        """
        doc = ezdxf.new()
        msp = doc.modelspace()
        hx, hy, radius = 767810.8, -59746.4, 1103.8
        # 주방 스윙
        msp.add_arc(
            (hx, hy),
            radius,
            94.9,
            172.7,
            dxfattribs={"layer": "DOOR", "color": 3},
        )
        msp.add_lwpolyline(
            [
                (766715.9, -59646.7),
                (767715.9, -59646.7),
                (767715.9, -59606.7),
                (766715.9, -59606.7),
            ],
            close=True,
            dxfattribs={"layer": "DOOR", "color": 3},
        )
        # 이중벽 긴 면 (아래쪽 이미 WALL)
        msp.add_line(
            (767915.9, -63796.7),
            (767915.9, -61896.7),
            dxfattribs={"layer": "WALL", "color": 1},
        )
        msp.add_line(
            (767715.9, -63796.7),
            (767715.9, -61896.7),
            dxfattribs={"layer": "WALL", "color": 1},
        )
        # 화살표 구간: 바깥면 600 mm LWPOLY BASE
        stub = msp.add_lwpolyline(
            [(767915.9, -61896.7), (767915.9, -61296.7)],
            close=False,
            dxfattribs={"layer": "BASE", "color": 8},
        )
        # 안쪽 면 1200 mm BASE (스윙과 WALL 사이 600 mm 틈)
        inner = msp.add_line(
            (767715.9, -61296.7),
            (767715.9, -60096.7),
            dxfattribs={"layer": "BASE", "color": 8},
        )
        # 스윙 위쪽 안면 WALL
        msp.add_line(
            (767715.9, -59896.7),
            (767715.9, -58646.7),
            dxfattribs={"layer": "WALL", "color": 1},
        )

        n = lib_llm_correct.promote_swing_side_walls(msp)
        self.assertGreaterEqual(n, 1)
        self.assertEqual(stub.dxf.layer, "WALL")
        self.assertEqual(inner.dxf.layer, "WALL")

    def test_vanity_sink_outline_stays_base(self):
        """세면기 외곽(800×500)은 스윙 옆면 규칙으로 WALL이 되지 않는다."""
        doc = ezdxf.new()
        msp = doc.modelspace()
        hx, hy, radius = 95000.0, 26800.0, 900.0
        msp.add_arc((hx, hy), radius, 0.0, 90.0, dxfattribs={"layer": "DOOR"})
        # 힌지 근처 긴 WALL (세면기 옆면이 이어진 것처럼)
        msp.add_line(
            (94700.0, 27677.5),
            (94700.0, 29000.0),
            dxfattribs={"layer": "WALL", "color": 1},
        )
        vanity = msp.add_lwpolyline(
            [
                (94703.0, 27677.5),
                (95503.0, 27677.5),
                (95503.0, 28177.5),
                (94703.0, 28177.5),
            ],
            close=True,
            dxfattribs={"layer": "BASE", "color": 8},
        )
        n = lib_llm_correct.promote_swing_side_walls(msp)
        self.assertEqual(vanity.dxf.layer, "BASE")
        self.assertEqual(n, 0)


if __name__ == "__main__":
    unittest.main()
