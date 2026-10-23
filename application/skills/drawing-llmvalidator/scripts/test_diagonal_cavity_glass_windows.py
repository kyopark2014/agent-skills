"""사선 이중벽 면 사이 평행 유리선은 WINDOW, 바깥 면·끝막이는 WALL."""

from __future__ import annotations

import math
import sys
import unittest
from pathlib import Path

import ezdxf

sys.path.insert(0, str(Path(__file__).resolve().parent))

import lib_llm_correct


def _line(msp, origin, angle, length, offset, layer="BASE"):
    ux, uy = math.cos(angle), math.sin(angle)
    nx, ny = -uy, ux
    x0 = origin[0] + nx * offset
    y0 = origin[1] + ny * offset
    return msp.add_line(
        (x0, y0),
        (x0 + ux * length, y0 + uy * length),
        dxfattribs={"layer": layer},
    )


class DiagonalCavityGlassWindowTests(unittest.TestCase):
    def test_glass_between_diagonal_wall_faces_is_window(self):
        """전남대 예대 3F 교수연구실형: 400 mm 사선 이중벽 + 중간단 유리."""
        doc = ezdxf.new()
        msp = doc.modelspace()
        angle = math.radians(45.0)
        ux, uy = math.cos(angle), math.sin(angle)
        origin = (0.0, 0.0)
        # 바깥 면 (길이 2550, 간격 400)
        face0 = _line(msp, origin, angle, 2550.0, 0.0)
        face1 = _line(msp, origin, angle, 2550.0, 400.0)
        # 중간단 유리 (800 + 1630)
        glass = [
            _line(msp, origin, angle, 800.0, 120.0),
            _line(msp, origin, angle, 800.0, 140.0),
            _line(msp, (ux * 820.0, uy * 820.0), angle, 1630.0, 120.0),
            _line(msp, (ux * 820.0, uy * 820.0), angle, 1630.0, 140.0),
        ]
        # 유리 시작 끝막이 (두께 방향)
        nx, ny = -uy, ux
        jamb = msp.add_line(
            (0.0, 0.0),
            (nx * 400.0, ny * 400.0),
            dxfattribs={"layer": "BASE"},
        )
        outsider = _line(msp, (5000.0, 0.0), angle, 800.0, 120.0)

        n_window, n_wall = lib_llm_correct.promote_diagonal_cavity_glass_windows(msp)

        self.assertEqual(n_window, 4)
        self.assertGreaterEqual(n_wall, 2)
        self.assertEqual(face0.dxf.layer, "WALL")
        self.assertEqual(face1.dxf.layer, "WALL")
        self.assertTrue(all(line.dxf.layer == "WINDOW" for line in glass))
        self.assertEqual(jamb.dxf.layer, "WALL")
        self.assertEqual(outsider.dxf.layer, "BASE")

    def test_reclaims_door_misclassified_short_glass(self):
        """스윙 오인으로 DOOR 가 된 짧은 사선 캐비티 유리도 WINDOW 로 회수."""
        doc = ezdxf.new()
        msp = doc.modelspace()
        angle = math.radians(30.0)
        origin = (40000.0, 60000.0)
        # ~1 m 면 쌍(바로유 외창형) + 짧은 유리.
        _line(msp, origin, angle, 1100.0, 0.0, layer="WALL")
        _line(msp, origin, angle, 1100.0, 100.0, layer="WALL")
        glass = _line(msp, origin, angle, 220.0, 50.0, layer="DOOR")
        glass.dxf.color = 3

        n_window, _n_wall = lib_llm_correct.promote_diagonal_cavity_glass_windows(msp)
        self.assertGreaterEqual(n_window, 1)
        self.assertEqual(glass.dxf.layer, "WINDOW")


if __name__ == "__main__":
    unittest.main()
