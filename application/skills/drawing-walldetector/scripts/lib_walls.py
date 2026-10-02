#!/usr/bin/env python3
"""Wall detection helpers for tile DXFs from drawing-devider."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

import ezdxf
from ezdxf.document import Drawing
from ezdxf.entities import DXFEntity

# ACI red
WALL_COLOR = 1
BASE_COLOR = 8  # gray
WALL_LAYER = "WALL"
BASE_LAYER = "BASE"

GEOM_TYPES = frozenset({"LINE", "LWPOLYLINE", "POLYLINE", "ARC", "CIRCLE", "ELLIPSE", "SPLINE"})


@dataclass
class Seg:
    x0: float
    y0: float
    x1: float
    y1: float
    entity_idx: int
    seg_idx: int
    length: float = 0.0
    is_h: bool = False
    is_v: bool = False
    key: tuple[int, int] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        dx = self.x1 - self.x0
        dy = self.y1 - self.y0
        self.length = math.hypot(dx, dy)
        ang = abs(math.degrees(math.atan2(dy, dx))) % 180.0
        self.is_h = ang < 8.0 or abs(ang - 180.0) < 8.0
        self.is_v = abs(ang - 90.0) < 8.0
        self.key = (self.entity_idx, self.seg_idx)
        # normalize direction for overlap tests
        if self.is_h and self.x0 > self.x1:
            self.x0, self.x1 = self.x1, self.x0
            self.y0, self.y1 = self.y1, self.y0
        if self.is_v and self.y0 > self.y1:
            self.x0, self.x1 = self.x1, self.x0
            self.y0, self.y1 = self.y1, self.y0


def _overlap_1d(a0: float, a1: float, b0: float, b1: float) -> float:
    lo = max(min(a0, a1), min(b0, b1))
    hi = min(max(a0, a1), max(b0, b1))
    return max(0.0, hi - lo)


def extract_segments(entities: list[DXFEntity]) -> list[Seg]:
    segs: list[Seg] = []
    for ei, e in enumerate(entities):
        t = e.dxftype()
        if t == "LINE":
            s, ed = e.dxf.start, e.dxf.end
            segs.append(Seg(float(s.x), float(s.y), float(ed.x), float(ed.y), ei, 0))
        elif t == "LWPOLYLINE":
            pts = [(float(p[0]), float(p[1])) for p in e.get_points("xy")]
            if len(pts) < 2:
                continue
            pairs = list(zip(pts, pts[1:]))
            if e.closed and pts[0] != pts[-1]:
                pairs.append((pts[-1], pts[0]))
            for si, (a, b) in enumerate(pairs):
                segs.append(Seg(a[0], a[1], b[0], b[1], ei, si))
    return segs


def is_column_polyline(e: DXFEntity) -> bool:
    """작은 닫힌 사각형 ≈ 기둥 (레거시 별칭)."""
    return is_non_wall_closed_box(e, max_mm=1300.0)


def is_non_wall_closed_box(e: DXFEntity, *, max_mm: float = 3500.0) -> bool:
    """닫힌 소·중형 사각 ≈ 기둥·가구·설비 윤곽 (벽 아님).

    지원동 등에서 책상·캐비닛·배식대가 이중 사각(50–420 mm)으로 그려져
    벽과 동일 패턴이 되므로, max 변 ≤ max_mm 인 닫힌 박스는 통째로 제외.
    (실제 벽은 보통 긴 LINE/대형 폴리라인으로 그려짐)
    ※ H-Beam 기둥(중첩 정사각 ≤1.2 m)은 classify 에서 별도 WALL 처리.
    """
    if e.dxftype() != "LWPOLYLINE" or not e.closed:
        return False
    pts = [(float(p[0]), float(p[1])) for p in e.get_points("xy")]
    if len(pts) < 3:
        return False
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    w = max(xs) - min(xs)
    h = max(ys) - min(ys)
    if w <= 0 or h <= 0:
        return False
    if max(w, h) <= max_mm and min(w, h) >= 150:
        return True
    return False


def _box_metrics(
    e: DXFEntity, *, max_mm: float = 2600.0
) -> tuple[float, float, float, float, float, float, float, float] | None:
    """닫힌 사각/직사각이면 (cx, cy, w, h, x0, x1, y0, y1), 아니면 None."""
    if e.dxftype() != "LWPOLYLINE":
        return None
    pts = [(float(p[0]), float(p[1])) for p in e.get_points("xy")]
    if len(pts) < 4 or len(pts) > 8:
        return None
    closed = bool(e.closed) or (
        math.hypot(pts[0][0] - pts[-1][0], pts[0][1] - pts[-1][1]) < 50.0
    )
    if not closed:
        return None
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    x0, x1 = min(xs), max(xs)
    y0, y1 = min(ys), max(ys)
    w, h = x1 - x0, y1 - y0
    if not (150.0 <= w <= max_mm and 150.0 <= h <= max_mm):
        return None
    return ((x0 + x1) * 0.5, (y0 + y1) * 0.5, w, h, x0, x1, y0, y1)


def _square_metrics(e: DXFEntity) -> tuple[float, float, float, float] | None:
    """닫힌 대략 정사각이면 (cx, cy, w, h), 아니면 None."""
    m = _box_metrics(e, max_mm=1500.0)
    if not m:
        return None
    cx, cy, w, h, *_ = m
    # 1300×900 같은 가로로 긴 표식 사각은 기둥이 아니다.
    if abs(w - h) > max(w, h) * 0.22:
        return None
    return (cx, cy, w, h)


def find_hbeam_column_idxs(entities: list[DXFEntity]) -> set[int]:
    """H-Beam 기둥 엔티티 인덱스 — 정사각 + 중앙 '_' 만.

    외부 연결·직사각 슬리브 제외. 밀집 격자 제외.
    """
    squares: list[tuple[int, float, float, float, float]] = []
    dashes: list[tuple[float, float, float, int]] = []
    arc_pts: list[tuple[float, float]] = []
    for e in entities:
        if e.dxftype() not in ("ARC", "CIRCLE"):
            continue
        try:
            arc_pts.append((float(e.dxf.center.x), float(e.dxf.center.y)))
        except Exception:  # noqa: BLE001
            continue
    for ei, e in enumerate(entities):
        m = _square_metrics(e)
        if m:
            cx, cy, w, h = m
            if 450.0 <= max(w, h) <= 1500.0:
                squares.append((ei, cx, cy, w, h))
        if e.dxftype() == "LINE":
            try:
                x0, y0 = float(e.dxf.start.x), float(e.dxf.start.y)
                x1, y1 = float(e.dxf.end.x), float(e.dxf.end.y)
            except Exception:  # noqa: BLE001
                continue
            length = math.hypot(x1 - x0, y1 - y0)
            if not (60.0 <= length <= 700.0):
                continue
            dx, dy = abs(x1 - x0), abs(y1 - y0)
            if dy <= max(20.0, 0.15 * length) or dx <= max(20.0, 0.15 * length):
                dashes.append(((x0 + x1) * 0.5, (y0 + y1) * 0.5, length, ei))

    cands: list[tuple[float, float, float, set[int]]] = []
    for ei, cx, cy, w, h in squares:
        side = max(w, h)
        dash_idxs: set[int] = set()
        for mx, my, length, di in dashes:
            if length > min(w, h) * 0.85:
                continue
            if abs(mx - cx) <= side * 0.35 and abs(my - cy) <= side * 0.35:
                dash_idxs.add(di)
        if not dash_idxs:
            continue
        # 휠체어 등 픽토그램: 사각 안에 호가 여러 개이거나 짧은 선이 많다.
        x0, y0 = cx - w * 0.5, cy - h * 0.5
        x1, y1 = cx + w * 0.5, cy + h * 0.5
        n_arc = sum(1 for ax, ay in arc_pts if x0 <= ax <= x1 and y0 <= ay <= y1)
        if n_arc >= 2 or len(dash_idxs) >= 6:
            continue
        cands.append((cx, cy, side, {ei} | dash_idxs))

    merged: list[tuple[float, float, float, set[int]]] = []
    for cx, cy, sz, idxs in cands:
        found = False
        for i, (mx, my, msz, midxs) in enumerate(merged):
            if abs(cx - mx) <= 120.0 and abs(cy - my) <= 120.0:
                midxs |= idxs
                merged[i] = (mx, my, max(msz, sz), midxs)
                found = True
                break
        if not found:
            merged.append((cx, cy, sz, set(idxs)))
    out: set[int] = set()
    for cx, cy, sz, idxs in merged:
        rad = max(2500.0, sz * 4.0)
        n_near = sum(
            1 for ox, oy, _osz, _ in merged if math.hypot(cx - ox, cy - oy) <= rad
        )
        if n_near >= 4:
            continue
        out |= idxs
    return out


def is_hatch_or_landscape_polyline(e: DXFEntity) -> bool:
    """짧은 변이 많은 열린 폴리라인 ≈ 조경·해칭·곡선 분해."""
    if e.dxftype() != "LWPOLYLINE":
        return False
    pts = [(float(p[0]), float(p[1])) for p in e.get_points("xy")]
    if len(pts) < 10:
        return False
    lengths: list[float] = []
    pairs = list(zip(pts, pts[1:]))
    if e.closed and pts[0] != pts[-1]:
        pairs.append((pts[-1], pts[0]))
    for a, b in pairs:
        lengths.append(math.hypot(b[0] - a[0], b[1] - a[1]))
    if len(lengths) < 10:
        return False
    avg = sum(lengths) / len(lengths)
    # 변 ≥10개이고 평균 길이 < 1.5 m → 조경/해칭 후보
    return avg < 1500.0


def _median(vals: list[float]) -> float:
    ordered = sorted(vals)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2.0


def _segments_cross(
    a0: tuple[float, float],
    a1: tuple[float, float],
    b0: tuple[float, float],
    b1: tuple[float, float],
) -> bool:
    """끝점 접촉을 제외한 두 선분의 교차."""

    def orient(p, q, r) -> float:
        return (q[0] - p[0]) * (r[1] - q[1]) - (q[1] - p[1]) * (r[0] - q[0])

    o1 = orient(a0, a1, b0)
    o2 = orient(a0, a1, b1)
    o3 = orient(b0, b1, a0)
    o4 = orient(b0, b1, a1)
    return o1 * o2 < 0.0 and o3 * o4 < 0.0


def _polyline_xy(e: DXFEntity) -> list[tuple[float, float]]:
    pts = [(float(p[0]), float(p[1])) for p in e.get_points("xy")]
    if e.closed and len(pts) >= 2 and pts[0] != pts[-1]:
        pts = pts + [pts[0]]
    return pts


def is_door_x_polyline(e: DXFEntity) -> bool:
    """X자 문 심볼.

    개구 폭(약 0.5–2.2 m) 안에 교차하는 두 획이 있고, 짧은 변은 벽 두께
    이하(≤ 0.45 m)다. 평면에 납작한 X(간벽 150 mm 안의 문)도 포함한다.
    """
    if e.dxftype() != "LWPOLYLINE":
        return False
    pts = _polyline_xy(e)
    if len(pts) < 4:
        return False
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    w, h = max(xs) - min(xs), max(ys) - min(ys)
    short, long = min(w, h), max(w, h)
    if short <= 0 or short > 450.0 or not (500.0 <= long <= 2200.0):
        return False
    edges = list(zip(pts, pts[1:]))
    for i, (a0, a1) in enumerate(edges):
        for b0, b1 in edges[i + 1 :]:
            if _segments_cross(a0, a1, b0, b1):
                return True
    return False


def find_door_x_idxs(entities: list[DXFEntity]) -> set[int]:
    """X자 문 엔티티. 대각 LINE 쌍과 X 폴리라인을 벽에서 뺀다."""
    diags: list[tuple[int, float, float, float, float]] = []
    out: set[int] = set()
    for ei, e in enumerate(entities):
        if is_door_x_polyline(e):
            out.add(ei)
            continue
        if e.dxftype() != "LINE":
            continue
        x0, y0 = float(e.dxf.start.x), float(e.dxf.start.y)
        x1, y1 = float(e.dxf.end.x), float(e.dxf.end.y)
        dx, dy = abs(x1 - x0), abs(y1 - y0)
        length = math.hypot(dx, dy)
        if length < 400.0 or length > 1800.0:
            continue
        if dx <= 0.25 * length or dy <= 0.25 * length:
            continue
        diags.append((ei, x0, y0, x1, y1))
    for i, a in enumerate(diags):
        ax0, ay0, ax1, ay1 = a[1], a[2], a[3], a[4]
        amx, amy = (ax0 + ax1) * 0.5, (ay0 + ay1) * 0.5
        for b in diags[i + 1 :]:
            bx0, by0, bx1, by1 = b[1], b[2], b[3], b[4]
            if abs(amx - (bx0 + bx1) * 0.5) > 500.0 or abs(amy - (by0 + by1) * 0.5) > 500.0:
                continue
            if not _segments_cross((ax0, ay0), (ax1, ay1), (bx0, by0), (bx1, by1)):
                continue
            minx = min(ax0, ax1, bx0, bx1)
            maxx = max(ax0, ax1, bx0, bx1)
            miny = min(ay0, ay1, by0, by1)
            maxy = max(ay0, ay1, by0, by1)
            rw, rh = maxx - minx, maxy - miny
            if 400.0 <= max(rw, rh) <= 1800.0 and 200.0 <= min(rw, rh) <= 1400.0:
                out.add(a[0])
                out.add(b[0])
                break
    return out


def _interval_sep(a0: float, a1: float, b0: float, b1: float) -> float:
    if a1 < b0:
        return b0 - a1
    if b1 < a0:
        return a0 - b1
    return 0.0


def _collinear_runs(
    group: list[Seg],
    *,
    along_x: bool,
    gap_mm: float = 40.0,
    ortho_tol_mm: float = 8.0,
) -> dict[tuple[int, int], tuple[float, int]]:
    """같은 직선에서 맞닿거나 40 mm 이내로 이어진 조각의 (길이, 조각 수)."""
    items: list[tuple[float, float, float, tuple[int, int]]] = []
    for s in group:
        if along_x:
            ortho = (s.y0 + s.y1) * 0.5
            a0, a1 = s.x0, s.x1
        else:
            ortho = (s.x0 + s.x1) * 0.5
            a0, a1 = s.y0, s.y1
        if a0 > a1:
            a0, a1 = a1, a0
        items.append((ortho, a0, a1, s.key))
    items.sort(key=lambda t: (t[0], t[1]))
    parent = list(range(len(items)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(i: int, j: int) -> None:
        ri, rj = find(i), find(j)
        if ri != rj:
            parent[rj] = ri

    for i, (ortho, a0, a1, _) in enumerate(items):
        for j in range(i + 1, len(items)):
            ortho_b, b0, b1, _ = items[j]
            if ortho_b - ortho > ortho_tol_mm:
                break
            if abs(ortho_b - ortho) > ortho_tol_mm:
                continue
            if _interval_sep(a0, a1, b0, b1) <= gap_mm:
                union(i, j)
    span: dict[int, list[float]] = {}
    count: dict[int, int] = {}
    for i, (_, a0, a1, _) in enumerate(items):
        root = find(i)
        if root not in span:
            span[root] = [a0, a1]
            count[root] = 0
        span[root][0] = min(span[root][0], a0)
        span[root][1] = max(span[root][1], a1)
        count[root] += 1
    out: dict[tuple[int, int], tuple[float, int]] = {}
    for i, item in enumerate(items):
        root = find(i)
        out[item[3]] = (span[root][1] - span[root][0], count[root])
    return out


def _legacy_wall_pair(a: Seg, b: Seg, dist_mm: float, short_pair_max_mm: float) -> bool:
    """긴 이중선, 또는 1.7 m 이상·두께 250 mm 이하인 개구 조각."""
    if a.length >= short_pair_max_mm or b.length >= short_pair_max_mm:
        return True
    if min(a.length, b.length) >= 1700.0 and dist_mm <= 250.0:
        return True
    return False


def _door_opening_slots(
    entities: list[DXFEntity], door_idxs: set[int]
) -> list[tuple[bool, float, float, float, float]]:
    """X 폴리라인 개구. (벽이 수평이면 True, 면 좌표 둘, 개구 방향 범위).

    세로 문은 두 x면 사이, 가로 문은 두 y면 사이다.
    """
    slots: list[tuple[bool, float, float, float, float]] = []
    for ei in door_idxs:
        e = entities[ei]
        if e.dxftype() != "LWPOLYLINE":
            continue
        pts = _polyline_xy(e)
        if len(pts) < 2:
            continue
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        x0, x1 = min(xs), max(xs)
        y0, y1 = min(ys), max(ys)
        if (y1 - y0) >= (x1 - x0):
            slots.append((False, x0, x1, y0, y1))
        else:
            slots.append((True, y0, y1, x0, x1))
    return slots


def _abuts_door_opening(
    a: Seg,
    b: Seg,
    dist_mm: float,
    *,
    along_x: bool,
    slots: list[tuple[bool, float, float, float, float]],
) -> bool:
    """간벽 이중선이 X 문 개구와 같은 두 면에 맞닿아 있으면 벽.

    세로 벽은 조각이 1 m 안팎이라 2.2 m 런에 못 들어간다. 문 심볼 자체는 제외한다.
    """
    if not slots or not (120.0 <= dist_mm <= 180.0):
        return False
    if along_x:
        o0 = min((a.y0 + a.y1) * 0.5, (b.y0 + b.y1) * 0.5)
        o1 = max((a.y0 + a.y1) * 0.5, (b.y0 + b.y1) * 0.5)
        spans = (
            (min(a.x0, a.x1), max(a.x0, a.x1)),
            (min(b.x0, b.x1), max(b.x0, b.x1)),
        )
    else:
        o0 = min((a.x0 + a.x1) * 0.5, (b.x0 + b.x1) * 0.5)
        o1 = max((a.x0 + a.x1) * 0.5, (b.x0 + b.x1) * 0.5)
        spans = (
            (min(a.y0, a.y1), max(a.y0, a.y1)),
            (min(b.y0, b.y1), max(b.y0, b.y1)),
        )
    for slot_along_x, slo, shi, alo, ahi in slots:
        if slot_along_x != along_x:
            continue
        if abs(slo - o0) > 25.0 or abs(shi - o1) > 25.0:
            continue
        if any(_interval_sep(s0, s1, alo, ahi) <= 50.0 for s0, s1 in spans):
            return True
    return False


def _broken_partition_pair(
    a: Seg,
    b: Seg,
    dist_mm: float,
    runs: dict[tuple[int, int], tuple[float, int]],
) -> bool:
    """문·개구로 잘린 간벽.

    조각은 1.7 m 미만이어도, 같은 직선에서 맞닿은 런이 2.2 m 이상이고
    두 면 모두 조각이 둘 이상이며 간격이 간벽 두께(120–180 mm)이면 벽이다.
    옷장에 붙은 150 mm 이중선이 이 경우다.
    """
    if not (120.0 <= dist_mm <= 180.0):
        return False
    len_a, n_a = runs[a.key]
    len_b, n_b = runs[b.key]
    return min(len_a, len_b) >= 2200.0 and n_a >= 2 and n_b >= 2


def detect_wall_keys(
    segs: list[Seg],
    *,
    min_len_mm: float = 500.0,
    thick_min_mm: float = 30.0,
    thick_max_mm: float = 420.0,
    min_overlap_mm: float = 400.0,
    stair_count: int = 4,
    short_pair_max_mm: float = 2800.0,
    wall_pack_gap_mm: float = 160.0,
    door_entity_idxs: set[int] | None = None,
    door_slots: list[tuple[bool, float, float, float, float]] | None = None,
) -> set[tuple[int, int]]:
    """평행 이중선(벽 두께 대역) 기반 벽 세그먼트 키 집합.

    stair_count: 두께 대역 안 평행 이웃이 (stair_count-1)개 이상이고
    간격이 성기면 계단/해칭으로 제외 (기본 4 → 이웃 ≥3).

    같은 대역 안에 간격이 촘촘한 긴 선(외벽 여러 겹)은 벽으로 남긴다.
    wall_pack_gap_mm: 그 겹의 인접 간격 중앙값 상한.

    short_pair_max_mm: 양쪽 모두 이보다 짧은 이중선 쌍은 가구·설비 변으로 제외.
    다만 맞닿은 간벽 런(120–180 mm, 2.2 m 이상)은 개구로 잘린 벽으로 유지한다.

    door_entity_idxs: X자 문. 대각선과 문 심볼 획은 벽이 아니다.
    door_slots: X 개구의 두 면. 거기에 맞닿은 세로·가로 간벽 조각은 벽이다.
    """
    doors = door_entity_idxs or set()
    slots = door_slots or []
    cand = [
        s
        for s in segs
        if (s.is_h or s.is_v) and s.length >= min_len_mm and s.entity_idx not in doors
    ]
    wall: set[tuple[int, int]] = set()

    def mark_pairs(group: list[Seg], ortho_attr: str, along: str) -> None:
        runs = _collinear_runs(group, along_x=(along == "x"))

        def mid_ortho(s: Seg) -> float:
            if ortho_attr == "y":
                return (s.y0 + s.y1) * 0.5
            return (s.x0 + s.x1) * 0.5

        items = sorted(group, key=mid_ortho)
        n = len(items)
        for i in range(n):
            a = items[i]
            ma = mid_ortho(a)
            # neighbors within thickness band
            neighbors: list[tuple[float, Seg]] = []
            for j in range(i + 1, n):
                b = items[j]
                mb = mid_ortho(b)
                d = mb - ma
                if d > thick_max_mm:
                    break
                if d < thick_min_mm:
                    continue
                if along == "x":
                    ov = _overlap_1d(a.x0, a.x1, b.x0, b.x1)
                else:
                    ov = _overlap_1d(a.y0, a.y1, b.y0, b.y1)
                need = max(min_overlap_mm, 0.25 * min(a.length, b.length))
                if ov >= need:
                    neighbors.append((d, b))
            if not neighbors:
                continue
            # 평행선이 여러 겹. 간격이 촘촘하고 긴 선이면 외벽 포체, 성기면 계단/해칭.
            partners = neighbors
            if len(neighbors) >= stair_count - 1:
                long_enough = a.length >= short_pair_max_mm or any(
                    b.length >= short_pair_max_mm for _, b in neighbors
                )
                distances = sorted(d for d, _ in neighbors)
                gaps = [distances[0]] + [
                    distances[k] - distances[k - 1] for k in range(1, len(distances))
                ]
                real_gaps = [g for g in gaps if g >= 15.0] or gaps
                packed = _median(real_gaps) <= wall_pack_gap_mm
                if long_enough and packed:
                    wall.add(a.key)
                    for _, b in neighbors:
                        if b.length >= 1200.0:
                            wall.add(b.key)
                    continue
                if not packed:
                    continue
                # 개구로 잘려 2m 안팎인 외벽. 짧은 멀라이언은 빼고 긴 겹만 짝으로 본다.
                partners = [(d, b) for d, b in neighbors if b.length >= 1200.0]
                if not partners:
                    continue
            # 가장 가까운 면을 벽 짝으로. 그 선이 문 궤적(이중선 사이의 짧은 선)이면
            # 더 먼 간벽 면을 짝으로 본다. X 대각선은 cand 에 없다.
            d0, b0 = min(partners, key=lambda t: t[0])
            if not (thick_min_mm <= d0 <= thick_max_mm):
                continue
            if (
                _legacy_wall_pair(a, b0, d0, short_pair_max_mm)
                or _broken_partition_pair(a, b0, d0, runs)
                or _abuts_door_opening(a, b0, d0, along_x=(along == "x"), slots=slots)
            ):
                wall.add(a.key)
                wall.add(b0.key)
                continue
            # 문짝이 두 벽면 사이에 있으면 가장 가까운 선은 문이다.
            # 그 너머에서 길이 1.7 m 이상·간격 250 mm 이하인 면만 벽으로 둔다.
            for d2, c in sorted(partners, key=lambda t: t[0]):
                if d2 <= d0 + 1.0:
                    continue
                both_faces = min(a.length, c.length) >= 1500.0 and d2 <= 250.0
                if (
                    both_faces
                    or _broken_partition_pair(a, c, d2, runs)
                    or _abuts_door_opening(
                        a, c, d2, along_x=(along == "x"), slots=slots
                    )
                ):
                    wall.add(a.key)
                    wall.add(c.key)
                    break

    h_segs = [s for s in cand if s.is_h]
    v_segs = [s for s in cand if s.is_v]
    mark_pairs(h_segs, "y", "x")
    mark_pairs(v_segs, "x", "y")
    return wall


def promote_shared_panel_edges(
    entities: list[DXFEntity],
    segs: list[Seg],
    wall_keys: set[tuple[int, int]],
    *,
    min_len_mm: float = 1200.0,
    ortho_tol_mm: float = 15.0,
) -> set[tuple[int, int]]:
    """맞붙은 두 벽패널의 공유 변.

    침실 창호가 닫힌 사각 두 개로 나뉘면, 맞댄 중간 변은 같은 좌표에
    겹쳐 두께가 0이라 이중선 벽이 아니다. 양쪽 바깥 변이 이미 벽이면
    그 중간 세로·가로 변도 벽이다. 가구 사각으로 통째 제외돼도 이 변은 남긴다.
    """
    by_ent: dict[int, list[Seg]] = {}
    for s in segs:
        by_ent.setdefault(s.entity_idx, []).append(s)

    # (entity, seg, along-center, span0, span1, other-side center, vertical)
    edges: list[tuple[int, Seg, float, float, float, float, bool]] = []
    for ei, e in enumerate(entities):
        m = _box_metrics(e, max_mm=4000.0)
        if not m:
            continue
        _cx, _cy, _w, _h, x0, x1, y0, y1 = m
        for s in by_ent.get(ei, []):
            if s.is_v:
                x = (s.x0 + s.x1) * 0.5
                if abs(x - x0) <= 2.0:
                    other = x1
                elif abs(x - x1) <= 2.0:
                    other = x0
                else:
                    continue
                edges.append((ei, s, x, min(s.y0, s.y1), max(s.y0, s.y1), other, True))
            elif s.is_h:
                y = (s.y0 + s.y1) * 0.5
                if abs(y - y0) <= 2.0:
                    other = y1
                elif abs(y - y1) <= 2.0:
                    other = y0
                else:
                    continue
                edges.append((ei, s, y, min(s.x0, s.x1), max(s.x0, s.x1), other, False))

    def opposite_is_wall(edge: tuple) -> bool:
        ei, _s, _c, _a0, _a1, other, vertical = edge
        for ej, sj, c, _b0, _b1, _o, vert in edges:
            if ej == ei and vert == vertical and abs(c - other) <= 2.0:
                return sj.key in wall_keys
        return False

    promoted: set[tuple[int, int]] = set()
    n = len(edges)
    for i in range(n):
        a = edges[i]
        for j in range(i + 1, n):
            b = edges[j]
            if a[0] == b[0] or a[6] != b[6]:
                continue
            if abs(a[2] - b[2]) > ortho_tol_mm:
                continue
            # 공유 변 양쪽으로 패널이 갈라져 있어야 한다.
            if (a[5] - a[2]) * (b[5] - b[2]) >= 0.0:
                continue
            ov = _overlap_1d(a[3], a[4], b[3], b[4])
            short = min(a[4] - a[3], b[4] - b[3])
            if short < min_len_mm or ov < 0.85 * short:
                continue
            if not (opposite_is_wall(a) and opposite_is_wall(b)):
                continue
            promoted.add(a[1].key)
            promoted.add(b[1].key)
    return promoted


def entity_wall_fraction(
    e: DXFEntity,
    entity_idx: int,
    wall_keys: set[tuple[int, int]],
) -> float:
    """엔티티 세그먼트 중 벽으로 판정된 비율."""
    t = e.dxftype()
    if t == "LINE":
        return 1.0 if (entity_idx, 0) in wall_keys else 0.0
    if t == "LWPOLYLINE":
        pts = list(e.get_points("xy"))
        n = max(0, len(pts) - 1) + (1 if e.closed and len(pts) >= 2 else 0)
        if n <= 0:
            return 0.0
        hit = sum(1 for si in range(n) if (entity_idx, si) in wall_keys)
        return hit / n
    return 0.0


def _swing_door_leaves(entities: list[DXFEntity]) -> list[dict]:
    """여닫이문 잎. 두께 20–80 mm, 폭 0.65–1.45 m 인 닫힌 얇은 사각형."""
    leaves: list[dict] = []
    for ei, entity in enumerate(entities):
        if entity.dxftype() != "LWPOLYLINE" or not entity.closed:
            continue
        pts = [(float(p[0]), float(p[1])) for p in entity.get_points("xy")]
        if len(pts) < 4:
            continue
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        width, height = max(xs) - min(xs), max(ys) - min(ys)
        short, long = min(width, height), max(width, height)
        if not (20.0 <= short <= 80.0 and 650.0 <= long <= 1450.0):
            continue
        vertical = height >= width
        leaves.append(
            {
                "ei": ei,
                "vertical": vertical,
                "along0": min(ys) if vertical else min(xs),
                "along1": max(ys) if vertical else max(xs),
                "center": (min(xs) + max(xs)) / 2 if vertical else (min(ys) + max(ys)) / 2,
                "x0": min(xs),
                "x1": max(xs),
                "y0": min(ys),
                "y1": max(ys),
            }
        )
    return leaves


def _is_swing_leaf_line(seg: Seg, leaf: dict) -> bool:
    """문짝 잎과 겹치는 짧은 평행선. 길게 이어진 벽은 빼지 않는다."""
    if not (600.0 <= seg.length <= 1600.0):
        return False
    if leaf["vertical"] != seg.is_v:
        return False
    if leaf["vertical"]:
        dist = abs((seg.x0 + seg.x1) / 2 - leaf["center"])
        overlap = _overlap_1d(seg.y0, seg.y1, leaf["along0"] - 200.0, leaf["along1"] + 1400.0)
    else:
        dist = abs((seg.y0 + seg.y1) / 2 - leaf["center"])
        overlap = _overlap_1d(seg.x0, seg.x1, leaf["along0"] - 200.0, leaf["along1"] + 1400.0)
    return dist <= 180.0 and overlap >= 0.7 * seg.length


def _endpoint_gap(a: Seg, b: Seg) -> float:
    best = math.inf
    for ax, ay in ((a.x0, a.y0), (a.x1, a.y1)):
        for bx, by in ((b.x0, b.y0), (b.x1, b.y1)):
            best = min(best, math.hypot(ax - bx, ay - by))
    return best


def _apply_swing_doors(
    entities: list[DXFEntity],
    segs: list[Seg],
    wall_keys: set[tuple[int, int]],
) -> tuple[set[int], set[tuple[int, int]]]:
    """여닫이문 잎은 벽에서 빼고, 문끝에 붙은 짧은 벽은 벽으로 올린다."""
    leaves = _swing_door_leaves(entities)
    drop: set[int] = {leaf["ei"] for leaf in leaves}
    for seg in segs:
        if seg.entity_idx in drop:
            continue
        if any(_is_swing_leaf_line(seg, leaf) for leaf in leaves):
            drop.add(seg.entity_idx)
    for key in list(wall_keys):
        if key[0] in drop:
            wall_keys.discard(key)

    promoted: set[tuple[int, int]] = set()
    wall_segs = [seg for seg in segs if seg.key in wall_keys]
    for leaf in leaves:
        if leaf["vertical"]:
            ends = ((leaf["center"], leaf["y0"]), (leaf["center"], leaf["y1"]))
        else:
            ends = ((leaf["x0"], leaf["center"]), (leaf["x1"], leaf["center"]))
        for ex, ey in ends:
            for seg in segs:
                if seg.key in wall_keys or seg.entity_idx in drop:
                    continue
                if not (60.0 <= seg.length <= 800.0):
                    continue
                near = min(
                    math.hypot(seg.x0 - ex, seg.y0 - ey),
                    math.hypot(seg.x1 - ex, seg.y1 - ey),
                )
                if near > 500.0:
                    continue
                if any(_endpoint_gap(seg, wall) <= 80.0 for wall in wall_segs):
                    promoted.add(seg.key)
                    wall_keys.add(seg.key)
    return drop, promoted


def _segment_angle(a: tuple[float, float], b: tuple[float, float]) -> float:
    return math.degrees(math.atan2(b[1] - a[1], b[0] - a[0])) % 180.0


def _near_angle(angle: float, target: float, tol: float = 12.0) -> bool:
    delta = abs(angle - target) % 180.0
    return min(delta, 180.0 - delta) < tol


def _bay_window_entity_idxs(entities: list[DXFEntity]) -> set[int]:
    """돌출창. 45° 볼살, 바깥면, 반대 45° 볼살이 한 폴리선으로 붙는다."""
    found: set[int] = set()
    for ei, entity in enumerate(entities):
        if entity.dxftype() != "LWPOLYLINE":
            continue
        pts = [(float(p[0]), float(p[1])) for p in entity.get_points("xy")]
        if len(pts) < 4:
            continue
        segs: list[tuple[tuple[float, float], tuple[float, float], float, float]] = []
        span = len(pts) if entity.closed else len(pts) - 1
        for i in range(span):
            a, b = pts[i], pts[(i + 1) % len(pts)]
            length = math.hypot(b[0] - a[0], b[1] - a[1])
            if length < 80.0:
                continue
            segs.append((a, b, length, _segment_angle(a, b)))
        for i in range(len(segs) - 2):
            a0, _b0, length0, angle0 = segs[i]
            _a1, _b1, length1, angle1 = segs[i + 1]
            _a2, b2, length2, angle2 = segs[i + 2]
            if not (
                400.0 <= length0 <= 1600.0
                and 500.0 <= length1 <= 2500.0
                and 400.0 <= length2 <= 1600.0
            ):
                continue
            if max(length0, length2) / min(length0, length2) > 1.35:
                continue
            axis = min(angle1, abs(angle1 - 90.0), abs(angle1 - 180.0)) < 8.0
            cheek0_45 = _near_angle(angle0, 45.0)
            cheek1_45 = _near_angle(angle2, 45.0)
            if not axis or cheek0_45 == cheek1_45:
                continue
            if not (
                (_near_angle(angle0, 45.0) or _near_angle(angle0, 135.0))
                and (_near_angle(angle2, 45.0) or _near_angle(angle2, 135.0))
            ):
                continue
            face = segs[i + 1]
            face_mid = (
                (face[0][0] + face[1][0]) / 2.0,
                (face[0][1] + face[1][1]) / 2.0,
            )
            chord = math.hypot(b2[0] - a0[0], b2[1] - a0[1]) or 1.0
            projection = abs(
                (b2[0] - a0[0]) * (a0[1] - face_mid[1])
                - (a0[0] - face_mid[0]) * (b2[1] - a0[1])
            ) / chord
            if 250.0 <= projection <= 1000.0:
                found.add(ei)
                break
    return found


def classify_entities(
    entities: list[DXFEntity],
    *,
    min_len_mm: float = 500.0,
    thick_min_mm: float = 30.0,
    thick_max_mm: float = 420.0,
    entity_wall_ratio: float = 0.75,
    furniture_box_max_mm: float = 3500.0,
) -> dict[str, Any]:
    """벽 분류.

    - 닫힌 박스 ≤ furniture_box_max_mm → 가구로 제외
      ※ 단 H-Beam 중첩 정사각 기둥은 WALL
    - X자(교차 대각선)는 문 → 벽 아님. 그 옆 간벽 이중선은 벽
    - 짧은 다변 폴리라인 → 조경/해칭 제외
    - 폴리라인은 벽 비율 ≥ entity_wall_ratio 일 때만 통째로 WALL
      (미만이면 세그먼트만 wall_segs 로 빨강)
    - 맞붙은 창호 패널의 중간 공유 변은 가구 사각이어도 벽으로 남긴다
    """
    segs = extract_segments(entities)
    door_x_idxs = find_door_x_idxs(entities)
    wall_keys = detect_wall_keys(
        segs,
        min_len_mm=min_len_mm,
        thick_min_mm=thick_min_mm,
        thick_max_mm=thick_max_mm,
        door_entity_idxs=door_x_idxs,
        door_slots=_door_opening_slots(entities, door_x_idxs),
    )
    # 창호 중간 멀리언. 닫힌 사각이라 skip 에 들어가도 이 변은 빨강으로 남긴다.
    shared_keys = promote_shared_panel_edges(entities, segs, wall_keys)
    wall_keys |= shared_keys
    # 여닫이문 잎은 옆 벽과 나란히 붙어 벽으로 잡힌다. 잎은 빼고 문끝 벽은 올린다.
    door_drop, door_jamb_keys = _apply_swing_doors(entities, segs, wall_keys)
    shared_keys = {key for key in shared_keys if key[0] not in door_drop}
    hbeam_idxs = find_hbeam_column_idxs(entities)
    wall_entity_idxs: set[int] = set(hbeam_idxs)
    skip_idxs: set[int] = set(door_x_idxs) | door_drop
    n_furniture = 0
    n_hatch = 0
    n_hbeam = len(hbeam_idxs)
    for ei, e in enumerate(entities):
        if ei in hbeam_idxs:
            continue  # already WALL
        if ei in door_x_idxs:
            continue  # X자 문
        if is_non_wall_closed_box(e, max_mm=furniture_box_max_mm):
            skip_idxs.add(ei)
            n_furniture += 1
            continue
        if is_hatch_or_landscape_polyline(e):
            skip_idxs.add(ei)
            n_hatch += 1
            continue
        t = e.dxftype()
        if t in ("ARC", "CIRCLE", "TEXT", "MTEXT", "DIMENSION"):
            continue
        if t not in ("LINE", "LWPOLYLINE", "POLYLINE"):
            continue
        frac = entity_wall_fraction(e, ei, wall_keys)
        if t == "LINE":
            if (ei, 0) in wall_keys:
                wall_entity_idxs.add(ei)
            continue
        # 폴리라인: 높은 비율일 때만 통째 승격 (가구 부분매칭 전체도색 방지)
        if frac >= entity_wall_ratio:
            wall_entity_idxs.add(ei)

    # 돌출창은 이중선이 아니다. 볼살과 바깥면을 벽으로 둔다.
    bay_idxs = _bay_window_entity_idxs(entities)
    skip_idxs -= bay_idxs
    wall_entity_idxs |= bay_idxs
    for seg in segs:
        if seg.entity_idx in bay_idxs and seg.length >= 400.0:
            wall_keys.add(seg.key)

    wall_segs = [
        s
        for s in segs
        if s.key in wall_keys and (s.entity_idx not in skip_idxs or s.key in shared_keys)
    ]
    return {
        "wall_entity_idxs": sorted(wall_entity_idxs),
        "wall_keys": sorted(wall_keys),
        "wall_segs": wall_segs,
        "shared_panel_keys": sorted(shared_keys),
        "n_entities": len(entities),
        "n_wall_entities": len(wall_entity_idxs),
        "n_wall_segs": len(wall_segs),
        "n_segs": len(segs),
        "skip_column_idxs": sorted(skip_idxs),
        "n_furniture_skipped": n_furniture,
        "n_hatch_skipped": n_hatch,
        "n_hbeam_columns": n_hbeam,
        "n_door_x": len(door_x_idxs),
        "n_swing_doors": len(door_drop),
        "n_door_jambs": len(door_jamb_keys),
        "n_bay_windows": len(bay_idxs),
        "entity_wall_ratio": entity_wall_ratio,
        "furniture_box_max_mm": furniture_box_max_mm,
    }


def _copy_entity(msp, e: DXFEntity, *, layer: str, color: int) -> None:
    t = e.dxftype()
    attrs = {"layer": layer, "color": color}
    if t == "LINE":
        msp.add_line(e.dxf.start, e.dxf.end, dxfattribs=attrs)
    elif t == "CIRCLE":
        msp.add_circle(e.dxf.center, e.dxf.radius, dxfattribs=attrs)
    elif t == "ARC":
        msp.add_arc(
            e.dxf.center,
            e.dxf.radius,
            e.dxf.start_angle,
            e.dxf.end_angle,
            dxfattribs=attrs,
        )
    elif t == "LWPOLYLINE":
        pts = list(e.get_points("xyb"))
        pl = msp.add_lwpolyline(pts, dxfattribs=attrs)
        pl.closed = bool(e.closed)
    elif t == "TEXT":
        msp.add_text(
            e.dxf.text,
            height=e.dxf.height,
            dxfattribs={**attrs, "insert": e.dxf.insert},
        )
    elif t == "MTEXT":
        msp.add_mtext(
            e.text,
            dxfattribs={
                **attrs,
                "insert": e.dxf.insert,
                "char_height": getattr(e.dxf, "char_height", 2.5) or 2.5,
            },
        )


def write_walls_dxf(
    entities: list[DXFEntity],
    classification: dict[str, Any],
    out_path,
    *,
    include_base: bool = True,
) -> dict[str, int]:
    """베이스(회색) + 벽(빨간색) DXF 저장."""
    doc = ezdxf.new("R2010")
    if BASE_LAYER not in doc.layers:
        doc.layers.add(BASE_LAYER, color=BASE_COLOR)
    if WALL_LAYER not in doc.layers:
        doc.layers.add(WALL_LAYER, color=WALL_COLOR)
    msp = doc.modelspace()
    wall_idxs = set(classification["wall_entity_idxs"])
    skip = set(classification.get("skip_column_idxs") or [])
    counts = {"base": 0, "wall": 0, "wall_seg_lines": 0}

    if include_base:
        for ei, e in enumerate(entities):
            t = e.dxftype()
            if t not in GEOM_TYPES and t not in ("TEXT", "MTEXT"):
                continue
            try:
                _copy_entity(msp, e, layer=BASE_LAYER, color=BASE_COLOR)
                counts["base"] += 1
            except Exception:  # noqa: BLE001
                continue

    # 벽: 엔티티 단위 빨강 + 세그먼트 키 기반 LINE 보강
    for ei in wall_idxs:
        if ei in skip:
            continue
        e = entities[ei]
        try:
            _copy_entity(msp, e, layer=WALL_LAYER, color=WALL_COLOR)
            counts["wall"] += 1
        except Exception:  # noqa: BLE001
            continue

    # 부분만 벽인 폴리라인용: wall_segs를 빨간 LINE으로도 그림
    drawn = set()
    shared_keys = set(classification.get("shared_panel_keys") or [])
    for s in classification["wall_segs"]:
        if s.entity_idx in wall_idxs:
            continue  # already whole entity
        if s.entity_idx in skip and s.key not in shared_keys:
            continue
        key = (round(s.x0, 3), round(s.y0, 3), round(s.x1, 3), round(s.y1, 3))
        if key in drawn:
            continue
        drawn.add(key)
        msp.add_line(
            (s.x0, s.y0),
            (s.x1, s.y1),
            dxfattribs={"layer": WALL_LAYER, "color": WALL_COLOR},
        )
        counts["wall_seg_lines"] += 1

    out_path = __import__("pathlib").Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    doc.saveas(str(out_path))
    return counts


def render_walls_png(
    entities: list[DXFEntity],
    classification: dict[str, Any],
    png_path,
    *,
    linewidth: float = 0.35,
    wall_linewidth: float = 1.1,
    dpi: int = 200,
    px_width: int = 2400,
    title: str | None = None,
    bbox_mm: dict | tuple | list | None = None,
) -> tuple[int, int]:
    """베이스 검정 + 벽 빨강 PNG.

    bbox_mm 이 있으면(floor_original 등) 그 창으로 고정해 floor_original.png 과
    동일한 크롭·비율로 맞춘다. 없으면 entity extents 자동.
    """
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.collections import LineCollection
    from matplotlib.patches import Arc, Circle
    from pathlib import Path

    xs: list[float] = []
    ys: list[float] = []
    base_segs: list[list[tuple[float, float]]] = []
    wall_segs: list[list[tuple[float, float]]] = []
    texts: list[tuple[float, float, str, float, float]] = []
    wall_idxs = set(classification["wall_entity_idxs"])
    wall_keyset = set(classification["wall_keys"])
    skip = set(classification.get("skip_column_idxs") or [])

    import re as _re

    for ei, e in enumerate(entities):
        t = e.dxftype()
        try:
            if t == "LINE":
                p0 = (float(e.dxf.start.x), float(e.dxf.start.y))
                p1 = (float(e.dxf.end.x), float(e.dxf.end.y))
                xs.extend([p0[0], p1[0]])
                ys.extend([p0[1], p1[1]])
                if ei in wall_idxs or (ei, 0) in wall_keyset:
                    wall_segs.append([p0, p1])
                else:
                    base_segs.append([p0, p1])
            elif t == "LWPOLYLINE":
                pts = [(float(p[0]), float(p[1])) for p in e.get_points("xy")]
                if len(pts) < 2:
                    continue
                if e.closed and pts[0] != pts[-1]:
                    pts = pts + [pts[0]]
                for p in pts:
                    xs.append(p[0])
                    ys.append(p[1])
                if ei in skip:
                    base_segs.append(pts)
                elif ei in wall_idxs:
                    wall_segs.append(pts)
                else:
                    # mixed: draw base full + wall segs over
                    base_segs.append(pts)
            elif t == "CIRCLE":
                c = e.dxf.center
                r = float(e.dxf.radius)
                xs.extend([c.x - r, c.x + r])
                ys.extend([c.y - r, c.y + r])
            elif t == "ARC":
                c = e.dxf.center
                r = float(e.dxf.radius)
                xs.extend([c.x - r, c.x + r])
                ys.extend([c.y - r, c.y + r])
            elif t == "TEXT":
                texts.append(
                    (
                        float(e.dxf.insert.x),
                        float(e.dxf.insert.y),
                        str(e.dxf.text or ""),
                        float(e.dxf.height or 2.5),
                        float(getattr(e.dxf, "rotation", 0) or 0),
                    )
                )
            elif t == "MTEXT":
                raw = e.text or ""
                plain = _re.sub(r"\{[^;]*;", "", raw)
                plain = plain.replace("}", "").replace("\\P", "\n")
                plain = _re.sub(r"\\[A-Za-z][^;]*;", "", plain)
                h = float(getattr(e.dxf, "char_height", 2.5) or 2.5)
                texts.append(
                    (
                        float(e.dxf.insert.x),
                        float(e.dxf.insert.y),
                        plain.strip(),
                        h,
                        float(getattr(e.dxf, "rotation", 0) or 0),
                    )
                )
        except Exception:  # noqa: BLE001
            continue

    for s in classification["wall_segs"]:
        if s.entity_idx in wall_idxs:
            continue
        wall_segs.append([(s.x0, s.y0), (s.x1, s.y1)])

    if bbox_mm is not None:
        if isinstance(bbox_mm, dict):
            cx0 = float(bbox_mm["xmin"])
            cy0 = float(bbox_mm["ymin"])
            cx1 = float(bbox_mm["xmax"])
            cy1 = float(bbox_mm["ymax"])
        else:
            cx0, cy0, cx1, cy1 = (float(v) for v in bbox_mm)
        # floor_original(render_hires_png) 과 동일 여백 — 상·우 베이 없음 기준
        span_y0 = max(cy1 - cy0, 1.0)
        tick = max(span_y0 * 0.025, 1000.0)
        xmin = cx0 - max(tick * 0.8, 1500)
        ymin = cy0 - max(tick * 2.5, 4000)
        xmax = cx1 + max(tick * 2.0, 3000)
        ymax = cy1 + max(tick * 6.5, 9000)
        pad = 0.0
        tight = False
        content_bbox = (cx0, cy0, cx1, cy1, tick)
    else:
        if not xs:
            xs, ys = [0.0, 1.0], [0.0, 1.0]
        xmin, xmax = min(xs), max(xs)
        ymin, ymax = min(ys), max(ys)
        span_x0 = max(xmax - xmin, 1.0)
        span_y0 = max(ymax - ymin, 1.0)
        pad = max(span_x0, span_y0) * 0.02
        tight = True
        content_bbox = None

    span_x = max(xmax - xmin, 1.0)
    span_y = max(ymax - ymin, 1.0)
    aspect = span_y / span_x
    fig_w = px_width / dpi
    fig_h = fig_w * aspect
    fig, ax = plt.subplots(figsize=(fig_w, fig_h), dpi=dpi)

    # non-line patches in gray
    for ei, e in enumerate(entities):
        t = e.dxftype()
        try:
            if t == "CIRCLE":
                c = e.dxf.center
                ax.add_patch(
                    Circle(
                        (c.x, c.y),
                        float(e.dxf.radius),
                        fill=False,
                        edgecolor="#888888",
                        linewidth=linewidth,
                    )
                )
            elif t == "ARC":
                c = e.dxf.center
                r = float(e.dxf.radius)
                ax.add_patch(
                    Arc(
                        (c.x, c.y),
                        2 * r,
                        2 * r,
                        angle=0,
                        theta1=float(e.dxf.start_angle),
                        theta2=float(e.dxf.end_angle),
                        color="#888888",
                        linewidth=linewidth,
                    )
                )
        except Exception:  # noqa: BLE001
            continue

    if base_segs:
        ax.add_collection(
            LineCollection(base_segs, colors="#555555", linewidths=linewidth, antialiased=True)
        )
    if wall_segs:
        ax.add_collection(
            LineCollection(
                wall_segs, colors="#e74c3c", linewidths=wall_linewidth, antialiased=True
            )
        )

    # floor_original 과 동일: TEXT/MTEXT 실명·면적 라벨 (#1a5fb4, max 7pt)
    if texts:
        import matplotlib.font_manager as fm

        font_name = None
        for cand in (
            "AppleGothic",
            "Apple SD Gothic Neo",
            "NanumGothic",
            "Malgun Gothic",
            "Noto Sans CJK KR",
            "Noto Sans KR",
        ):
            matches = [f for f in fm.fontManager.ttflist if cand in f.name]
            if matches:
                font_name = matches[0].name
                break
        mm_per_inch = span_x / max(fig_w, 0.01)
        for tx, ty, s, th, rot in texts:
            if not s or not s.strip():
                continue
            fs = (max(th, 1.0) / mm_per_inch) * 72.0 * 1.6
            fs = max(5.0, min(7.0, fs))
            ax.text(
                tx,
                ty,
                s,
                color="#1a5fb4",
                fontsize=fs,
                rotation=rot,
                ha="left",
                va="bottom",
                fontname=font_name,
                clip_on=True,
                zorder=5,
            )

    ax.set_xlim(xmin - pad, xmax + pad)
    ax.set_ylim(ymin - pad, ymax + pad)
    ax.set_aspect("equal", adjustable="box", anchor="C")
    ax.set_xlim(xmin - pad, xmax + pad)
    ax.set_ylim(ymin - pad, ymax + pad)
    ax.axis("off")
    ax.set_facecolor("white")
    fig.patch.set_facecolor("white")

    if title and content_bbox is not None:
        # floor_original draw_dims_matplotlib 과 동일: 40pt bold #c0392b
        cx0, cy0, cx1, cy1, tick = content_bbox
        title_gap = tick * 1.6
        title_h = tick * 2.0
        title_y = cy1 + title_gap + title_h
        ax.text(
            (cx0 + cx1) / 2,
            title_y,
            title,
            ha="center",
            va="bottom",
            color="#c0392b",
            fontsize=40,
            fontweight="bold",
            clip_on=False,
        )
    elif title:
        ax.set_title(title, color="#c0392b", fontsize=11, pad=8)

    if tight:
        fig.subplots_adjust(left=0.02, right=0.98, bottom=0.02, top=0.94 if title else 0.98)
        save_kw = {"bbox_inches": "tight", "pad_inches": 0.05}
    else:
        fig.subplots_adjust(left=0.0, right=1.0, bottom=0.0, top=1.0)
        save_kw = {"bbox_inches": None, "pad_inches": 0}

    png_path = Path(png_path)
    png_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(png_path, dpi=dpi, facecolor="white", edgecolor="none", **save_kw)
    plt.close(fig)

    # floor_original 과 동일 PNG 픽셀 패딩
    if content_bbox is not None:
        try:
            from PIL import Image

            Image.MAX_IMAGE_PIXELS = None
            im = Image.open(png_path).convert("RGB")
            pr, pb, pt = 200, 120, 160
            w, h = im.size
            canvas = Image.new("RGB", (w + pr, h + pb + pt), (255, 255, 255))
            canvas.paste(im, (0, pt))
            canvas.save(png_path, optimize=True)
        except Exception:  # noqa: BLE001
            pass

    try:
        from PIL import Image

        Image.MAX_IMAGE_PIXELS = None
        return Image.open(png_path).size
    except Exception:  # noqa: BLE001
        return int(fig_w * dpi), int(fig_h * dpi)


def load_tile_entities(dxf_path) -> tuple[Drawing, list[DXFEntity]]:
    doc = ezdxf.readfile(str(dxf_path))
    entities = [e for e in doc.modelspace() if e.dxftype() != "DIMENSION"]
    return doc, entities
