"""복도를 가로지르는 가짜 문틈 bridge 를 막는다."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

import ezdxf

sys.path.insert(0, str(Path(__file__).resolve().parent))

import evaluate_room as room


class CorridorThroughBridgeTests(unittest.TestCase):
    def test_opposite_jambs_not_bridged_across_corridor(self):
        """위·아래 문틀이 같은 X에 있어도 복도 가로벽이 축을 관통하면 잇지 않는다."""
        doc = ezdxf.new()
        msp = doc.modelspace()
        # 로비(왼쪽) + 동쪽 복도 칸. 세로 문틀만 407764 에 위·아래로 있음.
        # 남·북 가로벽이 그 X를 지나 410500 까지 이어짐.
        msp.add_line((400000, -40821), (410464, -40821), dxfattribs={"layer": "WALL"})
        msp.add_line((401764, -38821), (410464, -38821), dxfattribs={"layer": "WALL"})
        # 아래 문틀(사무과 쪽) · 위 문틀(접견실 쪽)
        msp.add_line((407764, -41121), (407764, -40821), dxfattribs={"layer": "WALL"})
        msp.add_line((407764, -38821), (407764, -38521), dxfattribs={"layer": "WALL"})
        # 로비 서쪽·동쪽 끝
        msp.add_line((400000, -40821), (400000, -38821), dxfattribs={"layer": "WALL"})
        msp.add_line((410564, -40821), (410564, -38821), dxfattribs={"layer": "WALL"})
        msp.add_line((400000, -41121), (407764, -41121), dxfattribs={"layer": "WALL"})
        msp.add_line((400000, -38521), (407764, -38521), dxfattribs={"layer": "WALL"})

        segs = room.wall_segments(msp, [])
        _h, v_final, *_rest = room.bridged_runs(
            segs, (404000, -39800), 20000.0, boxes=[], open_doors=[]
        )
        for x, ivs in v_final.items():
            if abs(x - 407764) > 2.0:
                continue
            for a, b in ivs:
                # 복도 구간(-40821..-38821)을 한 세로선으로 메우면 안 된다.
                self.assertFalse(
                    a <= -40800 and b >= -38850,
                    f"corridor bridged at x={x}: {[a, b]}",
                )

        face, *_ = room._room_lines(msp, segs, (404000, -39800), [], [])
        self.assertIsNotNone(face)
        # 동쪽 복도 칸 중심이 면에 포함되어야 한다.
        from shapely.geometry import Point

        self.assertTrue(face.contains(Point(408500, -39800)))


if __name__ == "__main__":
    unittest.main()
