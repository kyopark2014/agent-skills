"""쪼개진 긴 이중선 BASE → WALL (소화수조 옆)."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

import ezdxf

sys.path.insert(0, str(Path(__file__).resolve().parent))

import lib_llm_correct


class CorridorSplitDoubleTests(unittest.TestCase):
    def test_split_upper_face_still_promotes(self):
        """아래 한 줄 + 위가 두 조각인 200 mm 이중선도 복도 승격한다."""
        doc = ezdxf.new()
        msp = doc.modelspace()
        lower = msp.add_line(
            (0, 0), (24300, 0), dxfattribs={"layer": "BASE", "color": 8}
        )
        upper_a = msp.add_line(
            (0, 200), (13850, 200), dxfattribs={"layer": "BASE", "color": 8}
        )
        upper_b = msp.add_line(
            (13850, 200), (24100, 200), dxfattribs={"layer": "BASE", "color": 8}
        )

        segs = lib_llm_correct.iter_axis_segs(msp, min_len_mm=70.0)
        promoted = lib_llm_correct.promote_corridor_walls(segs)
        ids = {id(s.entity) for s in promoted}
        self.assertIn(id(lower), ids)
        self.assertIn(id(upper_a), ids)
        self.assertIn(id(upper_b), ids)


if __name__ == "__main__":
    unittest.main()
