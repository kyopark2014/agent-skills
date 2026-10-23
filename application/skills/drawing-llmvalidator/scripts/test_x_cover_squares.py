"""Squares covering an X become walls. A thin door X does not."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

import ezdxf

sys.path.insert(0, str(Path(__file__).resolve().parent))

import lib_llm_correct


def _rect(msp, x0, y0, x1, y1, *, closed=True):
    pts = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
    if not closed:
        pts.append((x0, y0))
    entity = msp.add_lwpolyline(pts, dxfattribs={"layer": "BASE"})
    entity.closed = closed
    return entity


class XCoverSquareTests(unittest.TestCase):
    def test_inner_and_outer_squares_become_wall(self):
        doc = ezdxf.new()
        msp = doc.modelspace()
        left = msp.add_line((0, 0), (900, 800), dxfattribs={"layer": "DOOR"})
        right = msp.add_line((900, 0), (0, 800), dxfattribs={"layer": "DOOR"})
        inner = _rect(msp, 0, 0, 900, 800)
        outer = _rect(msp, -100, -100, 1000, 900, closed=False)
        far = _rect(msp, 5000, 5000, 5900, 5800)

        promoted = lib_llm_correct.promote_x_cover_squares(msp)

        self.assertEqual(promoted, 4)
        self.assertEqual(inner.dxf.layer, "WALL")
        self.assertEqual(outer.dxf.layer, "WALL")
        self.assertEqual(far.dxf.layer, "BASE")
        self.assertEqual(left.dxf.layer, "WALL")
        self.assertEqual(right.dxf.layer, "WALL")

    def test_door_thickness_x_square_stays_base(self):
        doc = ezdxf.new()
        msp = doc.modelspace()
        msp.add_line((0, 0), (900, 150), dxfattribs={"layer": "DOOR"})
        msp.add_line((900, 0), (0, 150), dxfattribs={"layer": "DOOR"})
        cover = _rect(msp, 0, 0, 900, 150)

        promoted = lib_llm_correct.promote_x_cover_squares(msp)

        self.assertEqual(promoted, 0)
        self.assertEqual(cover.dxf.layer, "BASE")
        walls = [entity for entity in msp if entity.dxftype() == "LINE" and entity.dxf.layer == "WALL"]
        self.assertEqual(walls, [])

    def test_square_door_x_inside_furniture_stays_base(self):
        """501호처럼 ~500×500 문 X가 가구 다점 윤곽 안에 있어도 올리지 않는다."""
        doc = ezdxf.new()
        msp = doc.modelspace()
        # 가구 아랫 칸의 정사각 DOOR X
        msp.add_line((0, 0), (500, 500), dxfattribs={"layer": "DOOR"})
        msp.add_line((500, 0), (0, 500), dxfattribs={"layer": "DOOR"})
        pocket = _rect(msp, 0, 0, 500, 500)
        # 지그재그 옷장/단차 윤곽(~500×3500) — X 변에 걸치면 통째 승격되던 형태
        furniture = msp.add_lwpolyline(
            [
                (500.0, 3500.0),
                (100.0, 3200.0),
                (100.0, 1680.0),
                (50.0, 1630.0),
                (0.0, 1580.0),
                (0.0, 250.0),
                (250.0, 0.0),
                (500.0, 0.0),
            ],
            close=True,
            dxfattribs={"layer": "BASE", "color": 8},
        )

        promoted = lib_llm_correct.promote_x_cover_squares(msp)

        self.assertEqual(promoted, 0)
        self.assertEqual(pocket.dxf.layer, "BASE")
        self.assertEqual(furniture.dxf.layer, "BASE")

    def test_bare_x_gets_a_virtual_square(self):
        doc = ezdxf.new()
        msp = doc.modelspace()
        left = msp.add_line((0, 0), (900, 800), dxfattribs={"layer": "DOOR"})
        right = msp.add_line((900, 0), (0, 800), dxfattribs={"layer": "DOOR"})

        promoted = lib_llm_correct.promote_x_cover_squares(msp)

        self.assertEqual(promoted, 6)
        self.assertEqual(left.dxf.layer, "WALL")
        self.assertEqual(right.dxf.layer, "WALL")
        walls = [
            (
                (round(entity.dxf.start.x), round(entity.dxf.start.y)),
                (round(entity.dxf.end.x), round(entity.dxf.end.y)),
            )
            for entity in msp
            if entity.dxftype() == "LINE" and entity.dxf.layer == "WALL"
        ]
        self.assertIn(((0, 0), (900, 800)), walls)
        self.assertIn(((900, 0), (0, 800)), walls)
        self.assertIn(((0, 0), (900, 0)), walls)
        self.assertIn(((900, 0), (900, 800)), walls)
        self.assertIn(((900, 800), (0, 800)), walls)
        self.assertIn(((0, 800), (0, 0)), walls)

    def test_rotated_bare_x_gets_oriented_square(self):
        """기운 승강기 X는 축정렬 AABB가 아니라 같은 각도의 네모를 그린다."""
        import math

        doc = ezdxf.new()
        msp = doc.modelspace()
        ang = math.radians(30.0)

        def rot(x: float, y: float) -> tuple[float, float]:
            return (
                x * math.cos(ang) - y * math.sin(ang),
                x * math.sin(ang) + y * math.cos(ang),
            )

        corners = [rot(x, y) for x, y in [(-850, -800), (850, -800), (850, 800), (-850, 800)]]
        diag_a = msp.add_line(corners[0], corners[2], dxfattribs={"layer": "BASE"})
        diag_b = msp.add_line(corners[1], corners[3], dxfattribs={"layer": "BASE"})
        # 예전에 잘못 그린 축정렬 AABB 변이 있어도 지우고 OBB만 남긴다.
        xs = [c[0] for c in corners]
        ys = [c[1] for c in corners]
        ax0, ax1, ay0, ay1 = min(xs), max(xs), min(ys), max(ys)
        bad = [
            msp.add_line((ax0, ay0), (ax1, ay0), dxfattribs={"layer": "WALL", "color": 1}),
            msp.add_line((ax1, ay0), (ax1, ay1), dxfattribs={"layer": "WALL", "color": 1}),
            msp.add_line((ax1, ay1), (ax0, ay1), dxfattribs={"layer": "WALL", "color": 1}),
            msp.add_line((ax0, ay1), (ax0, ay0), dxfattribs={"layer": "WALL", "color": 1}),
        ]

        promoted = lib_llm_correct.promote_x_cover_squares(msp)

        self.assertGreaterEqual(promoted, 6)
        self.assertEqual(diag_a.dxf.layer, "WALL")
        self.assertEqual(diag_b.dxf.layer, "WALL")
        for entity in bad:
            # 삭제됐거나 BASE로 내려감
            try:
                layer = entity.dxf.layer
            except Exception:  # noqa: BLE001
                continue
            self.assertNotEqual(layer, "WALL")
        side_angs = []
        for entity in msp:
            if entity.dxftype() != "LINE" or entity.dxf.layer != "WALL":
                continue
            x0, y0 = float(entity.dxf.start.x), float(entity.dxf.start.y)
            x1, y1 = float(entity.dxf.end.x), float(entity.dxf.end.y)
            length = math.hypot(x1 - x0, y1 - y0)
            if length > 2000.0:
                continue  # 대각선
            ang_deg = math.degrees(math.atan2(y1 - y0, x1 - x0)) % 180.0
            near_axis = min(abs(ang_deg - 0.0), abs(ang_deg - 90.0), abs(ang_deg - 180.0))
            self.assertGreater(near_axis, 15.0, msg=f"AABB side left L={length:.0f} ang={ang_deg:.1f}")
            side_angs.append(ang_deg)
        self.assertGreaterEqual(len(side_angs), 4)
        rounded = sorted(round(a / 5.0) * 5.0 for a in side_angs)
        self.assertTrue(any(abs(a - 30.0) <= 5.0 for a in rounded))
        self.assertTrue(any(abs(a - 120.0) <= 5.0 for a in rounded))

    def test_long_duct_x_line_box_becomes_wall(self):
        """DA 급배기덕트처럼 긴 X(~5.5 m)와 선 네 개 사각도 올린다."""
        doc = ezdxf.new()
        msp = doc.modelspace()
        diag_a = msp.add_line((0, 0), (5500, 1000), dxfattribs={"layer": "BASE"})
        diag_b = msp.add_line((5500, 0), (0, 1000), dxfattribs={"layer": "BASE"})
        bot = msp.add_line((0, 0), (5500, 0), dxfattribs={"layer": "BASE"})
        top = msp.add_line((0, 1000), (5500, 1000), dxfattribs={"layer": "BASE"})
        left = msp.add_line((0, 0), (0, 1000), dxfattribs={"layer": "BASE"})
        right = msp.add_line((5500, 0), (5500, 1000), dxfattribs={"layer": "BASE"})

        promoted = lib_llm_correct.promote_x_cover_squares(msp)

        self.assertEqual(promoted, 6)
        for entity in (diag_a, diag_b, bot, top, left, right):
            self.assertEqual(entity.dxf.layer, "WALL")

    def test_tall_elev_x_with_float_width_and_poly_edge(self):
        """승강기 옆처럼 짧은 변 ~500 mm(좌표 오차) X와 2점 폴리 변도 올린다."""
        doc = ezdxf.new()
        msp = doc.modelspace()
        # 실제 DXF처럼 짧은 변이 499.999…
        x0, x1, y0, y1 = 0.0, 499.99987, 0.0, 2050.00044
        diag_a = msp.add_line((x0, y0), (x1, y1), dxfattribs={"layer": "BASE"})
        diag_b = msp.add_line((x1, y0), (x0, y1), dxfattribs={"layer": "BASE"})
        bot = msp.add_line((x0, y0), (x1, y0), dxfattribs={"layer": "BASE"})
        top = msp.add_line((x0, y1), (x1, y1), dxfattribs={"layer": "BASE"})
        left = msp.add_line((x0, y0), (x0, y1), dxfattribs={"layer": "BASE"})
        right = msp.add_lwpolyline([(x1, y0), (x1, y1)], dxfattribs={"layer": "BASE"})

        promoted = lib_llm_correct.promote_x_cover_squares(msp)

        self.assertGreaterEqual(promoted, 6)
        for entity in (diag_a, diag_b, bot, top, left, right):
            self.assertEqual(entity.dxf.layer, "WALL")

    def test_zigzag_polyline_x_square_becomes_wall(self):
        """오송클리닉처럼 지그재그 LWPOLYLINE X와 감싼 네모도 올린다."""
        doc = ezdxf.new()
        msp = doc.modelspace()
        # 두 칸 옆 샤프트를 한 폴리선으로 이은 X (실제 sheet_01 형태)
        zig_a = msp.add_lwpolyline(
            [(0, 100), (600, 700), (1200, 100), (1800, 700)],
            dxfattribs={"layer": "BASE"},
        )
        zig_b = msp.add_lwpolyline(
            [(600, 100), (1200, 700), (1800, 100)],
            dxfattribs={"layer": "BASE"},
        )
        left = _rect(msp, 600, 100, 1200, 700)
        right = _rect(msp, 1200, 100, 1800, 700)

        promoted = lib_llm_correct.promote_x_cover_squares(msp)

        self.assertGreaterEqual(promoted, 4)
        self.assertEqual(zig_a.dxf.layer, "WALL")
        self.assertEqual(zig_b.dxf.layer, "WALL")
        self.assertEqual(left.dxf.layer, "WALL")
        self.assertEqual(right.dxf.layer, "WALL")

    def test_double_line_outer_and_pocket_outline_become_wall(self):
        """승강기 X: 문턱 쪽만 더 나간 바깥 이중선과 다점 안쪽 테두리도 올린다."""
        doc = ezdxf.new()
        msp = doc.modelspace()
        x0, y0, x1, y1 = 0.0, 0.0, 1600.0, 1500.0
        diag_a = msp.add_lwpolyline([(x0, y0), (x1, y1)], dxfattribs={"layer": "BASE"})
        diag_b = msp.add_lwpolyline([(x1, y0), (x0, y1)], dxfattribs={"layer": "BASE"})
        # 안쪽: 위쪽에 문턱 포켓이 있는 12점 윤곽(세 변이 X와 일치)
        pocket = msp.add_lwpolyline(
            [
                (400.0, y1),
                (400.0, y1 + 120.0),
                (460.0, y1 + 120.0),
                (460.0, y1),
                (x0, y1),
                (x0, y0),
                (x1, y0),
                (x1, y1),
                (1140.0, y1),
                (1140.0, y1 + 120.0),
                (1200.0, y1 + 120.0),
                (1200.0, y1),
            ],
            dxfattribs={"layer": "BASE"},
        )
        # 바깥 이중선: 좌·우·아래 ~32 mm, 위(문턱) ~203 mm
        outer = _rect(msp, x0 - 32.0, y0 - 32.0, x1 + 32.0, y1 + 203.0)

        promoted = lib_llm_correct.promote_x_cover_squares(msp)

        self.assertGreaterEqual(promoted, 4)
        self.assertEqual(diag_a.dxf.layer, "WALL")
        self.assertEqual(diag_b.dxf.layer, "WALL")
        self.assertEqual(pocket.dxf.layer, "WALL")
        self.assertEqual(outer.dxf.layer, "WALL")


if __name__ == "__main__":
    unittest.main()
