"""PDF 분절 호(ARC 없음) 1/4 스윙: 문짝·호 DOOR + 닫힌 현 WALL."""

from __future__ import annotations

import math
import sys
import unittest
from pathlib import Path

import ezdxf

sys.path.insert(0, str(Path(__file__).resolve().parent))

import lib_llm_correct


def _add_segmented_quarter_swing(
    msp,
    hx: float,
    hy: float,
    radius: float = 1050.0,
    *,
    open_angle: float = 30.0,
    closed_angle: float = 120.0,
    n_arc: int = 12,
    leaf_as_wall: bool = False,
) -> None:
    """문짝 1줄 + 원주 짧은 접선으로 1/4 스윙을 그린다 (PDF 변환형)."""
    base = {"layer": "BASE", "color": 8}
    wall = {"layer": "WALL", "color": 1}
    leaf_attrs = wall if leaf_as_wall else base
    oux = math.cos(math.radians(open_angle))
    ouy = math.sin(math.radians(open_angle))
    # 열린 문짝 (2점 LWPOLYLINE)
    msp.add_lwpolyline(
        [(hx, hy), (hx + radius * oux, hy + radius * ouy)],
        dxfattribs=leaf_attrs,
    )
    # 힌지 뒤 짧은 벽
    msp.add_lwpolyline(
        [
            (hx - 250.0 * oux, hy - 250.0 * ouy),
            (hx - 20.0 * oux, hy - 20.0 * ouy),
        ],
        dxfattribs=wall,
    )
    # 원주 접선 조각
    sa = math.radians(open_angle)
    ea = math.radians(closed_angle)
    for i in range(n_arc):
        t0 = sa + (ea - sa) * i / n_arc
        t1 = sa + (ea - sa) * (i + 1) / n_arc
        x0 = hx + radius * math.cos(t0)
        y0 = hy + radius * math.sin(t0)
        x1 = hx + radius * math.cos(t1)
        y1 = hy + radius * math.sin(t1)
        msp.add_lwpolyline([(x0, y0), (x1, y1)], dxfattribs=base)


class SegmentedSwingDoorTests(unittest.TestCase):
    def test_finds_and_paints_diagonal_segmented_swing(self):
        doc = ezdxf.new()
        msp = doc.modelspace()
        hx, hy, r = 61273.0, 40878.0, 1059.0
        _add_segmented_quarter_swing(
            msp, hx, hy, r, open_angle=30.0, closed_angle=120.0, leaf_as_wall=True
        )

        found = lib_llm_correct._find_segmented_swing_doors(msp)
        self.assertEqual(len(found), 1)
        self.assertIsNotNone(found[0]["close"])
        self.assertAlmostEqual(found[0]["hx"], hx, delta=1.0)
        self.assertAlmostEqual(found[0]["hy"], hy, delta=1.0)
        self.assertAlmostEqual(found[0]["radius"], r, delta=2.0)

        n_demote, _n_promote = lib_llm_correct.separate_segmented_swing_doors(msp)
        self.assertGreaterEqual(n_demote, 1)

        door_leaves = 0
        for e in msp.query("LWPOLYLINE"):
            if e.dxf.layer != "DOOR":
                continue
            pts = list(e.get_points("xy"))
            if len(pts) != 2:
                continue
            length = math.hypot(pts[1][0] - pts[0][0], pts[1][1] - pts[0][1])
            if abs(length - r) <= 5.0:
                door_leaves += 1
        self.assertGreaterEqual(door_leaves, 1)

        # 기울어진 스윙은 가상 WALL 닫힘을 넣지 않는다.
        cx0, cy0, cx1, cy1 = found[0]["close"]
        self.assertAlmostEqual(
            math.degrees(math.atan2(cy1 - cy0, cx1 - cx0)) % 360.0, 120.0, delta=8.0
        )
        virt = 0
        for e in msp.query("LINE"):
            if e.dxf.layer != "WALL":
                continue
            a, b = e.dxf.start, e.dxf.end
            L = math.hypot(b.x - a.x, b.y - a.y)
            if abs(L - r) > 80.0:
                continue
            if min(
                math.hypot(a.x - hx, a.y - hy),
                math.hypot(b.x - hx, b.y - hy),
            ) > 80.0:
                continue
            virt += 1
        self.assertEqual(virt, 0, "tilted swing must not get virtual WALL close")

    def test_axis_aligned_segmented_swing_also_found(self):
        doc = ezdxf.new()
        msp = doc.modelspace()
        hx, hy, r = 10000.0, 20000.0, 900.0
        _add_segmented_quarter_swing(
            msp, hx, hy, r, open_angle=0.0, closed_angle=90.0, n_arc=10
        )
        found = lib_llm_correct._find_segmented_swing_doors(msp)
        self.assertEqual(len(found), 1)
        n_demote, _n_promote = lib_llm_correct.separate_segmented_swing_doors(msp)
        self.assertGreaterEqual(n_demote, 1)

    def test_multipoly_double_leaf_and_arc_found(self):
        """이중 문짝 사각형·분절 호가 다중점 LWPOLY 한 줄이어도 잡는다.

        바로유 sheet_01 주방 스윙처럼 문짝 2줄+끝막이가 5점 폴리선이고
        호 조각도 여러 점 폴리선인 PDF 변환형을 재현한다.
        """
        doc = ezdxf.new()
        msp = doc.modelspace()
        hx, hy, r = 33100.0, 65362.0, 940.0
        pack = 32.0
        base = {"layer": "BASE", "color": 8}
        wall = {"layer": "WALL", "color": 1}
        # 열린 이중 문짝(남쪽) + 힌지·팁 끝막이 → 5점 폴리선.
        msp.add_lwpolyline(
            [
                (hx + pack, hy - r),
                (hx + pack, hy),
                (hx, hy),
                (hx, hy - r),
                (hx + pack, hy - r),
            ],
            dxfattribs=base,
        )
        # 개구 양옆 벽 스터브(닫힘·플랭크).
        msp.add_lwpolyline(
            [(hx - r - 800.0, hy), (hx - r - 40.0, hy)],
            dxfattribs=wall,
        )
        msp.add_lwpolyline(
            [(hx + 40.0, hy), (hx + 800.0, hy)],
            dxfattribs=wall,
        )
        msp.add_lwpolyline(
            [(hx - r - 800.0, hy + pack), (hx - r - 40.0, hy + pack)],
            dxfattribs=wall,
        )
        msp.add_lwpolyline(
            [(hx + 40.0, hy + pack), (hx + 800.0, hy + pack)],
            dxfattribs=wall,
        )
        # 호를 한 줄 다중점 폴리선으로 (서쪽 닫힘 ← 남쪽 열림).
        arc_pts = []
        for i in range(11):
            t = math.radians(-90.0 + (-90.0) * i / 10.0)
            arc_pts.append((hx + r * math.cos(t), hy + r * math.sin(t)))
        msp.add_lwpolyline(arc_pts, dxfattribs=base)

        found = lib_llm_correct._find_segmented_swing_doors(msp)
        self.assertGreaterEqual(len(found), 1)
        near = [
            s
            for s in found
            if abs(s["hx"] - hx) < 40.0 and abs(s["hy"] - hy) < 40.0
        ]
        self.assertGreaterEqual(len(near), 1)
        n_demote, n_promote = lib_llm_correct.separate_segmented_swing_doors(msp)
        self.assertGreaterEqual(n_demote, 1)
        self.assertGreaterEqual(n_promote, 1)
        door_polys = [
            e for e in msp.query("LWPOLYLINE") if e.dxf.layer == "DOOR"
        ]
        self.assertGreaterEqual(len(door_polys), 1)
        # 닫힌 현(서쪽) WALL 가상선 — 이중 문짝이면 이중 닫힘.
        wall_close = 0
        for e in msp.query("LINE"):
            if e.dxf.layer != "WALL":
                continue
            a, b = e.dxf.start, e.dxf.end
            ax, ay, bx, by = float(a.x), float(a.y), float(b.x), float(b.y)
            if abs(ay - hy) > 40.0 and abs(by - hy) > 40.0:
                continue
            along = abs(bx - ax)
            if along < r * 0.7:
                continue
            mid_x = (ax + bx) * 0.5
            if hx - r - 50.0 <= mid_x <= hx + 50.0:
                wall_close += 1
        self.assertGreaterEqual(wall_close, 2)

    def test_diagonal_double_stroke_multipoly_leaf_found(self):
        """대각 이중 스트로크 5점 문짝+분절 호도 잡되 가상 WALL 은 넣지 않는다.

        바로유 sheet_01 화장실 스윙처럼 문짝이 왕복 5점 LWPOLY(30°/210°)이고
        호만 짧은 접선인 PDF 변환형. 기울어진 스윙은 문짝·호만 DOOR.
        """
        doc = ezdxf.new()
        msp = doc.modelspace()
        hx, hy, r = 38694.0, 60860.0, 939.0
        pack = 28.0
        open_ang = math.radians(30.0)
        arc_end = math.radians(130.0)
        expect_close = 120.0
        base = {"layer": "BASE", "color": 8}
        wall = {"layer": "WALL", "color": 1}
        oux, ouy = math.cos(open_ang), math.sin(open_ang)
        nx, ny = -ouy, oux
        tip_x, tip_y = hx + r * oux, hy + r * ouy
        msp.add_lwpolyline(
            [
                (hx, hy),
                (tip_x, tip_y),
                (tip_x + pack * nx, tip_y + pack * ny),
                (hx + pack * nx, hy + pack * ny),
                (hx, hy),
            ],
            dxfattribs=base,
        )
        # 예전에 잘못 넣었던 대각 가상 닫힘 — separate 가 지워야 한다.
        msp.add_line(
            (hx, hy),
            (
                hx + r * math.cos(math.radians(130.0)),
                hy + r * math.sin(math.radians(130.0)),
            ),
            dxfattribs=wall,
        )
        for i in range(12):
            t0 = open_ang + (arc_end - open_ang) * i / 12
            t1 = open_ang + (arc_end - open_ang) * (i + 1) / 12
            msp.add_lwpolyline(
                [
                    (hx + r * math.cos(t0), hy + r * math.sin(t0)),
                    (hx + r * math.cos(t1), hy + r * math.sin(t1)),
                ],
                dxfattribs=base,
            )

        found = lib_llm_correct._find_segmented_swing_doors(msp)
        near = [
            s
            for s in found
            if abs(s["hx"] - hx) < 40.0 and abs(s["hy"] - hy) < 40.0
        ]
        self.assertGreaterEqual(len(near), 1, f"missed diagonal double-stroke leaf: {found}")
        self.assertAlmostEqual(near[0]["radius"], r, delta=5.0)
        cx0, cy0, cx1, cy1 = near[0]["close"]
        close_ang = math.degrees(math.atan2(cy1 - cy0, cx1 - cx0)) % 360.0
        self.assertAlmostEqual(close_ang, expect_close, delta=5.0)

        n_demote, _n_promote = lib_llm_correct.separate_segmented_swing_doors(msp)
        self.assertGreaterEqual(n_demote, 1)
        door_polys = [
            e for e in msp.query("LWPOLYLINE") if e.dxf.layer == "DOOR"
        ]
        self.assertGreaterEqual(len(door_polys), 1)
        virt = 0
        for e in msp.query("LINE"):
            if e.dxf.layer != "WALL":
                continue
            a, b = e.dxf.start, e.dxf.end
            L = math.hypot(b.x - a.x, b.y - a.y)
            if abs(L - r) > 80.0:
                continue
            if min(
                math.hypot(a.x - hx, a.y - hy),
                math.hypot(b.x - hx, b.y - hy),
            ) > 80.0:
                continue
            virt += 1
        self.assertEqual(virt, 0, "tilted swing must not keep/add virtual WALL close")

    def test_four_arc_segments_still_found(self):
        """호 조각 4개·중점 반지름이 조금 흔들려도 병실 화장실문처럼 잡는다."""
        doc = ezdxf.new()
        msp = doc.modelspace()
        hx, hy, r = 84899.0, 47037.0, 987.0
        wall = {"layer": "WALL", "color": 1}
        base = {"layer": "BASE", "color": 8}
        oux = math.cos(math.radians(30.0))
        ouy = math.sin(math.radians(30.0))
        msp.add_lwpolyline(
            [(hx, hy), (hx + r * oux, hy + r * ouy)],
            dxfattribs=wall,
        )
        # 실제 PDF처럼 짧은 접선 4개 + 반지름 ±15 mm 흔들림.
        for i, ang in enumerate((40.0, 55.0, 75.0, 100.0)):
            rr = r + (-15.0 if i % 2 else 12.0)
            t0 = math.radians(ang - 4.0)
            t1 = math.radians(ang + 4.0)
            msp.add_lwpolyline(
                [
                    (hx + rr * math.cos(t0), hy + rr * math.sin(t0)),
                    (hx + rr * math.cos(t1), hy + rr * math.sin(t1)),
                ],
                dxfattribs=base,
            )
        found = lib_llm_correct._find_segmented_swing_doors(msp)
        self.assertEqual(len(found), 1)
        n_demote, _n_promote = lib_llm_correct.separate_segmented_swing_doors(msp)
        self.assertGreaterEqual(n_demote, 1)

    def test_window_leaf_reclaimed_as_door(self):
        """창 승격이 문짝을 WINDOW로 올려도 후반 분절 스윙이 DOOR로 되돌린다."""
        doc = ezdxf.new()
        msp = doc.modelspace()
        hx, hy, r = 50000.0, 40000.0, 900.0
        _add_segmented_quarter_swing(
            msp, hx, hy, r, open_angle=20.0, closed_angle=110.0, n_arc=12
        )
        # 창 승격이 문짝만 WINDOW로 덮어쓴 상태.
        for e in msp.query("LWPOLYLINE"):
            pts = list(e.get_points("xy"))
            if len(pts) != 2:
                continue
            length = math.hypot(pts[1][0] - pts[0][0], pts[1][1] - pts[0][1])
            if abs(length - r) <= 5.0:
                e.dxf.layer = "WINDOW"
                e.dxf.color = 4

        found = lib_llm_correct._find_segmented_swing_doors(msp)
        self.assertEqual(len(found), 1)
        n_demote, _n_promote = lib_llm_correct.separate_segmented_swing_doors(msp)
        self.assertGreaterEqual(n_demote, 1)
        door_leaves = 0
        for e in msp.query("LWPOLYLINE"):
            if e.dxf.layer != "DOOR":
                continue
            pts = list(e.get_points("xy"))
            if len(pts) != 2:
                continue
            length = math.hypot(pts[1][0] - pts[0][0], pts[1][1] - pts[0][1])
            if abs(length - r) <= 5.0:
                door_leaves += 1
        self.assertGreaterEqual(door_leaves, 1)

    def test_mark_window_skips_door(self):
        doc = ezdxf.new()
        msp = doc.modelspace()
        line = msp.add_line((0, 0), (900, 0), dxfattribs={"layer": "DOOR", "color": 3})
        self.assertFalse(lib_llm_correct._mark_window(line))
        self.assertEqual(line.dxf.layer, "DOOR")

    def test_closed_leaf_still_gets_virtual_wall_close(self):
        """문짝이 닫힌 현과 겹쳐 WALL이었다가 DOOR로 내려도 가상 WALL 닫힘을 남긴다."""
        doc = ezdxf.new()
        msp = doc.modelspace()
        hx, hy, r = 20000.0, 30000.0, 900.0
        # 열린 각=닫힌 각 → 문짝이 현과 동일 방향. 문짝을 WALL로 두고 호만 BASE.
        _add_segmented_quarter_swing(
            msp,
            hx,
            hy,
            r,
            open_angle=0.0,
            closed_angle=90.0,
            n_arc=10,
            leaf_as_wall=True,
        )
        # 문짝을 닫힌 쪽으로 다시 그림(현과 일치).
        for e in list(msp.query("LWPOLYLINE")):
            pts = list(e.get_points("xy"))
            if len(pts) != 2:
                continue
            length = math.hypot(pts[1][0] - pts[0][0], pts[1][1] - pts[0][1])
            if abs(length - r) > 5.0:
                continue
            if e.dxf.layer != "WALL":
                continue
            # 기존 열린 문짝 삭제 후 닫힌 방향 문짝.
            msp.delete_entity(e)
            msp.add_lwpolyline(
                [(hx, hy), (hx + r, hy)],
                dxfattribs={"layer": "WALL", "color": 1},
            )
            break
        # 호를 0→90 에 맞춰 다시 두기 위해 위 helper 호를 그대로 쓴다.
        n_demote, n_promote = lib_llm_correct.separate_segmented_swing_doors(msp)
        self.assertGreaterEqual(n_demote, 1)
        self.assertGreaterEqual(n_promote, 1)
        found = lib_llm_correct._find_segmented_swing_doors(msp)
        self.assertEqual(len(found), 1)
        cx0, cy0, cx1, cy1 = found[0]["close"]
        wall_close = 0
        for e in msp.query("LINE"):
            if e.dxf.layer != "WALL":
                continue
            a, b = e.dxf.start, e.dxf.end
            if (
                math.hypot(a.x - cx0, a.y - cy0) < 8.0
                and math.hypot(b.x - cx1, b.y - cy1) < 8.0
            ) or (
                math.hypot(a.x - cx1, a.y - cy1) < 8.0
                and math.hypot(b.x - cx0, b.y - cy0) < 8.0
            ):
                wall_close += 1
        self.assertGreaterEqual(wall_close, 1)

    def test_second_pass_keeps_virtual_close_as_wall(self):
        """1차에서 만든 닫힌 현 WALL을 2차가 문짝으로 훔쳐 DOOR로 칠하지 않는다."""
        doc = ezdxf.new()
        msp = doc.modelspace()
        hx, hy, r = 64257.0, 30427.0, 711.0
        _add_segmented_quarter_swing(
            msp, hx, hy, r, open_angle=90.0, closed_angle=-17.8, n_arc=8
        )
        # 축정렬 문짝+가상 WALL LINE 닫힘이 같은 힌지 키를 쓸 때도 유지.
        n_demote1, n_promote1 = lib_llm_correct.separate_segmented_swing_doors(msp)
        self.assertGreaterEqual(n_demote1, 1)
        self.assertGreaterEqual(n_promote1, 1)

        found = lib_llm_correct._find_segmented_swing_doors(msp)
        real = [s for s in found if abs(s["hx"] - hx) < 2.0 and abs(s["hy"] - hy) < 2.0]
        self.assertEqual(len(real), 1)
        cx0, cy0, cx1, cy1 = real[0]["close"]

        def _wall_close_count() -> int:
            n = 0
            for e in msp.query("LINE"):
                if e.dxf.layer != "WALL":
                    continue
                a, b = e.dxf.start, e.dxf.end
                if (
                    math.hypot(a.x - cx0, a.y - cy0) < 8.0
                    and math.hypot(b.x - cx1, b.y - cy1) < 8.0
                ) or (
                    math.hypot(a.x - cx1, a.y - cy1) < 8.0
                    and math.hypot(b.x - cx0, b.y - cy0) < 8.0
                ):
                    n += 1
            return n

        self.assertGreaterEqual(_wall_close_count(), 1)
        # 2차: 닫힌 현을 문짝으로 오인하는 가짜 힌지가 생겨도 WALL 닫힘은 유지.
        lib_llm_correct.separate_segmented_swing_doors(msp)
        self.assertGreaterEqual(_wall_close_count(), 1)
        for e in msp.query("LINE"):
            if e.dxf.layer != "DOOR":
                continue
            a, b = e.dxf.start, e.dxf.end
            same = (
                math.hypot(a.x - cx0, a.y - cy0) < 8.0
                and math.hypot(b.x - cx1, b.y - cy1) < 8.0
            ) or (
                math.hypot(a.x - cx1, a.y - cy1) < 8.0
                and math.hypot(b.x - cx0, b.y - cy0) < 8.0
            )
            self.assertFalse(same, "virtual close must stay WALL, not DOOR")

    def test_double_stroke_leaf_still_found(self):
        """문짝 이중 스트로크(sep~25)가 있어도 1/4 스윙은 DOOR."""
        doc = ezdxf.new()
        msp = doc.modelspace()
        hx, hy, r = 45640.0, 61673.0, 1035.0
        _add_segmented_quarter_swing(
            msp, hx, hy, r, open_angle=180.0, closed_angle=270.0, n_arc=10
        )
        msp.add_lwpolyline(
            [(hx, hy + 25.0), (hx - r, hy + 25.0)],
            dxfattribs={"layer": "BASE", "color": 8},
        )
        found = lib_llm_correct._find_segmented_swing_doors(msp)
        self.assertGreaterEqual(len(found), 1)
        n_demote, _n_promote = lib_llm_correct.separate_segmented_swing_doors(msp)
        self.assertGreaterEqual(n_demote, 1)

    def test_residual_arc_segments_reclaimed(self):
        """탐지 때 빠진 원주 조각도 separate 가 DOOR 로 회수한다."""
        doc = ezdxf.new()
        msp = doc.modelspace()
        hx, hy, r = 20000.0, 30000.0, 1000.0
        _add_segmented_quarter_swing(
            msp, hx, hy, r, open_angle=0.0, closed_angle=90.0, n_arc=8
        )
        t0, t1 = math.radians(50.0), math.radians(58.0)
        rr = r + 55.0
        leftover = msp.add_lwpolyline(
            [
                (hx + rr * math.cos(t0), hy + rr * math.sin(t0)),
                (hx + rr * math.cos(t1), hy + rr * math.sin(t1)),
            ],
            dxfattribs={"layer": "BASE", "color": 8},
        )
        n_demote, _n_promote = lib_llm_correct.separate_segmented_swing_doors(msp)
        self.assertGreaterEqual(n_demote, 1)
        self.assertEqual(leftover.dxf.layer, "DOOR")

    def test_wall_pack_leaf_swaps_to_open_radial(self):
        """벽 다발 안 선은 닫힌 현, 호 끝 방사선이 열린 문짝·양옆 WALL."""
        doc = ezdxf.new()
        msp = doc.modelspace()
        hx, hy, r = 45640.0, 61673.0, 1035.0
        # 닫힌 위치(E-W) 선 + 벽 다발 평행선
        closed = msp.add_lwpolyline(
            [(hx, hy), (hx - r, hy)],
            dxfattribs={"layer": "BASE", "color": 8},
        )
        for off in (80.0, 160.0):
            msp.add_lwpolyline(
                [(hx - 200.0, hy + off), (hx - r - 200.0, hy + off)],
                dxfattribs={"layer": "BASE", "color": 8},
            )
        # 열린 문짝(S) + 호
        opened = msp.add_lwpolyline(
            [(hx, hy), (hx, hy - r)],
            dxfattribs={"layer": "BASE", "color": 8},
        )
        for i in range(10):
            t0 = math.radians(180.0 + 9.0 * i)
            t1 = math.radians(180.0 + 9.0 * (i + 1))
            msp.add_lwpolyline(
                [
                    (hx + r * math.cos(t0), hy + r * math.sin(t0)),
                    (hx + r * math.cos(t1), hy + r * math.sin(t1)),
                ],
                dxfattribs={"layer": "BASE", "color": 8},
            )
        # 양옆 플랭크
        left = msp.add_lwpolyline(
            [(hx - r - 50.0, hy), (hx - r - 550.0, hy)],
            dxfattribs={"layer": "BASE", "color": 8},
        )
        right = msp.add_lwpolyline(
            [(hx + 50.0, hy), (hx + 550.0, hy)],
            dxfattribs={"layer": "BASE", "color": 8},
        )

        found = lib_llm_correct._find_segmented_swing_doors(msp)
        self.assertGreaterEqual(len(found), 1)
        # 열린 문짝 각은 대략 남쪽(-90°/270°)
        leaf_deg = math.degrees(found[0]["leaf_ang"]) % 360.0
        self.assertTrue(
            abs(leaf_deg - 270.0) < 25.0 or abs(leaf_deg - 90.0) < 25.0,
            f"leaf_ang={leaf_deg}",
        )
        n_demote, n_promote = lib_llm_correct.separate_segmented_swing_doors(msp)
        self.assertGreaterEqual(n_demote, 1)
        self.assertGreaterEqual(n_promote, 1)
        self.assertEqual(opened.dxf.layer, "DOOR")
        self.assertEqual(closed.dxf.layer, "WALL")
        self.assertTrue(left.dxf.layer == "WALL" or right.dxf.layer == "WALL")

    def test_wall_pack_without_open_leaf_closes_along_wall(self):
        """열린 문짝 선 없이 벽 다발 안 선만 있어도 닫힘은 벽 방향·이중면."""
        doc = ezdxf.new()
        msp = doc.modelspace()
        hx, hy, r = 64175.0, 61997.0, 1105.0
        wall = {"layer": "WALL", "color": 1}
        base = {"layer": "BASE", "color": 8}
        # 힌지면·바깥면: 개구 양끝 스터브 (이중벽)
        msp.add_line((hx, hy), (hx + 900.0, hy), dxfattribs=wall)
        msp.add_line((hx - r - 900.0, hy), (hx - r, hy), dxfattribs=wall)
        msp.add_line((hx, hy - 120.0), (hx + 900.0, hy - 120.0), dxfattribs=wall)
        msp.add_line((hx - r - 900.0, hy - 120.0), (hx - r, hy - 120.0), dxfattribs=wall)
        # wall_pack≥2: 문짝과 겹치는 평행 다발
        for off in (80.0, 160.0):
            msp.add_lwpolyline(
                [(hx + 50.0, hy - off), (hx - r - 50.0, hy - off)],
                dxfattribs=base,
            )
        # 벽 두께 잼
        far_jamb = msp.add_line(
            (hx - r, hy - 120.0), (hx - r, hy), dxfattribs=base
        )
        near_jamb = msp.add_line((hx, hy - 120.0), (hx, hy), dxfattribs=wall)
        # 닫힌 위치 선만 (열린 문짝 없음) + 호 180→245 (65°만 벌어짐)
        closed = msp.add_lwpolyline(
            [(hx, hy), (hx - r, hy)],
            dxfattribs=base,
        )
        for i in range(10):
            t0 = math.radians(180.0 + 6.5 * i)
            t1 = math.radians(180.0 + 6.5 * (i + 1))
            msp.add_lwpolyline(
                [
                    (hx + r * math.cos(t0), hy + r * math.sin(t0)),
                    (hx + r * math.cos(t1), hy + r * math.sin(t1)),
                ],
                dxfattribs=base,
            )

        found = lib_llm_correct._find_segmented_swing_doors(msp)
        self.assertGreaterEqual(len(found), 1)
        hit = [s for s in found if abs(s["hx"] - hx) < 2.0 and abs(s["hy"] - hy) < 2.0]
        self.assertEqual(len(hit), 1)
        cx0, cy0, cx1, cy1 = hit[0]["close"]
        close_ang = math.degrees(math.atan2(cy1 - cy0, cx1 - cx0)) % 360.0
        # 닫힘은 E-W 벽 방향 (0° 또는 180°) — 호 끝 245°로 대각 오닫힘 금지
        self.assertTrue(
            abs(close_ang - 180.0) < 20.0 or abs(close_ang - 0.0) < 20.0,
            f"close_ang={close_ang}",
        )
        self.assertGreater(
            min(abs(close_ang - 245.0) % 360.0, 360.0 - abs(close_ang - 245.0) % 360.0),
            30.0,
            f"must not close diagonally toward arc tip, close_ang={close_ang}",
        )

        n_demote, n_promote = lib_llm_correct.separate_segmented_swing_doors(msp)
        self.assertGreaterEqual(n_demote, 1)
        self.assertGreaterEqual(n_promote, 1)
        self.assertEqual(closed.dxf.layer, "WALL")
        self.assertEqual(far_jamb.dxf.layer, "WALL")
        self.assertEqual(near_jamb.dxf.layer, "WALL")

        # 힌지면·바깥면 둘 다 개구가 막혀야 한다.
        def _face_covered(y: float) -> float:
            lo, hi = sorted((hx - r, hx))
            span = hi - lo
            covered = 0.0
            cursor = lo
            intervals = []
            for e in msp.query("LINE"):
                if e.dxf.layer != "WALL":
                    continue
                a, b = e.dxf.start, e.dxf.end
                if abs(a.y - y) > 8.0 or abs(b.y - y) > 8.0:
                    continue
                wlo, whi = sorted((float(a.x), float(b.x)))
                intervals.append((max(lo, wlo), min(hi, whi)))
            for e in msp.query("LWPOLYLINE"):
                if e.dxf.layer != "WALL":
                    continue
                pts = list(e.get_points("xy"))
                if len(pts) != 2:
                    continue
                if abs(pts[0][1] - y) > 8.0 or abs(pts[1][1] - y) > 8.0:
                    continue
                wlo, whi = sorted((float(pts[0][0]), float(pts[1][0])))
                intervals.append((max(lo, wlo), min(hi, whi)))
            intervals.sort()
            for a, b in intervals:
                if b <= a:
                    continue
                if a > cursor:
                    cursor = a
                if b > cursor:
                    covered += b - cursor
                    cursor = b
            return covered / span if span > 1.0 else 1.0

        self.assertGreaterEqual(_face_covered(hy), 0.85)
        self.assertGreaterEqual(_face_covered(hy - 120.0), 0.85)

    def test_closes_host_wall_pack_thickness_at_hinge(self):
        """E-W 닫힘 힌지가 N-S 이중벽에 닿으면 두께를 이중 수평 가상 WALL로 잇는다."""
        doc = ezdxf.new()
        msp = doc.modelspace()
        wall = {"layer": "WALL", "color": 1}
        # N-S 이중벽
        msp.add_line((35323.0, 62816.0), (35323.0, 64314.0), dxfattribs=wall)
        msp.add_line((35418.0, 62816.0), (35418.0, 67013.0), dxfattribs=wall)
        msp.add_line((35310.0, 64416.0), (35310.0, 66867.0), dxfattribs=wall)
        # E-W 닫힘 (두께 구간은 비어 있음)
        msp.add_line((34072.0, 64314.0), (35323.0, 64314.0), dxfattribs=wall)
        msp.add_line((34072.0, 64416.0), (35310.0, 64416.0), dxfattribs=wall)

        edges = []
        for e in msp.query("LINE"):
            a, b = e.dxf.start, e.dxf.end
            edges.append((float(a.x), float(a.y), float(b.x), float(b.y), "WALL", e))

        def _covered(x0, y0, x1, y1):
            for e in msp.query("LINE"):
                if e.dxf.layer != "WALL":
                    continue
                a, b = e.dxf.start, e.dxf.end
                if abs(float(a.y) - float(b.y)) > 20.0:
                    continue
                y = 0.5 * (float(a.y) + float(b.y))
                if abs(y - 0.5 * (y0 + y1)) > 20.0:
                    continue
                lo, hi = sorted((float(a.x), float(b.x)))
                if lo <= min(x0, x1) + 15.0 and hi >= max(x0, x1) - 15.0:
                    return True
            return False

        n = lib_llm_correct._close_host_wall_pack_thickness_at_hinge(
            msp, edges, 35323.0, 64314.0, 34072.0, 64314.0, is_covered=_covered
        )
        self.assertGreaterEqual(n, 1)
        spans = []
        for e in msp.query("LINE"):
            if e.dxf.layer != "WALL":
                continue
            a, b = e.dxf.start, e.dxf.end
            if abs(float(a.y) - float(b.y)) > 20.0:
                continue
            lo, hi = sorted((float(a.x), float(b.x)))
            if hi - lo > 200.0:
                continue
            if lo >= 35250.0 and hi <= 35500.0:
                spans.append((0.5 * (float(a.y) + float(b.y)), lo, hi))
        self.assertTrue(
            any(abs(y - 64314.0) < 5.0 and lo <= 35330.0 and hi >= 35400.0 for y, lo, hi in spans),
            f"missing thickness close at 64314: {spans}",
        )

    def test_snaps_wall_pack_close_to_wall_axis_not_tilted_leaf(self):
        """N-S 벽 다발에서 leaf가 ~82°로 기울어도 닫힘은 벽축(90°)으로 스냅한다."""
        doc = ezdxf.new()
        msp = doc.modelspace()
        hx, hy, r = 35303.5, 61844.3, 939.7
        wall = {"layer": "WALL", "color": 1}
        base = {"layer": "BASE", "color": 8}
        # 이중 N-S 벽면 (개구 아래·위 스터브)
        for x in (35323.0, 35418.0):
            msp.add_line((x, hy - 450.0), (x, hy - 30.0), dxfattribs=wall)
            msp.add_line((x, hy + r + 30.0), (x, hy + r + 800.0), dxfattribs=wall)
        # 기울어진 가짜 닫힌 현 (~82°) — 벽 다발과 평행으로 잡혀 wall_pack 후보가 됨
        tilt = math.radians(82.3)
        bad = msp.add_line(
            (hx, hy),
            (hx + r * math.cos(tilt), hy + r * math.sin(tilt)),
            dxfattribs=wall,
        )
        bad2 = msp.add_line(
            (hx, hy + 32.0),
            (
                hx + r * math.cos(tilt),
                hy + 32.0 + r * math.sin(tilt),
            ),
            dxfattribs=wall,
        )
        # 열린 문짝 (서쪽) + 분절 호
        msp.add_lwpolyline(
            [(hx, hy), (hx - r, hy)],
            dxfattribs=base,
        )
        for i in range(10):
            t0 = math.radians(180.0 - 7.0 * i)
            t1 = math.radians(180.0 - 7.0 * (i + 1))
            msp.add_lwpolyline(
                [
                    (hx + r * math.cos(t0), hy + r * math.sin(t0)),
                    (hx + r * math.cos(t1), hy + r * math.sin(t1)),
                ],
                dxfattribs=base,
            )

        lib_llm_correct.separate_segmented_swing_doors(msp)

        # 82° 대각 가상벽은 사라져야 함
        for e in (bad, bad2):
            try:
                layer = e.dxf.layer
            except Exception:
                layer = None
            # 삭제됐거나 WALL이 아닌 경우 OK; 남아 있으면 각도 검사
            if layer == "WALL":
                a, b = e.dxf.start, e.dxf.end
                ang = math.degrees(math.atan2(b.y - a.y, b.x - a.x)) % 360.0
                dang = min(abs(ang - 82.3) % 360.0, 360.0 - abs(ang - 82.3) % 360.0)
                self.assertGreater(dang, 15.0, f"tilted false close survived ang={ang}")

        # 벽축(N-S) 닫힘이 있어야 함
        ns_closes = 0
        for e in msp.query("LINE"):
            if e.dxf.layer != "WALL":
                continue
            a, b = e.dxf.start, e.dxf.end
            L = math.hypot(b.x - a.x, b.y - a.y)
            if abs(L - r) > 250.0:
                continue
            ang = math.degrees(math.atan2(b.y - a.y, b.x - a.x)) % 360.0
            if min(abs(ang - 90.0), abs(ang - 270.0)) <= 8.0:
                # 힌지 근처에서 출발
                if min(
                    math.hypot(a.x - hx, a.y - hy),
                    math.hypot(b.x - hx, b.y - hy),
                    math.hypot(a.x - hx, a.y - (hy + 32)),
                    math.hypot(b.x - hx, b.y - (hy + 32)),
                ) < 80.0:
                    ns_closes += 1
        self.assertGreaterEqual(ns_closes, 1, "expected N-S wall-axis close")

    def test_removes_diagonal_false_close_to_arc_tip(self):
        """호 끝 꼭지점으로 그은 대각 가상 WALL은 제거하고 벽축 닫힘만 남긴다."""
        doc = ezdxf.new()
        msp = doc.modelspace()
        hx, hy, r = 47755.0, 61673.0, 1067.0
        wall = {"layer": "WALL", "color": 1}
        base = {"layer": "BASE", "color": 8}
        msp.add_line((hx, hy), (hx + 900.0, hy), dxfattribs=wall)
        msp.add_line((hx - r - 900.0, hy), (hx - r, hy), dxfattribs=wall)
        msp.add_line((hx, hy - 51.0), (hx + 900.0, hy - 51.0), dxfattribs=wall)
        msp.add_line((hx - r - 900.0, hy - 51.0), (hx - r, hy - 51.0), dxfattribs=wall)
        for off in (80.0, 160.0):
            msp.add_lwpolyline(
                [(hx + 50.0, hy - off), (hx - r - 50.0, hy - off)],
                dxfattribs=base,
            )
        closed = msp.add_lwpolyline(
            [(hx, hy), (hx - r, hy)],
            dxfattribs=base,
        )
        for i in range(10):
            t0 = math.radians(180.0 + 6.5 * i)
            t1 = math.radians(180.0 + 6.5 * (i + 1))
            msp.add_lwpolyline(
                [
                    (hx + r * math.cos(t0), hy + r * math.sin(t0)),
                    (hx + r * math.cos(t1), hy + r * math.sin(t1)),
                ],
                dxfattribs=base,
            )
        # 잘못된 대각 가상 닫힘 (호 끝 ~245°)
        bad = msp.add_line(
            (hx, hy),
            (hx + r * math.cos(math.radians(245.0)), hy + r * math.sin(math.radians(245.0))),
            dxfattribs=wall,
        )
        bad2 = msp.add_line(
            (hx, hy - 51.0),
            (
                hx + r * math.cos(math.radians(245.0)),
                hy - 51.0 + r * math.sin(math.radians(245.0)),
            ),
            dxfattribs=wall,
        )

        lib_llm_correct.separate_segmented_swing_doors(msp)
        # 대각선은 삭제되어야 함
        surviving = []
        for e in msp.query("LINE"):
            if e.dxf.layer != "WALL":
                continue
            a, b = e.dxf.start, e.dxf.end
            for p, q, ox, oy in ((a.x, a.y, b.x, b.y), (b.x, b.y, a.x, a.y)):
                if math.hypot(p - hx, q - hy) > 60.0 and math.hypot(p - hx, q - (hy - 51)) > 60.0:
                    continue
                ang = math.degrees(math.atan2(oy - q, ox - p)) % 360.0
                dang = min(abs(ang - 245.0) % 360.0, 360.0 - abs(ang - 245.0) % 360.0)
                if dang < 15.0:
                    surviving.append(ang)
        self.assertEqual(surviving, [], f"diagonal false closes remain: {surviving}")
        self.assertEqual(closed.dxf.layer, "WALL")


if __name__ == "__main__":
    unittest.main()
