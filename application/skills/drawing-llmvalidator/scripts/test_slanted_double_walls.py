"""A slanted parallel pair at wall thickness is a wall."""

from __future__ import annotations

import math
import sys
import unittest
from pathlib import Path

import ezdxf

sys.path.insert(0, str(Path(__file__).resolve().parent))

import lib_llm_correct


def _line(msp, origin, angle, length, offset):
    ux, uy = math.cos(angle), math.sin(angle)
    nx, ny = -uy, ux
    x0, y0 = origin
    return msp.add_line(
        (x0 + nx * offset, y0 + ny * offset),
        (x0 + ux * length + nx * offset, y0 + uy * length + ny * offset),
        dxfattribs={"layer": "BASE"},
    )


class SlantedDoubleWallTests(unittest.TestCase):
    def test_near_vertical_double_becomes_wall(self):
        doc = ezdxf.new()
        msp = doc.modelspace()
        angle = math.radians(99.6)
        face = _line(msp, (0.0, 0.0), angle, 9000.0, 0.0)
        mate = _line(msp, (0.0, 0.0), angle, 4200.0, 200.0)
        thin = [
            _line(msp, (20000.0, 0.0), angle, 9000.0, 0.0),
            _line(msp, (20000.0, 0.0), angle, 9000.0, 24.0),
        ]

        promoted = lib_llm_correct.promote_slanted_double_walls(msp)

        self.assertEqual(promoted, 2)
        self.assertEqual(face.dxf.layer, "WALL")
        self.assertEqual(mate.dxf.layer, "WALL")
        self.assertTrue(all(line.dxf.layer == "BASE" for line in thin))

    def test_long_diagonal_beside_a_short_stub_stays_base(self):
        doc = ezdxf.new()
        msp = doc.modelspace()
        angle = math.radians(21.8)
        long = _line(msp, (0.0, 0.0), angle, 88797.0, 0.0)
        _line(msp, (70000.0, 0.0), angle, 2768.0, 188.0)

        lib_llm_correct.promote_slanted_double_walls(msp)

        self.assertEqual(long.dxf.layer, "BASE")

    def test_polyline_slanted_double_and_mullion_become_wall(self):
        """오송클리닉처럼 2점 폴리 대각 이중면과 사이 칸도 WALL."""
        doc = ezdxf.new()
        msp = doc.modelspace()
        angle = math.radians(-135.0)
        ux, uy = math.cos(angle), math.sin(angle)
        nx, ny = -uy, ux
        length = 8000.0
        gap = 148.0

        def _poly_face(offset):
            x0, y0 = nx * offset, ny * offset
            return msp.add_lwpolyline(
                [(x0, y0), (x0 + ux * length, y0 + uy * length)],
                dxfattribs={"layer": "BASE"},
            )

        face = _poly_face(0.0)
        mate = _poly_face(gap)
        # 면 사이·면에 평행한 얇은 멀리언(~18 mm × ~950 mm)
        a0 = 2000.0
        a1 = 2950.0
        mullion = msp.add_lwpolyline(
            [
                (ux * a0 + nx * 60.0, uy * a0 + ny * 60.0),
                (ux * a1 + nx * 60.0, uy * a1 + ny * 60.0),
                (ux * a1 + nx * 78.0, uy * a1 + ny * 78.0),
                (ux * a0 + nx * 78.0, uy * a0 + ny * 78.0),
            ],
            close=True,
            dxfattribs={"layer": "BASE"},
        )

        promoted = lib_llm_correct.promote_slanted_double_walls(msp)

        self.assertGreaterEqual(promoted, 3)
        self.assertEqual(face.dxf.layer, "WALL")
        self.assertEqual(mate.dxf.layer, "WALL")
        self.assertEqual(mullion.dxf.layer, "WALL")

    def test_corridor_parallel_wall_faces_not_treated_as_hatch(self):
        """복도처럼 벽 두께 이중면이 여러 줄 나란해도 해칭이 아니다.

        바로유 sheet_01 기울어진 빈 실 외곽: 120–150 mm 이중면 옆에
        다른 벽면이 200–350 mm 떨어져 있어도 WALL로 올린다.
        """
        doc = ezdxf.new()
        msp = doc.modelspace()
        angle = math.radians(30.0)
        face = _line(msp, (0.0, 0.0), angle, 6000.0, 0.0)
        mate = _line(msp, (0.0, 0.0), angle, 6000.0, 130.0)
        # 옆 복도·칸 벽 (해칭이 아닌 별도 이중면)
        _line(msp, (0.0, 0.0), angle, 5000.0, 260.0)
        _line(msp, (0.0, 0.0), angle, 5000.0, 390.0)
        _line(msp, (0.0, 0.0), angle, 4500.0, -280.0)

        promoted = lib_llm_correct.promote_slanted_double_walls(msp)

        self.assertGreaterEqual(promoted, 2)
        self.assertEqual(face.dxf.layer, "WALL")
        self.assertEqual(mate.dxf.layer, "WALL")

    def test_dense_hatch_band_stays_base(self):
        """간격 ≤45 mm로 5줄 이상 이어진 사선 해칭은 벽이 아니다."""
        doc = ezdxf.new()
        msp = doc.modelspace()
        angle = math.radians(30.0)
        lines = [_line(msp, (0.0, 0.0), angle, 6000.0, 40.0 * i) for i in range(5)]

        promoted = lib_llm_correct.promote_slanted_double_walls(msp)

        self.assertEqual(promoted, 0)
        self.assertTrue(all(line.dxf.layer == "BASE" for line in lines))

    def test_short_door_flank_double_becomes_wall(self):
        """문 옆 ~0.65–0.75 m 경사 이중면도 WALL (2 m 미만)."""
        doc = ezdxf.new()
        msp = doc.modelspace()
        angle = math.radians(30.0)
        face = _line(msp, (41780.0, 59222.0), angle, 748.0, 0.0)
        mate = _line(msp, (41780.0, 59222.0), angle, 646.0, 97.0)

        promoted = lib_llm_correct.promote_slanted_double_walls(msp)

        self.assertEqual(promoted, 2)
        self.assertEqual(face.dxf.layer, "WALL")
        self.assertEqual(mate.dxf.layer, "WALL")

    def test_door_opening_thickness_jamb_becomes_wall(self):
        """이중면 끝(문 개구)을 잇는 두께 잼도 WALL."""
        doc = ezdxf.new()
        msp = doc.modelspace()
        angle = math.radians(30.0)
        gap = 199.0
        face = _line(msp, (40504.0, 53634.0), angle, 953.0, 0.0)
        mate = _line(msp, (40504.0, 53634.0), angle, 748.0, gap)
        # 개구 쪽 끝(짧은 쪽)을 잇는 잼
        ux, uy = math.cos(angle), math.sin(angle)
        nx, ny = -uy, ux
        # face 끝점 (짧은 쪽 = origin + length*u 의 반대: origin)
        # _line origin is start; jamb at far ends of shorter overlap
        fx1 = 40504.0 + 953.0 * ux
        fy1 = 53634.0 + 953.0 * uy
        mx1 = 40504.0 + gap * nx + 748.0 * ux
        my1 = 53634.0 + gap * ny + 748.0 * uy
        # Use ends near the short face end — align jamb to mate end projecting to face
        # Simpler: jamb from face start to mate start (same along)
        jamb = msp.add_line(
            (40504.0, 53634.0),
            (40504.0 + gap * nx, 53634.0 + gap * ny),
            dxfattribs={"layer": "BASE"},
        )

        promoted = lib_llm_correct.promote_slanted_double_walls(msp)

        self.assertGreaterEqual(promoted, 3)
        self.assertEqual(face.dxf.layer, "WALL")
        self.assertEqual(mate.dxf.layer, "WALL")
        self.assertEqual(jamb.dxf.layer, "WALL")

    def test_reclaims_door_stolen_double_face(self):
        """분절 스윙이 이중면 한쪽을 DOOR로 올려도 짝과 함께 WALL로 되돌린다."""
        doc = ezdxf.new()
        msp = doc.modelspace()
        angle = math.radians(30.0)
        face = _line(msp, (41780.0, 59222.0), angle, 748.0, 0.0)
        mate = _line(msp, (41780.0, 59222.0), angle, 646.0, 97.0)
        face.dxf.layer = "DOOR"
        face.dxf.color = 3

        promoted = lib_llm_correct.promote_slanted_double_walls(msp)

        self.assertGreaterEqual(promoted, 2)
        self.assertEqual(face.dxf.layer, "WALL")
        self.assertEqual(mate.dxf.layer, "WALL")

    def test_multipoint_near_vertical_corner_double_becomes_wall(self):
        """WALL에 닿은 다중점·거의 수직 이중면도 WALL (바로유 병실 모서리)."""
        doc = ezdxf.new()
        msp = doc.modelspace()
        # 수평 WALL 끝에서 이어지는 분절 경로 (수직에서 1–5°).
        msp.add_line((0.0, 1000.0), (2000.0, 1000.0), dxfattribs={"layer": "WALL"})
        face = msp.add_lwpolyline(
            [(2000.0, 1000.0), (2010.0, 500.0), (2030.0, 0.0), (2060.0, -500.0)],
            dxfattribs={"layer": "BASE"},
        )
        mate = msp.add_lwpolyline(
            [(2190.0, 1000.0), (2200.0, 500.0), (2220.0, 0.0), (2250.0, -500.0)],
            dxfattribs={"layer": "BASE"},
        )
        # WALL에 안 닿은 가구형 이중면은 남긴다.
        furniture_a = msp.add_lwpolyline(
            [(5000.0, 0.0), (5005.0, -400.0), (5010.0, -800.0)],
            dxfattribs={"layer": "BASE"},
        )
        furniture_b = msp.add_lwpolyline(
            [(5190.0, 0.0), (5195.0, -400.0), (5200.0, -800.0)],
            dxfattribs={"layer": "BASE"},
        )

        promoted = lib_llm_correct.promote_slanted_double_walls(msp)

        self.assertGreaterEqual(promoted, 2)
        self.assertEqual(face.dxf.layer, "WALL")
        self.assertEqual(mate.dxf.layer, "WALL")
        self.assertEqual(furniture_a.dxf.layer, "BASE")
        self.assertEqual(furniture_b.dxf.layer, "BASE")

    def test_l_polyline_edge_pairs_thin_wall_mate(self):
        """L자 다중점의 긴 변이 얇은(≈48 mm) WALL 짝과 나란하면 WALL.

        바로유 sheet_01 대각 외창·문 사이 피어: 3점 폴리 긴 면이 시종점 현으로
        짝지어지지 않고, gap 70 하한에도 걸렸다.
        """
        doc = ezdxf.new()
        msp = doc.modelspace()
        angle = math.radians(120.3)
        ux, uy = math.cos(angle), math.sin(angle)
        nx, ny = -uy, ux
        face = 529.0
        gap = 48.0
        # Already-promoted mate face
        msp.add_line(
            (nx * gap, ny * gap),
            (face * ux + nx * gap, face * uy + ny * gap),
            dxfattribs={"layer": "WALL"},
        )
        # L-poly: long face + short return (same entity stays BASE until promote)
        stub = 148.0
        poly = msp.add_lwpolyline(
            [
                (0.0, 0.0),
                (face * ux, face * uy),
                (
                    face * ux + stub * math.cos(math.radians(31.0)),
                    face * uy + stub * math.sin(math.radians(31.0)),
                ),
            ],
            dxfattribs={"layer": "BASE"},
        )

        promoted = lib_llm_correct.promote_slanted_double_walls(msp)

        self.assertGreaterEqual(promoted, 1)
        self.assertEqual(poly.dxf.layer, "WALL")

    def test_slanted_wall_slab_polyline_becomes_wall(self):
        """한 폴리 기울어진 벽 슬랩(두께≤350)은 WALL."""
        doc = ezdxf.new()
        msp = doc.modelspace()
        angle = math.radians(30.0)
        ux, uy = math.cos(angle), math.sin(angle)
        nx, ny = -uy, ux
        face, thick = 600.0, 200.0
        slab = msp.add_lwpolyline(
            [
                (0.0, 0.0),
                (face * ux, face * uy),
                (face * ux + thick * nx, face * uy + thick * ny),
                (thick * nx, thick * ny),
                (0.0, 0.0),
            ],
            dxfattribs={"layer": "BASE"},
        )

        promoted = lib_llm_correct.promote_slanted_double_walls(msp)

        self.assertGreaterEqual(promoted, 1)
        self.assertEqual(slab.dxf.layer, "WALL")

    def test_thick_door_flank_slab_becomes_wall(self):
        """문 옆 두꺼운(~500 mm) 기울어진 슬랩도 WALL (바로유 스윙 옆)."""
        doc = ezdxf.new()
        msp = doc.modelspace()
        angle = math.radians(119.8)
        ux, uy = math.cos(angle), math.sin(angle)
        nx, ny = -uy, ux
        face, thick = 600.0, 498.0
        # Door leaf endpoint near first vertex
        msp.add_line(
            (50.0, 0.0),
            (50.0 + 900.0 * ux, 0.0 + 900.0 * uy),
            dxfattribs={"layer": "DOOR"},
        )
        slab = msp.add_lwpolyline(
            [
                (0.0, 0.0),
                (face * ux, face * uy),
                (face * ux + thick * nx, face * uy + thick * ny),
                (thick * nx, thick * ny),
                (0.0, 0.0),
            ],
            dxfattribs={"layer": "BASE"},
        )
        # Far square-ish thick slab without door stays BASE
        far = msp.add_lwpolyline(
            [
                (20000.0, 0.0),
                (20000.0 + 550.0 * ux, 550.0 * uy),
                (20000.0 + 550.0 * ux + 549.0 * nx, 550.0 * uy + 549.0 * ny),
                (20000.0 + 549.0 * nx, 549.0 * ny),
                (20000.0, 0.0),
            ],
            dxfattribs={"layer": "BASE"},
        )

        promoted = lib_llm_correct.promote_slanted_double_walls(msp)

        self.assertGreaterEqual(promoted, 1)
        self.assertEqual(slab.dxf.layer, "WALL")
        self.assertEqual(far.dxf.layer, "BASE")
        # AABB가 정사각처럼 보여도 기둥으로 덮어쓰지 않는다.
        lib_llm_correct.mark_doors_and_columns(msp)
        self.assertEqual(slab.dxf.layer, "WALL")


if __name__ == "__main__":
    unittest.main()
