"""연속 문 옆: stub 다음의 긴 이중선도 복도 문옆 승격 대상이다."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

import ezdxf

sys.path.insert(0, str(Path(__file__).resolve().parent))

from correct._rules import (
    BASE_LAYER,
    WALL_LAYER,
    iter_axis_segs,
    promote_corridor_door_flanks,
)


def _h(msp, x0, x1, y, layer):
    return msp.add_line((x0, y), (x1, y), dxfattribs={"layer": layer, "color": 1})


class CorridorDoorFlankChainTests(unittest.TestCase):
    def test_second_door_right_flank_promotes_after_stub(self):
        """WALL–문–stub–문–긴 이중선 → stub와 긴 면 모두 승격.

        김해복음병원 서관 2F 심장내과#3 하단과 같은 패턴.
        """
        doc = ezdxf.new()
        msp = doc.modelspace()
        # 이미 WALL인 복도/실 벽
        _h(msp, 0, 2670, 150, WALL_LAYER)
        _h(msp, 0, 2670, 0, WALL_LAYER)
        # 문1 (1280) 뒤 짧은 stub, 문2 (1280) 뒤 긴 이중선
        stub_a = _h(msp, 3950, 4450, 150, BASE_LAYER)
        stub_b = _h(msp, 3950, 4450, 0, BASE_LAYER)
        long_a = _h(msp, 5730, 8000, 150, BASE_LAYER)
        long_b = _h(msp, 5730, 8000, 0, BASE_LAYER)
        # 문 개구 안 가구(두께 40)는 올리면 안 된다.
        desk_a = _h(msp, 4650, 5970, 95, BASE_LAYER)
        desk_b = _h(msp, 4650, 5970, 55, BASE_LAYER)

        segs = list(iter_axis_segs(msp, min_len_mm=70.0))
        promoted = promote_corridor_door_flanks(segs)
        ids = {id(s.entity) for s in promoted}

        self.assertIn(id(stub_a), ids)
        self.assertIn(id(stub_b), ids)
        self.assertIn(id(long_a), ids)
        self.assertIn(id(long_b), ids)
        self.assertNotIn(id(desk_a), ids)
        self.assertNotIn(id(desk_b), ids)


if __name__ == "__main__":
    unittest.main()
