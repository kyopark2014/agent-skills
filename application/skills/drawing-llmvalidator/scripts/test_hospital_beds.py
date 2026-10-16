"""선분으로 그린 병원 침대 — 짧은 WALL 변을 기하로 demote."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

import ezdxf

sys.path.insert(0, str(Path(__file__).resolve().parent))

import lib_llm_correct


def _h(msp, x0, x1, y, layer="BASE"):
    return msp.add_line((x0, y), (x1, y), dxfattribs={"layer": layer, "color": 8 if layer == "BASE" else 1})


def _v(msp, x, y0, y1, layer="WALL"):
    return msp.add_line((x, y0), (x, y1), dxfattribs={"layer": layer, "color": 1 if layer == "WALL" else 8})


def _bed(msp, x0, y0, length=1950.0, width=900.0, *, pillow_at_right=False):
    """머리 이중 WALL + 발 WALL + 긴 BASE + 베개. pillow_at_right면 머리가 오른쪽."""
    x1 = x0 + length
    y1 = y0 + width
    left0 = _v(msp, x0, y0, y1, "WALL")
    left1 = _v(msp, x0 + 40.0, y0, y1, "WALL")
    right0 = _v(msp, x1 - 40.0, y0, y1, "WALL")
    right1 = _v(msp, x1, y0, y1, "WALL")
    _h(msp, x0 + 150.0, x1 - 150.0, y0, "BASE")
    _h(msp, x0 + 150.0, x1 - 150.0, y1, "BASE")
    if pillow_at_right:
        pillow = _v(msp, x1 - 140.0, y0 + 100.0, y1 - 100.0, "WALL")
        _v(msp, x1 - 100.0, y0 + 100.0, y1 - 100.0, "WALL")
    else:
        pillow = _v(msp, x0 + 100.0, y0 + 100.0, y1 - 100.0, "WALL")
        _v(msp, x0 + 140.0, y0 + 100.0, y1 - 100.0, "WALL")
    # 방 외벽(긴 벽) — demote 되면 안 됨
    wall = _v(msp, x0, y0 - 500.0, y1 + 500.0, "WALL")
    return left0, left1, right0, right1, pillow, wall


class HospitalBedTests(unittest.TestCase):
    def test_line_drawn_bed_short_walls_demoted(self):
        doc = ezdxf.new()
        msp = doc.modelspace()
        left0, left1, right0, right1, pillow, wall = _bed(msp, 0.0, 0.0)

        demoted = lib_llm_correct.demote_hospital_beds(msp)

        self.assertIn(id(left0), demoted)
        self.assertIn(id(left1), demoted)
        self.assertIn(id(right0), demoted)
        self.assertIn(id(right1), demoted)
        self.assertIn(id(pillow), demoted)
        self.assertNotIn(id(wall), demoted)

    def test_pillow_on_right_head_also_demoted(self):
        """503호 오른쪽 열처럼 머리가 반대쪽(오른쪽)인 침대도 demote."""
        doc = ezdxf.new()
        msp = doc.modelspace()
        left0, left1, right0, right1, pillow, wall = _bed(
            msp, 0.0, 0.0, pillow_at_right=True
        )

        demoted = lib_llm_correct.demote_hospital_beds(msp)

        self.assertIn(id(left0), demoted)
        self.assertIn(id(right0), demoted)
        self.assertIn(id(right1), demoted)
        self.assertIn(id(pillow), demoted)
        self.assertNotIn(id(wall), demoted)

    def test_plain_corridor_double_not_demoted(self):
        """침대 긴 변·베개 없는 짧은 이중면은 침대가 아니다."""
        doc = ezdxf.new()
        msp = doc.modelspace()
        a = _v(msp, 0.0, 0.0, 900.0, "WALL")
        b = _v(msp, 40.0, 0.0, 900.0, "WALL")
        c = _v(msp, 2000.0, 0.0, 900.0, "WALL")
        d = _v(msp, 2040.0, 0.0, 900.0, "WALL")

        demoted = lib_llm_correct.demote_hospital_beds(msp)

        self.assertFalse({id(a), id(b), id(c), id(d)} & demoted)

    def test_base_pillow_and_frame_block_promote(self):
        """503호처럼 WALL 짧은 변이 지워진 뒤 BASE 베개·프레임 잔여도 승격 차단."""
        doc = ezdxf.new()
        msp = doc.modelspace()
        # 머리·발 WALL 없이: BASE 베개(700) + BASE 프레임(1200) + 긴 BASE + 안쪽 베개
        foot = _v(msp, 0.0, 100.0, 800.0, "BASE")  # 700
        frame = _v(msp, 1860.0, 0.0, 1200.0, "BASE")  # 1200
        pillow = _v(msp, 160.0, 150.0, 850.0, "BASE")  # 700 near foot
        _h(msp, 50.0, 1800.0, 0.0, "BASE")
        _h(msp, 50.0, 1800.0, 1200.0, "BASE")
        promoted = _v(msp, 400.0, 100.0, 800.0, "WALL")  # remnant short WALL

        demoted = lib_llm_correct.demote_hospital_beds(msp)

        self.assertIn(id(foot), demoted)
        self.assertIn(id(frame), demoted)
        self.assertIn(id(pillow), demoted)
        self.assertIn(id(promoted), demoted)


if __name__ == "__main__":
    unittest.main()
