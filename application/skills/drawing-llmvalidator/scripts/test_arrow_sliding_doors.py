"""Diagonal --> arrow marks on thin diagonal sliding panels become DOOR."""

from __future__ import annotations

import math
import sys
import unittest
from pathlib import Path

import ezdxf

sys.path.insert(0, str(Path(__file__).resolve().parent))

import lib_llm_correct


class ArrowSlidingDoorTests(unittest.TestCase):
    def test_clubhouse_diagonal_arrow_thin_panel_becomes_door(self):
        """클럽하우스 1F 서비스동선: 사선 --> + 얇은 직사각 문짝 → DOOR."""
        doc = ezdxf.new()
        msp = doc.modelspace()
        # ~45° 얇은 문짝 (두께 70, 길이 1500) — 실제 좌표 비율
        ux, uy = math.cos(math.radians(-46)), math.sin(math.radians(-46))
        nx, ny = -uy, ux
        cx, cy = 0.0, 0.0
        half_l, half_t = 750.0, 35.0
        corners = [
            (cx - ux * half_l - nx * half_t, cy - uy * half_l - ny * half_t),
            (cx + ux * half_l - nx * half_t, cy + uy * half_l - ny * half_t),
            (cx + ux * half_l + nx * half_t, cy + uy * half_l + ny * half_t),
            (cx - ux * half_l + nx * half_t, cy - uy * half_l + ny * half_t),
        ]
        panel = msp.add_lwpolyline(corners, dxfattribs={"layer": "BASE"})
        panel.closed = True
        # 샤프트 1167, 미늘 449, 꺾임 ~37°, 문짝에서 수직 ~350 mm
        shaft = 1167.0
        ax = cx - ux * (shaft * 0.5) + nx * 350.0
        ay = cy - uy * (shaft * 0.5) + ny * 350.0
        bx = ax + ux * shaft
        by = ay + uy * shaft
        # 미늘: 샤프트 끝에서 ~37° 꺾임, 길이 449
        barb_ang = math.radians(-46 + 180 - 37)
        cx2 = bx + math.cos(barb_ang) * 449.0
        cy2 = by + math.sin(barb_ang) * 449.0
        arrow = msp.add_lwpolyline(
            [(ax, ay), (bx, by), (cx2, cy2)],
            dxfattribs={"layer": "BASE"},
        )
        arrow.closed = False
        # 화살표만 있고 얇은 문짝이 없는 먼 화살표는 무시
        far = msp.add_lwpolyline(
            [(20000.0, 0.0), (21167.0, 0.0), (20987.0, -135.0)],
            dxfattribs={"layer": "BASE"},
        )
        far.closed = False

        demoted, _promoted = lib_llm_correct.separate_arrow_sliding_doors(msp)

        self.assertGreaterEqual(demoted, 2)
        self.assertEqual(panel.dxf.layer, "DOOR")
        self.assertEqual(arrow.dxf.layer, "DOOR")
        self.assertEqual(far.dxf.layer, "BASE")

    def test_no_panel_keeps_arrow(self):
        doc = ezdxf.new()
        msp = doc.modelspace()
        arrow = msp.add_lwpolyline(
            [(0.0, 0.0), (1167.0, 0.0), (987.0, -224.0)],
            dxfattribs={"layer": "BASE"},
        )
        arrow.closed = False

        demoted, promoted = lib_llm_correct.separate_arrow_sliding_doors(msp)

        self.assertEqual(demoted, 0)
        self.assertEqual(promoted, 0)
        self.assertEqual(arrow.dxf.layer, "BASE")


if __name__ == "__main__":
    unittest.main()
