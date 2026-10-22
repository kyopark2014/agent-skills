"""Diagonal hatch (poche) filling a thin band is a wall."""

from __future__ import annotations

import math
import sys
import unittest
from pathlib import Path

import ezdxf

sys.path.insert(0, str(Path(__file__).resolve().parent))

import lib_llm_correct


def _stroke(msp, x0, y0, length=90.0, angle_deg=45.0, layer="BASE"):
    ang = math.radians(angle_deg)
    x1 = x0 + length * math.cos(ang)
    y1 = y0 + length * math.sin(ang)
    return msp.add_lwpolyline(
        [(x0, y0), (x1, y1)],
        dxfattribs={"layer": layer},
    )


class DiagonalHatchWallTests(unittest.TestCase):
    def test_closed_shell_with_diagonal_strokes_becomes_wall(self):
        doc = ezdxf.new()
        msp = doc.modelspace()
        shell = msp.add_lwpolyline(
            [(0, 0), (100, 0), (100, 2000), (0, 2000)],
            close=True,
            dxfattribs={"layer": "BASE"},
        )
        strokes = [
            _stroke(msp, 10, y, length=80.0, angle_deg=45.0)
            for y in range(50, 1950, 80)
        ]
        empty = msp.add_lwpolyline(
            [(400, 0), (500, 0), (500, 2000), (400, 2000)],
            close=True,
            dxfattribs={"layer": "BASE"},
        )

        promoted = lib_llm_correct.promote_diagonal_hatch_walls(msp)

        self.assertGreaterEqual(promoted, 1 + len(strokes))
        self.assertEqual(shell.dxf.layer, "WALL")
        self.assertTrue(all(stroke.dxf.layer == "WALL" for stroke in strokes))
        self.assertEqual(empty.dxf.layer, "BASE")

    def test_zigzag_band_becomes_wall(self):
        doc = ezdxf.new()
        msp = doc.modelspace()
        pts = []
        x0, x1 = 0.0, 60.0
        y = 0.0
        while y < 700.0:
            pts.append((x0, y))
            pts.append((x1, y + 40.0))
            y += 80.0
        zigzag = msp.add_lwpolyline(pts, dxfattribs={"layer": "BASE"})

        promoted = lib_llm_correct.promote_diagonal_hatch_walls(msp)

        self.assertGreaterEqual(promoted, 1)
        self.assertEqual(zigzag.dxf.layer, "WALL")

    def test_stroke_band_without_shell_becomes_wall(self):
        doc = ezdxf.new()
        msp = doc.modelspace()
        strokes = [
            _stroke(msp, x, 0.0, length=80.0, angle_deg=45.0)
            for x in range(0, 1600, 80)
        ]

        promoted = lib_llm_correct.promote_diagonal_hatch_walls(msp)

        self.assertGreaterEqual(promoted, len(strokes))
        self.assertTrue(all(stroke.dxf.layer == "WALL" for stroke in strokes))

    def test_shelless_band_with_283mm_strokes_becomes_wall(self):
        """CAD 포셰 사선이 ~283 mm 이면 짧은 획으로 묶어 외곽 없이 WALL."""
        doc = ezdxf.new()
        msp = doc.modelspace()
        strokes = [
            _stroke(msp, x, 0.0, length=283.0, angle_deg=45.0)
            for x in range(0, 1600, 90)
        ]

        promoted = lib_llm_correct.promote_diagonal_hatch_walls(msp)

        self.assertGreaterEqual(promoted, len(strokes))
        self.assertTrue(all(stroke.dxf.layer == "WALL" for stroke in strokes))

    def test_shelless_band_includes_end_stroke_outside_gap(self):
        """띠 끝에서 간격으로 빠진 이웃 사선도 같은 WALL."""
        doc = ezdxf.new()
        msp = doc.modelspace()
        main = [
            _stroke(msp, x, 0.0, length=90.0, angle_deg=45.0)
            for x in range(0, 1600, 80)
        ]
        # 그룹 간격(250) 밖으로 살짝 떨어진 끝단 획(띠 bbox 끝 +120 mm 안)
        tip = _stroke(msp, -80.0, 10.0, length=90.0, angle_deg=45.0)

        promoted = lib_llm_correct.promote_diagonal_hatch_walls(msp)

        self.assertGreaterEqual(promoted, len(main) + 1)
        self.assertEqual(tip.dxf.layer, "WALL")
        self.assertTrue(all(stroke.dxf.layer == "WALL" for stroke in main))

    def test_same_height_distant_bands_promote_separately(self):
        """층 전체 같은 높이 사선이 한 묶음이 되어 need가 부풀지 않게 한다."""
        doc = ezdxf.new()
        msp = doc.modelspace()
        left = [
            _stroke(msp, x, 0.0, length=80.0, angle_deg=45.0)
            for x in range(0, 1600, 80)
        ]
        right = [
            _stroke(msp, x, 0.0, length=80.0, angle_deg=45.0)
            for x in range(20000, 21600, 80)
        ]

        promoted = lib_llm_correct.promote_diagonal_hatch_walls(msp)

        self.assertGreaterEqual(promoted, len(left) + len(right))
        self.assertTrue(all(stroke.dxf.layer == "WALL" for stroke in left))
        self.assertTrue(all(stroke.dxf.layer == "WALL" for stroke in right))

    def test_chamfered_shell_with_strokes_becomes_wall(self):
        doc = ezdxf.new()
        msp = doc.modelspace()
        # 모따기 한 변 있는 얇은 세로 포셰
        shell = msp.add_lwpolyline(
            [
                (0, 800),
                (50, 800),
                (50, 0),
                (100, 0),
                (100, 2000),
                (0, 1900),
            ],
            close=True,
            dxfattribs={"layer": "BASE"},
        )
        strokes = [
            _stroke(msp, 10, y, length=80.0, angle_deg=45.0)
            for y in range(100, 1900, 80)
        ]

        promoted = lib_llm_correct.promote_diagonal_hatch_walls(msp)

        self.assertGreaterEqual(promoted, 1)
        self.assertEqual(shell.dxf.layer, "WALL")
        self.assertTrue(all(stroke.dxf.layer == "WALL" for stroke in strokes))

    def test_pier_shell_with_long_diagonal_fill_becomes_wall(self):
        """모서리 피어: 약 600×900 사각을 긴 사선으로 채운 면은 벽이다."""
        doc = ezdxf.new()
        msp = doc.modelspace()
        shell = msp.add_lwpolyline(
            [(0, 0), (600, 0), (600, 900), (0, 900)],
            close=True,
            dxfattribs={"layer": "BASE"},
        )
        strokes = []
        for i in range(8):
            x0 = 20.0 + i * 40.0
            y0 = 20.0
            length = 500.0
            ang = math.radians(45.0)
            strokes.append(
                msp.add_lwpolyline(
                    [
                        (x0, y0),
                        (x0 + length * math.cos(ang), y0 + length * math.sin(ang)),
                    ],
                    dxfattribs={"layer": "BASE"},
                )
            )

        promoted = lib_llm_correct.promote_diagonal_hatch_walls(msp)

        self.assertGreaterEqual(promoted, 1)
        self.assertEqual(shell.dxf.layer, "WALL")
        self.assertTrue(all(stroke.dxf.layer == "WALL" for stroke in strokes))

    def test_square_pier_with_full_diagonal_stays_wall_not_column(self):
        """외벽 정사각 사선 피어는 WALL이며 COLUMN으로 나누지 않는다."""
        doc = ezdxf.new()
        msp = doc.modelspace()
        shell = msp.add_lwpolyline(
            [(0, 0), (600, 0), (600, 600), (0, 600)],
            close=True,
            dxfattribs={"layer": "BASE"},
        )
        diag = msp.add_lwpolyline(
            [(0, 0), (600, 600)],
            dxfattribs={"layer": "BASE"},
        )

        lib_llm_correct.promote_diagonal_hatch_walls(msp)
        lib_llm_correct.mark_doors_and_columns(msp)

        self.assertEqual(shell.dxf.layer, "WALL")
        self.assertEqual(diag.dxf.layer, "WALL")

    def test_three_point_diagonal_stroke_in_pier_becomes_wall(self):
        """중간 점이 있는 거의 일직선 사선도 피어 채움으로 본다."""
        doc = ezdxf.new()
        msp = doc.modelspace()
        shell = msp.add_lwpolyline(
            [(0, 0), (600, 0), (600, 900), (0, 900)],
            close=True,
            dxfattribs={"layer": "BASE"},
        )
        strokes = []
        for i in range(6):
            x0 = 30.0 + i * 50.0
            y0 = 30.0
            # 끝점 사이 대각선, 중간에 끊긴 점
            x1 = x0 + 400.0
            y1 = y0 + 400.0
            mid = ((x0 + x1) * 0.5, (y0 + y1) * 0.5)
            strokes.append(
                msp.add_lwpolyline(
                    [(x0, y0), mid, (x1, y1)],
                    dxfattribs={"layer": "BASE"},
                )
            )

        promoted = lib_llm_correct.promote_diagonal_hatch_walls(msp)

        self.assertGreaterEqual(promoted, 1)
        self.assertEqual(shell.dxf.layer, "WALL")
        self.assertTrue(all(s.dxf.layer == "WALL" for s in strokes))

    def test_square_pier_about_900mm_with_long_diagonals_becomes_wall(self):
        """약 900 mm 정사각 외벽 피어도 긴 사선 채움이면 WALL이다."""
        doc = ezdxf.new()
        msp = doc.modelspace()
        shell = msp.add_lwpolyline(
            [(0, 0), (902, 0), (902, 902), (0, 902)],
            close=True,
            dxfattribs={"layer": "BASE"},
        )
        strokes = []
        for i in range(6):
            x0 = 20.0 + i * 60.0
            y0 = 20.0
            length = 700.0
            ang = math.radians(45.0)
            strokes.append(
                msp.add_lwpolyline(
                    [
                        (x0, y0),
                        (x0 + length * math.cos(ang), y0 + length * math.sin(ang)),
                    ],
                    dxfattribs={"layer": "BASE"},
                )
            )

        promoted = lib_llm_correct.promote_diagonal_hatch_walls(msp)

        self.assertGreaterEqual(promoted, 1)
        self.assertEqual(shell.dxf.layer, "WALL")
        self.assertTrue(all(s.dxf.layer == "WALL" for s in strokes))

    def test_empty_pier_sized_box_abutting_hatch_stays_base(self):
        """사선 없는 피어 크기 빈 네모는 포쉐에 붙어 있어도 가구(BASE)다."""
        doc = ezdxf.new()
        msp = doc.modelspace()
        thin = msp.add_lwpolyline(
            [(0, 0), (1200, 0), (1200, 100), (0, 100)],
            close=True,
            dxfattribs={"layer": "BASE"},
        )
        thin_strokes = [
            _stroke(msp, x, 10.0, length=80.0, angle_deg=45.0)
            for x in range(20, 1180, 70)
        ]
        # 침대 옆 빈 사각(~1048×603): 사선 거의 없음
        empty = msp.add_lwpolyline(
            [(100, 100), (1148, 100), (1148, 703), (100, 703)],
            close=True,
            dxfattribs={"layer": "BASE"},
        )

        lib_llm_correct.promote_diagonal_hatch_walls(msp)

        self.assertEqual(thin.dxf.layer, "WALL")
        self.assertTrue(all(s.dxf.layer == "WALL" for s in thin_strokes))
        self.assertEqual(empty.dxf.layer, "BASE")

    def test_elbow_poche_abutting_thin_band_becomes_wall(self):
        """L자 두꺼운 포셰가 사선 채움 얇은 띠에 붙으면 WALL이다."""
        doc = ezdxf.new()
        msp = doc.modelspace()
        thin = msp.add_lwpolyline(
            [(0, 0), (800, 0), (800, 100), (0, 100)],
            close=True,
            dxfattribs={"layer": "BASE"},
        )
        thin_strokes = [
            _stroke(msp, x, 10.0, length=80.0, angle_deg=45.0)
            for x in range(20, 780, 70)
        ]
        # L: 세로 팔 + 아래 가로 팔 (오송클리닉 sheet_01 형태)
        elbow = msp.add_lwpolyline(
            [
                (200, 100),
                (200, 1800),
                (850, 1800),
                (850, 700),
                (800, 700),
                (800, 100),
            ],
            close=True,
            dxfattribs={"layer": "BASE"},
        )
        elbow_strokes = [
            _stroke(msp, 220, y, length=120.0, angle_deg=45.0)
            for y in range(200, 1600, 200)
        ]

        promoted = lib_llm_correct.promote_diagonal_hatch_walls(msp)

        self.assertGreaterEqual(promoted, 1)
        self.assertEqual(thin.dxf.layer, "WALL")
        self.assertEqual(elbow.dxf.layer, "WALL")
        self.assertTrue(all(s.dxf.layer == "WALL" for s in thin_strokes))
        self.assertTrue(all(s.dxf.layer == "WALL" for s in elbow_strokes))

    def test_review_demote_keeps_diagonal_hatch_poche(self):
        doc = ezdxf.new()
        msp = doc.modelspace()
        shell = msp.add_lwpolyline(
            [(0, 0), (4500, 0), (4500, 100), (0, 100)],
            close=True,
            dxfattribs={"layer": "WALL", "color": 1},
        )
        strokes = [
            _stroke(msp, x, 10.0, length=80.0, angle_deg=45.0, layer="WALL")
            for x in range(50, 4400, 80)
        ]
        # 실제 침대 크기(~2.2×0.9 m). 피어(≤1.05×1.5 m)와 겹치지 않게 둔다.
        bed = msp.add_lwpolyline(
            [(2000, -500), (4180, -500), (4180, 440), (2000, 440)],
            close=True,
            dxfattribs={"layer": "WALL", "color": 1},
        )

        demote = lib_llm_correct.review_demote_ids(
            msp,
            [
                {
                    "xmin": 1900.0,
                    "ymin": -600.0,
                    "xmax": 4300.0,
                    "ymax": 550.0,
                    "label": "침대",
                }
            ],
        )

        self.assertNotIn(id(shell), demote)
        self.assertTrue(all(id(stroke) not in demote for stroke in strokes))
        self.assertIn(id(bed), demote)

    def test_fused_diagonal_and_axis_polyline_becomes_wall(self):
        """사선+수평이 한 3점 폴리로 붙은 포셰 획도 WALL(오송클리닉 카운터 위)."""
        doc = ezdxf.new()
        msp = doc.modelspace()
        shell = msp.add_lwpolyline(
            [(0, 0), (2000, 0), (2000, 100), (0, 100)],
            close=True,
            dxfattribs={"layer": "BASE"},
        )
        strokes = [
            _stroke(msp, x, 10.0, length=80.0, angle_deg=45.0)
            for x in range(50, 1800, 80)
        ]
        # 실제 CAD처럼 사선 한 획과 셸 바닥 수평이 한 폴리로 이어짐.
        fused = msp.add_lwpolyline(
            [(1900.0, 100.0), (1800.0, 0.0), (1100.0, 0.0)],
            dxfattribs={"layer": "BASE"},
        )

        promoted = lib_llm_correct.promote_diagonal_hatch_walls(msp)

        self.assertGreaterEqual(promoted, 1 + len(strokes) + 1)
        self.assertEqual(shell.dxf.layer, "WALL")
        self.assertEqual(fused.dxf.layer, "WALL")
        self.assertTrue(all(stroke.dxf.layer == "WALL" for stroke in strokes))

    def test_thick_bar_abutting_elbow_becomes_wall(self):
        """사선 L자 코너에 붙는 두꺼운 긴 포셰 띠도 WALL이다."""
        doc = ezdxf.new()
        msp = doc.modelspace()
        elbow = msp.add_lwpolyline(
            [
                (4100, 0),
                (5700, 0),
                (5700, 500),
                (4700, 500),
                (4700, 300),
                (4100, 300),
            ],
            close=True,
            dxfattribs={"layer": "BASE"},
        )
        elbow_strokes = [
            _stroke(msp, 4200 + i * 120, 50.0, length=200.0, angle_deg=45.0)
            for i in range(8)
        ]
        # 코너와 ~300 mm 떨어진 두꺼운 수평 포셰(실제 sheet_01 형태)
        bar = msp.add_lwpolyline(
            [(0, 0), (3800, 0), (3800, 650), (0, 650)],
            close=True,
            dxfattribs={"layer": "BASE"},
        )

        promoted = lib_llm_correct.promote_diagonal_hatch_walls(msp)

        self.assertGreaterEqual(promoted, 1)
        self.assertEqual(elbow.dxf.layer, "WALL")
        self.assertEqual(bar.dxf.layer, "WALL")
        self.assertTrue(all(s.dxf.layer == "WALL" for s in elbow_strokes))


if __name__ == "__main__":
    unittest.main()
