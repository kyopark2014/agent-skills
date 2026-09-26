#!/usr/bin/env python3
"""Vision-guided wall corrections: floor_wall_original → floor_wall_validated."""

from __future__ import annotations

import json
import math
import re
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import ezdxf
from ezdxf.document import Drawing

WALL_LAYER = "WALL"
BASE_LAYER = "BASE"
WALL_COLOR = 1
BASE_COLOR = 8

# 강당·오픈홀: 중앙 통로/보이드에 벽이 있을 수 없음
_OPEN_HALL_RE = re.compile(r"(강당|AUDITORIUM|\bAUDI\b)", re.IGNORECASE)
_OPEN_HALL_SKIP_RE = re.compile(
    r"(AHU|설비|기계|덕트|FAN|ELEV|엘리베이터|조정실)",
    re.IGNORECASE,
)


def normalize_bbox(raw: Any) -> dict[str, float] | None:
    """Vision review bbox → {xmin, ymin, xmax, ymax}.

    허용 형식:
      - dict with xmin/ymin/xmax/ymax (또는 x_min, x0/x1, left/right …)
      - dict with nested bbox / bbox_mm
      - [xmin, ymin, xmax, ymax]
    키 누락·형식 오류면 None.
    """
    if raw is None:
        return None
    if isinstance(raw, (list, tuple)) and len(raw) >= 4:
        try:
            xmin, ymin, xmax, ymax = (float(raw[0]), float(raw[1]), float(raw[2]), float(raw[3]))
        except (TypeError, ValueError):
            return None
        return {
            "xmin": min(xmin, xmax),
            "ymin": min(ymin, ymax),
            "xmax": max(xmin, xmax),
            "ymax": max(ymin, ymax),
        }
    if not isinstance(raw, dict):
        return None

    nested = raw.get("bbox") or raw.get("bbox_mm") or raw.get("rect")
    if isinstance(nested, (list, tuple, dict)):
        got = normalize_bbox(nested)
        if got:
            return got

    def _pick(*names: str) -> float | None:
        for n in names:
            if n in raw and raw[n] is not None:
                try:
                    return float(raw[n])
                except (TypeError, ValueError):
                    return None
        return None

    xmin = _pick("xmin", "x_min", "x0", "left", "x")
    ymin = _pick("ymin", "y_min", "y0", "bottom", "y")
    xmax = _pick("xmax", "x_max", "x1", "right")
    ymax = _pick("ymax", "y_max", "y1", "top")
    if None in (xmin, ymin, xmax, ymax):
        return None
    assert xmin is not None and ymin is not None and xmax is not None and ymax is not None
    return {
        "xmin": min(xmin, xmax),
        "ymin": min(ymin, ymax),
        "xmax": max(xmin, xmax),
        "ymax": max(ymin, ymax),
    }


def normalize_bbox_list(items: Any, *, field: str = "bboxes") -> list[dict[str, float]]:
    """review demote/promote_bboxes 정규화. 잘못된 항목은 skip + stderr 경고."""
    if not items:
        return []
    if isinstance(items, dict):
        # {"areas": [...]} 형태 방어
        for k in ("bboxes", "areas", "items", "rects"):
            if isinstance(items.get(k), list):
                items = items[k]
                break
        else:
            items = [items]
    out: list[dict[str, float]] = []
    if not isinstance(items, list):
        print(f"[warn] {field}: list 아님 → 무시 ({type(items).__name__})", file=sys.stderr)
        return out
    for i, raw in enumerate(items):
        got = normalize_bbox(raw)
        if got is None:
            keys = list(raw.keys()) if isinstance(raw, dict) else type(raw).__name__
            print(
                f"[warn] {field}[{i}] xmin/ymin/xmax/ymax 없음 → skip keys={keys}",
                file=sys.stderr,
            )
            continue
        # demote_bboxes 가 층을 반쯤 덮으면 이웃 WALL·복도까지 삭제됨 → skip
        if field == "demote_bboxes":
            w_m = (got["xmax"] - got["xmin"]) / 1000.0
            h_m = (got["ymax"] - got["ymin"]) / 1000.0
            if w_m > 25.0 or h_m > 25.0 or w_m * h_m > 200.0:
                print(
                    f"[warn] demote_bboxes[{i}] 너무 큼 "
                    f"({w_m:.1f}×{h_m:.1f} m) → skip (가구 단위 ≤25 m)",
                    file=sys.stderr,
                )
                continue
        out.append(got)
    return out


@dataclass
class AxisSeg:
    x0: float
    y0: float
    x1: float
    y1: float
    length: float
    is_h: bool
    is_v: bool
    entity: Any
    layer: str

    @property
    def ortho(self) -> float:
        return (self.y0 + self.y1) * 0.5 if self.is_h else (self.x0 + self.x1) * 0.5

    @property
    def along0(self) -> float:
        return min(self.x0, self.x1) if self.is_h else min(self.y0, self.y1)

    @property
    def along1(self) -> float:
        return max(self.x0, self.x1) if self.is_h else max(self.y0, self.y1)


def _bucket(v: float, step: float = 50.0) -> int:
    return int(round(v / step) * step)


def _normalize_hv(
    x0: float, y0: float, x1: float, y1: float
) -> tuple[float, float, float, float, float, bool, bool] | None:
    length = math.hypot(x1 - x0, y1 - y0)
    if length < 1.0:
        return None
    ang = abs(math.degrees(math.atan2(y1 - y0, x1 - x0))) % 180.0
    is_h = ang < 8.0 or abs(ang - 180.0) < 8.0
    is_v = abs(ang - 90.0) < 8.0
    if not (is_h or is_v):
        return None
    if is_h and x0 > x1:
        x0, x1 = x1, x0
        y0, y1 = y1, y0
    if is_v and y0 > y1:
        x0, x1 = x1, x0
        y0, y1 = y1, y0
    return x0, y0, x1, y1, length, is_h, is_v


def iter_axis_segs(msp, *, min_len_mm: float = 500.0) -> list[AxisSeg]:
    out: list[AxisSeg] = []
    for e in msp:
        layer = e.dxf.layer
        if layer not in (WALL_LAYER, BASE_LAYER):
            continue
        t = e.dxftype()
        pairs: list[tuple[float, float, float, float]] = []
        if t == "LINE":
            pairs.append(
                (
                    float(e.dxf.start.x),
                    float(e.dxf.start.y),
                    float(e.dxf.end.x),
                    float(e.dxf.end.y),
                )
            )
        elif t == "LWPOLYLINE":
            pts = [(float(p[0]), float(p[1])) for p in e.get_points("xy")]
            segs = list(zip(pts, pts[1:]))
            if e.closed and len(pts) >= 2 and pts[0] != pts[-1]:
                segs.append((pts[-1], pts[0]))
            for a, b in segs:
                pairs.append((a[0], a[1], b[0], b[1]))
        for x0, y0, x1, y1 in pairs:
            norm = _normalize_hv(x0, y0, x1, y1)
            if not norm:
                continue
            x0, y0, x1, y1, length, is_h, is_v = norm
            if length < min_len_mm:
                continue
            out.append(
                AxisSeg(x0, y0, x1, y1, length, is_h, is_v, e, layer)
            )
    return out


def _index_wall_runs(
    wall: list[AxisSeg],
) -> tuple[dict[int, list[tuple[float, float, float]]], dict[int, list[tuple[float, float, float]]]]:
    h: dict[int, list[tuple[float, float, float]]] = defaultdict(list)
    v: dict[int, list[tuple[float, float, float]]] = defaultdict(list)
    for s in wall:
        if s.is_h:
            h[_bucket(s.ortho)].append((s.along0, s.along1, s.length))
        else:
            v[_bucket(s.ortho)].append((s.along0, s.along1, s.length))
    return h, v


def _covered(
    intervals: list[tuple[float, float, float]], a0: float, a1: float, frac: float = 0.55
) -> bool:
    span = max(a1 - a0, 1.0)
    for b0, b1, _ in intervals:
        if min(a1, b1) - max(a0, b0) >= span * frac:
            return True
    return False


def _fills_true_gap(
    intervals: list[tuple[float, float, float]],
    a0: float,
    a1: float,
    *,
    gap_max: float = 4500.0,
) -> bool:
    """True only if segment overlaps a gap *between* two existing WALL runs."""
    if len(intervals) < 2:
        return False
    iv = sorted(intervals, key=lambda t: t[0])
    # merge overlapping wall intervals first
    merged: list[list[float]] = []
    for b0, b1, _ in iv:
        if not merged or b0 > merged[-1][1] + 50:
            merged.append([b0, b1])
        else:
            merged[-1][1] = max(merged[-1][1], b1)
    for i in range(len(merged) - 1):
        left1 = merged[i][1]
        right0 = merged[i + 1][0]
        gap = right0 - left1
        if gap <= 80 or gap > gap_max:
            continue
        # candidate must cover most of the gap (continuity repair)
        ov = min(a1, right0) - max(a0, left1)
        if ov >= min(gap * 0.5, a1 - a0) and ov >= 400:
            return True
    return False


def _has_parallel_pair(
    cand: AxisSeg,
    base: list[AxisSeg],
    *,
    thick_min: float = 50.0,
    thick_max: float = 420.0,
) -> bool:
    """BASE 이중선(벽 두께) 짝이 있으면 구조 벽 후보로 본다."""
    for o in base:
        if o.is_h != cand.is_h:
            continue
        if id(o.entity) == id(cand.entity) and abs(o.along0 - cand.along0) < 1:
            continue
        d = abs(o.ortho - cand.ortho)
        if not (thick_min <= d <= thick_max):
            continue
        ov = min(cand.along1, o.along1) - max(cand.along0, o.along0)
        if ov >= max(400.0, 0.25 * min(cand.length, o.length)):
            return True
    return False


def find_promote_segments(
    segs: list[AxisSeg],
    *,
    min_len_mm: float = 1200.0,
    gap_max_mm: float = 4500.0,
    require_double: bool = True,
) -> list[AxisSeg]:
    wall = [s for s in segs if s.layer == WALL_LAYER]
    base = [s for s in segs if s.layer == BASE_LAYER]
    h_wall, v_wall = _index_wall_runs(wall)
    out: list[AxisSeg] = []
    seen: set[tuple[float, float, float, float]] = set()

    for s in base:
        if s.length < min_len_mm:
            continue
        if s.is_h:
            buckets = [_bucket(s.ortho) + d * 50 for d in (-2, -1, 0, 1, 2)]
            iv: list[tuple[float, float, float]] = []
            for b in buckets:
                iv.extend(h_wall.get(b, []))
        else:
            buckets = [_bucket(s.ortho) + d * 50 for d in (-2, -1, 0, 1, 2)]
            iv = []
            for b in buckets:
                iv.extend(v_wall.get(b, []))
        if not iv:
            continue
        if _covered(iv, s.along0, s.along1):
            continue
        if not _fills_true_gap(iv, s.along0, s.along1, gap_max=gap_max_mm):
            continue
        if require_double and not _has_parallel_pair(s, base):
            continue
        key = (
            round(s.x0, 1),
            round(s.y0, 1),
            round(s.x1, 1),
            round(s.y1, 1),
        )
        if key in seen:
            continue
        seen.add(key)
        out.append(s)
    return out


def find_demote_wall_entities(
    segs: list[AxisSeg],
    *,
    short_max_mm: float = 2800.0,
    long_min_mm: float = 5000.0,
) -> set[int]:
    """WALL 엔티티 중 긴 벽 런에 붙지 않은 짧은 세그만 가진 것 → demote.

    Returns set of id(entity) for WALL entities to remove.
    """
    wall = [s for s in segs if s.layer == WALL_LAYER]
    h_wall, v_wall = _index_wall_runs([s for s in wall if s.length >= long_min_mm])

    # also keep shorts that sit on a long-run ortholine with overlap
    demote_ents: set[int] = set()
    keep_ents: set[int] = set()

    for s in wall:
        eid = id(s.entity)
        if s.length >= short_max_mm:
            keep_ents.add(eid)
            continue
        if s.is_h:
            iv: list[tuple[float, float, float]] = []
            for d in (-2, -1, 0, 1, 2):
                iv.extend(h_wall.get(_bucket(s.ortho) + d * 50, []))
            on_long = any(
                L2 >= long_min_mm and min(s.along1, b1) - max(s.along0, b0) > 200
                for b0, b1, L2 in iv
            )
            # adjacent to long wall end (door jamb continuation)
            near = any(
                abs(b1 - s.along0) <= 400 or abs(s.along1 - b0) <= 400 for b0, b1, _ in iv
            )
            if on_long or near:
                keep_ents.add(eid)
            else:
                demote_ents.add(eid)
        else:
            iv = []
            for d in (-2, -1, 0, 1, 2):
                iv.extend(v_wall.get(_bucket(s.ortho) + d * 50, []))
            on_long = any(
                L2 >= long_min_mm and min(s.along1, b1) - max(s.along0, b0) > 200
                for b0, b1, L2 in iv
            )
            near = any(
                abs(b1 - s.along0) <= 400 or abs(s.along1 - b0) <= 400 for b0, b1, _ in iv
            )
            if on_long or near:
                keep_ents.add(eid)
            else:
                demote_ents.add(eid)

    # entity kept if any long/keep segment; demote only if all segs demoted
    # Rebuild per-entity
    by_ent: dict[int, list[AxisSeg]] = defaultdict(list)
    for s in wall:
        by_ent[id(s.entity)].append(s)

    result: set[int] = set()
    for eid, elist in by_ent.items():
        if eid in keep_ents:
            continue
        if all(s.length < short_max_mm for s in elist) and eid in demote_ents:
            result.add(eid)
    return result


def demote_parallel_packs(
    segs: list[AxisSeg],
    *,
    pack_count: int = 4,
    thick_max_mm: float = 420.0,
    max_len_mm: float = 8000.0,
) -> set[int]:
    """짧은 평행 WALL 다발(객석·계단 트레드·테라스) 엔티티 demote."""
    wall = [s for s in segs if s.layer == WALL_LAYER and s.length <= max_len_mm]
    demote: set[int] = set()

    def mark(group: list[AxisSeg], ortho_attr: str) -> None:
        items = sorted(group, key=lambda s: s.ortho)
        n = len(items)
        for i in range(n):
            a = items[i]
            neigh = 0
            for j in range(i + 1, n):
                d = items[j].ortho - a.ortho
                if d > thick_max_mm:
                    break
                if d < 40:
                    continue
                # overlap along
                ov = min(a.along1, items[j].along1) - max(a.along0, items[j].along0)
                if ov >= 0.4 * min(a.length, items[j].length):
                    neigh += 1
            if neigh >= pack_count - 1:
                demote.add(id(a.entity))
                for j in range(i + 1, min(i + pack_count + 2, n)):
                    if items[j].ortho - a.ortho <= thick_max_mm:
                        demote.add(id(items[j].entity))

    mark([s for s in wall if s.is_h], "y")
    mark([s for s in wall if s.is_v], "x")
    return demote


def demote_closed_furniture_boxes(
    msp,
    *,
    max_span_mm: float = 8000.0,
    min_span_mm: float = 400.0,
    exclude_ids: set[int] | None = None,
) -> set[int]:
    """닫힌 소·중형 WALL 폴리라인(책상·캐비닛·랙 윤곽) demote.

    H-Beam 기둥(중첩 사각)은 exclude_ids 로 제외한다.
    """
    exclude_ids = exclude_ids or set()
    result: set[int] = set()
    for e in msp:
        if e.dxf.layer != WALL_LAYER or e.dxftype() != "LWPOLYLINE":
            continue
        if id(e) in exclude_ids:
            continue
        if not e.closed:
            continue
        pts = [(float(p[0]), float(p[1])) for p in e.get_points("xy")]
        if len(pts) < 3:
            continue
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        w = max(xs) - min(xs)
        h = max(ys) - min(ys)
        if min(w, h) < min_span_mm:
            continue
        if max(w, h) <= max_span_mm:
            result.add(id(e))
    return result


def _iter_closed_squares(
    msp,
    *,
    min_mm: float = 150.0,
    max_mm: float = 1200.0,
) -> list[tuple[float, float, float, float, Any]]:
    """닫힌 대략 정사각 LWPOLYLINE → (cx, cy, w, h, entity)."""
    out: list[tuple[float, float, float, float, Any]] = []
    for e in msp:
        if e.dxftype() != "LWPOLYLINE":
            continue
        pts = [(float(p[0]), float(p[1])) for p in e.get_points("xy")]
        if len(pts) < 4:
            continue
        # closed 또는 시작≈끝
        closed = bool(e.closed) or (
            math.hypot(pts[0][0] - pts[-1][0], pts[0][1] - pts[-1][1]) < 50.0
        )
        if not closed:
            continue
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        w = max(xs) - min(xs)
        h = max(ys) - min(ys)
        if not (min_mm <= w <= max_mm and min_mm <= h <= max_mm):
            continue
        if abs(w - h) > max(w, h) * 0.35:
            continue
        out.append(((min(xs) + max(xs)) * 0.5, (min(ys) + max(ys)) * 0.5, w, h, e))
    return out


def find_hbeam_column_entities(msp) -> set[int]:
    """H-Beam 기둥 엔티티 id.

    패턴: 동심 중첩 정사각(외곽·내곽) ± 내부 짧은 가로/세로 대시.
    """
    squares = _iter_closed_squares(msp)
    by_cell: dict[tuple[int, int], list[tuple[float, float, float, float, Any]]] = defaultdict(
        list
    )
    for cx, cy, w, h, e in squares:
        by_cell[(int(round(cx / 50.0) * 50), int(round(cy / 50.0) * 50))].append(
            (cx, cy, w, h, e)
        )

    column_ids: set[int] = set()
    centers: list[tuple[float, float, float]] = []  # cx, cy, outer_size
    for items in by_cell.values():
        if len(items) < 2:
            continue
        items = sorted(items, key=lambda t: max(t[2], t[3]))
        for i, a in enumerate(items):
            for b in items[i + 1 :]:
                if abs(a[0] - b[0]) > 100.0 or abs(a[1] - b[1]) > 100.0:
                    continue
                inner_sz = max(a[2], a[3])
                outer_sz = max(b[2], b[3])
                if outer_sz < inner_sz * 1.05 or outer_sz > inner_sz * 1.55:
                    continue
                # 테두리 두께 대략 벽두께 대역
                gap = (outer_sz - inner_sz) * 0.5
                if gap < 20.0 or gap > 250.0:
                    continue
                column_ids.add(id(a[4]))
                column_ids.add(id(b[4]))
                centers.append((a[0], a[1], outer_sz))

    # 내부 H 대시 (짧은 직교 LINE)
    for e in msp:
        if e.dxftype() != "LINE":
            continue
        try:
            x0, y0 = float(e.dxf.start.x), float(e.dxf.start.y)
            x1, y1 = float(e.dxf.end.x), float(e.dxf.end.y)
        except Exception:  # noqa: BLE001
            continue
        length = math.hypot(x1 - x0, y1 - y0)
        if not (60.0 <= length <= 700.0):
            continue
        dx, dy = abs(x1 - x0), abs(y1 - y0)
        is_h = dy <= max(20.0, 0.15 * length)
        is_v = dx <= max(20.0, 0.15 * length)
        if not (is_h or is_v):
            continue
        mx, my = (x0 + x1) * 0.5, (y0 + y1) * 0.5
        for cx, cy, sz in centers:
            if abs(mx - cx) <= sz * 0.35 and abs(my - cy) <= sz * 0.35:
                column_ids.add(id(e))
                break
    return column_ids


def promote_hbeam_columns(msp) -> int:
    """H-Beam 기둥 BASE → WALL (레이어·색 변경). Returns n promoted ents."""
    ids = find_hbeam_column_entities(msp)
    if not ids:
        return 0
    n = 0
    for e in msp:
        if id(e) not in ids:
            continue
        if e.dxf.layer == WALL_LAYER:
            continue
        e.dxf.layer = WALL_LAYER
        try:
            e.dxf.color = WALL_COLOR
        except Exception:  # noqa: BLE001
            pass
        n += 1
    return n


def demote_dense_short_clusters(
    segs: list[AxisSeg],
    *,
    cell_mm: float = 4000.0,
    short_max_mm: float = 3200.0,
    min_count: int = 8,
    long_min_mm: float = 8000.0,
) -> set[int]:
    """짧은 WALL이 밀집한 셀(가구 포드)만 demote. 긴 벽 런에 붙은 조각은 유지."""
    wall = [s for s in segs if s.layer == WALL_LAYER]
    long_wall = [s for s in wall if s.length >= long_min_mm]
    h_long, v_long = _index_wall_runs(long_wall)

    def on_long_run(s: AxisSeg) -> bool:
        if s.is_h:
            iv = []
            for d in (-2, -1, 0, 1, 2):
                iv.extend(h_long.get(_bucket(s.ortho) + d * 50, []))
        else:
            iv = []
            for d in (-2, -1, 0, 1, 2):
                iv.extend(v_long.get(_bucket(s.ortho) + d * 50, []))
        return any(
            min(s.along1, b1) - max(s.along0, b0) > 200
            or abs(b1 - s.along0) <= 500
            or abs(s.along1 - b0) <= 500
            for b0, b1, _ in iv
        )

    grid: dict[tuple[int, int], list[AxisSeg]] = defaultdict(list)
    for s in wall:
        if s.length > short_max_mm:
            continue
        mx = (s.x0 + s.x1) * 0.5
        my = (s.y0 + s.y1) * 0.5
        grid[(int(mx // cell_mm), int(my // cell_mm))].append(s)

    demote: set[int] = set()
    for cell_segs in grid.values():
        if len(cell_segs) < min_count:
            continue
        for s in cell_segs:
            if not on_long_run(s):
                demote.add(id(s.entity))
    return demote


def promote_room_row_dividers(
    segs: list[AxisSeg],
    bboxes: Iterable[Any],
    *,
    min_len_mm: float = 2500.0,
    neighbor_max_mm: float = 10000.0,
) -> list[AxisSeg]:
    """연속 실 열에서 이웃 칸막이는 WALL인데 자신만 BASE인 이중선(긴 칸막이) 승격."""
    boxes = normalize_bbox_list(list(bboxes), field="promote_bboxes")
    if not boxes:
        return []
    wall = [s for s in segs if s.layer == WALL_LAYER]
    base = [s for s in segs if s.layer == BASE_LAYER]
    h_wall, v_wall = _index_wall_runs(wall)
    out: list[AxisSeg] = []

    for s in base:
        if s.length < min_len_mm:
            continue
        mx = (s.x0 + s.x1) * 0.5
        my = (s.y0 + s.y1) * 0.5
        if not any(
            b["xmin"] <= mx <= b["xmax"] and b["ymin"] <= my <= b["ymax"] for b in boxes
        ):
            continue
        if not _has_parallel_pair(s, base):
            continue
        if s.is_v:
            iv = []
            for d in (-2, -1, 0, 1, 2):
                iv.extend(v_wall.get(_bucket(s.ortho) + d * 50, []))
        else:
            iv = []
            for d in (-2, -1, 0, 1, 2):
                iv.extend(h_wall.get(_bucket(s.ortho) + d * 50, []))
        if _covered(iv, s.along0, s.along1, frac=0.55):
            continue
        # 같은 방향·비슷한 길이의 WALL 이웃 ≥2 → 연속 실 열
        near = 0
        for w in wall:
            if w.is_h != s.is_h:
                continue
            if abs(w.ortho - s.ortho) > neighbor_max_mm or abs(w.ortho - s.ortho) < 500:
                continue
            ov = min(s.along1, w.along1) - max(s.along0, w.along0)
            if ov >= 0.5 * min(s.length, w.length) and w.length >= min_len_mm:
                near += 1
        if near >= 2:
            out.append(s)
    return out


def demote_in_bboxes(
    msp,
    bboxes: Iterable[Any],
) -> set[int]:
    """review.json demote_bboxes 안의 WALL 엔티티."""
    result: set[int] = set()
    boxes = normalize_bbox_list(list(bboxes), field="demote_bboxes")
    if not boxes:
        return result
    for e in msp:
        if e.dxf.layer != WALL_LAYER:
            continue
        t = e.dxftype()
        pts: list[tuple[float, float]] = []
        if t == "LINE":
            pts = [
                (float(e.dxf.start.x), float(e.dxf.start.y)),
                (float(e.dxf.end.x), float(e.dxf.end.y)),
            ]
        elif t == "LWPOLYLINE":
            pts = [(float(p[0]), float(p[1])) for p in e.get_points("xy")]
        if not pts:
            continue
        mx = sum(p[0] for p in pts) / len(pts)
        my = sum(p[1] for p in pts) / len(pts)
        for b in boxes:
            if b["xmin"] <= mx <= b["xmax"] and b["ymin"] <= my <= b["ymax"]:
                result.add(id(e))
                break
    return result


def promote_in_bboxes(
    segs: list[AxisSeg],
    bboxes: Iterable[Any],
    *,
    min_len_mm: float = 1000.0,
    gap_max_mm: float = 6000.0,
) -> list[AxisSeg]:
    """Vision bbox 안에서도 'WALL 런 사이 갭' + 이중선만 승격."""
    boxes = normalize_bbox_list(list(bboxes), field="promote_bboxes")
    if not boxes:
        return []
    # first get global gap candidates (slightly looser), then filter by bbox
    cands = find_promote_segments(
        segs, min_len_mm=min_len_mm, gap_max_mm=gap_max_mm, require_double=True
    )
    out: list[AxisSeg] = []
    for s in cands:
        mx = (s.x0 + s.x1) * 0.5
        my = (s.y0 + s.y1) * 0.5
        for b in boxes:
            if b["xmin"] <= mx <= b["xmax"] and b["ymin"] <= my <= b["ymax"]:
                out.append(s)
                break
    # also: vertical room dividers in bbox that have double-line but may not sit
    # in a collinear gap (orphan missing partition between red rooms)
    wall = [s for s in segs if s.layer == WALL_LAYER]
    base = [s for s in segs if s.layer == BASE_LAYER]
    h_wall, v_wall = _index_wall_runs(wall)
    for s in base:
        if s.length < max(min_len_mm, 2500.0) or not s.is_v:
            continue
        mx = (s.x0 + s.x1) * 0.5
        my = (s.y0 + s.y1) * 0.5
        in_box = any(
            b["xmin"] <= mx <= b["xmax"] and b["ymin"] <= my <= b["ymax"] for b in boxes
        )
        if not in_box:
            continue
        if not _has_parallel_pair(s, base):
            continue
        iv = []
        for d in (-2, -1, 0, 1, 2):
            iv.extend(v_wall.get(_bucket(s.ortho) + d * 50, []))
        if _covered(iv, s.along0, s.along1):
            continue
        # neighbor walls within 8 m horizontally → room row context (≥2)
        near_wall = 0
        for w in wall:
            if not w.is_v:
                continue
            d = abs(w.ortho - s.ortho)
            if d < 500 or d > 8000:
                continue
            ov = min(s.along1, w.along1) - max(s.along0, w.along0)
            if ov >= 0.5 * min(s.length, w.length) and w.length >= 2500:
                near_wall += 1
        if near_wall >= 2:
            out.append(s)
    return out


def protect_corridor_wall_entities(
    segs: list[AxisSeg],
    *,
    long_min_mm: float = 6000.0,
    mid_min_mm: float = 2500.0,
    exclude_ids: set[int] | None = None,
) -> set[int]:
    """복도·긴 이중선 벽 WALL 엔티티는 demote 금지.

    - 긴 세그먼트(≥ long_min) 포함 엔티티
    - 중·장 세그먼트(≥ mid_min) + 벽두께 이중선 쌍이 있는 엔티티
    - exclude_ids: 오픈홀 중앙 오검출 등 protect 제외 대상
    """
    wall = [s for s in segs if s.layer == WALL_LAYER]
    exclude_ids = exclude_ids or set()
    protect: set[int] = set()
    for s in wall:
        eid = id(s.entity)
        if eid in exclude_ids:
            continue
        if s.length >= long_min_mm:
            protect.add(eid)
            continue
        if s.length >= mid_min_mm and _has_parallel_pair(s, wall):
            protect.add(eid)
    return protect


def _iter_text_labels(msp) -> list[tuple[float, float, str]]:
    """TEXT/MTEXT → (x, y, plain_text)."""
    out: list[tuple[float, float, str]] = []
    for e in msp:
        t = e.dxftype()
        try:
            if t == "TEXT":
                s = str(e.dxf.text or "").strip()
                x, y = float(e.dxf.insert.x), float(e.dxf.insert.y)
            elif t == "MTEXT":
                raw = e.text or ""
                s = re.sub(r"\{[^;]*;", "", raw)
                s = s.replace("}", "").replace("\\P", " ")
                s = re.sub(r"\\[A-Za-z][^;]*;", "", s).strip()
                x, y = float(e.dxf.insert.x), float(e.dxf.insert.y)
            else:
                continue
        except Exception:  # noqa: BLE001
            continue
        if s:
            out.append((x, y, s))
    return out


def find_open_hall_labels(msp) -> list[tuple[float, float, str]]:
    """강당·오픈홀 실명 라벨. AHU(강당) 등 설비명은 제외."""
    hits: list[tuple[float, float, str]] = []
    for x, y, s in _iter_text_labels(msp):
        if not _OPEN_HALL_RE.search(s):
            continue
        if _OPEN_HALL_SKIP_RE.search(s):
            continue
        hits.append((x, y, s))
    return hits


def _nearest_hall_side_walls(
    segs: list[AxisSeg],
    lx: float,
    ly: float,
    *,
    search_mm: float = 25000.0,
    long_min_mm: float = 8000.0,
    clear_mm: float = 12000.0,
) -> tuple[float | None, float | None]:
    """라벨에서 clear_mm 이상 떨어진 최근접 좌·우 이중선 벽 x.

    라벨 근처 이중선(통로 오검출 쌍)은 외곽으로 쓰지 않는다.
    """
    near = [
        s
        for s in segs
        if s.is_v
        and s.length >= long_min_mm
        and abs((s.x0 + s.x1) * 0.5 - lx) <= search_mm
        and abs((s.y0 + s.y1) * 0.5 - ly) <= search_mm
    ]
    along_pad = 5000.0
    left_x: float | None = None
    right_x: float | None = None
    for s in near:
        if not (s.along0 - along_pad <= ly <= s.along1 + along_pad):
            continue
        if not _has_parallel_pair(s, near):
            continue
        if s.ortho <= lx - clear_mm:
            if left_x is None or s.ortho > left_x:
                left_x = s.ortho
        elif s.ortho >= lx + clear_mm:
            if right_x is None or s.ortho < right_x:
                right_x = s.ortho
    return left_x, right_x


def collect_open_hall_regions(
    msp,
    segs: list[AxisSeg],
) -> list[tuple[float, float, str, float, float, float | None, float | None]]:
    """(lx, ly, label, cx, band_mm, left_wall_x, right_wall_x).

    cx = 홀 좌·우 외곽 중점(없으면 라벨 x).
    band_mm = 중점에서 중앙 통로 demote 반경.
    """
    regions: list[tuple[float, float, str, float, float, float | None, float | None]] = []
    seen: list[tuple[float, float]] = []
    for lx, ly, name in find_open_hall_labels(msp):
        if any(abs(lx - sx) < 16000 and abs(ly - sy) < 12000 for sx, sy in seen):
            continue
        left_x, right_x = _nearest_hall_side_walls(segs, lx, ly)
        if left_x is not None and right_x is not None:
            cx = (left_x + right_x) * 0.5
            half = min(cx - left_x, right_x - cx)
            # 외곽에서 ≥3.5 m 안쪽만 — 측면 구조벽과 중앙 통로 사이
            band = min(half * 0.48, max(half - 4000.0, 4500.0))
            band = max(4500.0, min(band, 8500.0))
        else:
            cx = lx
            band = 5500.0
        seen.append((lx, ly))
        regions.append((lx, ly, name, cx, band, left_x, right_x))
    return regions


def _is_open_hall_interior_seg(
    s: AxisSeg,
    halls: list[tuple[float, float, str, float, float, float | None, float | None]],
    all_segs: list[AxisSeg] | None = None,
    *,
    min_len_mm: float = 8000.0,
) -> bool:
    """강당 중앙 통로를 가로지르는 긴 수직선인가."""
    if not s.is_v or s.length < min_len_mm:
        return False
    mx = (s.x0 + s.x1) * 0.5
    peers = [x for x in (all_segs or []) if x.is_v and x.length >= min_len_mm]
    for lx, ly, _name, cx, band, left_x, right_x in halls:
        if left_x is not None and abs(mx - left_x) <= 500.0:
            continue
        if right_x is not None and abs(mx - right_x) <= 500.0:
            continue
        half = None
        if left_x is not None and right_x is not None:
            half = min(cx - left_x, right_x - cx)
        near_label = abs(mx - lx) <= min(5000.0, band)
        near_center = abs(mx - cx) <= band
        # 단일선(이중선 아님)은 조금 더 넓은 대역에서도 통로 오검출로 본다
        # 단, 외곽 근처(≤5.5 m) 단일선은 외벽 후보이므로 제외
        wide_single = False
        near_outer = (
            (left_x is not None and abs(mx - left_x) <= 5500.0)
            or (right_x is not None and abs(mx - right_x) <= 5500.0)
        )
        if (
            half is not None
            and peers
            and not near_outer
            and not _has_parallel_pair(s, peers)
        ):
            wide_single = abs(mx - cx) <= min(half * 0.65, 10000.0)
        if not (near_label or near_center or wide_single):
            continue
        if ly < s.along0:
            gap = s.along0 - ly
        elif ly > s.along1:
            gap = ly - s.along1
        else:
            gap = 0.0
        if gap <= 20000.0:
            return True
    return False


def demote_open_hall_center_walls(
    msp,
    segs: list[AxisSeg],
    *,
    min_len_mm: float = 8000.0,
) -> set[int]:
    """강당·오픈홀 중앙을 가로지르는 긴 WALL demote.

    「강당」실명 라벨/홀 중심 대역을 관통하는 장축 수직선은 객석 통로/보이드이며
    벽이 될 수 없다. protect_corridor 보다 우선.
    """
    halls = collect_open_hall_regions(msp, segs)
    if not halls:
        return set()
    demote: set[int] = set()
    for s in segs:
        if s.layer != WALL_LAYER:
            continue
        if _is_open_hall_interior_seg(s, halls, segs, min_len_mm=min_len_mm):
            demote.add(id(s.entity))

    # 중앙 통로 오검출이 벽두께 쌍으로 잡힌 경우, 짝도 함께 제거
    wall_v = [
        s
        for s in segs
        if s.layer == WALL_LAYER and s.is_v and s.length >= min_len_mm
    ]
    demoted_segs = [s for s in wall_v if id(s.entity) in demote]
    for s in wall_v:
        if id(s.entity) in demote:
            continue
        mx = (s.x0 + s.x1) * 0.5
        if any(
            (left_x is not None and abs(mx - left_x) <= 500.0)
            or (right_x is not None and abs(mx - right_x) <= 500.0)
            for _lx, _ly, _n, _cx, _band, left_x, right_x in halls
        ):
            continue
        in_pair_zone = any(
            abs(mx - cx) <= band * 1.25 for _lx, _ly, _n, cx, band, _l, _r in halls
        )
        if not in_pair_zone or not demoted_segs:
            continue
        # 통로 오검출 쌍은 일반 벽두께(420)보다 넓게 잡히는 경우 있음
        if _has_parallel_pair(s, demoted_segs, thick_min=40.0, thick_max=900.0):
            demote.add(id(s.entity))
    return demote


def filter_promote_away_from_open_halls(
    promote: list[AxisSeg],
    msp,
    segs: list[AxisSeg],
    *,
    min_len_mm: float = 8000.0,
) -> list[AxisSeg]:
    """강당 중앙 통로 promote 후보 제거."""
    halls = collect_open_hall_regions(msp, segs)
    if not halls:
        return promote
    return [
        s
        for s in promote
        if not _is_open_hall_interior_seg(s, halls, segs, min_len_mm=min_len_mm)
    ]


def promote_corridor_walls(
    segs: list[AxisSeg],
    *,
    min_len_mm: float = 6000.0,
) -> list[AxisSeg]:
    """복도 양측처럼 긴 이중선 BASE → WALL 승격 (갭 조건 없이).

    가구·계단 해칭은 보통 이 길이의 단일 이중선이 아니므로 min_len으로 걸러낸다.
    """
    wall = [s for s in segs if s.layer == WALL_LAYER]
    base = [s for s in segs if s.layer == BASE_LAYER]
    h_wall, v_wall = _index_wall_runs(wall)
    out: list[AxisSeg] = []
    seen: set[tuple[float, float, float, float]] = set()

    for s in base:
        if s.length < min_len_mm:
            continue
        if not _has_parallel_pair(s, base):
            continue
        # 이미 WALL로 대부분 덮인 구간은 skip
        if s.is_h:
            iv: list[tuple[float, float, float]] = []
            for d in (-2, -1, 0, 1, 2):
                iv.extend(h_wall.get(_bucket(s.ortho) + d * 50, []))
        else:
            iv = []
            for d in (-2, -1, 0, 1, 2):
                iv.extend(v_wall.get(_bucket(s.ortho) + d * 50, []))
        if _covered(iv, s.along0, s.along1, frac=0.85):
            continue
        key = (
            round(s.x0, 1),
            round(s.y0, 1),
            round(s.x1, 1),
            round(s.y1, 1),
        )
        if key in seen:
            continue
        seen.add(key)
        out.append(s)
    return out


def find_stair_cores(msp) -> list[tuple[float, float, list[str]]]:
    """UP/DN TEXT 클러스터 → 계단실 중심 (cx, cy, labels)."""
    pts: list[tuple[float, float, str]] = []
    for x, y, s in _iter_text_labels(msp):
        u = s.strip().upper()
        if u in ("UP", "DN", "DOWN") or u.startswith("UP") or u.startswith("DN"):
            if len(u) <= 6:  # UP/DN/DOWN only, not longer words
                pts.append((x, y, u))
    if not pts:
        return []
    used = [False] * len(pts)
    cores: list[tuple[float, float, list[str]]] = []
    for i, (x, y, lab) in enumerate(pts):
        if used[i]:
            continue
        group = [(x, y, lab)]
        used[i] = True
        changed = True
        while changed:
            changed = False
            for j, (x2, y2, lab2) in enumerate(pts):
                if used[j]:
                    continue
                if any(math.hypot(x2 - gx, y2 - gy) < 8000.0 for gx, gy, _ in group):
                    group.append((x2, y2, lab2))
                    used[j] = True
                    changed = True
        cx = sum(g[0] for g in group) / len(group)
        cy = sum(g[1] for g in group) / len(group)
        cores.append((cx, cy, [g[2] for g in group]))
    return cores


def _estimate_stair_bbox(
    segs: list[AxisSeg],
    cx: float,
    cy: float,
    *,
    search_mm: float = 9000.0,
    min_len_mm: float = 2200.0,
) -> dict[str, float] | None:
    """계단실 주변 중·장축 선으로 외곽 bbox 추정."""
    near = [
        s
        for s in segs
        if s.length >= min_len_mm
        and abs((s.x0 + s.x1) * 0.5 - cx) <= search_mm
        and abs((s.y0 + s.y1) * 0.5 - cy) <= search_mm
    ]
    left = sorted(
        {
            s.ortho
            for s in near
            if s.is_v and s.ortho < cx - 800.0 and s.along0 - 500 <= cy <= s.along1 + 500
        },
        reverse=True,
    )
    right = sorted(
        {
            s.ortho
            for s in near
            if s.is_v and s.ortho > cx + 800.0 and s.along0 - 500 <= cy <= s.along1 + 500
        }
    )
    bot = sorted(
        {
            s.ortho
            for s in near
            if s.is_h and s.ortho < cy - 800.0 and s.along0 - 500 <= cx <= s.along1 + 500
        },
        reverse=True,
    )
    top = sorted(
        {
            s.ortho
            for s in near
            if s.is_h and s.ortho > cy + 800.0 and s.along0 - 500 <= cx <= s.along1 + 500
        }
    )
    if not (left and right):
        return {
            "xmin": cx - 3500.0,
            "xmax": cx + 3500.0,
            "ymin": cy - 5500.0,
            "ymax": cy + 5500.0,
        }
    xmin, xmax = left[0], right[0]
    if bot and top:
        ymin, ymax = bot[0], top[0]
    else:
        ymin, ymax = cy - 5500.0, cy + 5500.0
    w, h = xmax - xmin, ymax - ymin
    # 계단실 규모: 너무 크면(복도·홀) 거부, 너무 작으면 패딩
    if w < 2000.0 or w > 12000.0 or h < 3000.0 or h > 18000.0:
        return {
            "xmin": cx - 3500.0,
            "xmax": cx + 3500.0,
            "ymin": cy - 5500.0,
            "ymax": cy + 5500.0,
        }
    return {"xmin": xmin, "ymin": ymin, "xmax": xmax, "ymax": ymax}


def promote_stair_enclosure_walls(
    msp,
    segs: list[AxisSeg],
    *,
    min_len_mm: float = 2200.0,
    max_len_mm: float = 14000.0,
    edge_tol_mm: float = 700.0,
) -> list[AxisSeg]:
    """계단실(UP/DN) 외곽 이중선 BASE → WALL.

    계단은 문 개구를 제외하고 벽으로 둘러싸인다.
    중심 난간·트레드 해칭은 외곽이 아니므로 승격하지 않는다.
    """
    cores = find_stair_cores(msp)
    if not cores:
        return []
    wall = [s for s in segs if s.layer == WALL_LAYER]
    base = [s for s in segs if s.layer == BASE_LAYER]
    h_wall, v_wall = _index_wall_runs(wall)
    out: list[AxisSeg] = []
    seen: set[tuple[float, float, float, float]] = set()

    for cx, cy, _labs in cores:
        bbox = _estimate_stair_bbox(segs, cx, cy)
        if not bbox:
            continue
        for s in base:
            if not (min_len_mm <= s.length <= max_len_mm):
                continue
            if not _has_parallel_pair(s, base):
                continue
            mx = (s.x0 + s.x1) * 0.5
            my = (s.y0 + s.y1) * 0.5
            # 외곽만: 좌·우·상·하 변 근처. 중심 난간(중선) 제외
            on_left = abs(mx - bbox["xmin"]) <= edge_tol_mm and s.is_v
            on_right = abs(mx - bbox["xmax"]) <= edge_tol_mm and s.is_v
            on_bot = abs(my - bbox["ymin"]) <= edge_tol_mm and s.is_h
            on_top = abs(my - bbox["ymax"]) <= edge_tol_mm and s.is_h
            if not (on_left or on_right or on_bot or on_top):
                continue
            # 변이 계단 bbox와 충분히 겹쳐야 함
            if s.is_v:
                ov = min(s.along1, bbox["ymax"]) - max(s.along0, bbox["ymin"])
                if ov < min(s.length, bbox["ymax"] - bbox["ymin"]) * 0.35:
                    continue
            else:
                ov = min(s.along1, bbox["xmax"]) - max(s.along0, bbox["xmin"])
                if ov < min(s.length, bbox["xmax"] - bbox["xmin"]) * 0.35:
                    continue
            if s.is_h:
                iv = []
                for d in (-2, -1, 0, 1, 2):
                    iv.extend(h_wall.get(_bucket(s.ortho) + d * 50, []))
            else:
                iv = []
                for d in (-2, -1, 0, 1, 2):
                    iv.extend(v_wall.get(_bucket(s.ortho) + d * 50, []))
            if _covered(iv, s.along0, s.along1, frac=0.80):
                continue
            key = (
                round(s.x0, 1),
                round(s.y0, 1),
                round(s.x1, 1),
                round(s.y1, 1),
            )
            if key in seen:
                continue
            seen.add(key)
            out.append(s)
    return out


def protect_stair_enclosure_entities(
    msp,
    segs: list[AxisSeg],
    *,
    min_len_mm: float = 2200.0,
    edge_tol_mm: float = 700.0,
) -> set[int]:
    """계단실 외곽 WALL은 demote 금지 (문 개구 갭은 원래 없음)."""
    cores = find_stair_cores(msp)
    if not cores:
        return set()
    protect: set[int] = set()
    wall = [s for s in segs if s.layer == WALL_LAYER and s.length >= min_len_mm]
    for cx, cy, _labs in cores:
        bbox = _estimate_stair_bbox(segs, cx, cy)
        if not bbox:
            continue
        for s in wall:
            mx = (s.x0 + s.x1) * 0.5
            my = (s.y0 + s.y1) * 0.5
            on_edge = (
                (s.is_v and (abs(mx - bbox["xmin"]) <= edge_tol_mm or abs(mx - bbox["xmax"]) <= edge_tol_mm))
                or (
                    s.is_h
                    and (abs(my - bbox["ymin"]) <= edge_tol_mm or abs(my - bbox["ymax"]) <= edge_tol_mm)
                )
            )
            if not on_edge:
                continue
            if s.is_v:
                ov = min(s.along1, bbox["ymax"]) - max(s.along0, bbox["ymin"])
            else:
                ov = min(s.along1, bbox["xmax"]) - max(s.along0, bbox["xmin"])
            if ov >= 800.0:
                protect.add(id(s.entity))
    return protect


def find_elevator_shafts(msp) -> list[dict[str, float]]:
    """엘리베이터 샤프트 — 교차 대각선(X) 쌍 → 중심·반폭·반높이."""
    diags: list[tuple[float, float, float, float, float, float, float, float]] = []
    for e in msp:
        if e.dxftype() != "LINE":
            continue
        try:
            x0, y0 = float(e.dxf.start.x), float(e.dxf.start.y)
            x1, y1 = float(e.dxf.end.x), float(e.dxf.end.y)
        except Exception:  # noqa: BLE001
            continue
        length = math.hypot(x1 - x0, y1 - y0)
        dx, dy = abs(x1 - x0), abs(y1 - y0)
        if not (1800.0 <= length <= 4500.0 and dx > 800.0 and dy > 800.0):
            continue
        diags.append((length, (x0 + x1) * 0.5, (y0 + y1) * 0.5, x0, y0, x1, y1, dx, dy))

    shafts: list[dict[str, float]] = []
    for i, a in enumerate(diags):
        for b in diags[i + 1 :]:
            if math.hypot(a[1] - b[1], a[2] - b[2]) > 600.0:
                continue
            sax, say = a[5] - a[3], a[6] - a[4]
            sbx, sby = b[5] - b[3], b[6] - b[4]
            if sax * sbx > 0 and say * sby > 0:
                continue
            cx = (a[1] + b[1]) * 0.5
            cy = (a[2] + b[2]) * 0.5
            # 대각선 span → 실제 문/측벽 면은 약간 더 큼
            half_w = max(a[7], b[7]) * 0.5 * 1.25
            half_h = max(a[8], b[8]) * 0.5 * 1.15
            half_w = min(max(half_w, 850.0), 1600.0)
            half_h = min(max(half_h, 1000.0), 1800.0)
            shafts.append({"cx": cx, "cy": cy, "half_w": half_w, "half_h": half_h})

    uniq: list[dict[str, float]] = []
    for sh in shafts:
        if any(math.hypot(sh["cx"] - u["cx"], sh["cy"] - u["cy"]) < 1500.0 for u in uniq):
            continue
        uniq.append(sh)
    return uniq


def find_elevator_shaft_centers(msp) -> list[tuple[float, float]]:
    """엘리베이터 샤프트 중심 (하위 호환)."""
    return [(s["cx"], s["cy"]) for s in find_elevator_shafts(msp)]


def find_elevator_banks(
    msp,
    *,
    cluster_mm: float = 10000.0,
) -> list[dict[str, float | int]]:
    """엘리베이터 샤프트 클러스터 → bank dict."""
    shafts = find_elevator_shafts(msp)
    if not shafts:
        return []
    used = [False] * len(shafts)
    banks: list[dict[str, float | int]] = []
    for i, sh in enumerate(shafts):
        if used[i]:
            continue
        group = [sh]
        used[i] = True
        changed = True
        while changed:
            changed = False
            for j, sh2 in enumerate(shafts):
                if used[j]:
                    continue
                if any(
                    math.hypot(sh2["cx"] - g["cx"], sh2["cy"] - g["cy"]) < cluster_mm
                    for g in group
                ):
                    group.append(sh2)
                    used[j] = True
                    changed = True
        xs = [p["cx"] for p in group]
        ys = [p["cy"] for p in group]
        hw = max(p["half_w"] for p in group)
        hh = max(p["half_h"] for p in group)
        if max(xs) - min(xs) < 500.0:
            xs = [xs[0] - hw, xs[0] + hw]
        if max(ys) - min(ys) < 500.0:
            ys = [ys[0] - hh, ys[0] + hh]
        banks.append(
            {
                "cx": sum(p["cx"] for p in group) / len(group),
                "cy": sum(p["cy"] for p in group) / len(group),
                "xmin": min(xs),
                "xmax": max(xs),
                "ymin": min(ys),
                "ymax": max(ys),
                "n": len(group),
                "door_faces_x": 1 if _elevator_door_faces_along_x(group) else 0,
                "shafts": group,  # type: ignore[dict-item]
            }
        )
    return banks


def _elevator_door_faces_along_x(shafts: list[dict[str, float]]) -> bool:
    """문이 X축 방향(좌·우면)에 있으면 True — 도어면은 수직선(is_v).

    뱅크 중앙 복도 갭이 X에 있으면 문이 서로를 향해 X로 열린다.
    복도 갭을 못 찾으면 샤프트 짧은 변 쪽(통상 문면)을 사용.
    """
    xs = sorted({round(s["cx"] / 100.0) * 100.0 for s in shafts})
    ys = sorted({round(s["cy"] / 100.0) * 100.0 for s in shafts})

    def _gaps(vals: list[float]) -> list[float]:
        return [vals[i + 1] - vals[i] for i in range(len(vals) - 1)]

    gx, gy = _gaps(xs), _gaps(ys)

    def _corridor_gap(vals: list[float], gaps: list[float]) -> float:
        # 격자 3열 이상에서만 복도(중앙·비정상 피치) 판정
        if len(gaps) < 2:
            return 0.0
        med = sorted(gaps)[len(gaps) // 2]
        mid = (vals[0] + vals[-1]) * 0.5
        best = 0.0
        for i, g in enumerate(gaps):
            center = (vals[i] + vals[i + 1]) * 0.5
            if abs(center - mid) > (vals[-1] - vals[0]) * 0.35:
                continue
            if g < med * 0.85 or abs(g - med) > med * 0.15:
                best = max(best, g)
        return best

    cx_gap = _corridor_gap(xs, gx) if gx else 0.0
    cy_gap = _corridor_gap(ys, gy) if gy else 0.0
    if cx_gap > 0 or cy_gap > 0:
        return cx_gap >= cy_gap
    # 폴백: 문면 = 평면상 짧은 변 (half_w ≤ half_h → 좌우면)
    hw = sum(s["half_w"] for s in shafts) / len(shafts)
    hh = sum(s["half_h"] for s in shafts) / len(shafts)
    return hw <= hh


def _elevator_enclosure_bbox(
    bank: dict[str, float | int],
    *,
    pad_mm: float = 2800.0,
) -> dict[str, float]:
    """샤프트 bbox + 외곽 구조물까지 여유."""
    return {
        "xmin": float(bank["xmin"]) - pad_mm,
        "xmax": float(bank["xmax"]) + pad_mm,
        "ymin": float(bank["ymin"]) - pad_mm,
        "ymax": float(bank["ymax"]) + pad_mm,
    }


def _shaft_door_sign(shaft: dict[str, float], bank: dict[str, float | int]) -> int:
    """문면 방향: +1 = ortho 증가쪽, -1 = 감소쪽.

    뱅크 안 인접 열이 로비 간격(약 5–9.5 m)이면 그 로비를 향해 문이 열린다.
    (예: 4열 → 로비는 1-2열·3-4열 사이, 2-3열 중앙은 후면)
    """
    shafts: list[dict[str, float]] = bank.get("shafts") or []  # type: ignore[assignment]
    if not shafts:
        return -1
    if bool(bank.get("door_faces_x", 1)):
        xs = sorted({round(s["cx"] / 50.0) * 50.0 for s in shafts})
        cx = round(shaft["cx"] / 50.0) * 50.0
        for i in range(len(xs) - 1):
            gap = xs[i + 1] - xs[i]
            if not (5000.0 <= gap <= 9500.0):
                continue
            if abs(cx - xs[i]) <= 100.0:
                return 1  # 오른쪽 로비
            if abs(cx - xs[i + 1]) <= 100.0:
                return -1  # 왼쪽 로비
        return -1 if shaft["cx"] <= float(bank["cx"]) else 1
    ys = sorted({round(s["cy"] / 50.0) * 50.0 for s in shafts})
    cy = round(shaft["cy"] / 50.0) * 50.0
    for i in range(len(ys) - 1):
        gap = ys[i + 1] - ys[i]
        if not (5000.0 <= gap <= 9500.0):
            continue
        if abs(cy - ys[i]) <= 100.0:
            return 1
        if abs(cy - ys[i + 1]) <= 100.0:
            return -1
    return -1 if shaft["cy"] <= float(bank["cy"]) else 1



def _is_elevator_door_or_back_seg(
    s: AxisSeg,
    shaft: dict[str, float],
    *,
    door_faces_x: bool,
    face_tol_mm: float = 280.0,
) -> bool:
    """샤프트 문면·후면의 전고 세그먼트(문 개구를 가로지르는 긴 선)인가."""
    cx, cy = shaft["cx"], shaft["cy"]
    hw, hh = shaft["half_w"], shaft["half_h"]
    if door_faces_x:
        if not s.is_v:
            return False
        if abs(s.ortho - (cx - hw)) > face_tol_mm and abs(s.ortho - (cx + hw)) > face_tol_mm:
            return False
        # 전고(샤프·문 개구 포함)만 — 짧은 잼은 제외
        if s.length < hh * 1.2:
            return False
        ov = min(s.along1, cy + hh + 200.0) - max(s.along0, cy - hh - 200.0)
        return ov >= min(s.length * 0.40, hh * 0.8)
    if not s.is_h:
        return False
    if abs(s.ortho - (cy - hh)) > face_tol_mm and abs(s.ortho - (cy + hh)) > face_tol_mm:
        return False
    if s.length < hw * 1.2:
        return False
    ov = min(s.along1, cx + hw + 200.0) - max(s.along0, cx - hw - 200.0)
    return ov >= min(s.length * 0.40, hw * 0.8)


def _is_elevator_back_structure_seg(
    s: AxisSeg,
    shaft: dict[str, float],
    bank: dict[str, float | int],
) -> bool:
    """엘리베이터 후면(입구 반대쪽) 구조 — 전고·짧은 선 모두 비벽."""
    door_x = bool(bank.get("door_faces_x", 1))
    back_sign = -_shaft_door_sign(shaft, bank)
    cx, cy = shaft["cx"], shaft["cy"]
    hw, hh = shaft["half_w"], shaft["half_h"]
    if door_x:
        if not s.is_v:
            return False
        toward = (s.ortho - cx) * back_sign
        if not (hw - 400.0 <= toward <= hw + 1000.0):
            return False
        my = (s.along0 + s.along1) * 0.5
        return abs(my - cy) <= hh + 600.0 and 300.0 <= s.length <= 5000.0
    if not s.is_h:
        return False
    toward = (s.ortho - cy) * back_sign
    if not (hh - 400.0 <= toward <= hh + 1000.0):
        return False
    mx = (s.along0 + s.along1) * 0.5
    return abs(mx - cx) <= hw + 600.0 and 300.0 <= s.length <= 5000.0


def _is_elevator_center_spine_seg(
    s: AxisSeg,
    bank: dict[str, float | int],
    shafts: list[dict[str, float]],
) -> bool:
    """대향 뱅크 중앙 스파인(후면이 마주보는 축) — 비벽."""
    if not shafts or len(shafts) < 2:
        return False
    door_x = bool(bank.get("door_faces_x", 1))
    if door_x:
        if not s.is_v or s.length < 2000.0:
            return False
        xs = sorted({round(sh["cx"] / 100.0) * 100.0 for sh in shafts})
        if len(xs) < 2:
            return False
        bank_mid = float(bank["cx"])
        # 뱅크 중심에 가장 가까운 갭 = 대향 복도
        gaps = [(xs[i + 1] - xs[i], (xs[i] + xs[i + 1]) * 0.5) for i in range(len(xs) - 1)]
        gap, mid = min(gaps, key=lambda t: abs(t[1] - bank_mid))
        if gap < 2500.0:
            return False
        if abs(s.ortho - mid) > max(gap * 0.55, 1500.0):
            return False
        ov = min(s.along1, float(bank["ymax"]) + 1500.0) - max(
            s.along0, float(bank["ymin"]) - 1500.0
        )
        return ov >= 1500.0
    if not s.is_h or s.length < 2000.0:
        return False
    ys = sorted({round(sh["cy"] / 100.0) * 100.0 for sh in shafts})
    if len(ys) < 2:
        return False
    bank_mid = float(bank["cy"])
    gaps = [(ys[i + 1] - ys[i], (ys[i] + ys[i + 1]) * 0.5) for i in range(len(ys) - 1)]
    gap, mid = min(gaps, key=lambda t: abs(t[1] - bank_mid))
    if gap < 2500.0:
        return False
    if abs(s.ortho - mid) > max(gap * 0.55, 1500.0):
        return False
    ov = min(s.along1, float(bank["xmax"]) + 1500.0) - max(
        s.along0, float(bank["xmin"]) - 1500.0
    )
    return ov >= 1500.0


def _is_elevator_cab_side_seg(
    s: AxisSeg,
    shaft: dict[str, float],
    *,
    door_faces_x: bool,
    face_tol_mm: float = 350.0,
) -> bool:
    """엘리베이터 카 좌·우 측면(도어축 ⊥, 평면상 상·하) — 벽이 아님."""
    cx, cy = shaft["cx"], shaft["cy"]
    hw, hh = shaft["half_w"], shaft["half_h"]
    if door_faces_x:
        if not s.is_h:
            return False
        if abs(s.ortho - (cy - hh)) > face_tol_mm and abs(s.ortho - (cy + hh)) > face_tol_mm:
            return False
        ov = min(s.along1, cx + hw + 400.0) - max(s.along0, cx - hw - 400.0)
        return ov >= min(s.length * 0.35, hw * 0.6) and 800.0 <= s.length <= 5000.0
    if not s.is_v:
        return False
    if abs(s.ortho - (cx - hw)) > face_tol_mm and abs(s.ortho - (cx + hw)) > face_tol_mm:
        return False
    ov = min(s.along1, cy + hh + 400.0) - max(s.along0, cy - hh - 400.0)
    return ov >= min(s.length * 0.35, hh * 0.6) and 800.0 <= s.length <= 5000.0


def _is_elevator_door_jamb_seg(
    s: AxisSeg,
    shaft: dict[str, float],
    bank: dict[str, float | int],
    *,
    face_tol_mm: float = 450.0,
) -> bool:
    """문 개구를 위·아래로 감싸는 입구면 잼 구조물.

    전고 문면 선이 아니라, 샤프트 상·하단에 걸친 짧은 세그먼트.
    """
    door_x = bool(bank.get("door_faces_x", 1))
    sign = _shaft_door_sign(shaft, bank)
    cx, cy = shaft["cx"], shaft["cy"]
    hw, hh = shaft["half_w"], shaft["half_h"]
    if door_x:
        if not s.is_v:
            return False
        toward = (s.ortho - cx) * sign
        if not (hw - face_tol_mm <= toward <= hw + 900.0):
            return False
        if not (300.0 <= s.length <= max(hh * 0.95, 900.0)):
            return False
        near_bot = abs(s.along0 - (cy - hh)) <= 450.0 or abs(
            (s.along0 + s.along1) * 0.5 - (cy - hh + s.length * 0.5)
        ) <= 400.0
        near_top = abs(s.along1 - (cy + hh)) <= 450.0 or abs(
            (s.along0 + s.along1) * 0.5 - (cy + hh - s.length * 0.5)
        ) <= 400.0
        return near_bot or near_top
    if not s.is_h:
        return False
    toward = (s.ortho - cy) * sign
    if not (hh - face_tol_mm <= toward <= hh + 900.0):
        return False
    if not (300.0 <= s.length <= max(hw * 0.95, 900.0)):
        return False
    near_l = abs(s.along0 - (cx - hw)) <= 450.0
    near_r = abs(s.along1 - (cx + hw)) <= 450.0
    return near_l or near_r


def _is_elevator_entrance_portal_seg(
    s: AxisSeg,
    shaft: dict[str, float],
    bank: dict[str, float | int],
) -> bool:
    """입구쪽 도어 포털(문 옆 두꺼운 수직 박스 변) — 입구 측면 벽."""
    door_x = bool(bank.get("door_faces_x", 1))
    sign = _shaft_door_sign(shaft, bank)
    if door_x:
        if not s.is_v:
            return False
        toward = (s.ortho - shaft["cx"]) * sign
        if not (shaft["half_w"] - 350.0 <= toward <= shaft["half_w"] + 500.0):
            return False
        # 문 개구 높이 대역의 포털 (전고보다 짧고 잼보다 김)
        if not (900.0 <= s.length <= min(shaft["half_h"] * 2.0, 1800.0)):
            return False
        my = (s.along0 + s.along1) * 0.5
        return abs(my - shaft["cy"]) <= shaft["half_h"] * 0.55
    if not s.is_h:
        return False
    toward = (s.ortho - shaft["cy"]) * sign
    if not (shaft["half_h"] - 350.0 <= toward <= shaft["half_h"] + 500.0):
        return False
    if not (900.0 <= s.length <= min(shaft["half_w"] * 2.0, 1800.0)):
        return False
    mx = (s.along0 + s.along1) * 0.5
    return abs(mx - shaft["cx"]) <= shaft["half_w"] * 0.55



def _is_elevator_door_interstitial_seg(
    s: AxisSeg,
    shaft: dict[str, float],
    bank: dict[str, float | int],
    shafts: list[dict[str, float]],
) -> bool:
    """적층 엘리베이터 사이(문면 쪽) 짧은 수직 칸막이 — 문 사이 구조물."""
    door_x = bool(bank.get("door_faces_x", 1))
    sign = _shaft_door_sign(shaft, bank)
    if door_x:
        if not s.is_v or not (800.0 <= s.length <= 2200.0):
            return False
        toward = (s.ortho - shaft["cx"]) * sign
        if not (shaft["half_w"] - 200.0 <= toward <= shaft["half_w"] + 1200.0):
            return False
        my = (s.along0 + s.along1) * 0.5
        # 이웃 샤프트와의 중간 밴드
        for o in shafts:
            if abs(o["cx"] - shaft["cx"]) > 500.0:
                continue
            if abs(o["cy"] - shaft["cy"]) < 500.0:
                continue
            mid = (shaft["cy"] + o["cy"]) * 0.5
            if abs(my - mid) <= 900.0:
                return True
        return False
    if not s.is_h or not (800.0 <= s.length <= 2200.0):
        return False
    toward = (s.ortho - shaft["cy"]) * sign
    if not (shaft["half_h"] - 200.0 <= toward <= shaft["half_h"] + 1200.0):
        return False
    mx = (s.along0 + s.along1) * 0.5
    for o in shafts:
        if abs(o["cy"] - shaft["cy"]) > 500.0:
            continue
        if abs(o["cx"] - shaft["cx"]) < 500.0:
            continue
        mid = (shaft["cx"] + o["cx"]) * 0.5
        if abs(mx - mid) <= 900.0:
            return True
    return False


def _is_elevator_bank_flank_seg(
    s: AxisSeg,
    bank: dict[str, float | int],
    *,
    door_faces_x: bool,
    edge_tol_mm: float = 1200.0,
) -> bool:
    """뱅크 외곽 좌·우(또는 상·하) 긴 외벽."""
    pad = 2800.0
    if door_faces_x:
        if not s.is_v or s.length < 2000.0:
            return False
        left = float(bank["xmin"]) - pad
        right = float(bank["xmax"]) + pad
        on_left = abs(s.ortho - left) <= edge_tol_mm or (
            s.ortho < float(bank["xmin"]) - 200.0 and s.ortho >= left - 400.0
        )
        on_right = abs(s.ortho - right) <= edge_tol_mm or (
            s.ortho > float(bank["xmax"]) + 200.0 and s.ortho <= right + 400.0
        )
        if not (on_left or on_right):
            return False
        ov = min(s.along1, float(bank["ymax"]) + pad) - max(
            s.along0, float(bank["ymin"]) - pad
        )
        return ov >= 1500.0
    if not s.is_h or s.length < 2000.0:
        return False
    bot = float(bank["ymin"]) - pad
    top = float(bank["ymax"]) + pad
    on_bot = abs(s.ortho - bot) <= edge_tol_mm or (
        s.ortho < float(bank["ymin"]) - 200.0 and s.ortho >= bot - 400.0
    )
    on_top = abs(s.ortho - top) <= edge_tol_mm or (
        s.ortho > float(bank["ymax"]) + 200.0 and s.ortho <= top + 400.0
    )
    if not (on_bot or on_top):
        return False
    ov = min(s.along1, float(bank["xmax"]) + pad) - max(
        s.along0, float(bank["xmin"]) - pad
    )
    return ov >= 1500.0


def _elevator_bank_box(
    bank: dict[str, float | int],
    shafts: list[dict[str, float]],
    segs: list[AxisSeg] | None = None,
) -> dict[str, float]:
    """엘리베이터 뱅크 네모 박스 (외곽 플랭크 포함)."""
    hw = max((s["half_w"] for s in shafts), default=1200.0)
    hh = max((s["half_h"] for s in shafts), default=1400.0)
    xmin = min(s["cx"] for s in shafts) - hw
    xmax = max(s["cx"] for s in shafts) + hw
    ymin = min(s["cy"] for s in shafts) - hh
    ymax = max(s["cy"] for s in shafts) + hh
    # 외곽 플랭크: 샤프트에 가장 가까운 이중 외벽만 채택 (먼 복도벽 제외)
    shaft_xmin = xmin
    shaft_xmax = xmax
    shaft_ymin = ymin
    shaft_ymax = ymax
    if segs and bool(bank.get("door_faces_x", 1)):
        y_span = max(shaft_ymax - shaft_ymin, 1.0)
        left_xs: list[float] = []
        right_xs: list[float] = []
        for s in segs:
            if not s.is_v or s.length < 5000.0:
                continue
            ov = min(s.along1, shaft_ymax + 500.0) - max(s.along0, shaft_ymin - 500.0)
            if ov < y_span * 0.55:
                continue
            if shaft_xmin - 2500.0 <= s.ortho <= shaft_xmin + 400.0:
                left_xs.append(s.ortho)
            if shaft_xmax - 400.0 <= s.ortho <= shaft_xmax + 2500.0:
                right_xs.append(s.ortho)
        if left_xs:
            # 샤프트에 가장 가까운 좌측 플랭크 묶음
            near = max(left_xs)
            xmin = min(x for x in left_xs if x >= near - 500.0)
        if right_xs:
            near = min(right_xs)
            xmax = max(x for x in right_xs if x <= near + 500.0)
        x_span = max(xmax - xmin, 1.0)
        bot_ys: list[float] = []
        top_ys: list[float] = []
        for s in segs:
            if not s.is_h or s.length < 2000.0:
                continue
            ov = min(s.along1, xmax + 500.0) - max(s.along0, xmin - 500.0)
            if ov < min(x_span * 0.25, 2500.0):
                continue
            if shaft_ymin - 2500.0 <= s.ortho <= shaft_ymin + 400.0:
                bot_ys.append(s.ortho)
            if shaft_ymax - 400.0 <= s.ortho <= shaft_ymax + 2500.0:
                top_ys.append(s.ortho)
        if bot_ys:
            near = max(bot_ys)
            ymin = min(y for y in bot_ys if y >= near - 500.0)
        if top_ys:
            near = min(top_ys)
            ymax = max(y for y in top_ys if y <= near + 500.0)
    return {"xmin": xmin, "xmax": xmax, "ymin": ymin, "ymax": ymax}


def _elevator_bank_corners(box: dict[str, float]) -> list[tuple[str, float, float]]:
    return [
        ("TL", box["xmin"], box["ymax"]),
        ("BL", box["xmin"], box["ymin"]),
        ("TR", box["xmax"], box["ymax"]),
        ("BR", box["xmax"], box["ymin"]),
    ]


def _clip_axis_seg_to_range(
    s: AxisSeg,
    a0: float,
    a1: float,
) -> AxisSeg | None:
    """세그먼트를 along 구간으로 잘라 새 AxisSeg 반환."""
    lo = max(s.along0, a0)
    hi = min(s.along1, a1)
    if hi - lo < 350.0:
        return None
    if s.is_h:
        y = s.ortho
        return AxisSeg(lo, y, hi, y, hi - lo, True, False, s.entity, s.layer)
    x = s.ortho
    return AxisSeg(x, lo, x, hi, hi - lo, False, True, s.entity, s.layer)


def _elevator_corner_leg_segs(
    segs: list[AxisSeg],
    box: dict[str, float],
    *,
    corner_mm: float = 2600.0,
    ortho_tol_mm: float = 700.0,
) -> list[AxisSeg]:
    """뱅크 네 모서리 L자 (수직+수평 다리) 세그먼트 — 긴 벽은 모서리만 클립."""
    out: list[AxisSeg] = []
    seen: set[tuple[float, float, float, float]] = set()
    corners = _elevator_bank_corners(box)
    for _name, cx, cy in corners:
        for s in segs:
            clipped: AxisSeg | None = None
            if s.is_v and abs(s.ortho - cx) <= ortho_tol_mm:
                clipped = _clip_axis_seg_to_range(s, cy - corner_mm, cy + corner_mm)
            elif s.is_h and abs(s.ortho - cy) <= ortho_tol_mm:
                clipped = _clip_axis_seg_to_range(s, cx - corner_mm, cx + corner_mm)
            if clipped is None:
                continue
            key = (
                round(clipped.x0, 1),
                round(clipped.y0, 1),
                round(clipped.x1, 1),
                round(clipped.y1, 1),
            )
            if key in seen:
                continue
            seen.add(key)
            out.append(clipped)
    return out


def _is_elevator_perimeter_seg(
    s: AxisSeg,
    box: dict[str, float],
    *,
    edge_tol_mm: float = 800.0,
) -> bool:
    """엘리베이터 뱅크 네모 외곽(좌·우·상·하 + 모서리 L) 세그먼트인가."""
    xmin, xmax = box["xmin"], box["xmax"]
    ymin, ymax = box["ymin"], box["ymax"]
    if s.is_v:
        on_left = abs(s.ortho - xmin) <= edge_tol_mm
        on_right = abs(s.ortho - xmax) <= edge_tol_mm
        if not (on_left or on_right):
            return False
        ov = min(s.along1, ymax + 400.0) - max(s.along0, ymin - 400.0)
        return ov >= 400.0
    if s.is_h:
        on_bot = abs(s.ortho - ymin) <= edge_tol_mm
        on_top = abs(s.ortho - ymax) <= edge_tol_mm
        if not (on_bot or on_top):
            return False
        ov = min(s.along1, xmax + 400.0) - max(s.along0, xmin - 400.0)
        return ov >= 400.0
    return False


def _is_elevator_door_opening_seg(
    s: AxisSeg,
    shaft: dict[str, float],
    bank: dict[str, float | int],
) -> bool:
    """엘리베이터 문 개구(전고 문면·문 포털) — 주위에서 제외할 비벽."""
    door_x = bool(bank.get("door_faces_x", 1))
    # 전고 문면
    if _is_elevator_door_or_back_seg(s, shaft, door_faces_x=door_x):
        # 후면이 아니라 문면만
        sign = _shaft_door_sign(shaft, bank)
        if door_x:
            if not s.is_v:
                return False
            toward = (s.ortho - shaft["cx"]) * sign
            return shaft["half_w"] - 350.0 <= toward <= shaft["half_w"] + 400.0
        if not s.is_h:
            return False
        toward = (s.ortho - shaft["cy"]) * sign
        return shaft["half_h"] - 350.0 <= toward <= shaft["half_h"] + 400.0
    if _is_elevator_entrance_portal_seg(s, shaft, bank):
        return True
    return False


def promote_elevator_enclosure_walls(
    msp,
    segs: list[AxisSeg],
    *,
    min_len_mm: float = 300.0,
    max_len_mm: float = 25000.0,
) -> list[AxisSeg]:
    """엘리베이터: 입구 측면 잼(화살표) + 뱅크 주위 외곽 → WALL. 문 개구 제외."""
    banks = find_elevator_banks(msp)
    if not banks:
        return []
    base = [s for s in segs if s.layer == BASE_LAYER and min_len_mm <= s.length <= max_len_mm]
    wall = [s for s in segs if s.layer == WALL_LAYER]
    h_wall, v_wall = _index_wall_runs(wall)
    out: list[AxisSeg] = []
    seen: set[tuple[float, float, float, float]] = set()

    for bank in banks:
        shafts: list[dict[str, float]] = bank.get("shafts") or []  # type: ignore[assignment]
        if not shafts:
            continue
        box = _elevator_bank_box(bank, shafts, segs)
        for s in base:
            is_jamb = any(_is_elevator_door_jamb_seg(s, sh, bank) for sh in shafts)
            is_inter = any(
                _is_elevator_door_interstitial_seg(s, sh, bank, shafts) for sh in shafts
            )
            is_peri = _is_elevator_perimeter_seg(s, box)
            if not (is_jamb or is_inter or is_peri):
                continue
            # 전고 문 개구만 제외 (잼은 유지)
            if (
                not is_jamb
                and not is_inter
                and any(_is_elevator_door_opening_seg(s, sh, bank) for sh in shafts)
            ):
                continue
            if not _has_parallel_pair(s, base, thick_min=15.0, thick_max=500.0):
                if not is_jamb and s.length > 2500.0:
                    continue
            # 입구 잼·문사이는 이중선 한 쪽만 WALL이어도 나머지 BASE를 꼭 승격
            if not is_jamb and not is_inter:
                if s.is_h:
                    iv: list[tuple[float, float, float]] = []
                    for d in (-2, -1, 0, 1, 2):
                        iv.extend(h_wall.get(_bucket(s.ortho) + d * 50, []))
                else:
                    iv = []
                    for d in (-2, -1, 0, 1, 2):
                        iv.extend(v_wall.get(_bucket(s.ortho) + d * 50, []))
                if _covered(iv, s.along0, s.along1, frac=0.90):
                    continue
            key = (
                round(s.x0, 1),
                round(s.y0, 1),
                round(s.x1, 1),
                round(s.y1, 1),
            )
            if key in seen:
                continue
            seen.add(key)
            out.append(s)
    return out


def demote_elevator_door_back_faces(
    msp,
    segs: list[AxisSeg],
) -> set[int]:
    """엘리베이터: 전고 문 개구·후면·카 측면·중앙 스파인 제거. 입구 잼·주위는 유지."""
    banks = find_elevator_banks(msp)
    if not banks:
        return set()
    out: set[int] = set()
    wall = [s for s in segs if s.layer == WALL_LAYER]
    for bank in banks:
        door_x = bool(bank.get("door_faces_x", 1))
        shafts: list[dict[str, float]] = bank.get("shafts") or []  # type: ignore[assignment]
        if not shafts:
            continue
        box = _elevator_bank_box(bank, shafts, segs)
        for s in wall:
            # 입구 잼·문사이 칸막이·주위 외곽은 유지
            if any(_is_elevator_door_jamb_seg(s, sh, bank) for sh in shafts):
                continue
            if any(
                _is_elevator_door_interstitial_seg(s, sh, bank, shafts) for sh in shafts
            ):
                continue
            if _is_elevator_perimeter_seg(s, box):
                if any(_is_elevator_door_opening_seg(s, sh, bank) for sh in shafts):
                    out.add(id(s.entity))
                continue
            if _is_elevator_center_spine_seg(s, bank, shafts):
                out.add(id(s.entity))
                continue
            if any(_is_elevator_door_opening_seg(s, sh, bank) for sh in shafts):
                out.add(id(s.entity))
                continue
            if any(_is_elevator_back_structure_seg(s, sh, bank) for sh in shafts):
                out.add(id(s.entity))
                continue
            if any(
                _is_elevator_door_or_back_seg(s, sh, door_faces_x=door_x) for sh in shafts
            ):
                out.add(id(s.entity))
                continue
            if any(
                _is_elevator_cab_side_seg(s, sh, door_faces_x=door_x) for sh in shafts
            ):
                out.add(id(s.entity))
    return out


def filter_promote_away_from_elevator_doors(
    promote: list[AxisSeg],
    msp,
) -> list[AxisSeg]:
    """문 개구·후면·카 내부·중앙 promote 차단. 입구 잼·주위는 통과."""
    banks = find_elevator_banks(msp)
    if not banks:
        return promote
    boxes: list[dict[str, float]] = []
    checks: list[tuple[dict, list]] = []
    for bank in banks:
        shafts: list[dict[str, float]] = bank.get("shafts") or []  # type: ignore[assignment]
        if not shafts:
            continue
        boxes.append(_elevator_bank_box(bank, shafts, None))
        checks.append((bank, shafts))

    kept: list[AxisSeg] = []
    for s in promote:
        keep = False
        for bank, shafts in checks:
            if any(_is_elevator_door_jamb_seg(s, sh, bank) for sh in shafts):
                keep = True
                break
            if any(
                _is_elevator_door_interstitial_seg(s, sh, bank, shafts) for sh in shafts
            ):
                keep = True
                break
        if keep:
            kept.append(s)
            continue
        if any(_is_elevator_perimeter_seg(s, box) for box in boxes):
            drop_door = False
            for bank, shafts in checks:
                if any(_is_elevator_door_opening_seg(s, sh, bank) for sh in shafts):
                    drop_door = True
                    break
            if not drop_door:
                kept.append(s)
            continue
        drop = False
        for bank, shafts in checks:
            door_x = bool(bank.get("door_faces_x", 1))
            if _is_elevator_center_spine_seg(s, bank, shafts):
                drop = True
                break
            if any(_is_elevator_door_opening_seg(s, sh, bank) for sh in shafts):
                drop = True
                break
            if any(_is_elevator_back_structure_seg(s, sh, bank) for sh in shafts):
                drop = True
                break
            if any(
                _is_elevator_door_or_back_seg(s, sh, door_faces_x=door_x) for sh in shafts
            ):
                drop = True
                break
            if any(
                _is_elevator_cab_side_seg(s, sh, door_faces_x=door_x) for sh in shafts
            ):
                drop = True
                break
        if not drop:
            kept.append(s)
    return kept


def protect_elevator_enclosure_entities(
    msp,
    segs: list[AxisSeg],
    *,
    min_len_mm: float = 300.0,
) -> set[int]:
    """입구 잼·문사이 칸막이·뱅크 주위 외곽 WALL demote 금지."""
    banks = find_elevator_banks(msp)
    if not banks:
        return set()
    protect: set[int] = set()
    wall = [s for s in segs if s.layer == WALL_LAYER and s.length >= min_len_mm]
    for bank in banks:
        shafts: list[dict[str, float]] = bank.get("shafts") or []  # type: ignore[assignment]
        if not shafts:
            continue
        box = _elevator_bank_box(bank, shafts, segs)
        for s in wall:
            if any(_is_elevator_door_jamb_seg(s, sh, bank) for sh in shafts):
                protect.add(id(s.entity))
                continue
            if any(
                _is_elevator_door_interstitial_seg(s, sh, bank, shafts) for sh in shafts
            ):
                protect.add(id(s.entity))
                continue
            if _is_elevator_perimeter_seg(s, box):
                if not any(_is_elevator_door_opening_seg(s, sh, bank) for sh in shafts):
                    protect.add(id(s.entity))
    return protect


def promote_collinear_room_walls(
    segs: list[AxisSeg],
    *,
    min_len_mm: float = 2500.0,
    max_len_mm: float = 12000.0,
    abut_mm: float = 600.0,
) -> list[AxisSeg]:
    """연속 방 열: 이웃만 WALL인 동일 축 BASE 칸막이/외곽선을 승격.

    find_promote_segments 는 '양쪽 WALL 사이 갭'만 메운다.
    방 한 칸만 빠진 끝단·한쪽 이웃만 있는 경우도 여기서 잡는다.
    """
    wall = [s for s in segs if s.layer == WALL_LAYER]
    base = [s for s in segs if s.layer == BASE_LAYER]
    h_wall, v_wall = _index_wall_runs(wall)
    out: list[AxisSeg] = []
    seen: set[tuple[float, float, float, float]] = set()

    for s in base:
        if not (min_len_mm <= s.length <= max_len_mm):
            continue
        if not _has_parallel_pair(s, base):
            continue
        if s.is_h:
            iv: list[tuple[float, float, float]] = []
            for d in (-2, -1, 0, 1, 2):
                iv.extend(h_wall.get(_bucket(s.ortho) + d * 50, []))
        else:
            iv = []
            for d in (-2, -1, 0, 1, 2):
                iv.extend(v_wall.get(_bucket(s.ortho) + d * 50, []))
        if _covered(iv, s.along0, s.along1, frac=0.55):
            continue
        # 좌·우(또는 상·하)에 비슷한 길이의 WALL이 맞닿아 있으면 연속 방 열
        abut_left = any(
            abs(b1 - s.along0) <= abut_mm and L2 >= min_len_mm * 0.6 for b0, b1, L2 in iv
        )
        abut_right = any(
            abs(b0 - s.along1) <= abut_mm and L2 >= min_len_mm * 0.6 for b0, b1, L2 in iv
        )
        if not (abut_left or abut_right):
            continue
        key = (
            round(s.x0, 1),
            round(s.y0, 1),
            round(s.x1, 1),
            round(s.y1, 1),
        )
        if key in seen:
            continue
        seen.add(key)
        out.append(s)
    return out


def apply_corrections(
    doc: Drawing,
    *,
    review: dict[str, Any] | None = None,
    do_gap_promote: bool = True,
    do_short_demote: bool = False,
    do_pack_demote: bool = True,
    do_dense_demote: bool = True,
    do_box_demote: bool = True,
    do_corridor_promote: bool = True,
    do_open_hall_demote: bool = True,
    do_stair_promote: bool = True,
    do_elevator_promote: bool = True,
    do_column_promote: bool = True,
) -> dict[str, Any]:
    """In-place modify doc modelspace WALL layer. Returns stats."""
    msp = doc.modelspace()
    review = review or {}
    segs = iter_axis_segs(msp)

    hbeam_ids: set[int] = set()
    if do_column_promote:
        hbeam_ids = find_hbeam_column_entities(msp)

    # promote 후보를 demote 전에 확정 (넓은 demote_bbox가 이웃 WALL을
    # 지우면 갭/연속방 승격 맥락이 사라져 한 칸만 회색으로 남는 문제 방지)
    promote: list[AxisSeg] = []
    if do_gap_promote:
        promote.extend(find_promote_segments(segs))
    if do_corridor_promote:
        promote.extend(promote_corridor_walls(segs))
    promote.extend(promote_collinear_room_walls(segs))
    n_stair_promote = 0
    if do_stair_promote:
        stair_prom = promote_stair_enclosure_walls(msp, segs)
        n_stair_promote = len(stair_prom)
        promote.extend(stair_prom)
    n_elev_promote = 0
    if do_elevator_promote:
        elev_prom = promote_elevator_enclosure_walls(msp, segs)
        n_elev_promote = len(elev_prom)
        promote.extend(elev_prom)
    promote.extend(
        promote_in_bboxes(segs, review.get("promote_bboxes") or [])
    )
    promote.extend(
        promote_room_row_dividers(segs, review.get("promote_bboxes") or [])
    )
    # 강당 중앙 통로·보이드를 WALL로 승격하지 않음
    if do_open_hall_demote:
        promote = filter_promote_away_from_open_halls(promote, msp, segs)
    # 엘리베이터 문·후면은 승격 금지 (측벽만 벽)
    if do_elevator_promote:
        promote = filter_promote_away_from_elevator_doors(promote, msp)

    open_hall_demote: set[int] = set()
    if do_open_hall_demote:
        open_hall_demote = demote_open_hall_center_walls(msp, segs)

    elev_door_demote: set[int] = set()
    if do_elevator_promote:
        elev_door_demote = demote_elevator_door_back_faces(msp, segs)

    protect_ids = protect_corridor_wall_entities(
        segs, exclude_ids=open_hall_demote
    )
    if do_stair_promote:
        protect_ids |= protect_stair_enclosure_entities(msp, segs)
    if do_elevator_promote:
        protect_ids |= protect_elevator_enclosure_entities(msp, segs)
    protect_ids |= hbeam_ids

    demote_ids: set[int] = set()
    if do_short_demote:
        demote_ids |= find_demote_wall_entities(segs)
    if do_pack_demote:
        demote_ids |= demote_parallel_packs(segs)
    if do_dense_demote:
        demote_ids |= demote_dense_short_clusters(segs)
    if do_box_demote:
        demote_ids |= demote_closed_furniture_boxes(msp, exclude_ids=hbeam_ids)
    demote_ids |= demote_in_bboxes(msp, review.get("demote_bboxes") or [])
    n_protected = len(demote_ids & protect_ids)
    demote_ids -= protect_ids
    # 오픈홀 중앙·엘리베이터 문/후면은 protect보다 우선 demote
    demote_ids |= open_hall_demote
    demote_ids |= elev_door_demote

    n_demoted = 0
    for e in list(msp):
        if e.dxf.layer == WALL_LAYER and id(e) in demote_ids:
            msp.delete_entity(e)
            n_demoted += 1

    seen: set[tuple[float, float, float, float]] = set()
    n_promoted = 0
    for s in promote:
        key = (round(s.x0, 1), round(s.y0, 1), round(s.x1, 1), round(s.y1, 1))
        if key in seen:
            continue
        seen.add(key)
        e = s.entity
        # BASE LINE 은 레이어를 바꿔 회색이 남지 않게 한다 (겹친 WALL 추가 금지).
        # LWPOLYLINE 일부만 승격할 때는 새 LINE 을 추가한다.
        if (
            e is not None
            and getattr(e, "dxftype", lambda: "")() == "LINE"
            and getattr(e.dxf, "layer", None) == BASE_LAYER
        ):
            e.dxf.layer = WALL_LAYER
            try:
                e.dxf.color = WALL_COLOR
            except Exception:  # noqa: BLE001
                pass
        else:
            msp.add_line(
                (s.x0, s.y0),
                (s.x1, s.y1),
                dxfattribs={"layer": WALL_LAYER, "color": WALL_COLOR},
            )
        n_promoted += 1

    # promote 로 들어온 홀 중앙선도 제거 (1차 demote 이후 추가분)
    n_open_hall_post = 0
    if do_open_hall_demote:
        segs_after = iter_axis_segs(msp)
        post_demote = demote_open_hall_center_walls(msp, segs_after)
        for e in list(msp):
            if e.dxf.layer == WALL_LAYER and id(e) in post_demote:
                msp.delete_entity(e)
                n_demoted += 1
                n_open_hall_post += 1

    # promote 로 다시 올라온 엘리베이터 문/후면 제거
    n_elev_door_post = 0
    if do_elevator_promote:
        segs_after = iter_axis_segs(msp)
        post_elev = demote_elevator_door_back_faces(msp, segs_after)
        for e in list(msp):
            if e.dxf.layer == WALL_LAYER and id(e) in post_elev:
                msp.delete_entity(e)
                n_demoted += 1
                n_elev_door_post += 1

    # H-Beam 기둥: demote 이후 BASE→WALL (가구 demote에 안 걸림)
    n_column_promoted = 0
    if do_column_promote:
        n_column_promoted = promote_hbeam_columns(msp)

    n_wall = sum(1 for e in msp if e.dxf.layer == WALL_LAYER)
    n_base = sum(1 for e in msp if e.dxf.layer == BASE_LAYER)
    return {
        "n_demoted": n_demoted,
        "n_promoted": n_promoted,
        "n_corridor_protected": n_protected,
        "n_open_hall_demoted": len(open_hall_demote) + n_open_hall_post,
        "n_stair_promote_candidates": n_stair_promote,
        "n_elevator_promote_candidates": n_elev_promote,
        "n_elevator_door_demoted": len(elev_door_demote) + n_elev_door_post,
        "n_column_promoted": n_column_promoted,
        "n_wall_after": n_wall,
        "n_base": n_base,
        "review_demote_bboxes": len(review.get("demote_bboxes") or []),
        "review_promote_bboxes": len(review.get("promote_bboxes") or []),
    }


def render_wall_dxf_png(
    dxf_path: Path,
    png_path: Path,
    *,
    bbox_mm: dict[str, float] | None = None,
    title: str | None = None,
    dpi: int = 200,
    px_width: int = 14200,
) -> tuple[int, int]:
    """Render BASE(gray)+WALL(red)+TEXT(blue) DXF to PNG (floor_original 라벨과 동일)."""
    import re as _re

    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.collections import LineCollection

    doc = ezdxf.readfile(str(dxf_path))
    msp = doc.modelspace()
    base_segs: list[list[tuple[float, float]]] = []
    wall_segs: list[list[tuple[float, float]]] = []
    texts: list[tuple[float, float, str, float, float]] = []
    xs: list[float] = []
    ys: list[float] = []

    for e in msp:
        layer = e.dxf.layer
        t = e.dxftype()
        try:
            if t == "LINE":
                pts = [
                    (float(e.dxf.start.x), float(e.dxf.start.y)),
                    (float(e.dxf.end.x), float(e.dxf.end.y)),
                ]
            elif t == "LWPOLYLINE":
                pts = [(float(p[0]), float(p[1])) for p in e.get_points("xy")]
                if e.closed and pts and pts[0] != pts[-1]:
                    pts = pts + [pts[0]]
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
                continue
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
                continue
            else:
                continue
        except Exception:  # noqa: BLE001
            continue
        if len(pts) < 2:
            continue
        for p in pts:
            xs.append(p[0])
            ys.append(p[1])
        if layer == WALL_LAYER:
            wall_segs.append(pts)
        elif layer == BASE_LAYER:
            base_segs.append(pts)

    if bbox_mm:
        cx0 = float(bbox_mm["xmin"])
        cy0 = float(bbox_mm["ymin"])
        cx1 = float(bbox_mm["xmax"])
        cy1 = float(bbox_mm["ymax"])
        span_y0 = max(cy1 - cy0, 1.0)
        tick = max(span_y0 * 0.025, 1000.0)
        xmin = cx0 - max(tick * 0.8, 1500)
        ymin = cy0 - max(tick * 2.5, 4000)
        xmax = cx1 + max(tick * 2.0, 3000)
        ymax = cy1 + max(tick * 6.5, 9000)
        content_bbox = (cx0, cy0, cx1, cy1, tick)
        tight = False
        pad = 0.0
    else:
        if not xs:
            xs, ys = [0.0, 1.0], [0.0, 1.0]
        xmin, xmax = min(xs), max(xs)
        ymin, ymax = min(ys), max(ys)
        pad = max(xmax - xmin, ymax - ymin) * 0.02
        content_bbox = None
        tight = True

    span_x = max(xmax - xmin, 1.0)
    span_y = max(ymax - ymin, 1.0)
    fig_w = px_width / dpi
    fig_h = fig_w * (span_y / span_x)
    fig, ax = plt.subplots(figsize=(fig_w, fig_h), dpi=dpi)
    if base_segs:
        ax.add_collection(
            LineCollection(base_segs, colors="#555555", linewidths=0.35, antialiased=True)
        )
    if wall_segs:
        ax.add_collection(
            LineCollection(wall_segs, colors="#e74c3c", linewidths=1.1, antialiased=True)
        )

    # floor_original / walldetector 와 동일: 실명·면적 라벨 (#1a5fb4)
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
                fontname=font_name if font_name else None,
                clip_on=True,
                zorder=5,
            )

    ax.set_xlim(xmin - pad, xmax + pad)
    ax.set_ylim(ymin - pad, ymax + pad)
    ax.set_aspect("equal", adjustable="box")
    ax.axis("off")
    ax.set_facecolor("white")
    fig.patch.set_facecolor("white")

    if title and content_bbox is not None:
        cx0, cy0, cx1, cy1, tick = content_bbox
        ax.text(
            (cx0 + cx1) / 2,
            cy1 + tick * 1.6 + tick * 2.0,
            title,
            ha="center",
            va="bottom",
            color="#c0392b",
            fontsize=40,
            fontweight="bold",
            clip_on=False,
        )

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


def load_review(path: Path | None) -> dict[str, Any]:
    if not path or not path.is_file():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        return {}
    data["demote_bboxes"] = normalize_bbox_list(
        data.get("demote_bboxes") or [], field="demote_bboxes"
    )
    data["promote_bboxes"] = normalize_bbox_list(
        data.get("promote_bboxes") or [], field="promote_bboxes"
    )
    return data
