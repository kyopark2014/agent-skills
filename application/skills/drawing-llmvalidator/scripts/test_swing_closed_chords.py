"""스윙 닫힌 현 — 열린 문짝 쪽이 아니라 스트라이크 쪽으로 WALL을 긋는다."""

from __future__ import annotations

import math
import sys
import unittest
from pathlib import Path

import ezdxf

sys.path.insert(0, str(Path(__file__).resolve().parent))

import lib_llm_correct


def _wall_lines(msp):
    out = []
    for entity in msp:
        if entity.dxftype() != "LINE" or entity.dxf.layer != "WALL":
            continue
        x0, y0 = float(entity.dxf.start.x), float(entity.dxf.start.y)
        x1, y1 = float(entity.dxf.end.x), float(entity.dxf.end.y)
        out.append((x0, y0, x1, y1, math.hypot(x1 - x0, y1 - y0)))
    return out


class SwingClosedChordTests(unittest.TestCase):
    def test_strike_jamb_over_500mm_closes_vertical_not_open_leaf(self):
        """clubhouse 예약룸3형: 스트라이크 횡벽 526 mm + 열린 가로 문짝.

        닫힌 방향(남·수직)으로만 현을 긋고, 열린 쪽(서·수평)으로는 긋지 않는다.
        """
        doc = ezdxf.new()
        msp = doc.modelspace()
        hx, hy, radius = 1000.0, 2000.0, 1100.0
        # 호: 서쪽(열림) ↔ 남쪽(닫힘), sweep ≈ 82°
        msp.add_arc(
            (hx, hy),
            radius,
            -176.0,
            -94.0,
            dxfattribs={"layer": "BASE", "color": 8},
        )
        # 열린 문짝(수평, 힌지에서 약간 남쪽)
        msp.add_line(
            (hx - 20.0, hy - 77.0),
            (hx - 20.0 - 1020.0, hy - 77.0),
            dxfattribs={"layer": "BASE", "color": 8},
        )
        # 문틀 두께 짧은 횡선(잼브 스텁) — 예전엔 열린 쪽 점수를 부풀렸다
        msp.add_line(
            (hx + 22.0, hy - 37.0),
            (hx - 178.0, hy - 37.0),
            dxfattribs={"layer": "BASE", "color": 8},
        )
        msp.add_line(
            (hx + 22.0, hy - 77.0),
            (hx - 178.0, hy - 77.0),
            dxfattribs={"layer": "BASE", "color": 8},
        )
        # 힌지 북쪽·남쪽의 끊긴 수직 벽(개구 구간은 비어 있음)
        msp.add_line(
            (hx + 22.0, hy + 560.0),
            (hx + 22.0, hy + 1310.0),
            dxfattribs={"layer": "BASE", "color": 8},
        )
        msp.add_line(
            (hx + 22.0, hy - radius - 640.0),
            (hx + 22.0, hy - radius - 140.0),
            dxfattribs={"layer": "BASE", "color": 8},
        )
        # 스트라이크 횡벽 526 mm — 예전 jamb 상한 500 mm에 걸려 무시되던 케이스
        msp.add_line(
            (hx - 500.0, hy - radius + 3.0),
            (hx + 26.0, hy - radius + 3.0),
            dxfattribs={"layer": "BASE", "color": 8},
        )

        added = lib_llm_correct.add_swing_closed_chords(msp)
        self.assertGreaterEqual(added, 1)

        vertical = [
            w for w in _wall_lines(msp)
            if abs(w[0] - w[2]) <= 30.0 and w[4] >= 0.75 * radius
        ]
        horizontal = [
            w for w in _wall_lines(msp)
            if abs(w[1] - w[3]) <= 30.0 and w[4] >= 0.75 * radius
        ]
        self.assertTrue(vertical, msg="닫힌 방향(수직) 현이 없다")
        self.assertFalse(horizontal, msg="열린 방향(수평)으로 잘못된 현이 생겼다")
        chord = vertical[0]
        y_lo, y_hi = min(chord[1], chord[3]), max(chord[1], chord[3])
        self.assertLessEqual(abs(min(chord[0], chord[2]) - hx), 40.0)
        self.assertLessEqual(y_lo, hy + 40.0)
        self.assertGreaterEqual(y_hi, hy - 40.0)
        self.assertLessEqual(y_lo, hy - 0.7 * radius)

    def test_wrong_open_chord_is_demoted_when_strike_known(self):
        """이미 열린 쪽으로 그은 WALL 현이 있으면 BASE로 내리고 닫힌 쪽을 긋는다."""
        doc = ezdxf.new()
        msp = doc.modelspace()
        hx, hy, radius = 1000.0, 2000.0, 1100.0
        msp.add_arc(
            (hx, hy),
            radius,
            -176.0,
            -94.0,
            dxfattribs={"layer": "BASE", "color": 8},
        )
        msp.add_line(
            (hx, hy - 77.0),
            (hx - 1020.0, hy - 77.0),
            dxfattribs={"layer": "BASE", "color": 8},
        )
        msp.add_line(
            (hx - 500.0, hy - radius + 3.0),
            (hx + 26.0, hy - radius + 3.0),
            dxfattribs={"layer": "BASE", "color": 8},
        )
        wrong = msp.add_line(
            (hx, hy),
            (hx - radius, hy),
            dxfattribs={"layer": "WALL", "color": 1},
        )

        lib_llm_correct.add_swing_closed_chords(msp)

        self.assertEqual(wrong.dxf.layer, "BASE")
        vertical = [
            w for w in _wall_lines(msp)
            if abs(w[0] - w[2]) <= 30.0 and w[4] >= 0.75 * radius
        ]
        self.assertTrue(vertical)

    def test_thick_lwpoly_door_leaf_closes_strike_not_open(self):
        """clubhouse 캐디휴게실형: 두꺼운 DOOR 사각 문짝만 있고 LINE 문짝은 없다.

        문짝 중심선이 서쪽(열림)이므로 닫힌 현은 북쪽(수직)으로만 긋는다.
        개구 옆 수직 WALL(반지름 길이)이 있어도 문짝으로 오인하지 않는다.
        """
        doc = ezdxf.new()
        msp = doc.modelspace()
        hx, hy, radius = 767810.8, -64946.4, 1103.8
        msp.add_arc(
            (hx, hy),
            radius,
            94.9,
            172.7,
            dxfattribs={"layer": "BASE", "color": 8},
        )
        # 열린 문짝: 1000×40 사각 LWPOLY (DOOR)
        msp.add_lwpolyline(
            [
                (766715.9, -64846.7),
                (767715.9, -64846.7),
                (767715.9, -64806.7),
                (766715.9, -64806.7),
            ],
            close=True,
            dxfattribs={"layer": "DOOR", "color": 3},
        )
        # 이중벽 면(동) — 승격 후 개구 옆이 반지름 길이로 이어진 상태.
        # 바깥면 BASE가 남아 있어도 DOOR 문짝보다 우선하지 않아야 한다.
        msp.add_line(
            (767715.9, hy + 50.0),
            (767715.9, hy + radius),
            dxfattribs={"layer": "WALL", "color": 1},
        )
        msp.add_line(
            (767715.9, hy - radius - 200.0),
            (767715.9, hy - 200.0),
            dxfattribs={"layer": "WALL", "color": 1},
        )
        msp.add_line(
            (767915.9, hy + 50.0),
            (767915.9, hy + radius),
            dxfattribs={"layer": "BASE", "color": 8},
        )
        msp.add_line(
            (767915.9, hy - radius - 200.0),
            (767915.9, hy - 200.0),
            dxfattribs={"layer": "WALL", "color": 1},
        )

        added = lib_llm_correct.add_swing_closed_chords(msp)
        self.assertGreaterEqual(added, 1)

        vertical = [
            w for w in _wall_lines(msp)
            if abs(w[0] - w[2]) <= 30.0 and w[4] >= 0.75 * radius
            and abs((w[0] + w[2]) * 0.5 - hx) <= 40.0
        ]
        horizontal = [
            w for w in _wall_lines(msp)
            if abs(w[1] - w[3]) <= 30.0 and w[4] >= 0.75 * radius
            and abs((w[1] + w[3]) * 0.5 - hy) <= 40.0
        ]
        self.assertTrue(vertical, msg="닫힌 방향(수직) 현이 없다")
        self.assertFalse(horizontal, msg="열린 방향(수평 문짝)으로 잘못된 현이 생겼다")


if __name__ == "__main__":
    unittest.main()
