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
    if abs(w - h) > max(w, h) * 0.35:
        return None
    return (cx, cy, w, h)


def find_hbeam_column_idxs(entities: list[DXFEntity]) -> set[int]:
    """H-Beam 기둥 엔티티 인덱스 — 정사각 + 중앙 '_' 만.

    외부 연결·직사각 슬리브 제외. 밀집 격자 제외.
    """
    squares: list[tuple[int, float, float, float, float]] = []
    dashes: list[tuple[float, float, float, int]] = []
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


def detect_wall_keys(
    segs: list[Seg],
    *,
    min_len_mm: float = 500.0,
    thick_min_mm: float = 50.0,
    thick_max_mm: float = 420.0,
    min_overlap_mm: float = 400.0,
    stair_count: int = 4,
    short_pair_max_mm: float = 2800.0,
) -> set[tuple[int, int]]:
    """평행 이중선(벽 두께 대역) 기반 벽 세그먼트 키 집합.

    stair_count: 동일 두께 대역 내 평행 이웃이 (stair_count-1)개 이상이면
    계단/해칭으로 보고 제외 (기본 4 → 이웃 ≥3).

    short_pair_max_mm: 양쪽 모두 이보다 짧은 이중선 쌍은 가구·설비 변으로 제외.
    """
    cand = [s for s in segs if (s.is_h or s.is_v) and s.length >= min_len_mm]
    wall: set[tuple[int, int]] = set()

    def mark_pairs(group: list[Seg], ortho_attr: str, along: str) -> None:
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
            # too many parallel lines → stair/hatch cluster
            if len(neighbors) >= stair_count - 1:
                continue
            # take nearest as wall pair face
            d0, b0 = min(neighbors, key=lambda t: t[0])
            if not (thick_min_mm <= d0 <= thick_max_mm):
                continue
            # 짧은-짧은 이중선 ≈ 책상·캐비닛 변 (긴 벽 런이 아님)
            if a.length < short_pair_max_mm and b0.length < short_pair_max_mm:
                continue
            wall.add(a.key)
            wall.add(b0.key)

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


def classify_entities(
    entities: list[DXFEntity],
    *,
    min_len_mm: float = 500.0,
    thick_min_mm: float = 50.0,
    thick_max_mm: float = 420.0,
    entity_wall_ratio: float = 0.75,
    furniture_box_max_mm: float = 3500.0,
) -> dict[str, Any]:
    """벽 분류.

    - 닫힌 박스 ≤ furniture_box_max_mm → 가구로 제외
      ※ 단 H-Beam 중첩 정사각 기둥은 WALL
    - 짧은 다변 폴리라인 → 조경/해칭 제외
    - 폴리라인은 벽 비율 ≥ entity_wall_ratio 일 때만 통째로 WALL
      (미만이면 세그먼트만 wall_segs 로 빨강)
    """
    segs = extract_segments(entities)
    wall_keys = detect_wall_keys(
        segs,
        min_len_mm=min_len_mm,
        thick_min_mm=thick_min_mm,
        thick_max_mm=thick_max_mm,
    )
    hbeam_idxs = find_hbeam_column_idxs(entities)
    wall_entity_idxs: set[int] = set(hbeam_idxs)
    skip_idxs: set[int] = set()
    n_furniture = 0
    n_hatch = 0
    n_hbeam = len(hbeam_idxs)
    for ei, e in enumerate(entities):
        if ei in hbeam_idxs:
            continue  # already WALL
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
