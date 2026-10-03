#!/usr/bin/env python3
"""Wall detection helpers for tile DXFs from drawing-devider."""

from __future__ import annotations

import copy
import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import ezdxf
from ezdxf.document import Drawing
from ezdxf.entities import DXFEntity

# ACI red
WALL_COLOR = 1
BASE_COLOR = 8  # gray
DOOR_COLOR = 3
WINDOW_COLOR = 4
COLUMN_COLOR = 5
WALL_LAYER = "WALL"
BASE_LAYER = "BASE"
DOOR_LAYER = "DOOR"
WINDOW_LAYER = "WINDOW"
COLUMN_LAYER = "COLUMN"

GEOM_TYPES = frozenset({"LINE", "LWPOLYLINE", "POLYLINE", "ARC", "CIRCLE", "ELLIPSE", "SPLINE"})

CONDITIONS_PATH = Path(__file__).resolve().parent.parent / "wall_conditions.json"


def read_wall_conditions(path: Path | None = None) -> dict:
    src = path or CONDITIONS_PATH
    return json.loads(src.read_text(encoding="utf-8"))


def resolve_project_name(hint: str | None, raw: dict | None = None) -> str | None:
    """프로젝트 키 또는 match 별칭을 projects 키로 바꾼다. 없으면 None."""
    if not hint:
        return None
    data = raw if raw is not None else read_wall_conditions()
    projects = data.get("projects") or {}
    if hint in projects:
        return hint
    folded = hint.casefold()
    for name, cfg in projects.items():
        aliases = [name, *(cfg.get("match") or [])]
        if any(str(alias).casefold() == folded for alias in aliases):
            return name
    return None


def _deep_merge(base: dict, override: dict) -> dict:
    """project에 없는 키는 common 값을 유지한다."""
    merged = copy.deepcopy(base)
    for key, value in override.items():
        if key in ("match", "_project"):
            continue
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = copy.deepcopy(value)
    return merged


def _common_condition_list(common: Any) -> list[dict]:
    """common은 조건 객체 배열이다. 예전 단일 객체도 한 칸으로 읽는다."""
    if isinstance(common, dict):
        return [copy.deepcopy(common)]
    if isinstance(common, list):
        items = [copy.deepcopy(item) for item in common if isinstance(item, dict)]
        if len(items) != len(common):
            raise TypeError("wall_conditions common items must be objects")
        return items
    raise TypeError("wall_conditions common must be a list of condition objects")


def load_wall_conditions(project: str | None = None, path: Path | None = None) -> list[dict]:
    """적용할 평행 이중선 조건 목록.

    common[0], common[1], … 를 순서대로 쓴다. project가 맞으면
    projects.<이름> 조건을 같은 형식으로 뒤에 붙인다. 별칭은 projects 키로 푼다.
    """
    raw = read_wall_conditions(path)
    conditions = _common_condition_list(raw["common"])
    if not conditions:
        raise ValueError("wall_conditions common is empty")
    resolved = None
    if project:
        resolved = resolve_project_name(project, raw)
        if resolved is None:
            known = ", ".join(sorted(raw.get("projects") or {}))
            raise KeyError(f"unknown wall project: {project} (known: {known})")
        extra = copy.deepcopy(raw["projects"][resolved])
        extra.pop("match", None)
        bases = list(conditions)
        for base in bases:
            conditions.append(_deep_merge(base, extra))
    for cond in conditions:
        cond["_project"] = resolved
    return conditions


def _wall_condition_profiles(
    conditions: dict | list[dict] | None,
    project: str | None,
) -> list[dict]:
    """검출에 쓸 조건 목록. 이미 고른 목록은 프로젝트를 다시 붙이지 않는다."""
    if conditions is None:
        return load_wall_conditions(project)
    if isinstance(conditions, list):
        return [copy.deepcopy(item) for item in conditions if isinstance(item, dict)]
    if isinstance(conditions, dict):
        return [copy.deepcopy(conditions)]
    raise TypeError("wall conditions must be an object or a list of objects")


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
    angle_tol_deg: float = 8.0

    def __post_init__(self) -> None:
        dx = self.x1 - self.x0
        dy = self.y1 - self.y0
        self.length = math.hypot(dx, dy)
        ang = abs(math.degrees(math.atan2(dy, dx))) % 180.0
        tol = self.angle_tol_deg
        self.is_h = ang < tol or abs(ang - 180.0) < tol
        self.is_v = abs(ang - 90.0) < tol
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


def extract_segments(entities: list[DXFEntity], *, angle_tol_deg: float = 8.0) -> list[Seg]:
    segs: list[Seg] = []
    for ei, e in enumerate(entities):
        t = e.dxftype()
        if t == "LINE":
            s, ed = e.dxf.start, e.dxf.end
            segs.append(
                Seg(float(s.x), float(s.y), float(ed.x), float(ed.y), ei, 0, angle_tol_deg=angle_tol_deg)
            )
        elif t == "LWPOLYLINE":
            pts = [(float(p[0]), float(p[1])) for p in e.get_points("xy")]
            if len(pts) < 2:
                continue
            pairs = list(zip(pts, pts[1:]))
            if e.closed and pts[0] != pts[-1]:
                pairs.append((pts[-1], pts[0]))
            for si, (a, b) in enumerate(pairs):
                segs.append(Seg(a[0], a[1], b[0], b[1], ei, si, angle_tol_deg=angle_tol_deg))
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
    cfg: dict,
) -> bool:
    """간벽 이중선이 X 문 개구와 같은 두 면에 맞닿아 있으면 벽.

    세로 벽은 조각이 1 m 안팎이라 2.2 m 런에 못 들어간다. 문 심볼 자체는 제외한다.
    """
    if not cfg.get("enabled", True) or not slots:
        return False
    gap = cfg["gap_mm"]
    if not (gap["min"] <= dist_mm <= gap["max"]):
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
        face_tol = cfg["face_tolerance_mm"]
        if abs(slo - o0) > face_tol or abs(shi - o1) > face_tol:
            continue
        touch = cfg["end_touch_mm"]
        if any(_interval_sep(s0, s1, alo, ahi) <= touch for s0, s1 in spans):
            return True
    return False


def _pair_overlap(a: Seg, b: Seg, *, along_x: bool) -> float:
    if along_x:
        return _overlap_1d(a.x0, a.x1, b.x0, b.x1)
    return _overlap_1d(a.y0, a.y1, b.y0, b.y1)


def _long_double_wall(
    a: Seg,
    b: Seg,
    dist_mm: float,
    overlap_mm: float,
    cfg: dict,
) -> bool:
    """연속된 실 테두리. enabled인 프로젝트에서만 벽으로 둔다."""
    if not cfg.get("enabled"):
        return False
    gap = cfg["gap_mm"]
    if not (gap["min"] <= dist_mm <= gap["max"]):
        return False
    short = min(a.length, b.length)
    if short < cfg["shorter_length_mm_gte"]:
        return False
    return overlap_mm >= cfg["overlap_ratio_of_shorter_gte"] * short


def _remember_face(
    faces: list[tuple[float, float]],
    ortho_a: float,
    ortho_b: float,
    *,
    tolerance_mm: float,
) -> None:
    lo, hi = (ortho_a, ortho_b) if ortho_a <= ortho_b else (ortho_b, ortho_a)
    for a, b in faces:
        if abs(a - lo) <= tolerance_mm and abs(b - hi) <= tolerance_mm:
            return
    faces.append((lo, hi))


def _broken_partition_pair(
    a: Seg,
    b: Seg,
    dist_mm: float,
    runs: dict[tuple[int, int], tuple[float, int]],
    cfg: dict,
) -> bool:
    """문·개구로 잘린 간벽.

    같은 직선에서 맞닿은 런이 2.2 m 이상이고 간격이 간벽 두께(120–180 mm)이면 벽이다.
    한 면이 조각 하나여도 된다. 옷장에 붙은 150 mm 이중선이 이 경우다.
    """
    if not cfg.get("enabled", True):
        return False
    gap = cfg["gap_mm"]
    if not (gap["min"] <= dist_mm <= gap["max"]):
        return False
    len_a, _n_a = runs[a.key]
    len_b, _n_b = runs[b.key]
    return min(len_a, len_b) >= cfg["run_min_length_mm"]


def _apply_candidate_overrides(
    cond: dict,
    *,
    min_len_mm: float | None,
    thick_min_mm: float | None,
    thick_max_mm: float | None,
    min_overlap_mm: float | None,
    stair_count: int | None,
    wall_pack_gap_mm: float | None,
) -> dict:
    """CLI로 넘긴 값이 있으면 JSON 후보·여러 겹 값을 덮어쓴다."""
    cand = cond["candidate"]
    if min_len_mm is not None:
        cand["min_length_mm"] = min_len_mm
    if thick_min_mm is not None:
        cand["gap_mm"]["min"] = thick_min_mm
    if thick_max_mm is not None:
        cand["gap_mm"]["max"] = thick_max_mm
    if min_overlap_mm is not None:
        cand["overlap_mm_min"] = min_overlap_mm
    packed = cond["packed_lines"]
    if stair_count is not None:
        packed["neighbor_count_gte"] = stair_count - 1
    if wall_pack_gap_mm is not None:
        packed["median_gap_mm_lte"] = wall_pack_gap_mm
    return cond


def detect_wall_keys(
    segs: list[Seg],
    *,
    project: str | None = None,
    conditions: dict | list[dict] | None = None,
    min_len_mm: float | None = None,
    thick_min_mm: float | None = None,
    thick_max_mm: float | None = None,
    min_overlap_mm: float | None = None,
    stair_count: int | None = None,
    wall_pack_gap_mm: float | None = None,
    door_entity_idxs: set[int] | None = None,
    door_slots: list[tuple[bool, float, float, float, float]] | None = None,
) -> set[tuple[int, int]]:
    """평행 이중선 벽 세그먼트 키.

    common 배열의 각 조건으로 고른 벽을 합친다. project가 맞으면 그 조건을 뒤에 붙여 같이 고른다.
    min_len_mm 등을 넘기면 조건마다 그 값만 JSON을 덮어쓴다.
    """
    doors = door_entity_idxs or set()
    slots = door_slots or []
    wall: set[tuple[int, int]] = set()
    for profile in _wall_condition_profiles(conditions, project):
        tuned = _apply_candidate_overrides(
            profile,
            min_len_mm=min_len_mm,
            thick_min_mm=thick_min_mm,
            thick_max_mm=thick_max_mm,
            min_overlap_mm=min_overlap_mm,
            stair_count=stair_count,
            wall_pack_gap_mm=wall_pack_gap_mm,
        )
        wall |= _detect_wall_keys_one(segs, tuned, doors, slots)
    return wall


def _detect_wall_keys_one(
    segs: list[Seg],
    cond: dict,
    doors: set[int],
    slots: list[tuple[bool, float, float, float, float]],
) -> set[tuple[int, int]]:
    """조건 하나에서 평행 이중선 벽 키를 고른다."""
    cand_cfg = cond["candidate"]
    packed_cfg = cond["packed_lines"]
    part_cfg = cond["broken_partition"]
    door_cfg = cond["abuts_door_opening"]
    long_cfg = cond.get("long_double_wall") or {"enabled": False}
    min_len = float(cand_cfg["min_length_mm"])
    thick_min = float(cand_cfg["gap_mm"]["min"])
    thick_max = float(cand_cfg["gap_mm"]["max"])
    min_overlap = float(cand_cfg["overlap_mm_min"])
    overlap_ratio = float(cand_cfg["overlap_ratio_of_shorter"])
    cand = [
        s
        for s in segs
        if (s.is_h or s.is_v) and s.length >= min_len and s.entity_idx not in doors
    ]
    wall: set[tuple[int, int]] = set()

    def mark_pairs(group: list[Seg], ortho_attr: str, along: str) -> None:
        runs = _collinear_runs(
            group,
            along_x=(along == "x"),
            gap_mm=float(part_cfg["join_gap_mm"]),
            ortho_tol_mm=float(part_cfg["ortho_tolerance_mm"]),
        )

        def mid_ortho(s: Seg) -> float:
            if ortho_attr == "y":
                return (s.y0 + s.y1) * 0.5
            return (s.x0 + s.x1) * 0.5

        items = sorted(group, key=mid_ortho)
        n = len(items)
        long_faces: list[tuple[float, float]] = []
        along_x = along == "x"
        for i in range(n):
            a = items[i]
            ma = mid_ortho(a)
            # neighbors within thickness band
            neighbors: list[tuple[float, Seg]] = []
            for j in range(i + 1, n):
                b = items[j]
                mb = mid_ortho(b)
                d = mb - ma
                if d > thick_max:
                    break
                if d < thick_min:
                    continue
                ov = _pair_overlap(a, b, along_x=along_x)
                need = max(min_overlap, overlap_ratio * min(a.length, b.length))
                if ov >= need:
                    neighbors.append((d, b))
            if not neighbors:
                continue
            # 평행선이 여러 겹이면 성긴 간격만 계단/해칭으로 뺀다.
            # 촘촘하다고 긴 선을 외벽으로 넣지는 않는다.
            partners = neighbors
            if len(neighbors) >= int(packed_cfg["neighbor_count_gte"]):
                distances = sorted(d for d, _ in neighbors)
                gaps = [distances[0]] + [
                    distances[k] - distances[k - 1] for k in range(1, len(distances))
                ]
                ignore_below = float(packed_cfg["ignore_gap_below_mm"])
                real_gaps = [g for g in gaps if g >= ignore_below] or gaps
                packed = _median(real_gaps) <= float(packed_cfg["median_gap_mm_lte"])
                if not packed:
                    continue
                partner_len = float(packed_cfg["partner_length_mm_gte"])
                partners = [(d, b) for d, b in neighbors if b.length >= partner_len]
                if not partners:
                    continue
            d0, b0 = min(partners, key=lambda t: t[0])
            if not (thick_min <= d0 <= thick_max):
                continue
            # 간벽은 여러 겹 필터로 빠진 짧은 조각도 본다. 조각 수는 조건이 아니다.
            for d_part, part in sorted(neighbors, key=lambda t: t[0]):
                if _broken_partition_pair(a, part, d_part, runs, part_cfg):
                    wall.add(a.key)
                    wall.add(part.key)
                    break
            else:
                part = None
            if part is not None:
                continue
            if _abuts_door_opening(a, b0, d0, along_x=along_x, slots=slots, cfg=door_cfg):
                wall.add(a.key)
                wall.add(b0.key)
                continue
            if long_cfg.get("enabled"):
                # 끝의 짧은 문짝은 여러 겹이 아니다.
                neighbor_len = float(long_cfg["long_neighbor_length_mm_gte"])
                neighbor_ratio = float(long_cfg["long_neighbor_overlap_ratio_gte"])
                long_neighbors = [
                    b
                    for _, b in neighbors
                    if b.length >= neighbor_len
                    and _pair_overlap(a, b, along_x=along_x)
                    >= neighbor_ratio * min(a.length, b.length)
                ]
                crowded = len(long_neighbors) >= int(long_cfg["long_neighbor_count_gte"])
                ov0 = _pair_overlap(a, b0, along_x=along_x)
                if not crowded and _long_double_wall(a, b0, d0, ov0, long_cfg):
                    wall.add(a.key)
                    wall.add(b0.key)
                    _remember_face(
                        long_faces,
                        ma,
                        mid_ortho(b0),
                        tolerance_mm=float(long_cfg["face_tolerance_mm"]),
                    )
                    continue
            # 가장 가까운 선이 벽이 아니면, 더 먼 평행선은 길이만으로 짝이지 않다.
            for d2, c in sorted(partners, key=lambda t: t[0]):
                if d2 <= d0 + 1.0:
                    continue
                if _abuts_door_opening(
                    a, c, d2, along_x=along_x, slots=slots, cfg=door_cfg
                ):
                    wall.add(a.key)
                    wall.add(c.key)
                    break

        if long_cfg.get("enabled") and long_faces:
            face_tol = float(long_cfg["face_tolerance_mm"])
            piece_len = float(long_cfg["same_face_shorter_length_mm_gte"])
            piece_ratio = float(long_cfg["overlap_ratio_of_shorter_gte"])
            for lo, hi in long_faces:
                side_lo = [
                    s
                    for s in group
                    if s.length >= piece_len and abs(mid_ortho(s) - lo) <= face_tol
                ]
                side_hi = [
                    s
                    for s in group
                    if s.length >= piece_len and abs(mid_ortho(s) - hi) <= face_tol
                ]
                for left in side_lo:
                    for right in side_hi:
                        short = min(left.length, right.length)
                        ov = _pair_overlap(left, right, along_x=along_x)
                        if ov >= piece_ratio * short:
                            wall.add(left.key)
                            wall.add(right.key)

    h_segs = [s for s in cand if s.is_h]
    v_segs = [s for s in cand if s.is_v]
    mark_pairs(h_segs, "y", "x")
    mark_pairs(v_segs, "x", "y")
    return wall


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
    """여닫이문 잎만 벽에서 빼고, 문끝에 붙은 짧은 벽은 벽으로 올린다.

    잎 옆의 선이 가깝다는 이유만으로 다른 도형을 문으로 넣지 않는다.
    """
    leaves = _swing_door_leaves(entities)
    drop: set[int] = {leaf["ei"] for leaf in leaves}
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


def _window_frame_entity_idxs(entities: list[DXFEntity]) -> set[int]:
    """같은 개구에 나란히 겹친 얇은 창틀. 기둥과 같이 벽으로 둔다."""
    panels: list[tuple[int, bool, float, float, float]] = []
    for ei, entity in enumerate(entities):
        if entity.dxftype() != "LWPOLYLINE" or not entity.closed:
            continue
        pts = [(float(p[0]), float(p[1])) for p in entity.get_points("xy")]
        if len(pts) < 4:
            continue
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
        short, long = min(x1 - x0, y1 - y0), max(x1 - x0, y1 - y0)
        if not (15.0 <= short <= 90.0 and 400.0 <= long <= 1600.0):
            continue
        horizontal = (x1 - x0) >= (y1 - y0)
        along0 = x0 if horizontal else y0
        along1 = x1 if horizontal else y1
        perp = (y0 + y1) / 2.0 if horizontal else (x0 + x1) / 2.0
        panels.append((ei, horizontal, along0, along1, perp))
    found: set[int] = set()
    for i, (ei, horizontal, along0, along1, perp) in enumerate(panels):
        for ej, horizontal2, b0, b1, perp2 in panels[i + 1:]:
            if horizontal is not horizontal2:
                continue
            overlap = min(along1, b1) - max(along0, b0)
            shorter = min(along1 - along0, b1 - b0)
            if overlap < 0.75 * shorter:
                continue
            if 15.0 <= abs(perp - perp2) <= 130.0:
                found.add(ei)
                found.add(ej)
    return found


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
    project: str | None = None,
    conditions: dict | list[dict] | None = None,
    min_len_mm: float | None = None,
    thick_min_mm: float | None = None,
    thick_max_mm: float | None = None,
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
    - 두께 0으로 겹친 사각 공유 변은 벽이 아니다. 평행 이중선만 벽이다.
    """
    profiles = _wall_condition_profiles(conditions, project)
    angle_tol = max(float(profile["candidate"]["angle_tolerance_deg"]) for profile in profiles)
    segs = extract_segments(
        entities,
        angle_tol_deg=angle_tol,
    )
    door_x_idxs = find_door_x_idxs(entities)
    wall_keys = detect_wall_keys(
        segs,
        conditions=profiles,
        min_len_mm=min_len_mm,
        thick_min_mm=thick_min_mm,
        thick_max_mm=thick_max_mm,
        door_entity_idxs=door_x_idxs,
        door_slots=_door_opening_slots(entities, door_x_idxs),
    )
    # 여닫이문 잎만 빼고 문끝 벽은 올린다. 잎에 가까운 다른 도형은 문으로 넣지 않는다.
    door_drop, door_jamb_keys = _apply_swing_doors(entities, segs, wall_keys)
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
    # 겹친 창틀도 벽이다. 색 구분은 validator가 하고, 여기서는 WALL로 올린다.
    frame_idxs = _window_frame_entity_idxs(entities)
    skip_idxs -= frame_idxs
    wall_entity_idxs |= frame_idxs
    for seg in segs:
        if seg.entity_idx in bay_idxs and seg.length >= 400.0:
            wall_keys.add(seg.key)

    wall_segs = [s for s in segs if s.key in wall_keys and s.entity_idx not in skip_idxs]
    return {
        "wall_entity_idxs": sorted(wall_entity_idxs),
        "wall_keys": sorted(wall_keys),
        "wall_segs": wall_segs,
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
        "n_window_frames": len(frame_idxs),
        "window_entity_idxs": sorted(frame_idxs),
        "column_entity_idxs": sorted(hbeam_idxs),
        "door_entity_idxs": sorted(set(door_x_idxs) | set(door_drop)),
        "entity_wall_ratio": entity_wall_ratio,
        "furniture_box_max_mm": furniture_box_max_mm,
        "wall_project": (profiles[0].get("_project") if profiles else project),
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
    for name, color in (
        (BASE_LAYER, BASE_COLOR),
        (WALL_LAYER, WALL_COLOR),
        (DOOR_LAYER, DOOR_COLOR),
        (WINDOW_LAYER, WINDOW_COLOR),
        (COLUMN_LAYER, COLUMN_COLOR),
    ):
        if name not in doc.layers:
            doc.layers.add(name, color=color)
    msp = doc.modelspace()
    wall_idxs = set(classification["wall_entity_idxs"])
    window_idxs = set(classification.get("window_entity_idxs") or [])
    column_idxs = set(classification.get("column_entity_idxs") or [])
    door_idxs = set(classification.get("door_entity_idxs") or [])
    skip = set(classification.get("skip_column_idxs") or [])
    counts = {"base": 0, "wall": 0, "door": 0, "window": 0, "column": 0, "wall_seg_lines": 0}

    def _stored_layer(ei: int) -> tuple[str, int] | None:
        if ei in column_idxs:
            return COLUMN_LAYER, COLUMN_COLOR
        if ei in window_idxs:
            return WINDOW_LAYER, WINDOW_COLOR
        if ei in door_idxs:
            return DOOR_LAYER, DOOR_COLOR
        if ei in wall_idxs and ei not in skip:
            return WALL_LAYER, WALL_COLOR
        return None

    if include_base:
        for ei, e in enumerate(entities):
            t = e.dxftype()
            if t not in GEOM_TYPES and t not in ("TEXT", "MTEXT"):
                continue
            if _stored_layer(ei) is not None and t not in ("TEXT", "MTEXT"):
                continue
            try:
                _copy_entity(msp, e, layer=BASE_LAYER, color=BASE_COLOR)
                counts["base"] += 1
            except Exception:  # noqa: BLE001
                continue

    # 벽·문·창·기둥은 레이어를 나눠 저장한다.
    for ei, e in enumerate(entities):
        stored = _stored_layer(ei)
        if stored is None:
            continue
        layer, color = stored
        try:
            _copy_entity(msp, e, layer=layer, color=color)
            counts[{"WALL": "wall", "DOOR": "door", "WINDOW": "window", "COLUMN": "column"}[layer]] += 1
        except Exception:  # noqa: BLE001
            continue

    # 부분만 벽인 폴리라인용: wall_segs를 빨간 LINE으로도 그림
    drawn = set()
    for s in classification["wall_segs"]:
        if s.entity_idx in wall_idxs:
            continue  # already whole entity
        if s.entity_idx in skip:
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
    window_segs: list[list[tuple[float, float]]] = []
    column_segs: list[list[tuple[float, float]]] = []
    door_segs: list[list[tuple[float, float]]] = []
    texts: list[tuple[float, float, str, float, float]] = []
    wall_idxs = set(classification["wall_entity_idxs"])
    window_idxs = set(classification.get("window_entity_idxs") or [])
    column_idxs = set(classification.get("column_entity_idxs") or [])
    door_idxs = set(classification.get("door_entity_idxs") or [])
    wall_keyset = set(classification["wall_keys"])
    skip = set(classification.get("skip_column_idxs") or [])

    def _paint(ei: int) -> list[list[tuple[float, float]]]:
        if ei in column_idxs:
            return column_segs
        if ei in window_idxs:
            return window_segs
        if ei in door_idxs:
            return door_segs
        return wall_segs

    import re as _re

    for ei, e in enumerate(entities):
        t = e.dxftype()
        try:
            if t == "LINE":
                p0 = (float(e.dxf.start.x), float(e.dxf.start.y))
                p1 = (float(e.dxf.end.x), float(e.dxf.end.y))
                xs.extend([p0[0], p1[0]])
                ys.extend([p0[1], p1[1]])
                if ei in column_idxs or ei in window_idxs or ei in door_idxs or ei in wall_idxs or (ei, 0) in wall_keyset:
                    _paint(ei).append([p0, p1])
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
                if ei in column_idxs or ei in window_idxs or ei in door_idxs or (ei in wall_idxs and ei not in skip):
                    _paint(ei).append(pts)
                elif ei in skip:
                    base_segs.append(pts)
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
    if window_segs:
        ax.add_collection(
            LineCollection(window_segs, colors="#00bcd4", linewidths=wall_linewidth, antialiased=True)
        )
    if column_segs:
        ax.add_collection(
            LineCollection(column_segs, colors="#2980b9", linewidths=wall_linewidth, antialiased=True)
        )
    if door_segs:
        ax.add_collection(
            LineCollection(door_segs, colors="#7cba25", linewidths=wall_linewidth, antialiased=True)
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
