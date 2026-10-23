"""사선 짧은 유리 베이(WINDOW) + 양옆 두꺼운 피어(WALL)."""

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


class DiagonalWindowSidePierTests(unittest.TestCase):
    def test_bay_glass_is_window_and_flank_piers_are_walls(self):
        doc = ezdxf.new()
        msp = doc.modelspace()
        angle = math.radians(45.0)
        ux, uy = math.cos(angle), math.sin(angle)
        # 유리 베이: 간격 10 mm · 길이 800
        glass_origin = (0.0, 0.0)
        glass = [
            _line(msp, glass_origin, angle, 800.0, 0.0),
            _line(msp, glass_origin, angle, 800.0, 10.0),
        ]
        # 좌 피어(SW): 두께 ~120 mm 4줄 · 길이 700, 유리 시작 바로 앞
        left_origin = (-ux * 750.0, -uy * 750.0)
        left_piers = [
            _line(msp, left_origin, angle, 700.0, 0.0),
            _line(msp, left_origin, angle, 700.0, 20.0),
            _line(msp, left_origin, angle, 700.0, 70.0),
            _line(msp, left_origin, angle, 700.0, 120.0),
        ]
        # 우 피어(NE): 유리 끝 바로 뒤
        right_origin = (ux * 850.0, uy * 850.0)
        right_piers = [
            _line(msp, right_origin, angle, 700.0, 0.0),
            _line(msp, right_origin, angle, 700.0, 20.0),
            _line(msp, right_origin, angle, 700.0, 70.0),
            _line(msp, right_origin, angle, 700.0, 120.0),
        ]
        # 관련 없는 짧은 이중선
        lone = [
            _line(msp, (5000.0, 0.0), angle, 800.0, 0.0),
            _line(msp, (5000.0, 0.0), angle, 800.0, 10.0),
        ]

        n_window, n_wall = lib_llm_correct.promote_diagonal_window_side_piers(msp)

        self.assertEqual(n_window, 2)
        self.assertEqual(n_wall, 8)
        self.assertTrue(all(line.dxf.layer == "WINDOW" for line in glass))
        self.assertTrue(all(line.dxf.layer == "WALL" for line in left_piers))
        self.assertTrue(all(line.dxf.layer == "WALL" for line in right_piers))
        self.assertTrue(all(line.dxf.layer == "BASE" for line in lone))

    def test_sawtooth_collinear_bay_stays_window_not_tip_wall(self):
        """긴 톱니 유리와 같은 면의 짧은 베이는 tip WALL이 아니라 WINDOW."""
        doc = ezdxf.new()
        msp = doc.modelspace()
        angle = math.radians(45.0)
        ux, uy = math.cos(angle), math.sin(angle)
        # 긴 톱니 유리 쌍 (2.4 m, 간격 24 mm)
        long_glass = [
            _line(msp, (0.0, 0.0), angle, 2400.0, 0.0, "WALL"),
            _line(msp, (0.0, 0.0), angle, 2400.0, 24.0, "WALL"),
        ]
        # 양끝 연결면
        ret_ang = angle - math.pi / 2.0
        rx, ry = math.cos(ret_ang), math.sin(ret_ang)
        returns = [
            msp.add_line((0.0, 0.0), (rx * 136.0, ry * 136.0), dxfattribs={"layer": "BASE"}),
            msp.add_line(
                (ux * 2400.0, uy * 2400.0),
                (ux * 2400.0 + rx * 136.0, uy * 2400.0 + ry * 136.0),
                dxfattribs={"layer": "BASE"},
            ),
        ]
        # 같은 면의 짧은 베이 (긴 유리 앞쪽)
        bay = [
            _line(msp, (-ux * 900.0, -uy * 900.0), angle, 800.0, 0.0, "BASE"),
            _line(msp, (-ux * 900.0, -uy * 900.0), angle, 800.0, 24.0, "BASE"),
        ]

        n_window, n_wall = lib_llm_correct.separate_sawtooth_diagonal_windows(msp)

        self.assertGreaterEqual(n_window, 4)
        self.assertTrue(all(line.dxf.layer == "WINDOW" for line in long_glass))
        self.assertTrue(all(line.dxf.layer == "WINDOW" for line in bay))
        self.assertTrue(any(line.dxf.layer == "WALL" for line in returns))

    def test_lwpolyline_bay_with_long_face_walls(self):
        """PDF형 2점 폴리선 유리 + 긴 이중 외벽면 → WINDOW / WALL."""
        doc = ezdxf.new()
        msp = doc.modelspace()
        angle = math.radians(30.0)
        ux, uy = math.cos(angle), math.sin(angle)
        nx, ny = -uy, ux

        def _poly(origin, length, offset, layer="BASE"):
            x0 = origin[0] + nx * offset
            y0 = origin[1] + ny * offset
            return msp.add_lwpolyline(
                [(x0, y0), (x0 + ux * length, y0 + uy * length)],
                dxfattribs={"layer": layer},
            )

        # 유리 베이 4줄 (바로유 한방병원형)
        glass = [
            _poly((0.0, 0.0), 1060.0, 20.0),
            _poly((0.0, 0.0), 1060.0, 38.0),
            _poly((0.0, 0.0), 1060.0, 100.0),
            _poly((0.0, 0.0), 1060.0, 118.0),
        ]
        # 긴 이중 외벽면 (베이보다 양옆으로 이어짐)
        faces = [
            _poly((-ux * 800.0, -uy * 800.0), 2800.0, 0.0),
            _poly((-ux * 800.0, -uy * 800.0), 2800.0, 148.0),
        ]
        # 유리 끝 멀리언
        jamb = msp.add_lwpolyline(
            [(0.0, 0.0), (nx * 148.0, ny * 148.0)],
            dxfattribs={"layer": "BASE"},
        )

        n_window, n_wall = lib_llm_correct.promote_diagonal_window_side_piers(msp)

        self.assertGreaterEqual(n_window, 2)
        self.assertGreaterEqual(n_wall, 2)
        self.assertTrue(any(line.dxf.layer == "WINDOW" for line in glass))
        self.assertTrue(all(line.dxf.layer == "WALL" for line in faces))
        self.assertEqual(jamb.dxf.layer, "WALL")

    def test_multiline_bay_without_flank_piers_is_window(self):
        """피어 없이 5줄 평행 베이(바로유 sheet_01 대각 외창)도 WINDOW."""
        doc = ezdxf.new()
        msp = doc.modelspace()
        angle = math.radians(30.0)
        ux, uy = math.cos(angle), math.sin(angle)
        nx, ny = -uy, ux

        def _poly(origin, length, offset, layer="BASE"):
            x0 = origin[0] + nx * offset
            y0 = origin[1] + ny * offset
            return msp.add_lwpolyline(
                [(x0, y0), (x0 + ux * length, y0 + uy * length)],
                dxfattribs={"layer": layer},
            )

        # 두께 ~350 mm 5줄: 바깥·안쪽 + 가운데 유리 이중(18 mm)
        bay = [
            _poly((0.0, 0.0), 930.0, 0.0),
            _poly((0.0, 0.0), 925.0, 90.0),
            _poly((0.0, 0.0), 933.0, 108.0),
            _poly((0.0, 0.0), 930.0, 199.0),
            _poly((0.0, 0.0), 1032.0, 347.0),
        ]
        # 창 양옆(진행 방향) 평행선 — WALL 이어야 한다.
        # 왼쪽: 두꺼운 피어 팩 / 오른쪽: 얇은 이중(피어 거부) → flank fallback.
        left_flank = [
            _poly((-ux * 1100.0, -uy * 1100.0), 1050.0, 90.0),
            _poly((-ux * 1100.0, -uy * 1100.0), 1020.0, 160.0),
        ]
        right_flank = [
            _poly((ux * 980.0, uy * 980.0), 520.0, 0.0),
            _poly((ux * 980.0, uy * 980.0), 500.0, 30.0),
        ]
        # 베이와 길이 방향 겹치는 옆면 — 끝 피어로 오인되면 안 됨.
        bay_side = [
            _poly((-ux * 50.0, -uy * 50.0), 1030.0, 260.0),
            _poly((-ux * 50.0, -uy * 50.0), 1030.0, 400.0),
        ]
        lone = [
            _poly((5000.0, 0.0), 900.0, 0.0),
            _poly((5000.0, 0.0), 900.0, 18.0),
        ]

        n_window, n_wall = lib_llm_correct.promote_diagonal_window_side_piers(msp)

        self.assertGreaterEqual(n_window, 3)
        self.assertGreaterEqual(n_wall, 2)
        self.assertTrue(any(line.dxf.layer == "WINDOW" for line in bay[:4]))
        self.assertTrue(any(line.dxf.layer == "WALL" for line in left_flank))
        self.assertTrue(any(line.dxf.layer == "WALL" for line in right_flank))
        self.assertTrue(all(line.dxf.layer == "BASE" for line in lone))


if __name__ == "__main__":
    unittest.main()
