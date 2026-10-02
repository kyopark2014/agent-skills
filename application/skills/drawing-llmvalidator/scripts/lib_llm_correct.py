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
    """닫힌 소·중형 폴리라인(책상·캐비닛·랙 윤곽) 및 일치 WALL LINE demote.

    H-Beam 기둥(중첩 사각)은 exclude_ids 로 제외한다.
    BASE 닫힌 가구 윤곽과 겹치는 WALL LINE(소파 장변 잔여)도 제거.
    """
    exclude_ids = exclude_ids or set()
    result: set[int] = set()
    furniture_edges: list[tuple[bool, float, float, float]] = []
    # (is_h, ortho, along0, along1)
    for e in msp:
        if e.dxftype() != "LWPOLYLINE":
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
        if min(w, h) < 150.0:
            continue
        if max(w, h) > max_span_mm:
            continue
        if e.dxf.layer == WALL_LAYER and min(w, h) >= min_span_mm:
            result.add(id(e))
        # 가늘고 긴 가구(소파) 윤곽 → 장변 좌표 기록
        if max(w, h) < 1200.0 or max(w, h) / max(min(w, h), 1.0) < 1.6:
            continue
        x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
        if w >= h:
            furniture_edges.append((True, y0, x0, x1))
            furniture_edges.append((True, y1, x0, x1))
        else:
            furniture_edges.append((False, x0, y0, y1))
            furniture_edges.append((False, x1, y0, y1))

    for e in msp:
        if e.dxf.layer != WALL_LAYER or e.dxftype() != "LINE":
            continue
        if id(e) in exclude_ids:
            continue
        try:
            x0, y0 = float(e.dxf.start.x), float(e.dxf.start.y)
            x1, y1 = float(e.dxf.end.x), float(e.dxf.end.y)
        except Exception:  # noqa: BLE001
            continue
        length = math.hypot(x1 - x0, y1 - y0)
        if length < 1200.0:
            continue
        dx, dy = abs(x1 - x0), abs(y1 - y0)
        is_h = dy <= max(20.0, 0.15 * length)
        is_v = dx <= max(20.0, 0.15 * length)
        if not (is_h or is_v):
            continue
        if is_h:
            ortho = (y0 + y1) * 0.5
            a0, a1 = min(x0, x1), max(x0, x1)
        else:
            ortho = (x0 + x1) * 0.5
            a0, a1 = min(y0, y1), max(y0, y1)
        for eh, eo, ea0, ea1 in furniture_edges:
            if eh != is_h:
                continue
            if abs(ortho - eo) > 120.0:
                continue
            ov = min(a1, ea1) - max(a0, ea0)
            if ov >= length * 0.7:
                result.add(id(e))
                break
    return result

def demote_line_furniture_boxes(
    segs: list[AxisSeg],
    *,
    exclude_ids: set[int] | None = None,
    long_min_mm: float = 1200.0,
    long_max_mm: float = 3800.0,
    short_min_mm: float = 450.0,
    short_max_mm: float = 1400.0,
    aspect_min: float = 1.6,
    along_tol_mm: float = 150.0,
    corner_tol_mm: float = 180.0,
) -> set[int]:
    """LINE으로 이뤄진 닫힌 직사각 가구(소파·테이블) WALL demote.

    walldetector가 소파 이중선(≈3 m × 0.5–1.0 m)을 WALL로 올린 경우,
    protect_corridor(≥2.5 m + 평행쌍)에 걸려 기존 demote가 막힌다.
    장변·단변이 가구 크기이고 네 모서리가 닫히면 구조 벽이 아니다.
    단변 간격 < 450 mm 은 벽두께 이중선이므로 제외.
    """
    exclude_ids = exclude_ids or set()
    demote: set[int] = set()

    def _axis_furniture(
        long_segs: list[AxisSeg],
        short_segs: list[AxisSeg],
    ) -> None:
        groups: dict[tuple[int, int], list[AxisSeg]] = defaultdict(list)
        for s in long_segs:
            if not (long_min_mm <= s.length <= long_max_mm):
                continue
            key = (
                int(round(s.along0 / 50.0) * 50),
                int(round(s.along1 / 50.0) * 50),
            )
            groups[key].append(s)
        for group in groups.values():
            group = sorted(group, key=lambda s: s.ortho)
            n = len(group)
            for i in range(n):
                a = group[i]
                for j in range(i + 1, n):
                    b = group[j]
                    gap = b.ortho - a.ortho
                    if gap < short_min_mm:
                        continue
                    if gap > short_max_mm:
                        break
                    if a.length / max(gap, 1.0) < aspect_min:
                        continue
                    # 양끝 단변이 장변 간격을 잇는지 (WALL/BASE 모두 허용)
                    lo = (a.along0 + b.along0) * 0.5
                    hi = (a.along1 + b.along1) * 0.5

                    def _has_end(x: float) -> bool:
                        for v in short_segs:
                            if abs(v.ortho - x) > corner_tol_mm:
                                continue
                            if not (short_min_mm * 0.5 <= v.length <= short_max_mm * 1.5):
                                continue
                            ov = min(v.along1, b.ortho + 40.0) - max(
                                v.along0, a.ortho - 40.0
                            )
                            if ov >= gap * 0.55:
                                return True
                        return False

                    if not (_has_end(lo) and _has_end(hi)):
                        continue
                    # 장변·내부 평행선: 가구 윤곽 엔티티로 표시 (BASE 승격 차단용)
                    # demote 적용 시 WALL만 삭제. LWPOLYLINE 통째 피해 방지 → LINE만
                    for s in group:
                        if a.ortho - 40.0 <= s.ortho <= b.ortho + 40.0:
                            if abs(s.along0 - lo) > along_tol_mm + 200.0:
                                continue
                            if abs(s.along1 - hi) > along_tol_mm + 200.0:
                                continue
                            if id(s.entity) in exclude_ids:
                                continue
                            ent = s.entity
                            if ent is not None and getattr(ent, "dxftype", lambda: "")() != "LINE":
                                continue
                            demote.add(id(ent))
                    for v in short_segs:
                        if id(v.entity) in exclude_ids:
                            continue
                        # 가구 깊이 정도의 짧은 단변만 — 복도·실 장축 V 보호
                        if v.length > short_max_mm * 1.25:
                            continue
                        ent = v.entity
                        if ent is not None and getattr(ent, "dxftype", lambda: "")() != "LINE":
                            continue
                        if (
                            abs(v.ortho - lo) > corner_tol_mm
                            and abs(v.ortho - hi) > corner_tol_mm
                        ):
                            continue
                        ov = min(v.along1, b.ortho + 80.0) - max(
                            v.along0, a.ortho - 80.0
                        )
                        if ov >= min(gap, v.length) * 0.35:
                            demote.add(id(ent))

    h_long = [s for s in segs if s.is_h]
    v_short = [s for s in segs if s.is_v]
    _axis_furniture(h_long, v_short)
    # 세로로 긴 소파/벤치
    v_long = [s for s in segs if s.is_v]
    h_short = [s for s in segs if s.is_h]
    _axis_furniture(v_long, h_short)
    return demote


_MEETING_RE = re.compile(r"^접견실")


def demote_meeting_room_interiors(
    msp,
    segs: list[AxisSeg],
    *,
    exclude_ids: set[int] | None = None,
    inset_mm: float = 250.0,
) -> set[int]:
    """접견실 내부의 의자·프로젝터 받침대는 벽이 아니다.

    실명 라벨을 감싼 외곽 안쪽에 선 전체가 들어간 것만 제거한다.
    외벽과 H-Beam 기둥은 남긴다.
    """
    exclude_ids = exclude_ids or set()
    labels = [
        (x, y)
        for x, y, s in _iter_text_labels(msp)
        if _MEETING_RE.search(s.strip())
    ]
    if not labels:
        return set()
    # 좌우는 라벨을 지나는 장축. 상하는 그 폭을 거의 채우는 수평선(문 틈은 이어 붙임).
    # 스크린·의자처럼 가운데만 있는 선은 외곽이 아니다.
    vlong = [s for s in segs if s.is_v and s.layer == WALL_LAYER and s.length >= 2500.0]
    hwall = [s for s in segs if s.is_h and s.layer == WALL_LAYER and s.length >= 200.0]

    def _spans_room(cands: list[AxisSeg], a0: float, a1: float) -> bool:
        # 문 폭(약 1.1m)은 이어 붙이고, 의자 사이 빈 구간(2m 이상)은 끊는다.
        intervals = sorted((s.along0, s.along1) for s in cands)
        if not intervals:
            return False
        merged: list[list[float]] = []
        for a, b in intervals:
            if not merged or a > merged[-1][1] + 1600.0:
                merged.append([a, b])
            else:
                merged[-1][1] = max(merged[-1][1], b)
        return any(a <= a0 + 800.0 and b >= a1 - 800.0 for a, b in merged)

    boxes: list[tuple[float, float, float, float]] = []
    for lx, ly in labels:
        lefts = [
            s.ortho
            for s in vlong
            if lx - 12000.0 < s.ortho < lx - 400.0
            and s.along0 - 800.0 <= ly <= s.along1 + 800.0
        ]
        rights = [
            s.ortho
            for s in vlong
            if lx + 400.0 < s.ortho < lx + 12000.0
            and s.along0 - 800.0 <= ly <= s.along1 + 800.0
        ]
        if not (lefts and rights):
            continue
        x0, x1 = max(lefts), min(rights)
        if not (3500.0 < x1 - x0 < 20000.0):
            continue
        bands: dict[int, list[AxisSeg]] = {}
        for s in hwall:
            if s.along1 < x0 - 200.0 or s.along0 > x1 + 200.0:
                continue
            bands.setdefault(int(round(s.ortho / 80.0)), []).append(s)
        below: list[float] = []
        above: list[float] = []
        for key, cands in bands.items():
            ortho = key * 80.0
            if not _spans_room(cands, x0, x1):
                continue
            if ly - 12000.0 < ortho < ly - 400.0:
                below.append(ortho)
            elif ly + 400.0 < ortho < ly + 12000.0:
                above.append(ortho)
        if not (below and above):
            continue
        y0, y1 = max(below), min(above)
        if not (3500.0 < y1 - y0 < 20000.0):
            continue
        boxes.append((x0, x1, y0, y1))
    if not boxes:
        return set()
    demote: set[int] = set()
    for s in segs:
        if s.layer != WALL_LAYER or id(s.entity) in exclude_ids:
            continue
        for x0, x1, y0, y1 in boxes:
            if not (
                x0 + inset_mm < min(s.x0, s.x1)
                and max(s.x0, s.x1) < x1 - inset_mm
                and y0 + inset_mm < min(s.y0, s.y1)
                and max(s.y0, s.y1) < y1 - inset_mm
            ):
                continue
            if s.length > min(x1 - x0, y1 - y0) * 0.85:
                continue
            demote.add(id(s.entity))
            break
    return demote


_SERVING_RE = re.compile(r"배식대")
_OTHER_ROOM_RE = re.compile(r"(실|창고|홀|조리|주방|식당|코어|계단)")


def demote_kitchen_equipment(
    msp,
    segs: list[AxisSeg],
    *,
    exclude_ids: set[int] | None = None,
) -> set[int]:
    """조리실 조리기구는 벽이 아니다. 옆의 H-Beam 기둥만 남긴다."""
    exclude_ids = exclude_ids or set()
    kitchens = [
        (x, y)
        for x, y, s in _iter_text_labels(msp)
        if "조리실" in s and "사무" not in s
    ]
    if not kitchens:
        return set()
    boxes: dict[int, list[float]] = {}
    for s in segs:
        if id(s.entity) not in exclude_ids or not (900.0 <= s.length <= 1600.0):
            continue
        b = boxes.setdefault(id(s.entity), [1e18, 1e18, -1e18, -1e18])
        b[0] = min(b[0], s.x0, s.x1)
        b[1] = min(b[1], s.y0, s.y1)
        b[2] = max(b[2], s.x0, s.x1)
        b[3] = max(b[3], s.y0, s.y1)
    columns = [
        b for b in boxes.values()
        if 800.0 <= b[2] - b[0] <= 1800.0 and 800.0 <= b[3] - b[1] <= 1800.0
        and any(abs((b[0] + b[2]) * 0.5 - lx) < 15000.0 and abs((b[1] + b[3]) * 0.5 - ly) < 15000.0 for lx, ly in kitchens)
    ]
    if not columns:
        return set()

    def _near_column(mx: float, my: float) -> bool:
        for x0, y0, x1, y1 in columns:
            dx = 0.0 if x0 <= mx <= x1 else min(abs(mx - x0), abs(mx - x1))
            dy = 0.0 if y0 <= my <= y1 else min(abs(my - y0), abs(my - y1))
            if dx * dx + dy * dy <= 2300.0 * 2300.0:
                return True
        return False

    shorts = []
    for s in segs:
        if s.layer != WALL_LAYER or s.entity is None or id(s.entity) in exclude_ids:
            continue
        if not (300.0 <= s.length <= 1000.0):
            continue
        mx, my = (s.x0 + s.x1) * 0.5, (s.y0 + s.y1) * 0.5
        if not any(abs(mx - lx) < 8000.0 and abs(my - ly) < 8000.0 for lx, ly in kitchens):
            continue
        if _near_column(mx, my):
            shorts.append(s)
    demote: set[int] = set()
    for s in shorts:
        mx, my = (s.x0 + s.x1) * 0.5, (s.y0 + s.y1) * 0.5
        neighbors = 0
        for o in shorts:
            if abs((o.x0 + o.x1) * 0.5 - mx) > 1400.0 or abs((o.y0 + o.y1) * 0.5 - my) > 1400.0:
                continue
            neighbors += 1
        if neighbors >= 4:
            demote.add(id(s.entity))
    return demote


def demote_conveyor_belt(
    msp,
    segs: list[AxisSeg],
    *,
    exclude_ids: set[int] | None = None,
) -> set[int]:
    """세척실 컨베이어벨트. 롤러가 늘어선 얇은 평행선 다발은 벽이 아니다."""
    exclude_ids = exclude_ids or set()
    ticks: list[tuple[bool, float, float, float]] = []
    for e in msp:
        if e.dxftype() != "LWPOLYLINE":
            continue
        pts = [(float(p[0]), float(p[1])) for p in e.get_points("xy")]
        if len(pts) < 8:
            continue
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        w, h = max(xs) - min(xs), max(ys) - min(ys)
        if min(w, h) > 250.0 or not (150.0 <= max(w, h) <= 500.0):
            continue
        vertical = h > w
        ticks.append((vertical, (min(xs) + max(xs)) * 0.5, min(ys), max(ys)))
    if len(ticks) < 8:
        return set()
    wash = [(x, y) for x, y, s in _iter_text_labels(msp) if "세척" in s]
    if not wash:
        return set()

    demote: set[int] = set()
    for vertical in (True, False):
        group = [t for t in ticks if t[0] == vertical]
        bands: dict[int, list[tuple[bool, float, float, float]]] = {}
        for t in group:
            bands.setdefault(round(t[2] / 200.0) * 200, []).append(t)
        for group in bands.values():
            group.sort(key=lambda t: t[1])
            run: list[tuple[bool, float, float, float]] = []

            def _flush(items: list[tuple[bool, float, float, float]]) -> None:
                if len(items) < 8:
                    return
                y0 = min(t[2] for t in items)
                y1 = max(t[3] for t in items)
                x0, x1 = items[0][1], items[-1][1]
                cy = (y0 + y1) * 0.5
                if vertical:
                    near = any(x0 - 3000.0 <= lx <= x1 + 3000.0 and abs(ly - cy) < 12000.0 for lx, ly in wash)
                else:
                    near = any(y0 - 3000.0 <= ly <= y1 + 3000.0 and abs(lx - cy) < 12000.0 for lx, ly in wash)
                if not near:
                    return
                for s in segs:
                    if s.layer != WALL_LAYER or s.entity is None or id(s.entity) in exclude_ids:
                        continue
                    if s.is_v == vertical or s.length < 2000.0:
                        continue
                    if not (y0 - 30.0 <= s.ortho <= y1 + 30.0):
                        continue
                    if min(s.along1, x1 + 800.0) - max(s.along0, x0 - 800.0) < 1500.0:
                        continue
                    demote.add(id(s.entity))

            for t in group:
                if run:
                    gap = t[1] - run[-1][1]
                    if gap < 400.0:
                        continue
                    same = min(t[3], run[-1][3]) - max(t[2], run[-1][2]) >= (t[3] - t[2]) * 0.6
                    # 기둥 간격(약 5 m)은 같은 벨트다.
                    if gap > 5600.0 or not same:
                        _flush(run)
                        run = []
                run.append(t)
            _flush(run)
    return demote


def demote_coffee_machine_row(
    msp,
    segs: list[AxisSeg],
    *,
    exclude_ids: set[int] | None = None,
) -> set[int]:
    """구성원 식당 아래 커피머신 줄. 머신 위·아래 선은 벽이 아니고 H-Beam 만 남긴다."""
    exclude_ids = exclude_ids or set()
    boxes: list[tuple[float, float, float, float]] = []
    for e in msp:
        if e.dxftype() != "LWPOLYLINE" or e.dxf.layer != BASE_LAYER:
            continue
        pts = [(float(p[0]), float(p[1])) for p in e.get_points("xy")]
        if len(pts) < 4:
            continue
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        w, h = max(xs) - min(xs), max(ys) - min(ys)
        if not (1200.0 <= w <= 1400.0 and 750.0 <= h <= 900.0):
            continue
        boxes.append((min(xs), min(ys), max(xs), max(ys)))
    if len(boxes) < 3:
        return set()
    halls = [(x, y) for x, y, s in _iter_text_labels(msp) if "구성원 식당" in s]
    if not halls:
        return set()
    boxes.sort()
    demote: set[int] = set()
    run: list[tuple[float, float, float, float]] = []

    def _flush(items: list[tuple[float, float, float, float]]) -> None:
        if len(items) < 3:
            return
        x0, y0 = items[0][0], min(b[1] for b in items)
        x1, y1 = items[-1][2], max(b[3] for b in items)
        cx = (x0 + x1) * 0.5
        if not any(abs(cx - lx) < 22000.0 and 4000.0 < ly - y1 < 14000.0 for lx, ly in halls):
            return
        for s in segs:
            if s.layer != WALL_LAYER or s.is_v or s.entity is None or id(s.entity) in exclude_ids:
                continue
            if s.length < 2000.0:
                continue
            if not (y0 - 200.0 <= s.ortho <= y1 + 200.0):
                continue
            if min(s.along1, x1) - max(s.along0, x0) < 2000.0:
                continue
            demote.add(id(s.entity))

    for b in boxes:
        if run:
            gap = b[0] - run[-1][2]
            same = min(b[3], run[-1][3]) - max(b[1], run[-1][1]) > 400.0
            if gap > 500.0 or gap < -200.0 or not same:
                _flush(run)
                run = []
        run.append(b)
    _flush(run)
    return demote


def demote_dining_hall_furniture(
    msp,
    segs: list[AxisSeg],
    *,
    exclude_ids: set[int] | None = None,
) -> set[int]:
    """구성원 식당#2 안은 벽이 없다. 식탁·배식대만 지우고 H-Beam 은 남긴다."""
    exclude_ids = exclude_ids or set()
    halls = [(x, y) for x, y, s in _iter_text_labels(msp) if "구성원 식당#2" in s]
    if not halls:
        return set()
    long_walls = [s for s in segs if s.layer == WALL_LAYER and s.length >= 8000.0]

    def _on_enclosure(s: AxisSeg) -> bool:
        for w in long_walls:
            if w.is_v != s.is_v or abs(w.ortho - s.ortho) > 80.0:
                continue
            if min(w.along1, s.along1) - max(w.along0, s.along0) > 0.0:
                return True
        return False

    demote: set[int] = set()
    for lx, ly in halls:
        for s in segs:
            if s.layer != WALL_LAYER or s.entity is None or id(s.entity) in exclude_ids:
                continue
            if not (250.0 <= s.length <= 5500.0):
                continue
            mx, my = (s.x0 + s.x1) * 0.5, (s.y0 + s.y1) * 0.5
            if abs(mx - lx) > 20000.0:
                continue
            if not (-12000.0 <= my - ly <= 4500.0):
                continue
            if _on_enclosure(s):
                continue
            demote.add(id(s.entity))
    return demote


def demote_cafeteria_counter_lines(
    msp,
    segs: list[AxisSeg],
    *,
    exclude_ids: set[int] | None = None,
) -> set[int]:
    """식당 안 배식대의 평행 이중선은 벽이 아니다. 끝의 H-Beam 기둥은 남긴다."""
    exclude_ids = exclude_ids or set()
    cafes = [
        (x, y)
        for x, y, s in _iter_text_labels(msp)
        if "식당" in s and "창고" not in s and "배식" not in s
    ]
    if not cafes:
        return set()
    faces = [
        s for s in segs
        if id(s.entity) in exclude_ids and 900.0 <= s.length <= 1600.0
    ]
    cands = [
        s for s in segs
        if s.layer == WALL_LAYER and s.entity is not None
        and id(s.entity) not in exclude_ids
        and 2000.0 <= s.length <= 5000.0
    ]

    def _near(s: AxisSeg) -> bool:
        mx = (s.along0 + s.along1) * 0.5 if not s.is_v else s.ortho
        my = s.ortho if not s.is_v else (s.along0 + s.along1) * 0.5
        return any(abs(mx - lx) < 16000.0 and abs(my - ly) < 8000.0 for lx, ly in cafes)

    def _butts(s: AxisSeg) -> bool:
        for f in faces:
            if f.is_v == s.is_v:
                continue
            if not (f.along0 - 80.0 <= s.ortho <= f.along1 + 80.0):
                continue
            if min(abs(s.along0 - f.ortho), abs(s.along1 - f.ortho)) <= 120.0:
                return True
        return False

    demote: set[int] = set()
    pool = [s for s in cands if _near(s)]
    for i, a in enumerate(pool):
        for b in pool[i + 1:]:
            if a.is_v != b.is_v:
                continue
            thick = abs(a.ortho - b.ortho)
            if not (180.0 <= thick <= 400.0):
                continue
            ov = min(a.along1, b.along1) - max(a.along0, b.along0)
            if ov < 2000.0 or ov < min(a.length, b.length) * 0.8:
                continue
            if not (_butts(a) and _butts(b)):
                continue
            demote.add(id(a.entity))
            demote.add(id(b.entity))
    return demote


def demote_serving_counter_walls(
    msp,
    segs: list[AxisSeg],
    *,
    exclude_ids: set[int] | None = None,
) -> set[int]:
    """배식대 안에는 벽이 없다. H-Beam 기둥만 남긴다."""
    exclude_ids = exclude_ids or set()
    cafe = demote_cafeteria_counter_lines(msp, segs, exclude_ids=exclude_ids)
    cafe |= demote_conveyor_belt(msp, segs, exclude_ids=exclude_ids)
    cafe |= demote_kitchen_equipment(msp, segs, exclude_ids=exclude_ids)
    cafe |= demote_dining_hall_furniture(msp, segs, exclude_ids=exclude_ids)
    cafe |= demote_coffee_machine_row(msp, segs, exclude_ids=exclude_ids)
    labels = [(x, y) for x, y, s in _iter_text_labels(msp) if _SERVING_RE.search(s)]
    if not labels:
        return cafe
    others = [
        (x, y)
        for x, y, s in _iter_text_labels(msp)
        if _OTHER_ROOM_RE.search(s) and not _SERVING_RE.search(s)
    ]
    verts = [s for s in segs if s.is_v and s.length >= 3500.0]
    hors = [s for s in segs if s.is_h and s.length >= 2500.0]
    boxes: list[tuple[float, float, float, float]] = []
    for lx, ly in labels:
        lefts = [
            s.ortho
            for s in verts
            if lx - 8000.0 < s.ortho < lx - 200.0 and s.along0 - 400.0 <= ly <= s.along1 + 400.0
        ]
        rights = [
            s.ortho
            for s in verts
            if lx + 200.0 < s.ortho < lx + 8000.0 and s.along0 - 400.0 <= ly <= s.along1 + 400.0
        ]
        if not lefts or not rights:
            continue
        near_l, near_r = max(lefts), min(rights)
        if not (2500.0 < near_r - near_l < 12000.0):
            continue
        x0 = min((o for o in lefts if near_l - 600.0 <= o <= near_l), default=near_l)
        x1 = max((o for o in rights if near_r <= o <= near_r + 600.0), default=near_r)

        def _spans(s: AxisSeg) -> bool:
            return min(s.along1, x1) - max(s.along0, x0) >= (x1 - x0) * 0.55

        below = [s.ortho for s in hors if _spans(s) and ly - 10000.0 < s.ortho < ly - 200.0]
        above = [s.ortho for s in hors if _spans(s) and ly + 200.0 < s.ortho < ly + 14000.0]
        if not below:
            continue
        y0 = max(below)
        y0 = min((o for o in below if y0 - 600.0 <= o <= y0), default=y0)

        def _blocked(ortho: float) -> bool:
            return any(x0 < ox < x1 and min(ly, ortho) < oy < max(ly, ortho) for ox, oy in others)

        above = [o for o in above if not _blocked(o)]
        if above:
            y1 = min(above)
            y1 = max((o for o in above if y1 <= o <= y1 + 600.0), default=y1)
        else:
            caps = [
                s.along1
                for s in verts
                if abs(s.ortho - x0) <= 40.0 or abs(s.ortho - x1) <= 40.0 or abs(s.ortho - near_l) <= 40.0 or abs(s.ortho - near_r) <= 40.0
            ]
            caps = [c for c in caps if ly + 1500.0 < c < ly + 8000.0 and not _blocked(c)]
            if not caps:
                continue
            y1 = max(caps)
        if not (4000.0 < y1 - y0 < 18000.0):
            continue
        if any(x0 + 400.0 < ox < x1 - 400.0 and y0 + 400.0 < oy < y1 - 400.0 for ox, oy in others):
            continue
        boxes.append((x0, x1, y0, y1))
    if not boxes:
        return cafe
    demote: set[int] = set()
    for s in segs:
        if s.layer != WALL_LAYER or s.entity is None or id(s.entity) in exclude_ids:
            continue
        for x0, x1, y0, y1 in boxes:
            if s.is_h:
                if not (y0 - 80.0 <= s.ortho <= y1 + 80.0):
                    continue
                span = x1 - x0
            else:
                if not (x0 - 80.0 <= s.ortho <= x1 + 80.0):
                    continue
                span = y1 - y0
            ov = min(s.along1, (x1 if s.is_h else y1) + 80.0) - max(s.along0, (x0 if s.is_h else y0) - 80.0)
            if ov >= 40.0 and ov >= min(s.length * 0.7, span * 0.45):
                demote.add(id(s.entity))
                break
    return demote | cafe


_FITNESS_RE = re.compile(r"(피트니스|FITNESS|헬스장|헬스\b|GX룸|GX\b)", re.IGNORECASE)


def find_fitness_labels(msp) -> list[tuple[float, float, str]]:
    """피트니스·헬스 실명 라벨."""
    hits: list[tuple[float, float, str]] = []
    for x, y, s in _iter_text_labels(msp):
        if _FITNESS_RE.search(s):
            hits.append((x, y, s))
    return hits


def demote_fitness_equipment(
    msp,
    segs: list[AxisSeg],
    *,
    exclude_ids: set[int] | None = None,
    label_radius_mm: float = 32000.0,
    cell_mm: float = 2000.0,
    dense_min: int = 25,
    max_len_mm: float = 2000.0,
    min_len_mm: float = 400.0,
    plate_r_min: float = 40.0,
    plate_r_max: float = 280.0,
) -> set[int]:
    """피트니스 운동기구(랙·바벨·런닝머신 프레임) WALL demote.

    「피트니스」라벨 주변에서, 웨이트 플레이트에 겹친 짧은 봉과
    기구 윤곽선이 빽빽한 곳의 짧은 WALL을 제거한다. 장축 벽과 H-Beam은 남긴다.
    """
    exclude_ids = exclude_ids or set()
    labels = find_fitness_labels(msp)
    if not labels:
        return set()

    def _near_fitness(mx: float, my: float) -> bool:
        return any(
            abs(mx - lx) <= label_radius_mm and abs(my - ly) <= label_radius_mm
            for lx, ly, _ in labels
        )

    plates: list[tuple[float, float]] = []
    for e in msp:
        if e.dxftype() not in ("ARC", "CIRCLE"):
            continue
        try:
            r = float(e.dxf.radius)
            cx, cy = float(e.dxf.center.x), float(e.dxf.center.y)
        except Exception:  # noqa: BLE001
            continue
        if not (plate_r_min <= r <= plate_r_max):
            continue
        if _near_fitness(cx, cy):
            plates.append((cx, cy))

    plate_grid: dict[tuple[int, int], int] = defaultdict(int)
    for px, py in plates:
        plate_grid[(int(px // cell_mm), int(py // cell_mm))] += 1

    base_grid: dict[tuple[int, int], int] = defaultdict(int)
    for s in segs:
        if s.layer != BASE_LAYER or s.length > 2500.0:
            continue
        mx = (s.x0 + s.x1) * 0.5
        my = (s.y0 + s.y1) * 0.5
        if not _near_fitness(mx, my):
            continue
        base_grid[(int(mx // cell_mm), int(my // cell_mm))] += 1

    plate_hot = {k for k, n in plate_grid.items() if n >= 3}
    base_hot = {k for k, n in base_grid.items() if n >= dense_min}
    # 플레이트 셀 ±2 + 플레이트 인접 밀집 BASE
    hot: set[tuple[int, int]] = set()
    for i, j in plate_hot:
        for di in (-2, -1, 0, 1, 2):
            for dj in (-2, -1, 0, 1, 2):
                hot.add((i + di, j + dj))
    for i, j in base_hot:
        if any(
            (i + di, j + dj) in plate_hot
            for di in (-2, -1, 0, 1, 2)
            for dj in (-2, -1, 0, 1, 2)
        ):
            hot.add((i, j))
        elif base_grid[(i, j)] >= dense_min + 20:
            # 플레이트 없는 케이블머신 등 — 매우 밀집만
            hot.add((i, j))

    long_wall = [s for s in segs if s.layer == WALL_LAYER and s.length >= 8000.0]
    h_long, v_long = _index_wall_runs(long_wall)

    def on_long_run(s: AxisSeg) -> bool:
        if s.is_h:
            iv: list[tuple[float, float, float]] = []
            for d in (-2, -1, 0, 1, 2):
                iv.extend(h_long.get(_bucket(s.ortho) + d * 50, []))
        else:
            iv = []
            for d in (-2, -1, 0, 1, 2):
                iv.extend(v_long.get(_bucket(s.ortho) + d * 50, []))
        return any(
            min(s.along1, b1) - max(s.along0, b0) > 200
            or abs(b1 - s.along0) <= 400
            or abs(s.along1 - b0) <= 400
            for b0, b1, _ in iv
        )

    demote: set[int] = set()
    if hot:
        for s in segs:
            if s.layer != WALL_LAYER:
                continue
            if not (min_len_mm <= s.length <= max_len_mm):
                continue
            if id(s.entity) in exclude_ids:
                continue
            ent = s.entity
            if ent is not None and getattr(ent, "dxftype", lambda: "")() != "LINE":
                continue
            mx = (s.x0 + s.x1) * 0.5
            my = (s.y0 + s.y1) * 0.5
            if not _near_fitness(mx, my):
                continue
            key = (int(mx // cell_mm), int(my // cell_mm))
            if key not in hot:
                continue
            if on_long_run(s):
                continue
            # 기구 존(플레이트 클러스터·인접 밀집 BASE) 안의 짧은 LINE
            demote.add(id(ent))

    # 아령 플레이트(r 18–220)와, 기구 심볼의 짧은 BASE 밀도
    symbol_plates: list[tuple[float, float, int, int]] = []
    for e in msp:
        if e.dxftype() not in ("ARC", "CIRCLE"):
            continue
        try:
            r = float(e.dxf.radius)
            cx, cy = float(e.dxf.center.x), float(e.dxf.center.y)
        except Exception:  # noqa: BLE001
            continue
        if not (18.0 <= r <= 220.0) or not _near_fitness(cx, cy):
            continue
        symbol_plates.append((cx, cy, int(cx // cell_mm), int(cy // cell_mm)))
    symbol_hot = {k for k, n in plate_grid.items() if n >= 3}
    extra_grid: dict[tuple[int, int], int] = defaultdict(int)
    for cx, cy, i, j in symbol_plates:
        extra_grid[(i, j)] += 1
    symbol_hot |= {k for k, n in extra_grid.items() if n >= 3}

    ink_cell = 500.0
    ink: dict[tuple[int, int], int] = defaultdict(int)
    for e in msp:
        if e.dxftype() != "LINE" or getattr(e.dxf, "layer", None) != BASE_LAYER:
            continue
        try:
            x0, y0 = float(e.dxf.start.x), float(e.dxf.start.y)
            x1, y1 = float(e.dxf.end.x), float(e.dxf.end.y)
        except Exception:  # noqa: BLE001
            continue
        length = math.hypot(x1 - x0, y1 - y0)
        if not (5.0 <= length <= 1500.0):
            continue
        mx = (x0 + x1) * 0.5
        my = (y0 + y1) * 0.5
        if not _near_fitness(mx, my):
            continue
        ink[(int(mx // ink_cell), int(my // ink_cell))] += 1

    def _ink_count(mx: float, my: float, rad: float = 800.0) -> int:
        i0, i1 = int((mx - rad) // ink_cell), int((mx + rad) // ink_cell)
        j0, j1 = int((my - rad) // ink_cell), int((my + rad) // ink_cell)
        return sum(
            ink.get((i, j), 0)
            for i in range(i0, i1 + 1)
            for j in range(j0, j1 + 1)
        )

    def _near_room(mx: float, my: float) -> bool:
        return any(
            abs(mx - lx) <= 45000.0 and abs(my - ly) <= 12000.0
            for lx, ly, _ in labels
        )

    confirmed: list[AxisSeg] = []
    for s in segs:
        if s.layer != WALL_LAYER or id(s.entity) in exclude_ids:
            continue
        if not (150.0 <= s.length <= 600.0) or on_long_run(s):
            continue
        mx = (s.x0 + s.x1) * 0.5
        my = (s.y0 + s.y1) * 0.5
        if not _near_fitness(mx, my):
            continue
        on_plate = False
        for px, py, i, j in symbol_plates:
            if (i, j) not in symbol_hot:
                continue
            if math.hypot(mx - px, my - py) <= 280.0:
                on_plate = True
                break
        dense_symbol = _near_room(mx, my) and _ink_count(mx, my) >= 40
        if not on_plate and not dense_symbol:
            continue
        demote.add(id(s.entity))
        if on_plate:
            confirmed.append(s)

    # 같은 선반 위에 반복된 봉(플레이트 없는 칸)도 함께 제거
    for s in segs:
        if s.layer != WALL_LAYER or id(s.entity) in exclude_ids or id(s.entity) in demote:
            continue
        if not (250.0 <= s.length <= 500.0) or on_long_run(s):
            continue
        mx = (s.x0 + s.x1) * 0.5
        my = (s.y0 + s.y1) * 0.5
        if not _near_room(mx, my):
            continue
        mid = (s.along0 + s.along1) * 0.5
        peers = [
            c
            for c in confirmed
            if c.is_h == s.is_h
            and abs(c.ortho - s.ortho) <= 50.0
            and abs(c.length - s.length) <= 80.0
        ]
        if not peers:
            continue
        alongs = [(c.along0 + c.along1) * 0.5 for c in peers]
        # 플레이트가 확인된 봉과 같은 축·같은 길이로 12 m 안에 반복되면 같은 기구다.
        if any(abs(mid - a) <= 12000.0 for a in alongs):
            demote.add(id(s.entity))
    return demote


def _is_organic_landscape_polyline(e) -> bool:
    """짧은 다변·넓은 물결형 BASE 폴리라인 ≈ 조경 윤곽."""
    if e.dxftype() != "LWPOLYLINE":
        return False
    pts = [(float(p[0]), float(p[1])) for p in e.get_points("xy")]
    if len(pts) < 10:
        return False
    pairs = list(zip(pts, pts[1:]))
    if e.closed and pts[0] != pts[-1]:
        pairs.append((pts[-1], pts[0]))
    if len(pairs) < 9:
        return False
    lengths = [math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in pairs]
    avg = sum(lengths) / len(lengths)
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    w = max(xs) - min(xs)
    h = max(ys) - min(ys)
    if avg < 1500.0:
        return True
    # 넓은 정원 물결 윤곽 (평균 변 < 4 m, 한 변 ≥ 8–10 m)
    if len(pts) >= 15 and (w >= 10000.0 or h >= 8000.0) and avg < 4000.0:
        return True
    return False


def _landscape_wave_bboxes(
    msp,
    *,
    min_span_mm: float = 10000.0,
    max_avg_edge_mm: float = 2000.0,
) -> list[tuple[float, float, float, float]]:
    """대형 유기 조경(물결) 폴리라인 bbox 목록."""
    out: list[tuple[float, float, float, float]] = []
    for e in msp:
        if getattr(e.dxf, "layer", None) != BASE_LAYER:
            continue
        if not _is_organic_landscape_polyline(e):
            continue
        pts = [(float(p[0]), float(p[1])) for p in e.get_points("xy")]
        pairs = list(zip(pts, pts[1:]))
        if e.closed and pts[0] != pts[-1]:
            pairs.append((pts[-1], pts[0]))
        lengths = [math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in pairs]
        avg = sum(lengths) / len(lengths)
        if avg > max_avg_edge_mm:
            continue
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        w = max(xs) - min(xs)
        h = max(ys) - min(ys)
        if max(w, h) < min_span_mm and min(w, h) < 8000.0:
            continue
        out.append((min(xs), max(xs), min(ys), max(ys)))
    return out


def _landscape_rock_centers(
    msp,
    *,
    min_mm: float = 300.0,
    max_mm: float = 1500.0,
) -> list[tuple[float, float]]:
    """바위·식재 등 비정형 닫힌 소형 조경 윤곽 중심점."""
    out: list[tuple[float, float]] = []
    for e in msp:
        if e.dxftype() != "LWPOLYLINE":
            continue
        if getattr(e.dxf, "layer", None) != BASE_LAYER:
            continue
        pts = [(float(p[0]), float(p[1])) for p in e.get_points("xy")]
        if not (8 <= len(pts) <= 40):
            continue
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
        out.append(((min(xs) + max(xs)) * 0.5, (min(ys) + max(ys)) * 0.5))
    return out


def demote_landscape_walls(
    msp,
    segs: list[AxisSeg],
    *,
    exclude_ids: set[int] | None = None,
    wave_margin_mm: float = 2800.0,
    rock_band_mm: float = 600.0,
    rock_min_count: int = 3,
    rock_max_len_mm: float = 2500.0,
) -> set[int]:
    """정원·조경 구역 WALL demote.

    정원에는 구조 벽이 있을 수 없다.
    - 대형 물결형 조경 폴리라인 bbox **깊숙한 내부**의 WALL
      (가장자리 facade·테라스 장축은 margin으로 보존)
    - 바위/식재 클러스터를 가로지르는 **짧은** WALL
    LINE 및 사실상 LINE인 짧은 LWPOLYLINE만 대상.
    """
    exclude_ids = exclude_ids or set()
    waves = _landscape_wave_bboxes(msp)
    rocks = _landscape_rock_centers(msp)
    if not waves and not rocks:
        return set()

    def deep_in_wave(mx: float, my: float) -> bool:
        m = wave_margin_mm
        for x0, x1, y0, y1 in waves:
            if x0 + m < mx < x1 - m and y0 + m < my < y1 - m:
                return True
        return False

    def rocks_on_seg(s: AxisSeg) -> int:
        n = 0
        for rx, ry in rocks:
            if s.is_h:
                if abs(ry - s.ortho) > rock_band_mm:
                    continue
                if s.along0 - rock_band_mm <= rx <= s.along1 + rock_band_mm:
                    n += 1
            else:
                if abs(rx - s.ortho) > rock_band_mm:
                    continue
                if s.along0 - rock_band_mm <= ry <= s.along1 + rock_band_mm:
                    n += 1
        return n

    def _allow_entity(ent: Any) -> bool:
        if ent is None or id(ent) in exclude_ids:
            return False
        t = getattr(ent, "dxftype", lambda: "")()
        if t == "LINE":
            return True
        # 사실상 단일 선분인 조경 오검출 PL
        if t == "LWPOLYLINE":
            try:
                pts = list(ent.get_points("xy"))
            except Exception:  # noqa: BLE001
                return False
            return len(pts) <= 3 and not bool(getattr(ent, "closed", False))
        return False

    demote: set[int] = set()
    for s in segs:
        if s.layer != WALL_LAYER:
            continue
        ent = s.entity
        if not _allow_entity(ent):
            continue
        mx = (s.x0 + s.x1) * 0.5
        my = (s.y0 + s.y1) * 0.5
        if deep_in_wave(mx, my):
            demote.add(id(ent))
            continue
        if s.length <= rock_max_len_mm and rocks_on_seg(s) >= rock_min_count:
            demote.add(id(ent))
    return demote


def _iter_closed_boxes(
    msp,
    *,
    min_mm: float = 150.0,
    max_mm: float = 3000.0,
    max_pts: int = 8,
) -> list[tuple[float, float, float, float, float, float, float, float, Any]]:
    """닫힌 사각/직사각 LWPOLYLINE → (cx, cy, w, h, x0, x1, y0, y1, entity)."""
    out: list[tuple[float, float, float, float, float, float, float, float, Any]] = []
    for e in msp:
        if e.dxftype() != "LWPOLYLINE":
            continue
        pts = [(float(p[0]), float(p[1])) for p in e.get_points("xy")]
        if len(pts) < 4 or len(pts) > max_pts:
            continue
        closed = bool(e.closed) or (
            math.hypot(pts[0][0] - pts[-1][0], pts[0][1] - pts[-1][1]) < 50.0
        )
        if not closed:
            continue
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        x0, x1 = min(xs), max(xs)
        y0, y1 = min(ys), max(ys)
        w, h = x1 - x0, y1 - y0
        if not (min_mm <= w <= max_mm and min_mm <= h <= max_mm):
            continue
        out.append(((x0 + x1) * 0.5, (y0 + y1) * 0.5, w, h, x0, x1, y0, y1, e))
    return out


def _iter_closed_squares(
    msp,
    *,
    min_mm: float = 150.0,
    max_mm: float = 1200.0,
) -> list[tuple[float, float, float, float, Any]]:
    """닫힌 대략 정사각 LWPOLYLINE → (cx, cy, w, h, entity)."""
    out: list[tuple[float, float, float, float, Any]] = []
    for cx, cy, w, h, _x0, _x1, _y0, _y1, e in _iter_closed_boxes(
        msp, min_mm=min_mm, max_mm=max_mm
    ):
        # 1300×900 휠체어 표식처럼 한 변이 확연히 긴 사각은 기둥이 아니다.
        if abs(w - h) > max(w, h) * 0.22:
            continue
        out.append((cx, cy, w, h, e))
    return out


def _iter_hbeam_dashes(msp) -> list[tuple[float, float, float, Any]]:
    """H-Beam 내부 '_' 대시 — 짧은 직교 LINE → (mx, my, length, entity)."""
    dashes: list[tuple[float, float, float, Any]] = []
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
        dashes.append(((x0 + x1) * 0.5, (y0 + y1) * 0.5, length, e))
    return dashes


def _pictogram_arc_points(msp) -> list[tuple[float, float]]:
    """ARC/CIRCLE 중심. 휠체어 바퀴처럼 사각 안에 있으면 기둥이 아니다."""
    pts: list[tuple[float, float]] = []
    for e in msp:
        if e.dxftype() not in ("ARC", "CIRCLE"):
            continue
        try:
            pts.append((float(e.dxf.center.x), float(e.dxf.center.y)))
        except Exception:  # noqa: BLE001
            continue
    return pts


def _is_pictogram_column_box(w: float, h: float, n_arc: int, n_line: int) -> bool:
    """사각 기둥이 아니라 장애인 표식·설비 심볼인가.

    진짜 H-Beam은 정사각과 '_' 한 줄이다. 호가 둘 이상이거나
    짧은 내부선이 많거나, 가로로 긴 사각에 선이 여럿이면 표식이다.
    """
    side = max(w, h)
    if side < 450.0 or side > 1600.0 or min(w, h) < 300.0:
        return False
    aspect = abs(w - h) / side
    if n_arc >= 2:
        return True
    if n_line >= 6:
        return True
    if aspect > 0.22 and n_line >= 3:
        return True
    return False


def demote_pictogram_columns(msp) -> set[int]:
    """휠체어 표식처럼 기둥으로 올라간 WALL 심볼을 demote.

    윤곽과, 그 안에 있는 짧은 WALL 선만 제거한다. 긴 구조벽은 남긴다.
    """
    cell = 2000.0

    def bucket(x: float, y: float) -> tuple[int, int]:
        return (int(math.floor(x / cell)), int(math.floor(y / cell)))

    arc_grid: dict[tuple[int, int], list[tuple[float, float]]] = defaultdict(list)
    line_grid: dict[tuple[int, int], list[tuple[float, float, float, Any]]] = defaultdict(list)
    boxes: list[tuple[float, float, float, float, float, float, Any]] = []
    for e in msp:
        t = e.dxftype()
        if t in ("ARC", "CIRCLE"):
            try:
                cx, cy = float(e.dxf.center.x), float(e.dxf.center.y)
            except Exception:  # noqa: BLE001
                continue
            arc_grid[bucket(cx, cy)].append((cx, cy))
            continue
        if t == "LINE":
            try:
                x0, y0 = float(e.dxf.start.x), float(e.dxf.start.y)
                x1, y1 = float(e.dxf.end.x), float(e.dxf.end.y)
            except Exception:  # noqa: BLE001
                continue
            length = math.hypot(x1 - x0, y1 - y0)
            if not (20.0 <= length <= 1700.0):
                continue
            mx, my = (x0 + x1) * 0.5, (y0 + y1) * 0.5
            line_grid[bucket(mx, my)].append((mx, my, length, e))
            continue
        if t != "LWPOLYLINE":
            continue
        pts = [(float(p[0]), float(p[1])) for p in e.get_points("xy")]
        if len(pts) < 4 or len(pts) > 8:
            continue
        closed = bool(e.closed) or (
            math.hypot(pts[0][0] - pts[-1][0], pts[0][1] - pts[-1][1]) < 50.0
        )
        if not closed:
            continue
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        x0, x1 = min(xs), max(xs)
        y0, y1 = min(ys), max(ys)
        w, h = x1 - x0, y1 - y0
        if max(w, h) < 450.0 or max(w, h) > 1600.0 or min(w, h) < 300.0:
            continue
        boxes.append((x0, y0, x1, y1, w, h, e))

    out: set[int] = set()
    for x0, y0, x1, y1, w, h, e in boxes:
        n_arc = 0
        n_line = 0
        inside_lines: list[Any] = []
        ix0, ix1 = int(math.floor(x0 / cell)) - 1, int(math.floor(x1 / cell)) + 1
        iy0, iy1 = int(math.floor(y0 / cell)) - 1, int(math.floor(y1 / cell)) + 1
        for ix in range(ix0, ix1 + 1):
            for iy in range(iy0, iy1 + 1):
                for ax, ay in arc_grid.get((ix, iy), ()):
                    if x0 <= ax <= x1 and y0 <= ay <= y1:
                        n_arc += 1
                for mx, my, length, le in line_grid.get((ix, iy), ()):
                    if not (x0 <= mx <= x1 and y0 <= my <= y1):
                        continue
                    # 표식 변 길이(최대 1.6 m)까지의 선은 심볼 일부다. 더 긴 구조벽은 남긴다.
                    if length <= max(w, h) + 30.0:
                        inside_lines.append(le)
                    if length <= 700.0:
                        n_line += 1
        if not _is_pictogram_column_box(w, h, n_arc, n_line):
            continue
        if e.dxf.layer == WALL_LAYER:
            out.add(id(e))
        for le in inside_lines:
            if le.dxf.layer == WALL_LAYER:
                out.add(id(le))
        # 가로로 긴 표식에 맞닿은 짧은 선(바로 아래 단차)만. 정사각 기둥 주변은 제외.
        side = max(w, h)
        if abs(w - h) / side > 0.22:
            gap_max = 250.0
            fx0 = int(math.floor((x0 - gap_max) / cell)) - 1
            fx1 = int(math.floor((x1 + gap_max) / cell)) + 1
            fy0 = int(math.floor((y0 - gap_max) / cell)) - 1
            fy1 = int(math.floor((y1 + gap_max) / cell)) + 1
            for ix in range(fx0, fx1 + 1):
                for iy in range(fy0, fy1 + 1):
                    for mx, my, length, le in line_grid.get((ix, iy), ()):
                        if length > side + 30.0 or le.dxf.layer != WALL_LAYER:
                            continue
                        half = length * 0.5
                        if x0 <= mx <= x1:
                            if my + half < y0:
                                gap = y0 - (my + half)
                            elif my - half > y1:
                                gap = (my - half) - y1
                            else:
                                gap = 0.0
                        elif y0 <= my <= y1:
                            if mx + half < x0:
                                gap = x0 - (mx + half)
                            elif mx - half > x1:
                                gap = (mx - half) - x1
                            else:
                                gap = 0.0
                        else:
                            continue
                        if gap <= gap_max:
                            out.add(id(le))
    return out


def find_hbeam_column_entities(msp) -> set[int]:
    """H-Beam 기둥 엔티티 id — 정사각 + 중앙 '_' 심볼만.

    외부 연결 벽·직사각 슬리브는 승격하지 않는다.
    동심 이중 정사각이면 '_' 를 품은 정사각(+ 대시)만 WALL.
    밀집 격자는 제외.
    """
    squares = _iter_closed_squares(msp, min_mm=450.0, max_mm=1500.0)
    dashes = _iter_hbeam_dashes(msp)
    arc_pts = _pictogram_arc_points(msp)

    candidates: list[tuple[float, float, float, set[int]]] = []
    for cx, cy, w, h, e in squares:
        side = max(w, h)
        dash_ids: set[int] = set()
        for mx, my, length, de in dashes:
            if length > min(w, h) * 0.85:
                continue
            if abs(mx - cx) <= side * 0.35 and abs(my - cy) <= side * 0.35:
                dash_ids.add(id(de))
        if not dash_ids:
            continue
        x0, y0 = cx - w * 0.5, cy - h * 0.5
        x1, y1 = cx + w * 0.5, cy + h * 0.5
        n_arc = sum(1 for ax, ay in arc_pts if x0 <= ax <= x1 and y0 <= ay <= y1)
        # 사각 안 호가 둘 이상이거나 '_' 가 여러 개면 장애인 표식 등이다.
        if n_arc >= 2 or len(dash_ids) >= 6:
            continue
        candidates.append((cx, cy, side, {id(e)} | dash_ids))

    merged: list[tuple[float, float, float, set[int]]] = []
    for cx, cy, sz, eids in candidates:
        found = False
        for i, (mx, my, msz, meds) in enumerate(merged):
            if abs(cx - mx) <= 120.0 and abs(cy - my) <= 120.0:
                meds |= eids
                merged[i] = (mx, my, max(msz, sz), meds)
                found = True
                break
        if not found:
            merged.append((cx, cy, sz, set(eids)))

    # 밀집 격자 제외 (구조 기둥은 ~8 m 간격)
    keep: list[tuple[float, float, float, set[int]]] = []
    for cx, cy, sz, eids in merged:
        rad = max(2500.0, sz * 4.0)
        n_near = sum(
            1 for ox, oy, _osz, _ in merged if math.hypot(cx - ox, cy - oy) <= rad
        )
        if n_near >= 4:
            continue
        keep.append((cx, cy, sz, eids))

    column_ids: set[int] = set()
    for _cx, _cy, _sz, eids in keep:
        column_ids |= eids
    return column_ids



def _hbeam_column_boxes(msp) -> list[tuple[float, float, float, float]]:
    """'_' 가 있는 H-Beam 정사각의 중심과 크기."""
    squares = _iter_closed_squares(msp, min_mm=450.0, max_mm=1500.0)
    dashes = _iter_hbeam_dashes(msp)
    arc_pts = _pictogram_arc_points(msp)
    found: list[tuple[float, float, float, float]] = []
    for cx, cy, w, h, _e in squares:
        side = max(w, h)
        n_dash = 0
        for mx, my, length, _de in dashes:
            if length > min(w, h) * 0.85:
                continue
            if abs(mx - cx) <= side * 0.35 and abs(my - cy) <= side * 0.35:
                n_dash += 1
        if n_dash < 1:
            continue
        x0, y0 = cx - w * 0.5, cy - h * 0.5
        x1, y1 = cx + w * 0.5, cy + h * 0.5
        n_arc = sum(1 for ax, ay in arc_pts if x0 <= ax <= x1 and y0 <= ay <= y1)
        if n_arc >= 2 or n_dash >= 6:
            continue
        if any(abs(cx - ox) <= 120.0 and abs(cy - oy) <= 120.0 for ox, oy, _ow, _oh in found):
            continue
        found.append((cx, cy, w, h))
    return found


def promote_hbeam_sleeves(msp) -> int:
    """H-Beam 을 감싼 닫힌 외곽 사각형은 기둥 외벽이다."""
    boxes = _hbeam_column_boxes(msp)
    if not boxes:
        return 0
    n = 0
    for e in msp:
        if e.dxftype() != "LWPOLYLINE" or not e.closed:
            continue
        if getattr(e.dxf, "layer", None) != BASE_LAYER:
            continue
        pts = [(float(p[0]), float(p[1])) for p in e.get_points("xy")]
        if len(pts) < 4 or len(pts) > 5:
            continue
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        x0, x1 = min(xs), max(xs)
        y0, y1 = min(ys), max(ys)
        rw, rh = x1 - x0, y1 - y0
        if not (1000.0 <= rw <= 2600.0 and 1000.0 <= rh <= 2600.0):
            continue
        for cx, cy, w, h in boxes:
            if not (x0 < cx < x1 and y0 < cy < y1):
                continue
            inset_l = (cx - w * 0.5) - x0
            inset_r = x1 - (cx + w * 0.5)
            inset_b = (cy - h * 0.5) - y0
            inset_t = y1 - (cy + h * 0.5)
            insets = (inset_l, inset_r, inset_b, inset_t)
            if not all(60.0 <= v <= 800.0 for v in insets):
                continue
            if not (w + 100.0 <= rw <= w + 1000.0 and h + 100.0 <= rh <= h + 1000.0):
                continue
            e.dxf.layer = WALL_LAYER
            try:
                e.dxf.color = WALL_COLOR
            except Exception:  # noqa: BLE001
                pass
            n += 1
            break
    return n


def promote_wall_square_columns(msp) -> int:
    """벽면 정사각 기둥(H-Beam 아님)을 WALL로 올린다.

    닫힌 정사각 윤곽만 있고 중앙 '_' 가 없다. 이중벽(간격 80–350 mm)이
    사각의 한 변에서 끊기면 그 사각은 기둥이다. 교육실 하단만이 아니다.
    """
    h_all: list[tuple[float, float, float, str, float, Any]] = []
    v_all: list[tuple[float, float, float, str, float, Any]] = []
    arcs: list[tuple[float, float]] = []
    swings: list[tuple[float, float, float]] = []
    for e in msp:
        kind = e.dxftype()
        if kind in ("ARC", "CIRCLE"):
            try:
                cx, cy = float(e.dxf.center.x), float(e.dxf.center.y)
                arcs.append((cx, cy))
                if kind == "ARC":
                    radius = float(e.dxf.radius)
                    if radius >= 400.0:
                        swings.append((cx, cy, radius))
            except Exception:  # noqa: BLE001
                pass
            continue
        parsed = _axis_line(e)
        if parsed is None:
            continue
        ori, coord, a, b, length = parsed
        layer = str(getattr(e.dxf, "layer", "") or "")
        if ori == "H":
            h_all.append((coord, a, b, layer, length, e))
        else:
            v_all.append((coord, a, b, layer, length, e))

    h_short = sorted(
        [t for t in h_all if 450.0 <= t[4] <= 1500.0],
        key=lambda t: t[0],
    )
    v_short = [t for t in v_all if 450.0 <= t[4] <= 1500.0]
    h_wall = [t for t in h_all if t[3] == WALL_LAYER and t[4] >= 1200.0]
    v_wall = [t for t in v_all if t[3] == WALL_LAYER and t[4] >= 1200.0]

    def pair_stops_on_vertical(x_edge: float, y0: float, y1: float, outward: int) -> bool:
        ends: list[float] = []
        for y, a, b, _layer, _length, _e in h_wall:
            if not (y0 - 40.0 <= y <= y1 + 40.0):
                continue
            if outward < 0 and abs(b - x_edge) <= 45.0 and a < x_edge - 120.0:
                ends.append(y)
            elif outward > 0 and abs(a - x_edge) <= 45.0 and b > x_edge + 120.0:
                ends.append(y)
        ends = sorted(set(round(y, 1) for y in ends))
        for i, ya in enumerate(ends):
            for yb in ends[i + 1 :]:
                if 80.0 <= yb - ya <= 350.0:
                    return True
        return False

    def pair_stops_on_horizontal(y_edge: float, x0: float, x1: float, outward: int) -> bool:
        ends: list[float] = []
        for x, a, b, _layer, _length, _e in v_wall:
            if not (x0 - 40.0 <= x <= x1 + 40.0):
                continue
            if outward < 0 and abs(b - y_edge) <= 45.0 and a < y_edge - 120.0:
                ends.append(x)
            elif outward > 0 and abs(a - y_edge) <= 45.0 and b > y_edge + 120.0:
                ends.append(x)
        ends = sorted(set(round(x, 1) for x in ends))
        for i, xa in enumerate(ends):
            for xb in ends[i + 1 :]:
                if 80.0 <= xb - xa <= 350.0:
                    return True
        return False

    def pair_offset_on_horizontal(y_edge: float, x0: float, x1: float, outward: int) -> bool:
        """이중벽이 변에서 80–500 mm 떨어져 있고, 짧은 선으로 그 변에 연결되면 True."""
        walls: list[float] = []
        for y, a, b, layer, length, _e in h_all:
            if layer != WALL_LAYER or length < 800.0:
                continue
            off = (y - y_edge) * outward
            if 80.0 <= off <= 500.0 and min(b, x1) - max(a, x0) >= 200.0:
                walls.append(y)
        walls = sorted(set(round(y, 1) for y in walls))
        pairs: list[tuple[float, float]] = []
        for i, ya in enumerate(walls):
            for yb in walls[i + 1 :]:
                if 80.0 <= yb - ya <= 350.0:
                    pairs.append((ya, yb))

        def stub_to(y_wall: float) -> bool:
            for x, a, b, _layer, length, _e in v_all:
                if not (80.0 <= length <= 600.0):
                    continue
                if not (x0 - 40.0 <= x <= x1 + 40.0):
                    continue
                if outward > 0 and abs(a - y_edge) <= 45.0 and abs(b - y_wall) <= 45.0:
                    return True
                if outward < 0 and abs(b - y_edge) <= 45.0 and abs(a - y_wall) <= 45.0:
                    return True
            return False

        return any(stub_to(ya) and stub_to(yb) for ya, yb in pairs)

    def pair_offset_on_vertical(x_edge: float, y0: float, y1: float, outward: int) -> bool:
        walls: list[float] = []
        for x, a, b, layer, length, _e in v_all:
            if layer != WALL_LAYER or length < 800.0:
                continue
            off = (x - x_edge) * outward
            if 80.0 <= off <= 500.0 and min(b, y1) - max(a, y0) >= 200.0:
                walls.append(x)
        walls = sorted(set(round(x, 1) for x in walls))
        pairs: list[tuple[float, float]] = []
        for i, xa in enumerate(walls):
            for xb in walls[i + 1 :]:
                if 80.0 <= xb - xa <= 350.0:
                    pairs.append((xa, xb))

        def stub_to(x_wall: float) -> bool:
            for y, a, b, _layer, length, _e in h_all:
                if not (80.0 <= length <= 600.0):
                    continue
                if not (y0 - 40.0 <= y <= y1 + 40.0):
                    continue
                if outward > 0 and abs(a - x_edge) <= 45.0 and abs(b - x_wall) <= 45.0:
                    return True
                if outward < 0 and abs(b - x_edge) <= 45.0 and abs(a - x_wall) <= 45.0:
                    return True
            return False

        return any(stub_to(xa) and stub_to(xb) for xa, xb in pairs)

    def pier_interrupted(
        edge: float, span0: float, span1: float, inward: int, horizontal: bool
    ) -> bool:
        """한 면은 사각 변을 지나고, 안쪽 면은 양쪽 모서리에서 끊기면 기둥이다."""
        group = h_all if horizontal else v_all
        host = False
        for coord, a, b, layer, length, _e in group:
            if layer != WALL_LAYER or length < 1200.0:
                continue
            if abs(coord - edge) > 40.0:
                continue
            if min(b, span1) - max(a, span0) < 0.7 * (span1 - span0):
                continue
            if a < span0 - 300.0 or b > span1 + 300.0:
                host = True
        if not host:
            return False
        mates: list[tuple[float, float]] = []
        for coord, a, b, layer, _length, _e in group:
            if layer != WALL_LAYER:
                continue
            off = (coord - edge) * inward
            if 80.0 <= off <= 350.0:
                mates.append((a, b))
        left_stop = any(abs(b - span0) <= 50.0 and a < span0 - 120.0 for a, b in mates)
        right_stop = any(abs(a - span1) <= 50.0 and b > span1 + 120.0 for a, b in mates)
        crosses = any(
            min(b, span1 - 80.0) - max(a, span0 + 80.0) > 200.0 for a, b in mates
        )
        return left_stop and right_stop and not crosses

    def on_double_wall(edge: float, span0: float, span1: float, horizontal: bool) -> bool:
        """사각 한 변이 이중벽 위에 있으면 기둥이다. 안쪽 면이 모서리에서 끊기지 않아도 된다."""
        group = h_all if horizontal else v_all
        host = False
        for coord, a, b, layer, length, _e in group:
            if layer != WALL_LAYER or length < 1200.0:
                continue
            if abs(coord - edge) > 40.0:
                continue
            if min(b, span1) - max(a, span0) < 0.7 * (span1 - span0):
                continue
            if a < span0 - 300.0 or b > span1 + 300.0:
                host = True
        if not host:
            return False
        for coord, a, b, layer, length, _e in group:
            if layer != WALL_LAYER or length < 800.0:
                continue
            if not (80.0 <= abs(coord - edge) <= 350.0):
                continue
            if a < span0 - 400.0 or b > span1 + 400.0:
                return True
        return False

    def short_pair_on_vertical(x_edge: float, y0: float, y1: float, outward: int) -> bool:
        """짧은 이중선(300–900 mm)이 변에서 밖으로 나가면 True. 탈의실 하단 기둥."""
        ends: list[float] = []
        for y, a, b, _layer, length, _e in h_all:
            if not (300.0 <= length <= 900.0):
                continue
            if not (y0 - 40.0 <= y <= y1 + 40.0):
                continue
            if outward < 0 and abs(b - x_edge) <= 45.0 and a < x_edge - 200.0:
                ends.append(y)
            elif outward > 0 and abs(a - x_edge) <= 45.0 and b > x_edge + 200.0:
                ends.append(y)
        ends = sorted(set(round(y, 1) for y in ends))
        for i, ya in enumerate(ends):
            for yb in ends[i + 1 :]:
                if 80.0 <= yb - ya <= 350.0:
                    return True
        return False

    def short_pair_on_horizontal(y_edge: float, x0: float, x1: float, outward: int) -> bool:
        ends: list[float] = []
        for x, a, b, _layer, length, _e in v_all:
            if not (300.0 <= length <= 900.0):
                continue
            if not (x0 - 40.0 <= x <= x1 + 40.0):
                continue
            if outward < 0 and abs(b - y_edge) <= 45.0 and a < y_edge - 200.0:
                ends.append(x)
            elif outward > 0 and abs(a - y_edge) <= 45.0 and b > y_edge + 200.0:
                ends.append(x)
        ends = sorted(set(round(x, 1) for x in ends))
        for i, xa in enumerate(ends):
            for xb in ends[i + 1 :]:
                if 80.0 <= xb - xa <= 350.0:
                    return True
        return False

    n = 0
    promoted_ids: set[int] = set()
    seen_centers: list[tuple[float, float]] = []
    boxes: list[tuple[float, float, float, float]] = []
    for i, (y0, a0, b0, _l0, _L0, e_bottom) in enumerate(h_short):
        width = b0 - a0
        for y1, a1, b1, _l1, _L1, e_top in h_short[i + 1 :]:
            height = y1 - y0
            if height < 450.0:
                continue
            if height > 1500.0:
                break
            if abs(a0 - a1) > 25.0 or abs(b0 - b1) > 25.0:
                continue
            if abs(width - height) > max(width, height) * 0.22:
                continue
            left = right = None
            for x, ya, yb, _lv, _Lv, ev in v_short:
                if abs(ya - y0) > 30.0 or abs(yb - y1) > 30.0:
                    continue
                if abs(x - a0) <= 30.0:
                    left = ev
                elif abs(x - b0) <= 30.0:
                    right = ev
            if left is None or right is None:
                continue
            cx, cy = (a0 + b0) * 0.5, (y0 + y1) * 0.5
            if any(abs(cx - ox) < 40.0 and abs(cy - oy) < 40.0 for ox, oy in seen_centers):
                continue
            if sum(1 for ax, ay in arcs if a0 + 40.0 <= ax <= b0 - 40.0 and y0 + 40.0 <= ay <= y1 - 40.0) >= 2:
                continue
            if any(
                60.0 <= length <= min(width, height) * 0.85
                and y0 + 40.0 <= y <= y1 - 40.0
                and a0 + 40.0 <= (a + b) * 0.5 <= b0 - 40.0
                for y, a, b, _layer, length, _e in h_all
            ):
                continue
            classic = (
                pair_stops_on_vertical(a0, y0, y1, -1)
                or pair_stops_on_vertical(b0, y0, y1, 1)
                or pair_stops_on_horizontal(y0, a0, b0, -1)
                or pair_stops_on_horizontal(y1, a0, b0, 1)
                or pair_offset_on_horizontal(y1, a0, b0, 1)
                or pair_offset_on_horizontal(y0, a0, b0, -1)
                or pair_offset_on_vertical(a0, y0, y1, -1)
                or pair_offset_on_vertical(b0, y0, y1, 1)
                or pier_interrupted(y0, a0, b0, 1, True)
                or pier_interrupted(y1, a0, b0, -1, True)
                or pier_interrupted(a0, y0, y1, 1, False)
                or pier_interrupted(b0, y0, y1, -1, False)
                or on_double_wall(y0, a0, b0, True)
                or on_double_wall(y1, a0, b0, True)
                or on_double_wall(a0, y0, y1, False)
                or on_double_wall(b0, y0, y1, False)
            )
            short_edges = sum(
                (
                    short_pair_on_vertical(a0, y0, y1, -1),
                    short_pair_on_vertical(b0, y0, y1, 1),
                    short_pair_on_horizontal(y0, a0, b0, -1),
                    short_pair_on_horizontal(y1, a0, b0, 1),
                )
            )

            def _stopping_mates() -> tuple[bool, list]:
                """회색 기둥 변에서 끊기는 이중선. 1600 mm 이하만 올린다."""
                paired = False
                mates: list = []

                def collect(group, along_edge: bool, edge: float, outward: int) -> None:
                    nonlocal paired
                    ends: list[tuple[float, float, Any]] = []
                    for coord, a, b, _layer, length, ent in group:
                        if not (300.0 <= length <= 4500.0):
                            continue
                        if along_edge:
                            if not (y0 - 40.0 <= coord <= y1 + 40.0):
                                continue
                        elif not (a0 - 40.0 <= coord <= b0 + 40.0):
                            continue
                        stops = (
                            outward < 0 and abs(b - edge) <= 45.0 and a < edge - 200.0
                        ) or (outward > 0 and abs(a - edge) <= 45.0 and b > edge + 200.0)
                        if stops:
                            ends.append((coord, length, ent))
                    for i, (c0, l0, e0) in enumerate(ends):
                        for c1, l1, e1 in ends[i + 1 :]:
                            if 80.0 <= abs(c1 - c0) <= 350.0 and max(l0, l1) >= 1000.0:
                                paired = True
                                if l0 <= 1600.0:
                                    mates.append(e0)
                                if l1 <= 1600.0:
                                    mates.append(e1)

                collect(h_all, True, a0, -1)
                collect(h_all, True, b0, 1)
                collect(v_all, False, y0, -1)
                collect(v_all, False, y1, 1)
                return paired, mates

            gray_edges = sum(
                getattr(ent.dxf, "layer", None) != WALL_LAYER
                for ent in (e_bottom, e_top, left, right)
            )
            paired, mate_lines = _stopping_mates() if gray_edges >= 3 else (False, [])
            if not classic and short_edges < 2 and not paired:
                continue
            seen_centers.append((cx, cy))
            if classic:
                boxes.append((a0, y0, b0, y1))
            else:
                for ent in mate_lines:
                    if id(ent) in promoted_ids:
                        continue
                    promoted_ids.add(id(ent))
                    if getattr(ent.dxf, "layer", None) == WALL_LAYER:
                        continue
                    ent.dxf.layer = WALL_LAYER
                    try:
                        ent.dxf.color = WALL_COLOR
                    except Exception:  # noqa: BLE001
                        pass
                    n += 1
                for y, a, b, _layer, length, e in h_all:
                    if not (300.0 <= length <= 900.0) or not (y0 - 40.0 <= y <= y1 + 40.0):
                        continue
                    if (abs(b - a0) <= 45.0 and a < a0 - 200.0) or (abs(a - b0) <= 45.0 and b > b0 + 200.0):
                        if id(e) in promoted_ids:
                            continue
                        promoted_ids.add(id(e))
                        if getattr(e.dxf, "layer", None) == WALL_LAYER:
                            continue
                        e.dxf.layer = WALL_LAYER
                        try:
                            e.dxf.color = WALL_COLOR
                        except Exception:  # noqa: BLE001
                            pass
                        n += 1
                for x, a, b, _layer, length, e in v_all:
                    if not (300.0 <= length <= 900.0) or not (a0 - 40.0 <= x <= b0 + 40.0):
                        continue
                    if (abs(b - y0) <= 45.0 and a < y0 - 200.0) or (abs(a - y1) <= 45.0 and b > y1 + 200.0):
                        if id(e) in promoted_ids:
                            continue
                        promoted_ids.add(id(e))
                        if getattr(e.dxf, "layer", None) == WALL_LAYER:
                            continue
                        e.dxf.layer = WALL_LAYER
                        try:
                            e.dxf.color = WALL_COLOR
                        except Exception:  # noqa: BLE001
                            pass
                        n += 1
            for e in (e_bottom, e_top, left, right):
                if id(e) in promoted_ids:
                    continue
                promoted_ids.add(id(e))
                if getattr(e.dxf, "layer", None) == WALL_LAYER:
                    continue
                e.dxf.layer = WALL_LAYER
                try:
                    e.dxf.color = WALL_COLOR
                except Exception:  # noqa: BLE001
                    pass
                n += 1
    n += _promote_column_adjacent_doubles(boxes, h_all, v_all, promoted_ids, swings)
    return n


def _point_on_square_edge(
    x: float, y: float, box: tuple[float, float, float, float], tol: float = 50.0
) -> bool:
    """점이 정사각 둘레에 붙어 있으면 True. 내부 깊숙한 점은 제외."""
    x0, y0, x1, y1 = box
    if x < x0 - tol or x > x1 + tol or y < y0 - tol or y > y1 + tol:
        return False
    return min(abs(x - x0), abs(x - x1), abs(y - y0), abs(y - y1)) <= tol


def _promote_column_adjacent_doubles(
    boxes: list[tuple[float, float, float, float]],
    h_all: list[tuple[float, float, float, str, float, Any]],
    v_all: list[tuple[float, float, float, str, float, Any]],
    promoted_ids: set[int],
    swings: list[tuple[float, float, float]] | None = None,
) -> int:
    """기둥에 닿아 밖으로 나가는 이중 평행선(간격 80–350 mm)을 WALL로 올린다."""
    if not boxes:
        return 0
    n = 0

    def touches(a_pt: tuple[float, float], b_pt: tuple[float, float], box) -> bool:
        x0, y0, x1, y1 = box
        on_a = _point_on_square_edge(*a_pt, box)
        on_b = _point_on_square_edge(*b_pt, box)
        if on_a == on_b:
            return False
        far = b_pt if on_a else a_pt
        return (
            far[0] < x0 - 150.0
            or far[0] > x1 + 150.0
            or far[1] < y0 - 150.0
            or far[1] > y1 + 150.0
        )

    def consider(group, horizontal: bool) -> None:
        nonlocal n
        touching: list[tuple[float, float, float, Any, tuple]] = []
        for coord, a, b, _layer, length, e in group:
            if length < 400.0 or id(e) in promoted_ids:
                continue
            for box in boxes:
                if horizontal:
                    ends = ((a, coord), (b, coord))
                else:
                    ends = ((coord, a), (coord, b))
                if touches(ends[0], ends[1], box):
                    touching.append((coord, a, b, e, box))
                    break
        used: set[int] = set()
        for i, (c0, a0, b0, e0, box0) in enumerate(touching):
            if id(e0) in used:
                continue
            for c1, a1, b1, e1, box1 in touching[i + 1 :]:
                if id(e1) in used or id(e0) == id(e1):
                    continue
                gap = abs(c1 - c0)
                if not (80.0 <= gap <= 350.0):
                    continue
                if abs(box0[0] - box1[0]) > 40.0 or abs(box0[1] - box1[1]) > 40.0:
                    continue
                overlap = min(b0, b1) - max(a0, a1)
                if overlap < 300.0:
                    continue

                def _swung(coord: float, a: float, b: float, length: float) -> bool:
                    if not swings:
                        return False
                    ends = ((a, coord), (b, coord)) if horizontal else ((coord, a), (coord, b))
                    for cx, cy, radius in swings:
                        if abs(radius - length) > max(80.0, 0.2 * length):
                            continue
                        if any(math.hypot(cx - px, cy - py) <= 80.0 for px, py in ends):
                            return True
                    return False

                if _swung(c0, a0, b0, b0 - a0) or _swung(c1, a1, b1, b1 - a1):
                    continue
                for e in (e0, e1):
                    if id(e) in promoted_ids:
                        continue
                    promoted_ids.add(id(e))
                    used.add(id(e))
                    if getattr(e.dxf, "layer", None) == WALL_LAYER:
                        continue
                    e.dxf.layer = WALL_LAYER
                    try:
                        e.dxf.color = WALL_COLOR
                    except Exception:  # noqa: BLE001
                        pass
                    n += 1

    consider(h_all, True)
    consider(v_all, False)
    return n


def _axis_line(e) -> tuple[str, float, float, float, float] | None:
    """축정렬 LINE → (H|V, coord, a, b, length). coord 는 고정축 좌표."""
    if e.dxftype() != "LINE":
        return None
    try:
        x0, y0 = float(e.dxf.start.x), float(e.dxf.start.y)
        x1, y1 = float(e.dxf.end.x), float(e.dxf.end.y)
    except Exception:  # noqa: BLE001
        return None
    length = math.hypot(x1 - x0, y1 - y0)
    if length < 40.0:
        return None
    dx, dy = abs(x1 - x0), abs(y1 - y0)
    tol = max(20.0, 0.12 * length)
    if dy <= tol and dx >= dy:
        return ("H", (y0 + y1) * 0.5, min(x0, x1), max(x0, x1), length)
    if dx <= tol and dy >= dx:
        return ("V", (x0 + x1) * 0.5, min(y0, y1), max(y0, y1), length)
    return None




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


def _paint_layer(ent, layer: str, color: int) -> bool:
    if getattr(ent.dxf, "layer", None) == layer:
        return False
    ent.dxf.layer = layer
    try:
        ent.dxf.color = color
    except Exception:  # noqa: BLE001
        pass
    return True


def _polyline_xy(ent) -> list[tuple[float, float]]:
    try:
        return [(float(p[0]), float(p[1])) for p in ent.get_points("xy")]
    except Exception:  # noqa: BLE001
        return []


def promote_h_symbol_columns(msp) -> tuple[int, list[tuple[float, float, float, float]]]:
    """작은 H형 기둥(닫힌 폴리선, 한 변 180–450 mm)을 WALL로 올린다.

    정사각 + '_' 규칙(450 mm 이상)에 안 걸리는 심볼이다.
    플랜지 안쪽에 웹이 두 개 있으면 기둥으로 본다.
    """
    boxes: list[tuple[float, float, float, float]] = []
    n = 0
    for ent in msp:
        if ent.dxftype() != "LWPOLYLINE" or not ent.closed:
            continue
        pts = _polyline_xy(ent)
        if len(pts) < 12:
            continue
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        x0, x1 = min(xs), max(xs)
        y0, y1 = min(ys), max(ys)
        w, h = x1 - x0, y1 - y0
        if not (180.0 <= w <= 450.0 and 180.0 <= h <= 450.0):
            continue
        if abs(w - h) > max(w, h) * 0.35:
            continue
        webs = 0
        for (ax, ay), (bx, by) in zip(pts, pts[1:] + pts[:1]):
            if abs(bx - ax) > 20.0:
                continue
            if abs(by - ay) < h * 0.55:
                continue
            mx = (ax + bx) * 0.5
            if x0 + w * 0.15 < mx < x1 - w * 0.15:
                webs += 1
        if webs < 2:
            continue
        if _paint_layer(ent, WALL_LAYER, WALL_COLOR):
            n += 1
        boxes.append((x0, y0, x1, y1))
    return n, boxes


def _x_door_openings(msp) -> list[tuple[bool, float, float, float, float]]:
    """X자 문 개구. (is_h, face0, face1, a0, a1). is_h 면 벽이 수평."""
    pieces: list[tuple[float, float, float, float]] = []
    for ent in msp:
        if getattr(ent.dxf, "layer", None) not in (WALL_LAYER, BASE_LAYER):
            continue
        spans: list[tuple[tuple[float, float], tuple[float, float]]] = []
        if ent.dxftype() == "LINE":
            try:
                spans = [(
                    (float(ent.dxf.start.x), float(ent.dxf.start.y)),
                    (float(ent.dxf.end.x), float(ent.dxf.end.y)),
                )]
            except Exception:  # noqa: BLE001
                continue
        elif ent.dxftype() == "LWPOLYLINE":
            pts = _polyline_xy(ent)
            spans = list(zip(pts, pts[1:]))
            if ent.closed and pts:
                spans.append((pts[-1], pts[0]))
        else:
            continue
        for (x0, y0), (x1, y1) in spans:
            dx, dy = abs(x1 - x0), abs(y1 - y0)
            length = math.hypot(dx, dy)
            if dx < 40.0 or dy < 40.0 or not (400.0 <= length <= 2400.0):
                continue
            if min(dx, dy) / max(dx, dy) < 0.08:
                continue
            pieces.append((x0, y0, x1, y1))

    used = [False] * len(pieces)
    doors: list[tuple[bool, float, float, float, float]] = []
    for i, a in enumerate(pieces):
        if used[i]:
            continue
        ax0, ax1 = min(a[0], a[2]), max(a[0], a[2])
        ay0, ay1 = min(a[1], a[3]), max(a[1], a[3])
        group = [i]
        used[i] = True
        for j in range(i + 1, len(pieces)):
            if used[j]:
                continue
            b = pieces[j]
            bx0, bx1 = min(b[0], b[2]), max(b[0], b[2])
            by0, by1 = min(b[1], b[3]), max(b[1], b[3])
            ox = min(ax1, bx1) - max(ax0, bx0)
            oy = min(ay1, by1) - max(ay0, by0)
            if ox > 30.0 and oy > 30.0:
                used[j] = True
                group.append(j)
                ax0, ax1 = min(ax0, bx0), max(ax1, bx1)
                ay0, ay1 = min(ay0, by0), max(ay1, by1)
        if len(group) < 2:
            continue
        signs: set[bool] = set()
        for k in group:
            x0, y0, x1, y1 = pieces[k]
            signs.add((x1 - x0) * (y1 - y0) > 0.0)
        if len(signs) < 2:
            continue
        width, height = ax1 - ax0, ay1 - ay0
        thick, span = min(width, height), max(width, height)
        if not (70.0 <= thick <= 420.0 and 550.0 <= span <= 2200.0):
            continue
        if thick / span > 0.55:
            continue
        is_h = width >= height
        face0, face1 = (ay0, ay1) if is_h else (ax0, ax1)
        a0, a1 = (ax0, ax1) if is_h else (ay0, ay1)
        doors.append((is_h, face0, face1, a0, a1))
    return doors


def _segment_in_door(
    is_h: bool,
    ortho: float,
    a0: float,
    a1: float,
    doors: list[tuple[bool, float, float, float, float]],
) -> bool:
    """이 선분이 문 개구를 가로지르면 True. 양옆 벽은 False."""
    length = a1 - a0
    if length < 40.0:
        return False
    for dh, face0, face1, d0, d1 in doors:
        if dh != is_h:
            continue
        if abs(ortho - face0) > 80.0 and abs(ortho - face1) > 80.0:
            if not (min(face0, face1) - 40.0 <= ortho <= max(face0, face1) + 40.0):
                continue
        overlap = min(a1, d1) - max(a0, d0)
        if overlap >= 200.0 and overlap >= length * 0.45:
            return True
    return False


def promote_column_adjacent_wall_pairs(
    msp,
    boxes: list[tuple[float, float, float, float]],
) -> int:
    """기둥에 닿는 이중 평행선(간격 80–350 mm)을 벽으로 올린다.

    문 개구를 지나는 선과, 같은 띠에 4개 이상 모인 평행선(계단·살대)은 올리지 않는다.
    """
    if not boxes:
        return 0
    doors = _x_door_openings(msp)
    segs: list[tuple[bool, float, float, float, str, Any]] = []
    for ent in msp:
        layer = getattr(ent.dxf, "layer", None)
        if layer not in (WALL_LAYER, BASE_LAYER):
            continue
        spans: list[tuple[tuple[float, float], tuple[float, float]]] = []
        if ent.dxftype() == "LINE":
            try:
                spans = [(
                    (float(ent.dxf.start.x), float(ent.dxf.start.y)),
                    (float(ent.dxf.end.x), float(ent.dxf.end.y)),
                )]
            except Exception:  # noqa: BLE001
                continue
        elif ent.dxftype() == "LWPOLYLINE":
            pts = _polyline_xy(ent)
            spans = list(zip(pts, pts[1:]))
            if ent.closed and len(pts) <= 5:
                spans.append((pts[-1], pts[0]))
            elif ent.closed and len(pts) > 5:
                continue
        else:
            continue
        for (x0, y0), (x1, y1) in spans:
            dx, dy = abs(x1 - x0), abs(y1 - y0)
            length = math.hypot(dx, dy)
            if length < 250.0 or length > 6000.0:
                continue
            if dx > 40.0 and dy > 40.0:
                continue
            is_h = dy <= dx
            ortho = (y0 + y1) * 0.5 if is_h else (x0 + x1) * 0.5
            a0, a1 = (min(x0, x1), max(x0, x1)) if is_h else (min(y0, y1), max(y0, y1))
            segs.append((is_h, ortho, a0, a1, layer, ent))

    def _touches(is_h: bool, a0: float, a1: float, ortho: float, box) -> bool:
        x0, y0, x1, y1 = box
        if is_h:
            if a1 < x0 - 200.0 or a0 > x1 + 200.0:
                return False
            if ortho < y0 - 400.0 or ortho > y1 + 400.0:
                return False
            return a0 <= x1 + 200.0 and a1 >= x0 - 200.0 and (
                abs(a0 - x0) <= 250.0
                or abs(a0 - x1) <= 250.0
                or abs(a1 - x0) <= 250.0
                or abs(a1 - x1) <= 250.0
                or (a0 <= x0 and a1 >= x1)
            )
        if a1 < y0 - 200.0 or a0 > y1 + 200.0:
            return False
        if ortho < x0 - 400.0 or ortho > x1 + 400.0:
            return False
        return a0 <= y1 + 200.0 and a1 >= y0 - 200.0 and (
            abs(a0 - y0) <= 250.0
            or abs(a0 - y1) <= 250.0
            or abs(a1 - y0) <= 250.0
            or abs(a1 - y1) <= 250.0
            or (a0 <= y0 and a1 >= y1)
        )

    n = 0
    promoted: set[int] = set()
    for box in boxes:
        near = [
            s for s in segs if _touches(s[0], s[2], s[3], s[1], box)
        ]
        for i, a in enumerate(near):
            for b in near[i + 1 :]:
                if a[0] != b[0]:
                    continue
                gap = abs(a[1] - b[1])
                if not (80.0 <= gap <= 350.0):
                    continue
                overlap = min(a[3], b[3]) - max(a[2], b[2])
                if overlap < 300.0:
                    continue
                # 이 쌍의 간격 안에 면이 4개 이상이면 계단·살대다.
                # 같은 면이 두 번 그려진 이중벽은 면이 2개다.
                face_orthos: set[int] = set()
                lo_o, hi_o = min(a[1], b[1]) - 30.0, max(a[1], b[1]) + 30.0
                for s in segs:
                    if s[0] != a[0] or not (lo_o <= s[1] <= hi_o):
                        continue
                    if min(s[3], max(a[3], b[3])) - max(s[2], min(a[2], b[2])) < 300.0:
                        continue
                    face_orthos.add(int(round(s[1] / 20.0)))
                if len(face_orthos) >= 4:
                    continue
                for seg in (a, b):
                    ent = seg[5]
                    if id(ent) in promoted:
                        continue
                    if seg[4] != BASE_LAYER:
                        continue
                    if _segment_in_door(seg[0], seg[1], seg[2], seg[3], doors):
                        continue
                    if _paint_layer(ent, WALL_LAYER, WALL_COLOR):
                        promoted.add(id(ent))
                        n += 1
    return n


def correct_door_leaves_and_flanks(msp) -> tuple[int, int]:
    """문은 내리고, 문 양옆 벽은 올린다.

    X자 개구 안의 선은 BASE. 개구 밖으로 이어진 같은 면의 선은 WALL.
    """
    doors = _x_door_openings(msp)
    if not doors:
        return (0, 0)

    segs: list[tuple[bool, float, float, float, Any]] = []
    diagonals: list[tuple[float, float, float, float, Any]] = []
    for ent in msp:
        layer = getattr(ent.dxf, "layer", None)
        if layer not in (WALL_LAYER, BASE_LAYER):
            continue
        spans: list[tuple[tuple[float, float], tuple[float, float]]] = []
        if ent.dxftype() == "LINE":
            try:
                spans = [(
                    (float(ent.dxf.start.x), float(ent.dxf.start.y)),
                    (float(ent.dxf.end.x), float(ent.dxf.end.y)),
                )]
            except Exception:  # noqa: BLE001
                continue
        elif ent.dxftype() == "LWPOLYLINE":
            pts = _polyline_xy(ent)
            spans = list(zip(pts, pts[1:]))
            if ent.closed and pts:
                spans.append((pts[-1], pts[0]))
        else:
            continue
        axis_only = True
        axis_spans: list[tuple[bool, float, float, float]] = []
        for (x0, y0), (x1, y1) in spans:
            dx, dy = abs(x1 - x0), abs(y1 - y0)
            length = math.hypot(dx, dy)
            if length < 40.0:
                continue
            if dx > 40.0 and dy > 40.0:
                axis_only = False
                diagonals.append((x0, y0, x1, y1, ent))
                continue
            is_h = dy <= dx
            ortho = (y0 + y1) * 0.5 if is_h else (x0 + x1) * 0.5
            a0, a1 = (min(x0, x1), max(x0, x1)) if is_h else (min(y0, y1), max(y0, y1))
            axis_spans.append((is_h, ortho, a0, a1))
        if axis_only:
            for is_h, ortho, a0, a1 in axis_spans:
                segs.append((is_h, ortho, a0, a1, ent))

    n_demote = 0
    n_promote = 0
    seen: set[int] = set()
    for x0, y0, x1, y1, ent in diagonals:
        if id(ent) in seen:
            continue
        ax0, ax1 = min(x0, x1), max(x0, x1)
        ay0, ay1 = min(y0, y1), max(y0, y1)
        for is_h, face0, face1, d0, d1 in doors:
            if is_h:
                if not (ay0 >= min(face0, face1) - 40.0 and ay1 <= max(face0, face1) + 40.0):
                    continue
                if min(ax1, d1) - max(ax0, d0) < 200.0:
                    continue
            else:
                if not (ax0 >= min(face0, face1) - 40.0 and ax1 <= max(face0, face1) + 40.0):
                    continue
                if min(ay1, d1) - max(ay0, d0) < 200.0:
                    continue
            seen.add(id(ent))
            if _paint_layer(ent, BASE_LAYER, BASE_COLOR):
                n_demote += 1
            break

    for is_h, ortho, a0, a1, ent in segs:
        if _segment_in_door(is_h, ortho, a0, a1, doors):
            if id(ent) in seen:
                continue
            # 폴리선 전체가 문 안에 있을 때만 내린다.
            if _paint_layer(ent, BASE_LAYER, BASE_COLOR):
                seen.add(id(ent))
                n_demote += 1

    for is_h, face0, face1, d0, d1 in doors:
        faces = (face0, face1)
        for ent_is_h, ortho, a0, a1, ent in segs:
            if ent_is_h != is_h:
                continue
            if not any(abs(ortho - face) <= 60.0 for face in faces):
                continue
            if _segment_in_door(is_h, ortho, a0, a1, doors):
                continue
            # 개구 바로 옆. 떨어진 가구선은 올리지 않는다.
            gap_lo = d0 - a1
            gap_hi = a0 - d1
            if not ((-40.0 <= gap_lo <= 80.0) or (-40.0 <= gap_hi <= 80.0)):
                continue
            if a1 - a0 < 80.0 or a1 - a0 > 4000.0:
                continue
            if id(ent) in seen:
                continue
            if _paint_layer(ent, WALL_LAYER, WALL_COLOR):
                seen.add(id(ent))
                n_promote += 1
    return n_demote, n_promote


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


def _seat_door_wall_orthos(segs: list[AxisSeg]) -> set[float]:
    """의자 사이를 지나고 문 스윙 안에 있는 긴 세로선의 x.

    더 바깥에 실제 벽이 있을 때만. 문틀이 된 바깥 벽은 넣지 않는다.
    벽두께 반대면도 같은 벽으로 포함한다.
    """
    cached = getattr(_seat_door_wall_orthos, "_cache", None)
    if cached is not None and cached[0] is segs:
        return cached[1]
    longs = [p for p in segs if p.is_v and p.length >= 6000.0 and p.entity is not None]
    chairs = [c for c in segs if c.is_v and 300.0 <= c.length <= 1000.0]
    hinges: list[tuple[float, float, float]] = []
    msp = None
    for s in longs:
        try:
            msp = s.entity.doc.modelspace()
            break
        except Exception:  # noqa: BLE001
            continue
    if msp is not None:
        for e in msp:
            if e.dxftype() != "ARC":
                continue
            r = float(e.dxf.radius)
            if 650.0 <= r <= 1400.0:
                c = e.dxf.center
                hinges.append((float(c.x), float(c.y), r))
    faces: list[float] = []
    for s in longs:
        if not _has_parallel_pair(s, longs, thick_min=40.0, thick_max=400.0):
            continue
        if not any(
            s.along0 - 800.0 <= hy <= s.along1 + 800.0 and abs(hx - s.ortho) < r + 80.0
            for hx, hy, r in hinges
        ):
            continue
        chair_l = 0
        chair_r = 0
        buckets: set[int] = set()
        for c in chairs:
            if abs(c.ortho - s.ortho) > 1200.0:
                continue
            ov = min(s.along1, c.along1) - max(s.along0, c.along0)
            if ov < 200.0:
                continue
            buckets.add(round(c.ortho / 80.0))
            if c.ortho < s.ortho:
                chair_l += 1
            else:
                chair_r += 1
        if len(buckets) < 8:
            continue
        # 의자 가장자리의 문 벽은 남긴다. 의자 양쪽을 가르는 선만 뺀다.
        if min(chair_l, chair_r) < 2:
            continue
        outward = 1.0 if chair_l >= chair_r else -1.0
        if any(
            p is not s
            and _has_parallel_pair(p, longs, thick_min=40.0, thick_max=400.0)
            and 1500.0 <= (p.ortho - s.ortho) * outward <= 6000.0
            for p in longs
        ):
            faces.append(s.ortho)
    orthos = set(faces)
    for s in longs:
        if any(40.0 <= abs(s.ortho - face) <= 400.0 for face in faces):
            orthos.add(s.ortho)
    _seat_door_wall_orthos._cache = (segs, orthos)  # type: ignore[attr-defined]
    return orthos


def _is_seat_door_wall(s: AxisSeg, segs: list[AxisSeg]) -> bool:
    if not s.is_v or s.length < 6000.0:
        return False
    return any(abs(s.ortho - ortho) <= 1.0 for ortho in _seat_door_wall_orthos(segs))


def _near_seat_door_wall(is_v: bool, ortho: float, segs: list[AxisSeg]) -> bool:
    if not is_v:
        return False
    return any(abs(ortho - banned) <= 450.0 for banned in _seat_door_wall_orthos(segs))


def _nearest_hall_end_walls(
    segs: list[AxisSeg],
    lx: float,
    ly: float,
    *,
    search_mm: float = 30000.0,
    long_min_mm: float = 8000.0,
    clear_mm: float = 6000.0,
) -> tuple[float | None, float | None]:
    """라벨에서 clear_mm 이상 떨어진 최근접 상·하 이중선 벽 y."""
    near = [
        s
        for s in segs
        if s.is_h
        and s.length >= long_min_mm
        and abs((s.along0 + s.along1) * 0.5 - lx) <= search_mm
        and abs(s.ortho - ly) <= search_mm
    ]
    along_pad = 5000.0
    bot_y: float | None = None
    top_y: float | None = None
    for s in near:
        if not (s.along0 - along_pad <= lx <= s.along1 + along_pad):
            continue
        if not _has_parallel_pair(s, near):
            continue
        if s.ortho <= ly - clear_mm:
            if bot_y is None or s.ortho > bot_y:
                bot_y = s.ortho
        elif s.ortho >= ly + clear_mm:
            if top_y is None or s.ortho < top_y:
                top_y = s.ortho
    return bot_y, top_y


def collect_open_hall_regions(
    msp,
    segs: list[AxisSeg],
) -> list[tuple[float, float, str, float, float, float | None, float | None, float | None, float | None]]:
    """(lx, ly, label, cx, band_mm, left_wall_x, right_wall_x, bot_wall_y, top_wall_y).

    cx = 홀 좌·우 외곽 중점(없으면 라벨 x).
    band_mm = 중점에서 중앙 통로 demote 반경.
    """
    regions: list[
        tuple[float, float, str, float, float, float | None, float | None, float | None, float | None]
    ] = []
    seen: list[tuple[float, float]] = []
    for lx, ly, name in find_open_hall_labels(msp):
        if any(abs(lx - sx) < 16000 and abs(ly - sy) < 12000 for sx, sy in seen):
            continue
        left_x, right_x = _nearest_hall_side_walls(segs, lx, ly)
        bot_y, top_y = _nearest_hall_end_walls(segs, lx, ly)
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
        regions.append((lx, ly, name, cx, band, left_x, right_x, bot_y, top_y))
    return regions


def _collinear_span(
    s: AxisSeg,
    peers: list[AxisSeg],
    *,
    ortho_tol: float = 80.0,
    gap_mm: float = 1500.0,
    bridge_x: float | None = None,
    bridge_gap: float = 8000.0,
) -> tuple[float, float]:
    """s와 같은 축 조각을 한 런으로 잇는다. (along0, along1).

    문 개구 정도는 gap_mm. 홀 중심을 지나는 보이드(≤ bridge_gap)만 더 넓게 잇는다.
    """
    intervals: list[tuple[float, float]] = [(s.along0, s.along1)]
    for p in peers:
        if p.is_h != s.is_h or abs(p.ortho - s.ortho) > ortho_tol:
            continue
        intervals.append((p.along0, p.along1))
    intervals.sort()
    merged: list[list[float]] = []
    for a0, a1 in intervals:
        if not merged:
            merged.append([a0, a1])
            continue
        sep = a0 - merged[-1][1]
        bridge = (
            bridge_x is not None
            and sep <= bridge_gap
            and merged[-1][1] <= bridge_x <= a0
        )
        if sep <= gap_mm or bridge:
            merged[-1][1] = max(merged[-1][1], a1)
        else:
            merged.append([a0, a1])
    for a0, a1 in merged:
        if s.along1 >= a0 and s.along0 <= a1:
            return a0, a1
    return s.along0, s.along1


_stage_encl_cache: dict[int, list[tuple[bool, float, float, float]]] = {}


def _stage_enclosure_runs(
    msp,
    halls: list[
        tuple[float, float, str, float, float, float | None, float | None, float | None, float | None]
    ],
    segs: list[AxisSeg],
) -> list[tuple[bool, float, float, float]]:
    """무대 3면(뒤·양옆) 이중선. (is_h, ortho, along0, along1).

    정면은 객석 쪽으로 열려 있다. 라벨에서 객석 반대편의 긴 이중선이 뒤벽이고,
    그 양 끝에서 객석 쪽으로 짧게 나온 이중선이 측면이다.
    """
    key = id(msp)
    hit = _stage_encl_cache.get(key)
    if hit is not None:
        return hit
    runs: list[tuple[bool, float, float, float]] = []
    verts = [s for s in segs if s.is_v and s.length >= 1500.0]
    hors = [s for s in segs if s.is_h and s.length >= 1200.0]
    for sx, sy in _stage_label_points(msp):
        hall = None
        for h in halls:
            left_x, right_x = h[5], h[6]
            if left_x is None or right_x is None:
                continue
            if left_x + 1200.0 <= sx <= right_x - 1200.0:
                hall = h
                break
        if hall is None:
            continue
        lx, ly = hall[0], hall[1]
        # 객석이 무대 위·아래에 있으면 뒤벽은 가로 이중선이다. 정면(객석 쪽)은 열어둠.
        if abs(ly - sy) > max(abs(lx - sx) * 1.5, 4000.0):
            aud_y = 1.0 if ly >= sy else -1.0
            hcands: list[AxisSeg] = []
            for s in hors:
                if s.length < 2500.0:
                    continue
                back = aud_y * (sy - s.ortho)
                if not (500.0 <= back <= 2200.0):
                    continue
                if not (s.along0 - 2000.0 <= sx <= s.along1 + 2000.0):
                    continue
                if not _has_parallel_pair(s, hors):
                    continue
                hcands.append(s)
            if hcands:
                orthos_h: set[float] = set()
                for s in hors:
                    if s.length < 2000.0:
                        continue
                    back = aud_y * (sy - s.ortho)
                    if not (400.0 <= back <= 2400.0):
                        continue
                    if not any(
                        abs(s.ortho - c.ortho) <= 40.0
                        or 50.0 <= abs(s.ortho - c.ortho) <= 420.0
                        for c in hcands
                    ):
                        continue
                    ov = max(
                        min(s.along1, c.along1) - max(s.along0, c.along0) for c in hcands
                    )
                    if ov < 1500.0:
                        continue
                    orthos_h.add(s.ortho)
                for o in orthos_h:
                    pieces = [
                        s
                        for s in hors
                        if abs(s.ortho - o) <= 40.0
                        and s.length >= 2000.0
                        and s.along0 - 2000.0 <= sx <= s.along1 + 2000.0
                    ]
                    if not pieces:
                        continue
                    runs.append(
                        (True, o, min(s.along0 for s in pieces), max(s.along1 for s in pieces))
                    )
                continue
        cx = hall[3]
        aud = 1.0 if cx >= sx else -1.0
        cands: list[AxisSeg] = []
        for s in verts:
            if s.length < 8000.0:
                continue
            back = aud * (sx - s.ortho)
            if not (300.0 <= back <= 4000.0):
                continue
            if not (s.along0 - 2000.0 <= sy <= s.along1 + 2000.0):
                continue
            if not _has_parallel_pair(s, verts):
                continue
            cands.append(s)
        if not cands:
            continue
        best = min(cands, key=lambda s: abs(s.ortho - sx))
        orthos = {best.ortho}
        for s in verts:
            if s.length < 4000.0:
                continue
            d = abs(s.ortho - best.ortho)
            if not (d <= 40.0 or 50.0 <= d <= 420.0):
                continue
            ov = min(best.along1, s.along1) - max(best.along0, s.along0)
            if ov < 2000.0:
                continue
            orthos.add(s.ortho)
        span0, span1 = 1e18, -1e18
        for s in verts:
            if s.length < 4000.0:
                continue
            if not any(abs(s.ortho - o) <= 40.0 for o in orthos):
                continue
            if not (s.along0 <= sy + 3000.0 and s.along1 >= sy - 3000.0):
                continue
            span0 = min(span0, s.along0)
            span1 = max(span1, s.along1)
        if span1 <= span0:
            continue
        for o in orthos:
            runs.append((False, o, span0, span1))
        back_x = sum(orthos) / len(orthos)
        for end_y in (span0, span1):
            wings = []
            for s in hors:
                if abs(s.ortho - end_y) > 500.0:
                    continue
                if not (1200.0 <= s.length <= 8000.0):
                    continue
                mid = (s.along0 + s.along1) * 0.5
                if aud * (mid - back_x) < 200.0:
                    continue
                if min(abs(s.along0 - back_x), abs(s.along1 - back_x)) > 2500.0:
                    continue
                back_end = s.along0 if aud > 0 else s.along1
                if aud * (back_x - back_end) > 1500.0:
                    continue
                if not _has_parallel_pair(s, hors):
                    continue
                wings.append(s)
            if not wings:
                continue
            seed = wings[0].ortho
            w_orthos = {s.ortho for s in wings if abs(s.ortho - seed) <= 420.0}
            a0 = min(s.along0 for s in wings if abs(s.ortho - seed) <= 420.0)
            a1 = max(s.along1 for s in wings if abs(s.ortho - seed) <= 420.0)
            for o in w_orthos:
                runs.append((True, o, a0, a1))
    _stage_encl_cache[key] = runs
    return runs


def _is_stage_enclosure_seg(
    s: AxisSeg,
    halls: list[
        tuple[float, float, str, float, float, float | None, float | None, float | None, float | None]
    ],
    all_segs: list[AxisSeg] | None,
) -> bool:
    """무대 뒤벽·양옆 이중선이면 참. 객석 쪽 정면은 아니다."""
    if s.entity is None:
        return False
    try:
        msp = s.entity.doc.modelspace()
    except Exception:  # noqa: BLE001
        return False
    for is_h, ortho, a0, a1 in _stage_enclosure_runs(msp, halls, all_segs or []):
        if s.is_h != is_h or abs(s.ortho - ortho) > 60.0:
            continue
        ov = min(s.along1, a1) - max(s.along0, a0)
        if ov >= min(600.0, s.length * 0.5):
            return True
    return False


def promote_stage_enclosure_walls(msp, segs: list[AxisSeg]) -> list[AxisSeg]:
    """무대 3면 이중선 BASE → WALL. 정면(객석 쪽)은 열어둠."""
    halls = collect_open_hall_regions(msp, segs)
    _stage_enclosure_runs(msp, halls, segs)
    return [
        s
        for s in segs
        if s.layer == BASE_LAYER and _is_stage_enclosure_seg(s, halls, segs)
    ]


def _is_open_hall_interior_seg(
    s: AxisSeg,
    halls: list[
        tuple[float, float, str, float, float, float | None, float | None, float | None, float | None]
    ],
    all_segs: list[AxisSeg] | None = None,
    *,
    min_len_mm: float = 8000.0,
) -> bool:
    """강당 중앙을 가로·세로로 가로지르는 긴 선인가 (객석 통로/열)."""
    # 무대 뒤·양옆 이중선은 벽이다. 정면만 열린다.
    if _is_stage_enclosure_seg(s, halls, all_segs):
        return False
    # 계단실 외곽은 강당 안쪽이어도 벽이다. 입구는 선이 없다.
    if s.entity is not None:
        try:
            stair_msp = s.entity.doc.modelspace()
        except Exception:  # noqa: BLE001
            stair_msp = None
        if stair_msp is not None and _on_stair_shell(
            s, _stair_bboxes(stair_msp, all_segs or [])
        ):
            return False
    # 가로선은 보이드로 쪼개진 4 m 조각도 합쳐 판정한다.
    if s.is_h:
        if s.length < min(min_len_mm, 4000.0):
            return False
        return _is_open_hall_interior_h_seg(s, halls, all_segs, min_len_mm=min_len_mm)
    if s.is_v and _is_stage_front_double_v(s, halls, all_segs):
        return True
    if s.is_v and _is_open_hall_center_double_v(s, halls, all_segs):
        return True
    if s.length < min_len_mm:
        return False
    if s.is_v:
        return _is_open_hall_interior_v_seg(s, halls, all_segs, min_len_mm=min_len_mm)
    return False


_stage_label_cache: dict[int, list[tuple[float, float]]] = {}


def _stage_label_points(msp) -> list[tuple[float, float]]:
    """「무대」라벨. 준비실 문자는 제외."""
    key = id(msp)
    hit = _stage_label_cache.get(key)
    if hit is not None:
        return hit
    pts: list[tuple[float, float]] = []
    for x, y, s in _iter_text_labels(msp):
        t = re.sub(r"\s+", "", s)
        if "무대" in t and "준비" not in t:
            pts.append((x, y))
    _stage_label_cache[key] = pts
    return pts


def _is_stage_front_double_v(
    s: AxisSeg,
    halls: list[
        tuple[float, float, str, float, float, float | None, float | None, float | None, float | None]
    ],
    all_segs: list[AxisSeg] | None,
) -> bool:
    """무대와 객석 사이의 이중선은 벽이 아니다.

    무대 앞에 벽이 있고 그 뒤에 의자가 있는 배치는 성립하지 않는다.
    이중선이라도 무대 라벨에서 6.7 m 안, 홀 외곽에서 떨어진 세로 쌍은 내부다.
    계단실 벽은 그보다 멀어서 이 반경에 들어오지 않는다.
    """
    if not s.is_v or s.length < 2000.0 or s.entity is None:
        return False
    try:
        stages = _stage_label_points(s.entity.doc.modelspace())
    except Exception:  # noqa: BLE001
        return False
    if not stages:
        return False
    mx = (s.x0 + s.x1) * 0.5
    my = (s.y0 + s.y1) * 0.5
    peers = [p for p in (all_segs or []) if p.is_v and p.length >= 1000.0]
    has_pair = False
    for p in peers:
        d = abs(p.ortho - s.ortho)
        if not (50.0 <= d <= 420.0):
            continue
        ov = min(s.along1, p.along1) - max(s.along0, p.along0)
        if ov >= max(400.0, 0.25 * min(s.length, p.length)):
            has_pair = True
            break
    if not has_pair:
        return False
    # 계단실 외곽은 무대 근처여도 벽이다. 입구는 선 자체가 없다.
    try:
        stair_msp = s.entity.doc.modelspace()
    except Exception:  # noqa: BLE001
        stair_msp = None
    if stair_msp is not None and _on_stair_shell(
        s, _stair_bboxes(stair_msp, all_segs or [])
    ):
        return False
    # 계단 트레드는 무대에서 5.5 m보다 멀다. 가까이 겹친 이중선은 벽두께라 여기서 지우지 않는다.
    for sx, sy in stages:
        if math.hypot(mx - sx, my - sy) > 6700.0:
            continue
        for _lx, _ly, _name, _cx, _band, left_x, right_x, bot_y, top_y in halls:
            if left_x is None or right_x is None:
                continue
            if not (left_x + 1200.0 <= sx <= right_x - 1200.0):
                continue
            if bot_y is not None and sy < bot_y - 2000.0:
                continue
            if top_y is not None and sy > top_y + 2000.0:
                continue
            if abs(mx - left_x) <= 800.0 or abs(mx - right_x) <= 800.0:
                continue
            if not (left_x + 1200.0 <= mx <= right_x - 1200.0):
                continue
            return True
    return False


def _is_open_hall_center_double_v(
    s: AxisSeg,
    halls: list[
        tuple[float, float, str, float, float, float | None, float | None, float | None, float | None]
    ],
    all_segs: list[AxisSeg] | None,
) -> bool:
    """홀 중심의 짧은 이중선(긴 선 옆 3–4 m 조각)은 벽이 아니다.

    양쪽이 2.8 m를 넘으면 walldetector가 벽으로 올린다.
    길이 8 m 미만이라 기존 세로 demote에는 안 걸린다.
    2 m 안에 평행 장축이 4개 이상이면 계단 트레드로 보고 여기서는 건드리지 않는다.
    """
    if not s.is_v or s.length < 1000.0:
        return False
    peers = [
        p
        for p in (all_segs or [])
        if p.is_v and p.layer == s.layer and p.length >= 1000.0
    ]
    long_peers = [p for p in peers if p.length >= 2500.0]
    for _lx, ly, _name, cx, band, left_x, right_x, _bot, _top in halls:
        if left_x is None or right_x is None:
            continue
        mx = (s.x0 + s.x1) * 0.5
        if abs(mx - left_x) <= 800.0 or abs(mx - right_x) <= 800.0:
            continue
        if abs(mx - cx) > band:
            continue
        # 라벨보다 무대·객석 쪽. 위쪽 화장실 벽은 제외.
        if s.along0 >= ly:
            continue
        has_pair = False
        for p in peers:
            d = abs(p.ortho - s.ortho)
            if not (50.0 <= d <= 420.0):
                continue
            ov = min(s.along1, p.along1) - max(s.along0, p.along0)
            if ov >= max(400.0, 0.25 * min(s.length, p.length)):
                has_pair = True
                break
        if not has_pair:
            continue
        # 계단 트레드(2 m 안 평행 장축 ≥4)는 제외
        orthos = {round(s.ortho / 50.0)}
        for p in long_peers:
            if abs(p.ortho - s.ortho) > 2000.0:
                continue
            ov = min(s.along1, p.along1) - max(s.along0, p.along0)
            if ov >= 400.0:
                orthos.add(round(p.ortho / 50.0))
        if len(orthos) >= 4:
            continue
        # 같은 축의 긴 선(≥8 m)에 3 m 이내로 이어진 조각만.
        # 긴 선 자체는 짝이 없어 벽이 아니고, 그 위 짧은 이중선만 오검출이다.
        partner_orthos = [s.ortho]
        for p in peers:
            d = abs(p.ortho - s.ortho)
            if 50.0 <= d <= 420.0:
                partner_orthos.append(p.ortho)
        continues_long = False
        for p in all_segs or []:
            if not p.is_v or p.length < 8000.0:
                continue
            if not any(abs(p.ortho - ox) <= 80.0 for ox in partner_orthos):
                continue
            if s.along1 < p.along0:
                sep = p.along0 - s.along1
            elif p.along1 < s.along0:
                sep = s.along0 - p.along1
            else:
                sep = 0.0
            # 긴 선 아래 조각, 긴 벽 한가운데에 겹치는 짧은 선은 제외.
            # 긴 선 끝에서 객석 쪽으로 벗어난 조각만.
            if s.along1 <= p.along0 + 200.0:
                continue
            if s.along0 < p.along1 - 500.0:
                continue
            if sep <= 3000.0:
                continues_long = True
                break
        if not continues_long:
            continue
        # 준비실 오른쪽처럼 위·아래가 가로벽에 물린 이중선은 실의 면이다.
        if _closed_room_side_v(s, all_segs or []):
            return False
        return True
    return False


def _is_open_hall_interior_v_seg(
    s: AxisSeg,
    halls: list[
        tuple[float, float, str, float, float, float | None, float | None, float | None, float | None]
    ],
    all_segs: list[AxisSeg] | None,
    *,
    min_len_mm: float,
) -> bool:
    """강당 중앙 통로를 가로지르는 긴 수직선인가."""
    mx = (s.x0 + s.x1) * 0.5
    peers = [x for x in (all_segs or []) if x.is_v and x.length >= min_len_mm]
    for lx, ly, _name, cx, band, left_x, right_x, _bot_y, _top_y in halls:
        if left_x is not None and abs(mx - left_x) <= 500.0:
            continue
        if right_x is not None and abs(mx - right_x) <= 500.0:
            continue
        half = None
        if left_x is not None and right_x is not None:
            half = min(cx - left_x, right_x - cx)
        near_label = abs(mx - lx) <= min(5000.0, band)
        near_center = abs(mx - cx) <= band
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
        # 외곽에서 1.5–6 m 안쪽을 객석 깊이로 내려가는 장축은 측면 벽이 아니다.
        chase = False
        # 양쪽 외곽에서 6 m보다 깊어도, 라벨보다 객석 쪽을 길게 가르면 실벽이 아니다.
        # 행사창고처럼 구역명만 있고 의자가 이어지면 설명용 선이다.
        seating_divider = False
        if left_x is not None and right_x is not None and s.length >= min_len_mm:
            inset_l = mx - left_x
            inset_r = right_x - mx
            in_chase = (1500.0 <= inset_l <= 6000.0) or (1500.0 <= inset_r <= 6000.0)
            spans_seating = s.along0 <= ly - 6000.0 and s.along1 >= ly - 15000.0
            chase = in_chase and spans_seating and s.along0 < ly
            seating_divider = (
                inset_l > 6000.0
                and inset_r > 6000.0
                and s.along0 < ly
                and s.along1 <= ly + 2000.0
                and s.along0 <= ly - 8000.0
            )
        if not (near_label or near_center or wide_single or chase or seating_divider):
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


def _closed_room_side_v(s: AxisSeg, segs: list[AxisSeg]) -> bool:
    """양쪽 끝이 가로벽 끝에 물린 이중선이면 실의 한 면이다."""
    if not s.is_v or not segs or s.length < 1500.0:
        return False
    same = [p for p in segs if p.is_v and p.length >= 800.0]
    if not _has_parallel_pair(s, same, thick_min=80.0, thick_max=320.0):
        return False
    # 계단·문짝처럼 비슷한 세로선이 여럿이면 실의 한 면이 아니다.
    orthos = {round(s.ortho / 50.0)}
    for p in same:
        if abs(p.length - s.length) > 800.0 or abs(p.ortho - s.ortho) > 2500.0:
            continue
        ov = min(s.along1, p.along1) - max(s.along0, p.along0)
        if ov < min(s.length, p.length) * 0.5:
            continue
        orthos.add(round(p.ortho / 50.0))
    if len(orthos) > 2:
        return False
    crosses = [p for p in segs if p.is_h and p.length >= 800.0]

    def _hits(along: float) -> bool:
        return any(
            abs(c.ortho - along) <= 250.0
            and (
                abs(c.along0 - s.ortho) <= 250.0 or abs(c.along1 - s.ortho) <= 250.0
            )
            for c in crosses
        )

    return _hits(s.along0) and _hits(s.along1)


def _closed_room_side(s: AxisSeg, segs: list[AxisSeg]) -> bool:
    """양쪽 끝이 긴 수직벽에 물린 이중선이면 실의 한 면이다."""
    if not s.is_h or not segs:
        return False
    same = [p for p in segs if p.is_h and p.length >= 800.0]
    if not _has_parallel_pair(s, same, thick_min=80.0, thick_max=320.0):
        return False
    crosses = [p for p in segs if p.is_v and p.length >= 2000.0]

    def _hits(along: float) -> bool:
        return any(
            abs(c.ortho - along) <= 200.0
            and c.along0 - 200.0 <= s.ortho <= c.along1 + 200.0
            for c in crosses
        )

    return _hits(s.along0) and _hits(s.along1)


def _is_open_hall_interior_h_seg(
    s: AxisSeg,
    halls: list[
        tuple[float, float, str, float, float, float | None, float | None, float | None, float | None]
    ],
    all_segs: list[AxisSeg] | None,
    *,
    min_len_mm: float,
) -> bool:
    """강당 객석을 가로지르는 긴 수평선인가 — 의자 위 벽 오검출."""
    my = s.ortho
    # 같은 레이어만 잇는다. 간격의 BASE가 좌·우 실벽을 한 줄로 붙여 지우지 않게.
    peers = [
        x
        for x in (all_segs or [])
        if x.is_h and x.length >= 1500.0 and x.layer == s.layer
    ]
    for lx, ly, _name, cx, band, left_x, right_x, bot_y, top_y in halls:
        # 상·하 외곽 벽은 유지
        if bot_y is not None and abs(my - bot_y) <= 500.0:
            continue
        if top_y is not None and abs(my - top_y) <= 500.0:
            continue
        # 홀 폭을 가로질러야 함. 보이드(≤8 m)로 끊긴 조각은 한 런으로 합쳐 잰다.
        if left_x is not None and right_x is not None:
            hall_w = right_x - left_x
            # 라벨보다 무대 쪽만 중심 보이드를 잇는다. 위쪽 화장실·창고 벽은 분리된 채로 둔다.
            bridge_x = cx if my <= ly else None
            a0, a1 = _collinear_span(s, peers, bridge_x=bridge_x)
            ov = min(a1, right_x - 2500.0) - max(a0, left_x + 2500.0)
            need = max(min_len_mm * 0.55, hall_w * 0.35)
            inside = min(a1, right_x - 800.0) - max(a0, left_x + 800.0)
            covers_center = a0 <= cx <= a1 and inside >= 4000.0
            # 짧은 중심 관통은 라벨 위 화장실·창고 대역이 아니라 객석 쪽만
            if covers_center and s.length < min_len_mm and my > ly + 2500.0:
                covers_center = False
            if ov < need and not covers_center:
                continue
            near_outer = (
                abs(my - bot_y) <= 4000.0 if bot_y is not None else False
            ) or (abs(my - top_y) <= 4000.0 if top_y is not None else False)
        else:
            # 외곽 미검출 시 라벨 주변 가로 장축
            if abs((s.along0 + s.along1) * 0.5 - lx) > band + 8000.0:
                continue
            near_outer = False
        # 상·하 외곽 근처(복도·실 경계)는 유지
        if near_outer:
            continue
        # 라벨~객석 대역 (라벨 위·아래)
        half_h = None
        cy = ly
        if bot_y is not None and top_y is not None:
            cy = (bot_y + top_y) * 0.5
            half_h = min(cy - bot_y, top_y - cy)
            y_band = min(half_h * 0.55, max(half_h - 3500.0, 5000.0))
            y_band = max(5000.0, min(y_band, 12000.0))
            near_center = abs(my - cy) <= y_band
        else:
            near_center = abs(my - ly) <= 12000.0
        near_label = abs(my - ly) <= 12000.0
        wide_single = False
        if (
            half_h is not None
            and peers
            and not _has_parallel_pair(s, peers)
        ):
            wide_single = abs(my - cy) <= min(half_h * 0.7, 14000.0)
        if not (near_label or near_center or wide_single):
            continue
        # X 방향으로 라벨/홀 중심 근처를 가로지름
        if s.along1 < lx - 20000.0 or s.along0 > lx + 20000.0:
            continue
        if left_x is not None and right_x is not None:
            if s.along1 < left_x + 1000.0 or s.along0 > right_x - 1000.0:
                continue
            # 준비실처럼 홀을 마주 보는 실의 한 면. 양쪽이 실 측벽에 물린 이중선은 객석 줄이 아니다.
            hall_w = right_x - left_x
            if (
                s.length < hall_w * 0.55
                and _closed_room_side(s, all_segs or [])
            ):
                continue
        return True
    return False


def demote_open_hall_center_walls(
    msp,
    segs: list[AxisSeg],
    *,
    min_len_mm: float = 8000.0,
) -> set[int]:
    """강당·오픈홀 중앙을 가로지르는 긴 WALL demote.

    「강당」실명 라벨/홀 중심 대역을 관통하는 장축(수직 통로·수평 객석열)은
    벽이 될 수 없다. protect_corridor 보다 우선.
    """
    halls = collect_open_hall_regions(msp, segs)
    if not halls:
        return set()
    stair_boxes = _stair_bboxes(msp, segs)
    demote: set[int] = set()
    for s in segs:
        if s.layer != WALL_LAYER:
            continue
        if _is_open_hall_interior_seg(s, halls, segs, min_len_mm=min_len_mm):
            demote.add(id(s.entity))
        elif _is_seat_door_wall(s, segs):
            demote.add(id(s.entity))
            for p in segs:
                if (
                    p.layer != WALL_LAYER
                    or not p.is_v
                    or p.length > 2200.0
                    or abs(p.ortho - s.ortho) > 450.0
                ):
                    continue
                if p.along1 < s.along0 - 1500.0 or p.along0 > s.along1 + 1500.0:
                    continue
                demote.add(id(p.entity))

    # 중앙 오검출이 벽두께 쌍으로 잡힌 경우, 짝도 함께 제거 (V·H)
    for is_v in (True, False):
        wall_axis = [
            s
            for s in segs
            if s.layer == WALL_LAYER
            and ((is_v and s.is_v) or ((not is_v) and s.is_h))
            and s.length >= min_len_mm
        ]
        demoted_segs = [s for s in wall_axis if id(s.entity) in demote]
        for s in wall_axis:
            if id(s.entity) in demote:
                continue
            ortho = s.ortho
            on_outer = False
            for _lx, _ly, _n, _cx, _band, left_x, right_x, bot_y, top_y in halls:
                if is_v:
                    if (left_x is not None and abs(ortho - left_x) <= 500.0) or (
                        right_x is not None and abs(ortho - right_x) <= 500.0
                    ):
                        on_outer = True
                        break
                else:
                    if (bot_y is not None and abs(ortho - bot_y) <= 500.0) or (
                        top_y is not None and abs(ortho - top_y) <= 500.0
                    ):
                        on_outer = True
                        break
            if on_outer:
                continue
            if is_v:
                in_pair_zone = any(
                    abs(ortho - cx) <= band * 1.25
                    for _lx, _ly, _n, cx, band, _l, _r, _b, _t in halls
                )
            else:
                in_pair_zone = any(
                    (
                        abs(ortho - ly) <= 14000.0
                        or (
                            bot_y is not None
                            and top_y is not None
                            and bot_y + 3500.0 <= ortho <= top_y - 3500.0
                        )
                    )
                    for _lx, ly, _n, _cx, _band, _l, _r, bot_y, top_y in halls
                )
            if not in_pair_zone or not demoted_segs:
                continue
            if _on_stair_shell(s, stair_boxes):
                continue
            if _has_parallel_pair(s, demoted_segs, thick_min=40.0, thick_max=900.0):
                demote.add(id(s.entity))

    # 같은 런의 짧은 잔여 조각(보이드·문 개구로 끊긴 3–4 m)도 함께 제거
    fragments = [
        s
        for s in segs
        if s.layer == WALL_LAYER and s.length >= 1000.0 and id(s.entity) not in demote
    ]
    demoted_segs = [
        s for s in segs if s.layer == WALL_LAYER and id(s.entity) in demote
    ]
    for s in fragments:
        ortho = s.ortho
        on_outer = False
        for _lx, _ly, _n, _cx, _band, left_x, right_x, bot_y, top_y in halls:
            if s.is_v:
                if (left_x is not None and abs(ortho - left_x) <= 500.0) or (
                    right_x is not None and abs(ortho - right_x) <= 500.0
                ):
                    on_outer = True
                    break
            elif (bot_y is not None and abs(ortho - bot_y) <= 500.0) or (
                top_y is not None and abs(ortho - top_y) <= 500.0
            ):
                on_outer = True
                break
        if on_outer or _on_stair_shell(s, stair_boxes) or _closed_room_side_v(s, segs):
            continue
        for d in demoted_segs:
            if d.is_h != s.is_h or abs(d.ortho - s.ortho) > 80.0:
                continue
            if s.along1 < d.along0:
                sep = d.along0 - s.along1
            elif d.along1 < s.along0:
                sep = s.along0 - d.along1
            else:
                sep = 0.0
            if sep <= 2500.0:
                demote.add(id(s.entity))
                break

    # 무대 앞 세로 이중선의 짧은 짝(8 m 미만)도 함께 제거. 가로 리턴은 계단 쪽으로 이어지므로 남긴다.
    demoted_segs = [
        s for s in segs if s.layer == WALL_LAYER and id(s.entity) in demote and s.is_v
    ]
    for s in segs:
        if (
            s.layer != WALL_LAYER
            or not s.is_v
            or s.length < 1000.0
            or id(s.entity) in demote
        ):
            continue
        ortho = s.ortho
        on_outer = False
        for _lx, _ly, _n, _cx, _band, left_x, right_x, bot_y, top_y in halls:
            if s.is_v:
                if (left_x is not None and abs(ortho - left_x) <= 500.0) or (
                    right_x is not None and abs(ortho - right_x) <= 500.0
                ):
                    on_outer = True
                    break
            elif (bot_y is not None and abs(ortho - bot_y) <= 500.0) or (
                top_y is not None and abs(ortho - top_y) <= 500.0
            ):
                on_outer = True
                break
        if on_outer or _on_stair_shell(s, stair_boxes) or _closed_room_side_v(s, segs):
            continue
        if _has_parallel_pair(s, demoted_segs, thick_min=50.0, thick_max=420.0):
            demote.add(id(s.entity))

    # 홀 장축 바로 위의 실벽 옆에 붙은 얇은 문짝은 다시 뺀다.
    anchors = []
    for s in segs:
        if (
            s.layer != WALL_LAYER
            or id(s.entity) in demote
            or s.length > 4500.0
            or not _closed_room_side_v(s, segs)
        ):
            continue
        for p in segs:
            if not p.is_v or p.length < 8000.0 or abs(p.ortho - s.ortho) > 80.0:
                continue
            if s.along0 >= p.along1:
                sep = s.along0 - p.along1
            elif p.along0 >= s.along1:
                sep = p.along0 - s.along1
            else:
                continue
            if 200.0 <= sep <= 2000.0:
                anchors.append(s)
                break
    anchor_orthos = [s.ortho for s in anchors]
    for s in segs:
        if (
            s.layer != WALL_LAYER
            or id(s.entity) in demote
            or s.length > 1200.0
            or not anchor_orthos
        ):
            continue
        if not _has_parallel_pair(s, segs, thick_min=15.0, thick_max=50.0):
            continue
        if any(
            abs(s.ortho - a.ortho) <= 350.0
            and min(s.along1, a.along1) - max(s.along0, a.along0) >= 400.0
            for a in segs
            if a.layer == WALL_LAYER
            and a.length >= 2500.0
            and any(
                abs(a.ortho - ax.ortho) <= 80.0
                and (
                    0.0 <= a.along0 - ax.along1 <= 2000.0
                    or 0.0 <= ax.along0 - a.along1 <= 2000.0
                    or a is ax
                )
                for ax in anchors
            )
        ):
            demote.add(id(s.entity))
    _demote_main_hall_stage_face(msp, segs, demote)
    return demote


def _demote_main_hall_stage_face(msp, segs: list[AxisSeg], demote: set[int]) -> None:
    """중강당 왼쪽(무대쪽) 세로 이중선과 객석 위 짧은 선은 벽이 아니다.

    라벨 높이를 지나고, 더 바깥에 긴 벽이 있는 가장 가까운 이중선만 뺀다.
    건물 외벽은 바깥쪽이 비어 있으므로 남긴다.
    """
    labels = [(x, y) for x, y, s in _iter_text_labels(msp) if "중강당" in s]
    if not labels:
        return
    # 위치는 BASE가 남아 있어도 잡는다. WALL만 뺀다.
    verts = [s for s in segs if s.is_v and s.length >= 8000.0]
    face_spans: list[tuple[float, float, float, float]] = []
    for lx, ly in labels:
        cands = []
        for s in verts:
            if not (lx - 20000.0 < s.ortho < lx - 5000.0):
                continue
            if not (s.along0 <= ly <= s.along1):
                continue
            has_mate = any(
                80.0 <= abs(p.ortho - s.ortho) <= 400.0
                and min(s.along1, p.along1) - max(s.along0, p.along0) >= 6000.0
                for p in verts
            )
            if not has_mate:
                continue
            outside = any(
                1500.0 <= s.ortho - q.ortho <= 12000.0
                and min(s.along1, q.along1) - max(s.along0, q.along0) >= 4000.0
                for q in verts
            )
            if outside:
                cands.append(s)
        if not cands:
            continue
        face_x = max(s.ortho for s in cands)
        y0 = min(s.along0 for s in cands if abs(s.ortho - face_x) <= 400.0)
        y1 = max(s.along1 for s in cands if abs(s.ortho - face_x) <= 400.0)
        face_spans.append((face_x, y0, y1, lx))
        for s in verts:
            if not (s.along0 <= ly <= s.along1):
                continue
            if abs(s.ortho - face_x) <= 400.0 or any(
                80.0 <= abs(s.ortho - c.ortho) <= 400.0
                for c in cands
                if abs(c.ortho - face_x) <= 400.0
            ):
                demote.add(id(s.entity))
    if not face_spans:
        return
    shorts = [
        s
        for s in iter_axis_segs(msp, min_len_mm=200.0)
        if s.is_h and s.layer == WALL_LAYER and 250.0 <= s.length <= 800.0
    ]
    for s in shorts:
        mx = (s.along0 + s.along1) * 0.5
        my = s.ortho
        if not any(
            y0 + 500.0 < my < y1 - 500.0 and face_x + 1500.0 < mx < lx + 4000.0
            for face_x, y0, y1, lx in face_spans
        ):
            continue
        near = sum(
            1
            for p in shorts
            if abs(p.ortho - my) < 800.0
            and abs((p.along0 + p.along1) * 0.5 - mx) < 800.0
        )
        if near >= 2:
            demote.add(id(s.entity))


def filter_promote_away_from_open_halls(
    promote: list[AxisSeg],
    msp,
    segs: list[AxisSeg],
    *,
    min_len_mm: float = 8000.0,
) -> list[AxisSeg]:
    """강당 중앙 통로·객석열 promote 후보 제거."""
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


def promote_corridor_door_flanks(
    segs: list[AxisSeg],
    *,
    corridor_run_min_mm: float = 4000.0,
    door_gap_min_mm: float = 700.0,
    door_gap_max_mm: float = 2200.0,
    flank_min_mm: float = 300.0,
    flank_max_mm: float = 6000.0,
    abut_mm: float = 450.0,
) -> list[AxisSeg]:
    """복도 벽 문 개구의 양옆(좌·우/상·하) BASE → WALL.

    한쪽만 WALL이고 반대 모서리가 회색인 경우를 메운다.
    """
    wall = [s for s in segs if s.layer == WALL_LAYER]
    base = [s for s in segs if s.layer == BASE_LAYER]
    out: list[AxisSeg] = []
    seen: set[tuple[float, float, float, float]] = set()

    # ortho bucket → segs (H/V 각각)
    by_ortho: dict[tuple[bool, int], list[AxisSeg]] = defaultdict(list)
    for s in wall + base:
        by_ortho[(s.is_h, _bucket(s.ortho))].append(s)

    # 인접 ortho를 합쳐 벽선 후보 구성
    processed: set[tuple[bool, int]] = set()
    for is_h, ortho_b in list(by_ortho.keys()):
        key0 = (is_h, ortho_b)
        if key0 in processed:
            continue
        # ±100 mm 묶음
        bundle_keys = [
            (is_h, ortho_b + d * 50)
            for d in (-2, -1, 0, 1, 2)
            if (is_h, ortho_b + d * 50) in by_ortho
        ]
        for bk in bundle_keys:
            processed.add(bk)
        bundle = []
        for bk in bundle_keys:
            bundle.extend(by_ortho[bk])
        wall_iv = sorted(
            [(s.along0, s.along1) for s in bundle if s.layer == WALL_LAYER],
            key=lambda t: t[0],
        )
        if not wall_iv:
            continue
        # merge WALL intervals
        merged: list[list[float]] = []
        for a0, a1 in wall_iv:
            if not merged or a0 > merged[-1][1] + 80:
                merged.append([a0, a1])
            else:
                merged[-1][1] = max(merged[-1][1], a1)
        wall_span = sum(m[1] - m[0] for m in merged)
        along_lo = min(s.along0 for s in bundle)
        along_hi = max(s.along1 for s in bundle)
        total_span = along_hi - along_lo
        # 복도 장축이거나, 복도 인접 실 벽(일부만 WALL)도 허용
        if wall_span < 800.0:
            continue
        if wall_span < corridor_run_min_mm and total_span < corridor_run_min_mm:
            continue
        # door-sized gaps: (a) WALL–WALL 사이 (b) WALL 끝 ↔ BASE 플랭크
        gaps: list[tuple[float, float]] = []
        for i in range(len(merged) - 1):
            g0, g1 = merged[i][1], merged[i + 1][0]
            gap = g1 - g0
            if door_gap_min_mm <= gap <= door_gap_max_mm:
                gaps.append((g0, g1))
        base_cands = [
            s
            for s in bundle
            if s.layer == BASE_LAYER and flank_min_mm <= s.length <= flank_max_mm
        ]
        # WALL 런 끝 너머 문 간격만큼 떨어진 BASE = 반대쪽 문틀
        for m0, m1 in merged:
            for s in base_cands:
                gap_l = m0 - s.along1
                if door_gap_min_mm <= gap_l <= door_gap_max_mm and s.along0 < s.along1 - 80:
                    gaps.append((s.along1, m0))
                gap_r = s.along0 - m1
                if door_gap_min_mm <= gap_r <= door_gap_max_mm and s.along1 > s.along0 + 80:
                    gaps.append((m1, s.along0))
        # dedupe gaps
        uniq_gaps: list[tuple[float, float]] = []
        for g0, g1 in sorted(gaps):
            if uniq_gaps and abs(g0 - uniq_gaps[-1][0]) < 50 and abs(g1 - uniq_gaps[-1][1]) < 50:
                continue
            uniq_gaps.append((g0, g1))
        if not uniq_gaps:
            continue
        for g0, g1 in uniq_gaps:
            # 이미 한쪽이 WALL인 문만 대상 (반대쪽 BASE 보완)
            has_wall_left = any(abs(m[1] - g0) <= abut_mm for m in merged)
            has_wall_right = any(abs(m[0] - g1) <= abut_mm for m in merged)
            if not (has_wall_left or has_wall_right):
                continue
            for s in base_cands:
                # 문 왼쪽/아래: 세그먼트가 gap 시작(g0)에 맞닿음
                left_abut = abs(s.along1 - g0) <= abut_mm and s.along0 < g0 - 50
                # 문 오른쪽/위: 세그먼트가 gap 끝(g1)에 맞닿음
                right_abut = abs(s.along0 - g1) <= abut_mm and s.along1 > g1 + 50
                if not (left_abut or right_abut):
                    continue
                # 왼쪽 BASE는 오른쪽에 WALL이 있을 때, 오른쪽 BASE는 왼쪽에 WALL이 있을 때
                if left_abut and not has_wall_right:
                    continue
                if right_abut and not has_wall_left:
                    continue
                if not _has_parallel_pair(
                    s, base, thick_min=15.0, thick_max=500.0
                ):
                    if s.length > 3500.0:
                        continue
                # 계단 디딤판은 문 옆 벽이 아니다.
                if _is_stair_tread_seg(s, segs) or _is_stair_nosing_seg(s, segs):
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
                # 같은 계단의 UP/DN만 묶는다. 홀 안 다른 방향 화살표(6–8 m)는 별개.
                if any(math.hypot(x2 - gx, y2 - gy) < 4500.0 for gx, gy, _ in group):
                    group.append((x2, y2, lab2))
                    used[j] = True
                    changed = True
        # 방향만 있는 UP 화살표는 계단실이 아니다. DN이 있는 클러스터만.
        if not any(g[2].startswith("DN") for g in group):
            continue
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
        and _has_parallel_pair(s, segs)
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
        return None
    xmin, xmax = left[0], right[0]
    if bot and top:
        ymin, ymax = bot[0], top[0]
    else:
        ymin, ymax = cy - 5500.0, cy + 5500.0
    w, h = xmax - xmin, ymax - ymin
    # 계단실 규모: 너무 크면(복도·홀) 거부, 너무 작으면 패딩
    if w < 2000.0 or w > 12000.0 or h < 3000.0 or h > 18000.0:
        return None
    # 좌·우 벽이 추정 바닥보다 더 내려가면 그 끝까지를 외곽으로 본다.
    side = [
        s
        for s in near
        if s.is_v
        and s.length >= 4000.0
        and (
            abs(s.ortho - xmin) <= 500.0 or abs(s.ortho - xmax) <= 500.0
        )
        and s.along0 <= cy <= s.along1
    ]
    if side:
        y0 = min(s.along0 for s in side)
        y1 = max(s.along1 for s in side)
        if ymin - 4000.0 <= y0 < ymin:
            ymin = y0
        if ymax < y1 <= ymax + 4000.0:
            ymax = y1
    return {"xmin": xmin, "ymin": ymin, "xmax": xmax, "ymax": ymax}


_stair_bbox_cache: dict[int, list[dict[str, float]]] = {}


def _stair_bboxes(msp, segs: list[AxisSeg]) -> list[dict[str, float]]:
    key = id(msp)
    hit = _stair_bbox_cache.get(key)
    if hit is not None:
        return hit
    boxes: list[dict[str, float]] = []
    for cx, cy, _labs in find_stair_cores(msp):
        bb = _estimate_stair_bbox(segs, cx, cy)
        if bb:
            boxes.append(bb)
    _stair_bbox_cache[key] = boxes
    return boxes


def _is_closed_box_side(s: AxisSeg, segs: list[AxisSeg]) -> bool:
    """양 끝이 마주보는 변으로 닫힌 사각이면 기둥이다."""
    if not (450.0 <= s.length <= 1600.0):
        return False
    crosses = [c for c in segs if c.is_h != s.is_h and c.length <= 1700.0]
    for p in segs:
        if p.is_h != s.is_h or abs(p.length - s.length) > 400.0:
            continue
        gap = abs(p.ortho - s.ortho)
        if not (450.0 <= gap <= 1500.0):
            continue
        ov = min(s.along1, p.along1) - max(s.along0, p.along0)
        if ov < min(s.length, p.length) * 0.7:
            continue
        lo, hi = min(s.ortho, p.ortho), max(s.ortho, p.ortho)

        def _spans(end: float) -> bool:
            return any(
                abs(c.ortho - end) <= 120.0
                and c.along0 - 120.0 <= lo
                and c.along1 + 120.0 >= hi
                for c in crosses
            )

        if _spans(s.along0) and _spans(s.along1):
            return True
    return False


def _is_stair_tread_seg(s: AxisSeg, segs: list[AxisSeg]) -> bool:
    """UP/DN 근처에서 길게 늘어선 짧은 평행선은 계단 디딤판이다.

    기둥 사각처럼 폭이 좁거나 닫힌 사각은 디딤이 아니다.
    """
    if s.entity is None or not (400.0 <= s.length <= 1800.0):
        return False
    if _is_closed_box_side(s, segs):
        return False
    try:
        msp = s.entity.doc.modelspace()
    except Exception:  # noqa: BLE001
        return False
    labels = getattr(_is_stair_tread_seg, "_labels", None)
    key = id(msp)
    if labels is None or getattr(_is_stair_tread_seg, "_key", None) != key:
        labels = []
        for x, y, text in _iter_text_labels(msp):
            u = text.strip().upper()
            if u in ("UP", "DN", "DOWN") or (
                len(u) <= 6 and (u.startswith("UP") or u.startswith("DN"))
            ):
                labels.append((x, y))
        _is_stair_tread_seg._labels = labels  # type: ignore[attr-defined]
        _is_stair_tread_seg._key = key  # type: ignore[attr-defined]
    mx = (s.x0 + s.x1) * 0.5
    my = (s.y0 + s.y1) * 0.5
    if not any(math.hypot(mx - x, my - y) <= 11000.0 for x, y in labels):
        return False
    orthos: set[int] = set()
    for p in segs:
        if p.is_h != s.is_h or not (400.0 <= p.length <= 1800.0):
            continue
        if abs(p.length - s.length) > 400.0 or abs(p.ortho - s.ortho) > 2800.0:
            continue
        ov = min(s.along1, p.along1) - max(s.along0, p.along0)
        if ov < min(s.length, p.length) * 0.6:
            continue
        orthos.add(round(p.ortho / 40.0))
    if len(orthos) < 5:
        return False
    # 기둥 사각은 폭이 3.5 m 에 못 미친다. 계단 비행만 넘긴다.
    return (max(orthos) - min(orthos)) * 40.0 >= 3500.0


def _is_stair_nosing_seg(s: AxisSeg, segs: list[AxisSeg]) -> bool:
    """디딤 끝을 잇는 600 mm 안쪽 선만 계단 표시로 본다."""
    if not (550.0 <= s.length <= 650.0):
        return False
    hits = 0
    for p in segs:
        if p.is_h == s.is_h:
            continue
        if min(abs(p.along0 - s.ortho), abs(p.along1 - s.ortho)) > 40.0:
            continue
        if not (s.along0 - 80.0 <= p.ortho <= s.along1 + 80.0):
            continue
        if _is_stair_tread_seg(p, segs):
            hits += 1
            if hits >= 2:
                return True
    return False


def demote_stair_treads(segs: list[AxisSeg]) -> set[int]:
    """계단 디딤판 WALL 은 벽이 아니다."""
    out: set[int] = set()
    for s in segs:
        if s.layer != WALL_LAYER or s.entity is None:
            continue
        if _is_stair_tread_seg(s, segs) or _is_stair_nosing_seg(s, segs):
            out.add(id(s.entity))
    return out


def _on_stair_shell(
    s: AxisSeg,
    boxes: list[dict[str, float]],
    *,
    edge_tol_mm: float = 700.0,
) -> bool:
    """계단실 외곽(입구 개구는 선이 없으므로 여기 안 들어온다)."""
    for bb in boxes:
        if s.is_v and (
            abs(s.ortho - bb["xmin"]) <= edge_tol_mm
            or abs(s.ortho - bb["xmax"]) <= edge_tol_mm
        ):
            ov = min(s.along1, bb["ymax"]) - max(s.along0, bb["ymin"])
            if ov >= 800.0:
                return True
        if s.is_h and (
            abs(s.ortho - bb["ymin"]) <= edge_tol_mm
            or abs(s.ortho - bb["ymax"]) <= edge_tol_mm
        ):
            ov = min(s.along1, bb["xmax"]) - max(s.along0, bb["xmin"])
            if ov >= 800.0:
                return True
    return False


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
        # 트레드 다발이 아닌 내부 이중선(계단 아래 칸막이). 입구는 선이 없어 비워 둔다.
        for s in base:
            if not (1800.0 <= s.length <= 6000.0):
                continue
            if not _has_parallel_pair(s, base):
                continue
            mx = (s.x0 + s.x1) * 0.5
            my = (s.y0 + s.y1) * 0.5
            if not (
                bbox["xmin"] + 300.0 <= mx <= bbox["xmax"] - 300.0
                and bbox["ymin"] + 300.0 <= my <= bbox["ymax"] - 300.0
            ):
                continue
            orthos = {round(s.ortho / 50.0)}
            for p in base:
                if p.is_h != s.is_h or p.length < 1000.0:
                    continue
                if abs(p.ortho - s.ortho) > 1500.0:
                    continue
                pov = min(s.along1, p.along1) - max(s.along0, p.along0)
                if pov >= 400.0:
                    orthos.add(round(p.ortho / 50.0))
            if len(orthos) >= 4:
                continue
            if s.is_h:
                iov = min(s.along1, bbox["xmax"]) - max(s.along0, bbox["xmin"])
                need = (bbox["xmax"] - bbox["xmin"]) * 0.35
            else:
                iov = min(s.along1, bbox["ymax"]) - max(s.along0, bbox["ymin"])
                need = (bbox["ymax"] - bbox["ymin"]) * 0.35
            if iov < need:
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


def _is_elevator_door_return_seg(
    s: AxisSeg,
    shaft: dict[str, float],
    bank: dict[str, float | int],
) -> bool:
    """복도↔엘리베이터 문 개구로 꺾이는 짧은 리턴(어깨) — 벽.

    복도 문 양옆과 같이, 문틀에서 직각으로 꺾여 들어가는 짧은 선.
    door_faces_x → 수평 리턴(개구 상·하 잼 모서리); door_faces_y → 수직 리턴.
    """
    door_x = bool(bank.get("door_faces_x", 1))
    sign = _shaft_door_sign(shaft, bank)
    cx, cy = shaft["cx"], shaft["cy"]
    hw, hh = shaft["half_w"], shaft["half_h"]
    if door_x:
        if not s.is_h:
            return False
        if not (70.0 <= s.length <= 900.0):
            return False
        # 문 개구 높이 대역(샤프트 반고) 안, 중앙(문짝) 제외 → 상·하 잼 모서리
        if not (cy - hh - 250.0 <= s.ortho <= cy + hh + 250.0):
            return False
        if abs(s.ortho - cy) < hh * 0.18:
            return False
        if abs(s.ortho - cy) > hh * 1.15:
            return False
        door_face = cx + sign * hw
        touches_face = (
            abs(s.along0 - door_face) <= 550.0
            or abs(s.along1 - door_face) <= 550.0
            or (
                min(s.along0, s.along1) - 120.0
                <= door_face
                <= max(s.along0, s.along1) + 120.0
            )
        )
        if not touches_face:
            # 포털 두꺼운 수직 박스(문면보다 로비쪽)에 붙은 어깨도 허용
            portal_lo = door_face - 50.0
            portal_hi = door_face + sign * 600.0
            if sign < 0:
                portal_lo, portal_hi = door_face + sign * 600.0, door_face + 50.0
            mid = (s.along0 + s.along1) * 0.5
            if not (portal_lo <= mid <= portal_hi):
                return False
        mid = (s.along0 + s.along1) * 0.5
        toward = (mid - cx) * sign
        return hw - 550.0 <= toward <= hw + 1400.0
    if not s.is_v:
        return False
    if not (70.0 <= s.length <= 900.0):
        return False
    if not (cx - hw - 250.0 <= s.ortho <= cx + hw + 250.0):
        return False
    if abs(s.ortho - cx) < hw * 0.18:
        return False
    if abs(s.ortho - cx) > hw * 1.15:
        return False
    door_face = cy + sign * hh
    touches_face = (
        abs(s.along0 - door_face) <= 550.0
        or abs(s.along1 - door_face) <= 550.0
        or (
            min(s.along0, s.along1) - 120.0
            <= door_face
            <= max(s.along0, s.along1) + 120.0
        )
    )
    if not touches_face:
        portal_lo = door_face - 50.0
        portal_hi = door_face + sign * 600.0
        if sign < 0:
            portal_lo, portal_hi = door_face + sign * 600.0, door_face + 50.0
        mid = (s.along0 + s.along1) * 0.5
        if not (portal_lo <= mid <= portal_hi):
            return False
    mid = (s.along0 + s.along1) * 0.5
    toward = (mid - cy) * sign
    return hh - 550.0 <= toward <= hh + 1400.0


def _is_elevator_corridor_turn_seg(
    s: AxisSeg,
    shaft: dict[str, float],
    bank: dict[str, float | int],
) -> bool:
    """복도 장축 벽이 엘리베이터 입구 alcove로 꺾이는 L자 다리 — 벽.

    문 어깨(return)에서 복도 벽까지 이어지는 입구 측면 이중선.
    (화살표가 가리키는 복도↔엘리베이터 모서리)
    """
    door_x = bool(bank.get("door_faces_x", 1))
    sign = _shaft_door_sign(shaft, bank)
    cx, cy = shaft["cx"], shaft["cy"]
    hw, hh = shaft["half_w"], shaft["half_h"]
    if door_x:
        if not s.is_v:
            return False
        if not (700.0 <= s.length <= 2800.0):
            return False
        toward = (s.ortho - cx) * sign
        if not (hw - 400.0 <= toward <= hw + 1600.0):
            return False
        top = cy + hh
        bot = cy - hh
        # 상단: 문 개구 상단 대역에서 시작해 샤프트 밖으로 복도까지
        if (
            s.along1 >= top + 150.0
            and cy + hh * 0.20 <= s.along0 <= top + 120.0
        ):
            return True
        # 하단: 복도에서 올라와 문 개구 하단 대역으로
        if (
            s.along0 <= bot - 150.0
            and bot - 120.0 <= s.along1 <= cy - hh * 0.20
        ):
            return True
        return False
    if not s.is_h:
        return False
    if not (700.0 <= s.length <= 2800.0):
        return False
    toward = (s.ortho - cy) * sign
    if not (hh - 400.0 <= toward <= hh + 1600.0):
        return False
    right = cx + hw
    left = cx - hw
    if (
        s.along1 >= right + 150.0
        and cx + hw * 0.20 <= s.along0 <= right + 120.0
    ):
        return True
    if (
        s.along0 <= left - 150.0
        and left - 120.0 <= s.along1 <= cx - hw * 0.20
    ):
        return True
    return False


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
    """엘리베이터: 입구 잼·문 어깨(꺾임)·복도 꺾임·문사이 + 뱅크 주위 외곽 → WALL. 문 개구 제외."""
    banks = find_elevator_banks(msp)
    if not banks:
        return []
    # 짧은 문 어깨(70–300 mm)도 포함
    base = [
        s
        for s in segs
        if s.layer == BASE_LAYER and 70.0 <= s.length <= max_len_mm
    ]
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
            is_return = any(
                _is_elevator_door_return_seg(s, sh, bank) for sh in shafts
            )
            is_turn = any(
                _is_elevator_corridor_turn_seg(s, sh, bank) for sh in shafts
            )
            is_inter = any(
                _is_elevator_door_interstitial_seg(s, sh, bank, shafts) for sh in shafts
            )
            is_peri = _is_elevator_perimeter_seg(s, box)
            if not (is_jamb or is_return or is_turn or is_inter or is_peri):
                continue
            # 짧은 어깨/잼·복도꺾임은 min_len 미만이어도 허용; 외곽은 기존 min_len
            if (
                is_peri
                and not (is_jamb or is_return or is_turn or is_inter)
                and s.length < min_len_mm
            ):
                continue
            # 전고 문 개구만 제외 (잼·어깨·복도꺾임은 유지)
            if (
                not is_jamb
                and not is_return
                and not is_turn
                and not is_inter
                and any(_is_elevator_door_opening_seg(s, sh, bank) for sh in shafts)
            ):
                continue
            # 외곽으로만 걸린 문짝(두께 약 40mm, 길이 1.1m)은 벽이 아니다.
            if (
                is_peri
                and not (is_jamb or is_return or is_turn or is_inter)
                and s.length <= 1600.0
                and not _has_parallel_pair(s, base, thick_min=80.0, thick_max=500.0)
            ):
                continue
            if not _has_parallel_pair(s, base, thick_min=15.0, thick_max=500.0):
                if not is_jamb and not is_return and not is_turn and s.length > 2500.0:
                    continue
            # 입구 잼·어깨·복도꺾임·문사이는 이중선 한 쪽만 WALL이어도 나머지 BASE를 꼭 승격
            if not is_jamb and not is_return and not is_turn and not is_inter:
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
            # 입구 잼·어깨·복도꺾임·문사이 칸막이·주위 외곽은 유지
            if any(_is_elevator_door_jamb_seg(s, sh, bank) for sh in shafts):
                continue
            if any(_is_elevator_door_return_seg(s, sh, bank) for sh in shafts):
                continue
            if any(_is_elevator_corridor_turn_seg(s, sh, bank) for sh in shafts):
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
            if any(_is_elevator_door_return_seg(s, sh, bank) for sh in shafts):
                keep = True
                break
            if any(_is_elevator_corridor_turn_seg(s, sh, bank) for sh in shafts):
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
    """입구 잼·문 어깨·복도 꺾임·문사이 칸막이·뱅크 주위 외곽 WALL demote 금지."""
    banks = find_elevator_banks(msp)
    if not banks:
        return set()
    protect: set[int] = set()
    wall = [s for s in segs if s.layer == WALL_LAYER]
    for bank in banks:
        shafts: list[dict[str, float]] = bank.get("shafts") or []  # type: ignore[assignment]
        if not shafts:
            continue
        box = _elevator_bank_box(bank, shafts, segs)
        for s in wall:
            if any(_is_elevator_door_jamb_seg(s, sh, bank) for sh in shafts):
                protect.add(id(s.entity))
                continue
            if any(_is_elevator_door_return_seg(s, sh, bank) for sh in shafts):
                protect.add(id(s.entity))
                continue
            if any(_is_elevator_corridor_turn_seg(s, sh, bank) for sh in shafts):
                protect.add(id(s.entity))
                continue
            if any(
                _is_elevator_door_interstitial_seg(s, sh, bank, shafts) for sh in shafts
            ):
                protect.add(id(s.entity))
                continue
            if s.length < min_len_mm:
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


def promote_butt_partitions(
    segs: list[AxisSeg],
    *,
    min_len_mm: float = 2400.0,
    max_len_mm: float = 2790.0,
    end_tol_mm: float = 80.0,
    wall_min_mm: float = 2500.0,
) -> list[AxisSeg]:
    """양끝이 긴 벽에 맞닿은 짧은 이중선은 칸막이다.

    면이 2.8m 에 못 미치면 검출기가 벽으로 두지 않는다.
    사무실·상담실 사이처럼 위·아래 벽에 물린 이중선만 벽으로 둔다.
    """
    wall = [s for s in segs if s.layer == WALL_LAYER and s.length >= wall_min_mm]
    base = [s for s in segs if s.layer == BASE_LAYER]
    h_wall = [s for s in wall if s.is_h]
    v_wall = [s for s in wall if s.is_v]
    out: list[AxisSeg] = []
    seen: set[tuple[float, float, float, float]] = set()

    def _hits(targets: list[AxisSeg], along_end: float, ortho: float) -> bool:
        return any(
            abs(w.ortho - along_end) <= end_tol_mm
            and w.along0 - 200.0 <= ortho <= w.along1 + 200.0
            for w in targets
        )

    def _full_mate(s: AxisSeg) -> bool:
        for o in base:
            if o.is_h != s.is_h:
                continue
            if id(o.entity) == id(s.entity) and abs(o.ortho - s.ortho) < 1.0:
                continue
            d = abs(o.ortho - s.ortho)
            if not (120.0 <= d <= 280.0):
                continue
            ov = min(s.along1, o.along1) - max(s.along0, o.along0)
            if ov >= s.length * 0.85 and abs(o.length - s.length) <= 200.0:
                return True
        return False

    for s in base:
        if not (min_len_mm <= s.length <= max_len_mm):
            continue
        if not _full_mate(s):
            continue
        # 같은 자리의 면이 이미 벽이면 다시 올리지 않는다.
        same = [
            (w.along0, w.along1, w.length)
            for w in wall
            if w.is_h == s.is_h and abs(w.ortho - s.ortho) <= 40.0
        ]
        if _covered(same, s.along0, s.along1):
            continue
        targets = h_wall if s.is_v else v_wall
        if not (
            _hits(targets, s.along0, s.ortho) and _hits(targets, s.along1, s.ortho)
        ):
            continue
        key = (round(s.x0, 1), round(s.y0, 1), round(s.x1, 1), round(s.y1, 1))
        if key in seen:
            continue
        seen.add(key)
        out.append(s)
    return out


def promote_room_corner_returns(
    segs: list[AxisSeg],
    *,
    min_len_mm: float = 1600.0,
    max_len_mm: float = 2600.0,
    end_tol_mm: float = 250.0,
    wall_min_mm: float = 2500.0,
) -> list[AxisSeg]:
    """긴 벽에 물린 짧은 L자 꺾임은 실의 모서리 벽이다.

    면이 2.8m에 못 미치면 검출기가 빼 둔다. 창고#3 오른쪽 위처럼
    한쪽은 긴 벽, 다른 쪽은 짝이 되는 꺾임에 닿는 이중선만 올린다.
    """
    walls = [s for s in segs if s.layer == WALL_LAYER and s.length >= wall_min_mm]
    base = [
        s
        for s in segs
        if s.layer == BASE_LAYER and min_len_mm <= s.length <= max_len_mm
    ]

    def _pair_orthos(s: AxisSeg) -> set[int]:
        orthos = {round(s.ortho / 40.0)}
        for o in base:
            if o.is_h != s.is_h or o is s:
                continue
            d = abs(o.ortho - s.ortho)
            if not (80.0 <= d <= 320.0):
                continue
            ov = min(s.along1, o.along1) - max(s.along0, o.along0)
            if ov >= min(s.length, o.length) * 0.7 and abs(o.length - s.length) <= 400.0:
                orthos.add(round(o.ortho / 40.0))
        return orthos

    # 문틀·멀리언처럼 평행선이 3개 이상이면 모서리가 아니다.
    cands = [s for s in base if len(_pair_orthos(s)) == 2]

    def _hits_wall(s: AxisSeg, end_along: float) -> bool:
        return any(
            w.is_h != s.is_h
            and abs(w.ortho - end_along) <= end_tol_mm
            and w.along0 - end_tol_mm <= s.ortho <= w.along1 + end_tol_mm
            for w in walls
        )

    def _legs_at(s: AxisSeg, end_along: float) -> list[AxisSeg]:
        return [
            o
            for o in cands
            if o.is_h != s.is_h
            and abs(o.ortho - end_along) <= end_tol_mm
            and o.along0 - end_tol_mm <= s.ortho <= o.along1 + end_tol_mm
        ]

    out: list[AxisSeg] = []
    seen: set[tuple[float, float, float, float]] = set()
    for s in cands:
        if _is_stair_tread_seg(s, segs) or _is_stair_nosing_seg(s, segs):
            continue
        ends = (s.along0, s.along1)
        wall_ends = [e for e in ends if _hits_wall(s, e)]
        if len(wall_ends) != 1:
            continue
        other = s.along1 if wall_ends[0] == s.along0 else s.along0
        legs = _legs_at(s, other)
        if not legs:
            continue
        if not any(
            _hits_wall(leg, leg.along0) or _hits_wall(leg, leg.along1) for leg in legs
        ):
            continue
        key = (round(s.x0, 1), round(s.y0, 1), round(s.x1, 1), round(s.y1, 1))
        if key in seen:
            continue
        seen.add(key)
        out.append(s)
    return out


def promote_wall_mate_faces(
    segs: list[AxisSeg],
    *,
    thick_min_mm: float = 150.0,
    thick_max_mm: float = 320.0,
) -> list[AxisSeg]:
    """이미 벽인 면이 짧은 모서리 앞에서 끊기고, 회색 이중선만 모서리까지 이어진 경우.

    행사용품 위쪽처럼 왼쪽 끝만 회색인 벽을 그 모서리까지 올린다.
    """
    walls = [s for s in segs if s.layer == WALL_LAYER]
    base = [s for s in segs if s.layer == BASE_LAYER and 2500.0 <= s.length <= 8000.0]
    out: list[AxisSeg] = []
    seen: set[tuple[float, float, float, float]] = set()

    def _short_butt(s: AxisSeg, end: float) -> bool:
        for w in walls:
            if w.is_h == s.is_h or not (400.0 <= w.length <= 1600.0):
                continue
            if abs(w.ortho - end) > 80.0:
                continue
            if w.along0 - 80.0 <= s.ortho <= w.along1 + 80.0:
                return True
        return False

    for s in base:
        covered = 0.0
        for w in walls:
            if w.is_h != s.is_h or abs(w.ortho - s.ortho) > 40.0:
                continue
            covered = max(
                covered,
                min(s.along1, w.along1) - max(s.along0, w.along0),
            )
        if covered >= s.length * 0.8:
            continue
        hit = False
        for w in walls:
            if w.is_h != s.is_h or w.length < 2000.0:
                continue
            if not (thick_min_mm <= abs(w.ortho - s.ortho) <= thick_max_mm):
                continue
            ov = min(s.along1, w.along1) - max(s.along0, w.along0)
            if ov < 1500.0:
                continue
            if s.along0 < w.along0 - 800.0 and s.along1 <= w.along1 + 250.0:
                end = s.along0
                extra = w.along0 - s.along0
            elif s.along1 > w.along1 + 800.0 and s.along0 >= w.along0 - 250.0:
                end = s.along1
                extra = s.along1 - w.along1
            else:
                continue
            if 800.0 <= extra <= 2000.0 and _short_butt(s, end):
                hit = True
                break
        if not hit:
            continue
        key = (round(s.x0, 1), round(s.y0, 1), round(s.x1, 1), round(s.y1, 1))
        if key in seen:
            continue
        seen.add(key)
        out.append(s)
    return out


def find_double_door_openings(
    msp,
) -> list[tuple[bool, float, float, float]]:
    """양개 문. 스윙 ARC 중심 두 개가 벽 방향으로 약 2배 반지름만큼 떨어져 있다.

    반환: (세로문인가, 벽 ortho, 개구 along0, along1). 문짝 구간이며 벽이 아니다.
    """
    centers: list[list[float]] = []
    for e in msp:
        if e.dxftype() != "ARC":
            continue
        try:
            r = float(e.dxf.radius)
            cx, cy = float(e.dxf.center.x), float(e.dxf.center.y)
            sweep = (float(e.dxf.end_angle) - float(e.dxf.start_angle)) % 360.0
        except Exception:  # noqa: BLE001
            continue
        if not (650.0 <= r <= 1600.0) or not (20.0 <= sweep <= 200.0):
            continue
        for c in centers:
            if math.hypot(cx - c[0], cy - c[1]) < 30.0:
                break
        else:
            centers.append([cx, cy, r])
    # 간격이 지름에 가장 가까운 쌍부터 묶는다.
    # 더 먼 이웃(옆 외여닫이)을 고르면 큰문 한가운데가 벽으로 남는다.
    cands: list[tuple[float, int, int]] = []
    for i, a in enumerate(centers):
        for j in range(i + 1, len(centers)):
            b = centers[j]
            if abs(a[2] - b[2]) > 25.0:
                continue
            dx, dy = abs(a[0] - b[0]), abs(a[1] - b[1])
            if dx > 40.0 and dy > 40.0:
                continue
            dist = math.hypot(dx, dy)
            target = a[2] + b[2]
            if not (0.8 * target <= dist <= 1.2 * target):
                continue
            cands.append((abs(dist - target), i, j))
    cands.sort()
    used = [False] * len(centers)
    openings: list[tuple[bool, float, float, float]] = []
    for _score, i, j in cands:
        if used[i] or used[j]:
            continue
        a, b = centers[i], centers[j]
        used[i] = used[j] = True
        is_v = abs(a[0] - b[0]) <= 40.0
        ortho = (a[0] + b[0]) * 0.5 if is_v else (a[1] + b[1]) * 0.5
        if is_v:
            along0, along1 = min(a[1], b[1]), max(a[1], b[1])
        else:
            along0, along1 = min(a[0], b[0]), max(a[0], b[0])
        openings.append((is_v, ortho, along0, along1))
    return openings


def find_single_door_openings(
    msp,
    double_openings: list[tuple[bool, float, float, float]],
) -> tuple[
    list[tuple[bool, float, float, float]],
    list[tuple[bool, float, float, float]],
    list[int],
]:
    """외여닫이 문. 1/4 스윙의 벽 방향이 개구, 수직으로 선 문짝은 벽이 아니다.

    반환: (개구 목록, 문짝 목록, 스윙 방향).
    개구·문짝은 (세로인가, ortho, along0, along1).
    스윙 방향은 개구와 같은 순서다. +1 은 ortho 가 큰 쪽, 0 이면 방향을 쓰지 않는다.
    """
    swings: list[tuple[float, float, float, float, float]] = []
    for e in msp:
        if e.dxftype() != "ARC":
            continue
        try:
            r = float(e.dxf.radius)
            cx, cy = float(e.dxf.center.x), float(e.dxf.center.y)
            sa, ea = float(e.dxf.start_angle), float(e.dxf.end_angle)
        except Exception:  # noqa: BLE001
            continue
        sweep = (ea - sa) % 360.0
        if not (650.0 <= r <= 1400.0) or not (50.0 <= sweep <= 130.0):
            continue
        if _door_hinge_used(cx, cy, double_openings):
            continue
        swings.append((cx, cy, r, sa, ea))

    # 힌지 근처를 지나는 장축 — 문짝이 벽과 나란한 방향.
    # 사무실·상담실처럼 벽이 LWPOLYLINE 인 경우도 포함한다.
    long_h: list[tuple[float, float, float]] = []
    long_v: list[tuple[float, float, float]] = []
    # 문 양옆처럼 1400 mm 보다 짧아도 개구에 맞닿는 면.
    jamb_h: list[tuple[float, float, float]] = []
    jamb_v: list[tuple[float, float, float]] = []
    for e in msp:
        t = e.dxftype()
        pairs: list[tuple[float, float, float, float]] = []
        try:
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
                pairs.extend(
                    (a[0], a[1], b[0], b[1]) for a, b in zip(pts, pts[1:])
                )
                if e.closed and len(pts) >= 2 and pts[0] != pts[-1]:
                    pairs.append((pts[-1][0], pts[-1][1], pts[0][0], pts[0][1]))
        except Exception:  # noqa: BLE001
            continue
        for x0, y0, x1, y1 in pairs:
            dx, dy = abs(x1 - x0), abs(y1 - y0)
            length = math.hypot(dx, dy)
            # 1400: 문 양옆 짧은 벽(창고#2 위·아래)도 호스트. 긴 벽은 중간 힌지도 허용.
            if length < 400.0 or (dx > 80.0 and dy > 80.0):
                continue
            if dy >= dx:
                ortho = (x0 + x1) * 0.5
                span = (ortho, min(y0, y1), max(y0, y1), length)
                jamb_v.append(span)
                if length >= 1400.0:
                    long_v.append(span)
            else:
                ortho = (y0 + y1) * 0.5
                span = (ortho, min(x0, x1), max(x0, x1), length)
                jamb_h.append(span)
                if length >= 1400.0:
                    long_h.append(span)

    def _nearest_wall(
        cx: float,
        cy: float,
        horizontal: bool,
        *,
        min_len: float,
        max_len: float | None = None,
        end_only: bool = False,
    ) -> tuple[float, float, float, float, float] | None:
        """(거리, 벽 ortho, along0, along1, 길이)."""
        best: tuple[float, float, float, float, float] | None = None
        pool = long_h if horizontal else long_v
        for oo, a0, a1, length in pool:
            if length < min_len or (max_len is not None and length >= max_len):
                continue
            if horizontal:
                if not (a0 - 200.0 <= cx <= a1 + 200.0):
                    continue
                dist = abs(cy - oo)
                end_dist = min(abs(cx - a0), abs(cx - a1))
            else:
                if not (a0 - 200.0 <= cy <= a1 + 200.0):
                    continue
                dist = abs(cx - oo)
                end_dist = min(abs(cy - a0), abs(cy - a1))
            if dist > 280.0:
                continue
            if end_only and end_dist > 280.0:
                continue
            if best is None or dist < best[0]:
                best = (dist, oo, a0, a1, length)
        return best

    openings: list[tuple[bool, float, float, float]] = []
    leaves: list[tuple[bool, float, float, float]] = []
    sides: list[int] = []
    seen: set[tuple[int, int, int]] = set()

    def _swing_side(horizontal: bool, host: float, arc_ends: list[tuple[float, float]]) -> int:
        """호가 벽 축에서 열리는 방향. +1 은 좌표가 큰 쪽이다."""
        best = 0.0
        for ex, ey in arc_ends:
            delta = (ey - host) if horizontal else (ex - host)
            if abs(delta) > abs(best):
                best = delta
        if abs(best) < 200.0:
            return 0
        return 1 if best > 0.0 else -1
    for cx, cy, r, sa, ea in swings:
        ends: list[tuple[float, float]] = []
        for ang in (sa, ea):
            rad = math.radians(ang)
            ends.append((cx + r * math.cos(rad), cy + r * math.sin(rad)))
        along: tuple[float, float] | None = None
        perp: tuple[float, float] | None = None
        for ex, ey in ends:
            dx, dy = abs(ex - cx), abs(ey - cy)
            if dx > 0.7 * r and dy < 0.35 * r:
                along = (ex, ey)
            elif dy > 0.7 * r and dx < 0.35 * r:
                perp = (ex, ey)
        if along is None or perp is None:
            continue
        # 가로·세로 끝이 모두 축에 맞으면, 힌지에서 더 가까운 벽이 개구다.
        # 방풍실#5 처럼 왼쪽 벽의 문을 위쪽 벽 개구로 자르지 않는다.
        long_h_hit = _nearest_wall(cx, cy, True, min_len=2500.0)
        long_v_hit = _nearest_wall(cx, cy, False, min_len=2500.0)
        short_h_hit = _nearest_wall(
            cx, cy, True, min_len=1400.0, max_len=2500.0, end_only=True
        )
        short_v_hit = _nearest_wall(
            cx, cy, False, min_len=1400.0, max_len=2500.0, end_only=True
        )

        def _pick(h_hit, v_hit):
            if h_hit is None and v_hit is None:
                return None
            horizontal_hit = v_hit is None or (h_hit is not None and h_hit[0] <= v_hit[0])
            return horizontal_hit, (h_hit if horizontal_hit else v_hit)

        def _opening_on_other_wall(horizontal_wall: bool, ex: float, ey: float) -> bool:
            """반대쪽 끝이 맞닿은 긴 벽 개구 위에 있는지.

            그 벽이 힌지 반대편으로도 이어질 때만 개구다. 모서리에서 끝나는
            벽은 식당창고#3 위쪽 문처럼 접힌 면이 아니다.
            """
            pool = long_v if horizontal_wall else long_h
            for oo, a0, a1, length in pool:
                if length < 2500.0:
                    continue
                if horizontal_wall:
                    if not (abs(ex - oo) <= 280.0 and a0 - 200.0 <= ey <= a1 + 200.0):
                        continue
                    if ey < cy - 50.0 and a1 < cy + 200.0:
                        continue
                    if ey > cy + 50.0 and a0 > cy - 200.0:
                        continue
                    return True
                if not (abs(ey - oo) <= 280.0 and a0 - 200.0 <= ex <= a1 + 200.0):
                    continue
                if ex < cx - 50.0 and a1 < cx + 200.0:
                    continue
                if ex > cx + 50.0 and a0 > cx - 200.0:
                    continue
                return True
            return False

        def _leaf_folds_on(horizontal: bool) -> bool:
            """문짝이 맞닿아 접히는 벽. 그 벽은 개구가 아니다.

            힌지가 그 벽 끝이고, 문짝 끝이 벽 위에 있으며, 다른 끝은
            맞닿은 벽에 있어야 한다. 자기 벽 안의 문(방풍실#5)은 해당 없다.
            """
            pool = long_h if horizontal else long_v
            for oo, a0, a1, length in pool:
                if length < 2500.0:
                    continue
                # 벽이 힌지에서 끝나는 경우만. 문 개구를 지나 이어진 벽은 그 문의 호스트다.
                if horizontal:
                    if min(abs(cx - a0), abs(cx - a1)) > 150.0 or abs(cy - oo) > 280.0:
                        continue
                elif min(abs(cy - a0), abs(cy - a1)) > 150.0 or abs(cx - oo) > 280.0:
                    continue
                for ex, ey in ends:
                    on_wall = (
                        abs(ey - oo) <= 80.0 and a0 + 200.0 < ex < a1 - 200.0
                        if horizontal
                        else abs(ex - oo) <= 80.0 and a0 + 200.0 < ey < a1 - 200.0
                    )
                    if not on_wall:
                        continue
                    other = next((p for p in ends if p != (ex, ey)), None)
                    if other and _opening_on_other_wall(horizontal, other[0], other[1]):
                        return True
            return False

        # 모서리 힌지: 더 가까운 벽이 문짝이 접히는 벽이면, 개구는 다른 쪽 벽이다.
        # 락커룸(여) 오른쪽 문처럼 세로 개구를 가로벽으로 자르지 않는다.
        chosen = None
        if long_h_hit is not None and long_v_hit is not None:
            fold_h, fold_v = _leaf_folds_on(True), _leaf_folds_on(False)
            if fold_h and not fold_v:
                along, perp = perp, along
                chosen = (False, long_v_hit[1])
            elif fold_v and not fold_h:
                chosen = (True, long_h_hit[1])
        # 짧은 벽이 더 가까우면 문 틈의 한쪽으로 본다. 아니면 긴 벽을 쓴다.
        short = _pick(short_h_hit, short_v_hit)
        long = _pick(long_h_hit, long_v_hit)
        if chosen is None and short is not None and (long is None or short[1][0] <= long[1][0]):
            horizontal, hit = short
            _saved_along, _saved_perp = along, perp
            if not horizontal:
                along, perp = perp, along
            if horizontal:
                open0, open1 = min(cx, along[0]), max(cx, along[0])
            else:
                open0, open1 = min(cy, along[1]), max(cy, along[1])
            _dist, host, host_a0, host_a1, _host_len = hit
            gap_ok = min(open1, host_a1) - max(open0, host_a0) <= 80.0
            far = open0 if abs(open0 - (host_a0 + host_a1) * 0.5) >= abs(open1 - (host_a0 + host_a1) * 0.5) else open1
            other = False
            if gap_ok:
                for oo, a0, a1, length in (long_h if horizontal else long_v):
                    if abs(oo - host) > 280.0 or length < 1800.0:
                        continue
                    if min(abs(a0 - far), abs(a1 - far)) > 150.0:
                        continue
                    if min(open1, a1) - max(open0, a0) > 80.0:
                        continue
                    other = True
                    break
            if gap_ok and other:
                chosen = (horizontal, host)
            else:
                along, perp = _saved_along, _saved_perp
        if chosen is None and long is not None:
            horizontal, hit = long
            if not horizontal:
                along, perp = perp, along
            chosen = (horizontal, hit[1])
        if chosen is None:
            # 힌지 면이 짧아 호스트가 없다. 문 양쪽에서 끊긴 벽면이 있으면 그 면이 개구다.
            # 창고#1 위쪽 1100처럼 양옆만 벽이고 문짝은 아니다.
            o0, o1 = min(cx, along[0]), max(cx, along[0])
            if o1 - o0 >= 650.0 and abs(along[1] - cy) <= 0.4 * r:
                sides_at: dict[int, set[str]] = {}
                ortho_at: dict[int, float] = {}
                for oo, a0, a1, length in jamb_h:
                    if length < 400.0 or abs(oo - cy) > 520.0:
                        continue
                    if min(a1, o1) - max(a0, o0) > 80.0:
                        continue
                    side = ""
                    if abs(a1 - o0) <= 120.0 and a0 <= o0 - 300.0:
                        side = "L"
                    elif abs(a0 - o1) <= 120.0 and a1 >= o1 + 300.0:
                        side = "R"
                    if not side:
                        continue
                    bucket = round(oo / 40.0)
                    sides_at.setdefault(bucket, set()).add(side)
                    ortho_at[bucket] = oo
                faces = [
                    ortho_at[b]
                    for b, sides in sides_at.items()
                    if sides >= {"L", "R"}
                ]
                if faces and any(abs(oo - cy) <= 80.0 for oo in faces):
                    key = (round(cx / 50.0), round(cy / 50.0), round(r / 50.0))
                    if key not in seen:
                        seen.add(key)
                        for oo in faces:
                            openings.append((False, oo, o0, o1))
                            sides.append(_swing_side(True, oo, ends))
                        leaves.append((True, cx, min(cy, perp[1]), max(cy, perp[1])))
            continue
        horizontal, host = chosen
        key = (round(cx / 50.0), round(cy / 50.0), round(r / 50.0))
        if key in seen:
            continue
        seen.add(key)
        if horizontal:
            openings.append((False, host, min(cx, along[0]), max(cx, along[0])))
            sides.append(_swing_side(True, host, ends))
            leaves.append((True, cx, min(cy, perp[1]), max(cy, perp[1])))
        else:
            openings.append((True, host, min(cy, along[1]), max(cy, along[1])))
            sides.append(_swing_side(False, host, ends))
            leaves.append((False, cy, min(cx, perp[0]), max(cx, perp[0])))
    return openings, leaves, sides


def _door_hinge_used(
    cx: float,
    cy: float,
    openings: list[tuple[bool, float, float, float]],
) -> bool:
    for is_v, ortho, a0, a1 in openings:
        if is_v:
            if abs(cx - ortho) <= 50.0 and (abs(cy - a0) <= 50.0 or abs(cy - a1) <= 50.0):
                return True
        elif abs(cy - ortho) <= 50.0 and (abs(cx - a0) <= 50.0 or abs(cx - a1) <= 50.0):
            return True
    return False


def find_narrow_leaf_openings(
    msp,
    existing: list[tuple[bool, float, float, float]],
) -> list[tuple[bool, float, float, float]]:
    """스윙이 30° 정도인 문. 문짝이 벽 위에 있고 벽이 양쪽으로 이어지면 그 구간은 개구다.

    냉장실#1 오른쪽 문처럼 1/4 스윙이 아닌 문도 양옆은 벽이다.
    """
    walls_h: list[tuple[float, float, float]] = []
    walls_v: list[tuple[float, float, float]] = []
    for e in msp:
        t = e.dxftype()
        pairs: list[tuple[float, float, float, float]] = []
        try:
            if t == "LINE":
                pairs.append((
                    float(e.dxf.start.x), float(e.dxf.start.y),
                    float(e.dxf.end.x), float(e.dxf.end.y),
                ))
            elif t == "LWPOLYLINE":
                pts = [(float(p[0]), float(p[1])) for p in e.get_points("xy")]
                pairs.extend((a[0], a[1], b[0], b[1]) for a, b in zip(pts, pts[1:]))
        except Exception:  # noqa: BLE001
            continue
        for x0, y0, x1, y1 in pairs:
            dx, dy = abs(x1 - x0), abs(y1 - y0)
            length = math.hypot(dx, dy)
            if length < 2000.0 or (dx > 80.0 and dy > 80.0):
                continue
            if dy >= dx:
                walls_v.append(((x0 + x1) * 0.5, min(y0, y1), max(y0, y1)))
            else:
                walls_h.append(((y0 + y1) * 0.5, min(x0, x1), max(x0, x1)))
    out: list[tuple[bool, float, float, float]] = []
    seen: set[tuple[int, int]] = set()
    for e in msp:
        if e.dxftype() != "ARC":
            continue
        try:
            r = float(e.dxf.radius)
            cx, cy = float(e.dxf.center.x), float(e.dxf.center.y)
            sweep = (float(e.dxf.end_angle) - float(e.dxf.start_angle)) % 360.0
            sa, ea = float(e.dxf.start_angle), float(e.dxf.end_angle)
        except Exception:  # noqa: BLE001
            continue
        if not (700.0 <= r <= 1200.0) or not (20.0 <= sweep <= 45.0):
            continue
        key = (round(cx / 40.0), round(cy / 40.0))
        if key in seen:
            continue
        ends = []
        for ang in (sa, ea):
            rad = math.radians(ang)
            ends.append((cx + r * math.cos(rad), cy + r * math.sin(rad)))
        opening: tuple[bool, float, float, float] | None = None
        for ex, ey in ends:
            if abs(ex - cx) <= 80.0 and abs(ey - cy) > 0.7 * r:
                opening = (True, cx, min(cy, ey), max(cy, ey))
                break
            if abs(ey - cy) <= 80.0 and abs(ex - cx) > 0.7 * r:
                opening = (False, cy, min(cx, ex), max(cx, ex))
                break
        if opening is None:
            continue
        is_v, ortho, a0, a1 = opening
        if any(
            ov == is_v and abs(oo - ortho) <= 400.0
            and min(a1, c1) - max(a0, c0) > (a1 - a0) * 0.5
            for ov, oo, c0, c1 in existing
        ):
            continue
        pool = walls_v if is_v else walls_h
        faces = 0
        for oo, b0, b1 in pool:
            if abs(oo - ortho) > 280.0:
                continue
            if a0 - b0 < 600.0 or b1 - a1 < 600.0:
                continue
            faces += 1
        if faces < 2:
            continue
        seen.add(key)
        out.append(opening)
    return out


def correct_walls_around_doors(msp) -> tuple[int, int]:
    """문 개구를 가로지르는 WALL은 끊고, 개구 양옆 벽은 WALL로 둔다.

    문짝(개구 안 선)은 벽이 아니다. 양옆은 벽이다.
    스윙이 열리는 반대편으로 40 mm 넘게 떨어진 면은 개구로 자르지 않고 벽으로 둔다.
    """
    openings = find_double_door_openings(msp)
    single_openings, door_leaves, swing_sides = find_single_door_openings(msp, openings)
    swing_side = {
        opening: side
        for opening, side in zip(single_openings, swing_sides)
        if side != 0
    }
    openings = openings + single_openings
    openings = openings + find_narrow_leaf_openings(msp, openings)
    if not openings and not door_leaves:
        return (0, 0)

    def _cuts_for(
        is_v: bool,
        ortho: float,
        a0: float,
        a1: float,
        *,
        include_leaves: bool = False,
    ) -> list[tuple[float, float]]:
        cuts: list[tuple[float, float]] = []
        pools = [(openings, 280.0)]
        if include_leaves:
            pools.append((door_leaves, 80.0))
        for pool, tol in pools:
            for ov, oo, c0, c1 in pool:
                if ov != is_v or abs(oo - ortho) > tol:
                    continue
                side = swing_side.get((ov, oo, c0, c1), 0)
                # 스윙 반대편 벽면. 힌지 선과 문짝(40 mm 안)은 그대로 개구다.
                if side != 0 and (ortho - oo) * side < -40.0:
                    continue
                lo, hi = max(a0, c0), min(a1, c1)
                if hi - lo >= 250.0:
                    cuts.append((c0, c1))
        if not cuts:
            return []
        cuts.sort()
        merged: list[list[float]] = []
        for c0, c1 in cuts:
            if not merged or c0 > merged[-1][1] - 20.0:
                merged.append([c0, c1])
            else:
                merged[-1][1] = max(merged[-1][1], c1)
        return [(c0, c1) for c0, c1 in merged]

    def _outside(a0: float, a1: float, cuts: list[tuple[float, float]]) -> list[tuple[float, float]]:
        pieces: list[tuple[float, float]] = []
        cursor = a0
        for c0, c1 in cuts:
            if c0 - cursor >= 100.0:
                pieces.append((cursor, c0))
            cursor = max(cursor, c1)
        if a1 - cursor >= 100.0:
            pieces.append((cursor, a1))
        return pieces

    # 같은 방향 선. 쌍이 있는 BASE만 양옆 벽으로 올린다.
    raw: list[tuple[bool, float, float, float, str]] = []
    for e in msp:
        layer = getattr(e.dxf, "layer", None)
        if layer not in (WALL_LAYER, BASE_LAYER):
            continue
        spans: list[tuple[tuple[float, float], tuple[float, float]]] = []
        if e.dxftype() == "LINE":
            try:
                spans = [(
                    (float(e.dxf.start.x), float(e.dxf.start.y)),
                    (float(e.dxf.end.x), float(e.dxf.end.y)),
                )]
            except Exception:  # noqa: BLE001
                continue
        elif e.dxftype() == "LWPOLYLINE":
            try:
                pts = [(float(p[0]), float(p[1])) for p in e.get_points("xy")]
            except Exception:  # noqa: BLE001
                continue
            spans = list(zip(pts, pts[1:]))
        else:
            continue
        for (x0, y0), (x1, y1) in spans:
            dx, dy = abs(x1 - x0), abs(y1 - y0)
            length = math.hypot(dx, dy)
            if length < 400.0 or (dx > 80.0 and dy > 80.0):
                continue
            is_v = dy >= dx
            ortho = (x0 + x1) * 0.5 if is_v else (y0 + y1) * 0.5
            along0, along1 = (min(y0, y1), max(y0, y1)) if is_v else (min(x0, x1), max(x0, x1))
            raw.append((is_v, ortho, along0, along1, layer))

    def _has_pair(
        is_v: bool, ortho: float, a0: float, a1: float, *, min_thick: float = 40.0
    ) -> bool:
        for iv, oo, b0, b1, _layer in raw:
            if iv != is_v or not (min_thick <= abs(oo - ortho) <= 420.0):
                continue
            if min(a1, b1) - max(a0, b0) >= 800.0:
                return True
        return False

    covered: list[tuple[bool, float, float, float]] = []

    def _already(is_v: bool, ortho: float, a0: float, a1: float) -> bool:
        for iv, oo, b0, b1 in covered:
            if iv == is_v and abs(oo - ortho) <= 40.0 and min(a1, b1) - max(a0, b0) >= (a1 - a0) * 0.7:
                return True
        return False

    def _is_opening_host(is_v: bool, ortho: float, a0: float, a1: float) -> bool:
        """이 선이 문의 호스트다. 의자선 자체는 빼고, 그 옆의 문 벽만 양옆을 남긴다."""
        if any(abs(ortho - ban) <= 1.0 for ban in _seat_door_wall_orthos(seat_segs)):
            return False
        for ov, oo, c0, c1 in openings:
            if ov != is_v or abs(oo - ortho) > 280.0:
                continue
            if min(a1, c1) - max(a0, c0) < 250.0:
                continue
            if a0 < c0 - 300.0 or a1 > c1 + 300.0:
                return True
        return False

    def _axis(x0: float, y0: float, x1: float, y1: float):
        dx, dy = abs(x1 - x0), abs(y1 - y0)
        length = math.hypot(dx, dy)
        if length < 400.0 or (dx > 80.0 and dy > 80.0):
            return None
        is_v = dy >= dx
        ortho = (x0 + x1) * 0.5 if is_v else (y0 + y1) * 0.5
        a0, a1 = (min(y0, y1), max(y0, y1)) if is_v else (min(x0, x1), max(x0, x1))
        return is_v, ortho, a0, a1

    def _is_door_panel_beside_hinge(is_v: bool, ortho: float, a0: float, a1: float) -> bool:
        """힌지에서 개구 반대편으로 문 폭만큼 나간 선은 문짝이다.

        직각 벽에서 끝나는 짧은 벽은 남긴다.
        """
        length = a1 - a0
        if not (800.0 <= length <= 1600.0):
            return False
        for ov, oo, c0, c1 in openings:
            if ov != is_v:
                continue
            width = c1 - c0
            if abs(length - width) > 80.0 or abs(ortho - oo) > 280.0:
                continue
            if min(a1, c1) - max(a0, c0) > 80.0:
                continue
            for hinge in (c0, c1):
                if min(abs(a0 - hinge), abs(a1 - hinge)) > 50.0:
                    continue
                far = a1 if abs(a0 - hinge) <= abs(a1 - hinge) else a0
                stopped = any(
                    iv != is_v
                    and layer == WALL_LAYER
                    and b1 - b0 >= 1500.0
                    and abs(wo - far) <= 200.0
                    and b0 - 200.0 <= ortho <= b1 + 200.0
                    for iv, wo, b0, b1, layer in raw
                )
                if not stopped:
                    return True
        return False

    n_cut = 0
    n_flank = 0
    seat_segs = iter_axis_segs(msp, min_len_mm=70.0)
    wall_ents = [
        e
        for e in list(msp)
        if e.dxftype() in ("LINE", "LWPOLYLINE") and getattr(e.dxf, "layer", None) == WALL_LAYER
    ]
    for e in wall_ents:
        if e.dxftype() == "LINE":
            spans = [(
                (float(e.dxf.start.x), float(e.dxf.start.y)),
                (float(e.dxf.end.x), float(e.dxf.end.y)),
            )]
        else:
            pts = [(float(p[0]), float(p[1])) for p in e.get_points("xy")]
            spans = list(zip(pts, pts[1:]))
            if e.closed and len(pts) > 2:
                spans.append((pts[-1], pts[0]))
        hit = False
        rebuilt: list[tuple[tuple[float, float], tuple[float, float]]] = []
        for a, b in spans:
            axis = _axis(a[0], a[1], b[0], b[1])
            if axis is None:
                rebuilt.append((a, b))
                continue
            is_v, ortho, a0, a1 = axis
            # 의자 위를 지나 문 스윙 안에 있는 선은 양옆 벽으로 다시 만들지 않는다.
            if _near_seat_door_wall(is_v, ortho, seat_segs) and not _is_opening_host(
                is_v, ortho, a0, a1
            ):
                hit = True
                continue
            # 힌지 밖 문 폭 선은 벽이 아니다.
            if _is_door_panel_beside_hinge(is_v, ortho, a0, a1):
                hit = True
                continue
            # 문짝과 겹치는 긴 벽은 문짝이 아니다. 문짝 길이의 선만 제거한다.
            cuts = _cuts_for(
                is_v,
                ortho,
                a0,
                a1,
                include_leaves=(a1 - a0) <= 1600.0,
            )
            if not cuts:
                rebuilt.append((a, b))
                continue
            hit = True
            for p0, p1 in _outside(a0, a1, cuts):
                if is_v:
                    rebuilt.append(((ortho, p0), (ortho, p1)))
                else:
                    rebuilt.append(((p0, ortho), (p1, ortho)))
                covered.append((is_v, ortho, p0, p1))
                n_flank += 1
        if not hit:
            continue
        if not rebuilt:
            # 개구를 다 채운 선은 문짝이다. 지우지 않고 회색으로 둔다.
            e.dxf.layer = BASE_LAYER
            e.dxf.color = BASE_COLOR
            n_cut += 1
            continue
        msp.delete_entity(e)
        n_cut += 1
        for a, b in rebuilt:
            if math.hypot(b[0] - a[0], b[1] - a[1]) < 100.0:
                continue
            msp.add_line(a, b, dxfattribs={"layer": WALL_LAYER, "color": WALL_COLOR})

    for is_v, ortho, a0, a1, layer in raw:
        if layer != BASE_LAYER:
            continue
        if _near_seat_door_wall(is_v, ortho, seat_segs) and not _is_opening_host(
            is_v, ortho, a0, a1
        ):
            continue
        if _is_door_panel_beside_hinge(is_v, ortho, a0, a1):
            continue
        cuts = _cuts_for(is_v, ortho, a0, a1)
        pieces: list[tuple[float, float]] = []
        # 개구를 가로지르는 면은 벽 두께가 아니어도 바깥 조각을 남긴다.
        # 맞닿기만 한 면은 80mm 이상(문짝 40mm 는 제외, 100mm 벽면은 포함).
        min_thick = 40.0
        opposite_span = False
        if cuts:
            pieces.extend(_outside(a0, a1, cuts))
        else:
            min_thick = 80.0
            for _ov, oo, c0, c1 in openings:
                if _ov != is_v or abs(oo - ortho) > 220.0:
                    continue
                if abs(a1 - c0) <= 80.0 or abs(a0 - c1) <= 80.0:
                    if a1 - a0 >= 400.0:
                        pieces.append((a0, a1))
                    break
            # 스윙 반대편을 한 줄로 지나는 면은 문 너비 안도 벽이다.
            if not pieces:
                for (ov, oo, c0, c1), side in swing_side.items():
                    if ov != is_v or abs(oo - ortho) > 280.0:
                        continue
                    if (ortho - oo) * side >= -40.0:
                        continue
                    if min(a1, c1) - max(a0, c0) < 250.0:
                        continue
                    pieces.append((a0, a1))
                    opposite_span = True
                    min_thick = 40.0
                    break
        if not pieces or not _has_pair(is_v, ortho, a0, a1, min_thick=min_thick):
            continue
        for p0, p1 in pieces:
            if not opposite_span and _already(is_v, ortho, p0, p1):
                continue
            if is_v:
                msp.add_line((ortho, p0), (ortho, p1), dxfattribs={"layer": WALL_LAYER, "color": WALL_COLOR})
            else:
                msp.add_line((p0, ortho), (p1, ortho), dxfattribs={"layer": WALL_LAYER, "color": WALL_COLOR})
            covered.append((is_v, ortho, p0, p1))
            n_flank += 1
    return (n_cut, n_flank)


def correct_control_room_bottom_door(msp) -> tuple[int, int]:
    """조정실 아래쪽 문은 개구다. 문짝은 BASE로 내리고 양옆 틀은 WALL로 둔다."""
    labels = [(x, y) for x, y, s in _iter_text_labels(msp) if "조정실" in s]
    if not labels:
        return 0, 0
    parsed: list[tuple[str, float, float, float, float, Any]] = []
    for e in msp:
        axis = _axis_line(e)
        if axis is None:
            continue
        ori, coord, a, b, length = axis
        if length < 40.0:
            continue
        parsed.append((ori, coord, a, b, length, e))

    n_demote = 0
    n_promote = 0
    seen_open: set[tuple[int, int]] = set()

    def _set_wall(e, wall: bool) -> bool:
        layer = WALL_LAYER if wall else BASE_LAYER
        if getattr(e.dxf, "layer", None) == layer:
            return False
        e.dxf.layer = layer
        try:
            e.dxf.color = WALL_COLOR if wall else 8
        except Exception:  # noqa: BLE001
            pass
        return True

    for lx, ly in labels:
        leaves = [
            t
            for t in parsed
            if t[0] == "H"
            and 400.0 <= t[4] <= 900.0
            and ly - 4500.0 < t[1] < ly - 600.0
            and abs((t[2] + t[3]) * 0.5 - lx) < 6000.0
        ]
        leaves.sort(key=lambda t: t[1])
        used = [False] * len(leaves)
        for i, leaf in enumerate(leaves):
            if used[i]:
                continue
            group = [leaf]
            used[i] = True
            changed = True
            while changed:
                changed = False
                for j, other in enumerate(leaves):
                    if used[j]:
                        continue
                    if any(
                        abs(other[1] - g[1]) <= 250.0
                        and min(other[3], g[3]) - max(other[2], g[2]) > -250.0
                        for g in group
                    ):
                        group.append(other)
                        used[j] = True
                        changed = True
            if len(group) < 4:
                continue
            pack0 = min(t[2] for t in group)
            pack1 = max(t[3] for t in group)
            if not (900.0 <= pack1 - pack0 <= 2200.0):
                continue
            y_lo = min(t[1] for t in group)
            y_hi = max(t[1] for t in group)
            def _near_pack(a: float, b: float) -> bool:
                if min(b, pack1) - max(a, pack0) > 200.0:
                    return True
                return min(abs(b - pack0), abs(a - pack1), abs(a - pack0), abs(b - pack1)) <= 200.0

            faces = [
                t[1]
                for t in parsed
                if t[0] == "H"
                and t[4] >= 2000.0
                and _near_pack(t[2], t[3])
                and (y_lo - 400.0 <= t[1] <= y_lo - 15.0 or y_hi + 15.0 <= t[1] <= y_hi + 400.0)
            ]
            faces = sorted(set(round(y, 1) for y in faces))
            pair = None
            for a in faces:
                for b in faces:
                    if 80.0 <= b - a <= 400.0 and a <= y_lo + 20.0 and b >= y_hi - 20.0:
                        pair = (a, b)
            if pair is None:
                continue
            f0, f1 = pair
            key = (round((pack0 + pack1) / 80.0), round((f0 + f1) / 80.0))
            if key in seen_open:
                continue
            seen_open.add(key)
            open0, open1 = pack0, pack1
            for ori, coord, a, b, length, _e in parsed:
                if ori != "H" or not (f0 - 30.0 <= coord <= f1 + 30.0):
                    continue
                if length > (pack1 - pack0) + 400.0:
                    continue
                ov = min(b, pack1) - max(a, pack0)
                if ov >= length * 0.7:
                    open0 = min(open0, a)
                    open1 = max(open1, b)

            for ori, coord, a, b, length, e in parsed:
                if getattr(e.dxf, "layer", None) != WALL_LAYER:
                    continue
                if ori == "H" and f0 - 30.0 <= coord <= f1 + 30.0:
                    ov = min(b, open1) - max(a, open0)
                    if ov >= length * 0.75 and length <= (open1 - open0) + 200.0:
                        if _set_wall(e, False):
                            n_demote += 1
                elif ori == "V" and open0 + 80.0 < coord < open1 - 80.0:
                    if length <= 500.0 and a >= f0 - 80.0 and b <= f1 + 80.0:
                        if _set_wall(e, False):
                            n_demote += 1

            for ori, coord, a, b, length, e in parsed:
                if getattr(e.dxf, "layer", None) == WALL_LAYER:
                    continue
                if ori == "H" and (abs(coord - f0) <= 30.0 or abs(coord - f1) <= 30.0):
                    if not (150.0 <= length <= 2500.0):
                        continue
                    outside = b <= open0 + 40.0 or a >= open1 - 40.0
                    abut = abs(b - open0) <= 80.0 or abs(a - open1) <= 80.0
                    if outside and abut and _set_wall(e, True):
                        n_promote += 1
                elif (
                    ori == "V"
                    and (f1 - f0) * 0.5 <= length <= (f1 - f0) + 80.0
                    and a >= f0 - 40.0
                    and b <= f1 + 80.0
                    and min(abs(coord - open0), abs(coord - open1)) <= 60.0
                    and _set_wall(e, True)
                ):
                    n_promote += 1
    return n_demote, n_promote


def correct_leaf_span_door(msp) -> tuple[int, int]:
    """문짝 평행선이 채워진 개구. 개구를 가로지르는 벽은 내리고 양옆은 올린다.

    일반영상검사실2 왼쪽처럼 이중벽 안에 긴 문짝이 있고 양옆 벽은 회색인 문.
    같은 형태는 가로·세로 모두, 층 전체에서 찾는다.
    """
    parsed: list[tuple[str, float, float, float, float, str, Any]] = []
    for e in msp:
        axis = _axis_line(e)
        if axis is None:
            continue
        ori, coord, a, b, length = axis
        if length < 40.0:
            continue
        parsed.append((ori, coord, a, b, length, str(getattr(e.dxf, "layer", "") or ""), e))

    openings: list[tuple[str, float, float, float, float, bool]] = []
    for ori in ("H", "V"):
        cands = [t for t in parsed if t[0] == ori and 1000.0 <= t[4] <= 4500.0]
        cands.sort(key=lambda t: t[1])
        seen_pair: set[tuple] = set()
        for i, (_, c0, a0, b0, length0, _layer0, _e0) in enumerate(cands):
            for _, c1, a1, b1, length1, _layer1, _e1 in cands[i + 1 :]:
                gap = c1 - c0
                if gap < 80.0:
                    continue
                if gap > 220.0:
                    break
                lo, hi = max(a0, a1), min(b0, b1)
                if hi - lo < 1200.0:
                    continue
                leaves = [
                    t
                    for t in parsed
                    if t[0] == ori
                    and t[4] >= 700.0
                    and c0 + 12.0 < t[1] < c1 - 12.0
                    and min(t[3], hi) - max(t[2], lo) > 400.0
                ]
                if len(leaves) < 4:
                    continue
                s0 = min(t[2] for t in leaves)
                s1 = max(t[3] for t in leaves)
                if not (1400.0 <= s1 - s0 <= 3600.0):
                    continue

                def _face_is_door(a: float, b: float, length: float) -> bool:
                    overlap = min(b, s1) - max(a, s0)
                    return overlap >= length * 0.65 and length <= (s1 - s0) + 500.0

                if not _face_is_door(a0, b0, length0) or not _face_is_door(a1, b1, length1):
                    continue
                key = (ori, round((c0 + c1) / 80.0), round((s0 + s1) / 160.0))
                if key in seen_pair:
                    continue
                seen_pair.add(key)

                def _flanks(side: int) -> list[Any]:
                    out = []
                    for _ori, coord, a, b, length, _layer, ent in parsed:
                        if _ori != ori or not (700.0 <= length <= 4500.0):
                            continue
                        on_face = abs(coord - c0) <= 45.0 or abs(coord - c1) <= 45.0
                        inward = c0 + 25.0 < coord < c1 - 25.0
                        if not on_face or inward:
                            continue
                        if side < 0:
                            if b > s0 + 100.0 or s0 - b > 180.0:
                                continue
                        elif a < s1 - 100.0 or a - s1 > 180.0:
                            continue
                        out.append(ent)
                    return out

                def _flanks_len(side: int, min_len: float) -> list[Any]:
                    out = []
                    for _ori, coord, a, b, length, _layer, ent in parsed:
                        if _ori != ori or not (min_len <= length <= 4500.0):
                            continue
                        on_face = abs(coord - c0) <= 45.0 or abs(coord - c1) <= 45.0
                        inward = c0 + 25.0 < coord < c1 - 25.0
                        if not on_face or inward:
                            continue
                        if side < 0:
                            if b > s0 + 100.0 or s0 - b > 180.0:
                                continue
                        elif a < s1 - 100.0 or a - s1 > 180.0:
                            continue
                        out.append(ent)
                    return out

                def _partial(side: int) -> list[tuple[float, float, float]]:
                    """개구 안으로 들어간 벽선 중, 밖에 350–1600mm만 남은 구간."""
                    out = []
                    for _ori, coord, a, b, length, _layer, _ent in parsed:
                        if _ori != ori or length > 4500.0:
                            continue
                        on_face = abs(coord - c0) <= 45.0 or abs(coord - c1) <= 45.0
                        inward = c0 + 25.0 < coord < c1 - 25.0
                        if not on_face or inward:
                            continue
                        if side < 0 and a < s0 - 350.0 and b > s0 + 80.0:
                            if 350.0 <= s0 - a <= 1600.0:
                                out.append((coord, a, s0))
                        elif side > 0 and b > s1 + 350.0 and a < s1 - 80.0:
                            if 350.0 <= b - s1 <= 1600.0:
                                out.append((coord, s1, b))
                    return out

                long_below, long_above = _flanks(-1), _flanks(1)
                if len(long_below) >= 1 and len(long_above) >= 1:
                    openings.append((ori, c0, c1, s0, s1, False))
                else:
                    span = s1 - s0
                    short_below = _flanks_len(-1, 400.0)
                    short_above = _flanks_len(1, 400.0)
                    part_below, part_above = _partial(-1), _partial(1)
                    wide = 2400.0 <= span <= 3200.0 and short_below and short_above
                    crossed = (
                        1600.0 <= span <= 2200.0
                        and (short_below or part_below)
                        and (short_above or part_above)
                        and (part_below or part_above)
                    )
                    if wide or crossed:
                        openings.append((ori, c0, c1, s0, s1, True))

    openings.sort(key=lambda t: t[4] - t[3], reverse=True)
    kept: list[tuple[str, float, float, float, float, bool]] = []
    for ori, c0, c1, s0, s1, short_ok in openings:
        nested = False
        for kori, k0, k1, a, b, _short in kept:
            if ori != kori:
                continue
            if abs((c0 + c1) * 0.5 - (k0 + k1) * 0.5) > 80.0:
                continue
            overlap = min(s1, b) - max(s0, a)
            if overlap > 0.8 * (s1 - s0):
                nested = True
                break
        if not nested:
            kept.append((ori, c0, c1, s0, s1, short_ok))

    n_demote = 0
    n_promote = 0
    touched: set[int] = set()
    for ori, c0, c1, s0, s1, short_ok in kept:
        for _ori, coord, a, b, length, layer, ent in parsed:
            if _ori != ori or layer != WALL_LAYER:
                continue
            if not (c0 - 80.0 <= coord <= c1 + 80.0):
                continue
            overlap = min(b, s1) - max(a, s0)
            if overlap < length * 0.7 or length > (s1 - s0) + 400.0:
                continue
            eid = id(ent)
            if eid in touched:
                continue
            touched.add(eid)
            ent.dxf.layer = BASE_LAYER
            try:
                ent.dxf.color = 8
            except Exception:  # noqa: BLE001
                pass
            n_demote += 1
        flank_min = 400.0 if short_ok else 700.0
        for side in (-1, 1):
            for _ori, coord, a, b, length, layer, ent in parsed:
                if _ori != ori or not (flank_min <= length <= 4500.0):
                    continue
                on_face = abs(coord - c0) <= 45.0 or abs(coord - c1) <= 45.0
                inward = c0 + 25.0 < coord < c1 - 25.0
                if not on_face or inward:
                    continue
                if side < 0:
                    if b > s0 + 100.0 or s0 - b > 180.0:
                        continue
                elif a < s1 - 100.0 or a - s1 > 180.0:
                    continue
                eid = id(ent)
                if eid in touched or layer == WALL_LAYER:
                    continue
                touched.add(eid)
                ent.dxf.layer = WALL_LAYER
                try:
                    ent.dxf.color = WALL_COLOR
                except Exception:  # noqa: BLE001
                    pass
                n_promote += 1
            if not short_ok:
                continue
            added_partial: set[tuple[int, int, int]] = set()
            for _ori, coord, a, b, length, layer, _ent in parsed:
                if _ori != ori or length > 4500.0:
                    continue
                on_face = abs(coord - c0) <= 45.0 or abs(coord - c1) <= 45.0
                inward = c0 + 25.0 < coord < c1 - 25.0
                if not on_face or inward:
                    continue
                if side < 0 and a < s0 - 350.0 and b > s0 + 80.0 and 350.0 <= s0 - a <= 1600.0:
                    p0, p1 = a, s0
                elif side > 0 and b > s1 + 350.0 and a < s1 - 80.0 and 350.0 <= b - s1 <= 1600.0:
                    p0, p1 = s1, b
                else:
                    continue
                covered = any(
                    o == ori
                    and abs(c - coord) <= 40.0
                    and ly == WALL_LAYER
                    and min(bb, p1) - max(aa, p0) >= (p1 - p0) * 0.8
                    for o, c, aa, bb, _length, ly, _e in parsed
                )
                if covered:
                    continue
                pkey = (round(coord), round(p0), round(p1))
                if pkey in added_partial:
                    continue
                added_partial.add(pkey)
                if ori == "H":
                    msp.add_line((p0, coord), (p1, coord), dxfattribs={"layer": WALL_LAYER, "color": WALL_COLOR})
                else:
                    msp.add_line((coord, p0), (coord, p1), dxfattribs={"layer": WALL_LAYER, "color": WALL_COLOR})
                n_promote += 1
    return n_demote, n_promote


def promote_angled_door_flanks(msp) -> tuple[int, int]:
    """사선 벽 위의 여닫이문. 문짝은 내리고 양쪽 짧은 벽만 올린다.

    MRI3 1시 방향처럼 스윙 호가 문짝 끝에 있고, 문 양쪽은 긴 벽에 물린 짧은 사선이다.
    """
    lines: list[tuple[float, float, float, float, float, float, Any]] = []
    arcs: list[tuple[float, float, float]] = []
    for e in msp:
        if e.dxftype() == "LINE":
            try:
                x0, y0 = float(e.dxf.start.x), float(e.dxf.start.y)
                x1, y1 = float(e.dxf.end.x), float(e.dxf.end.y)
            except Exception:  # noqa: BLE001
                continue
            length = math.hypot(x1 - x0, y1 - y0)
            if length < 40.0:
                continue
            ang = math.degrees(math.atan2(y1 - y0, x1 - x0)) % 180.0
            if min(ang, abs(ang - 90.0), abs(ang - 180.0)) < 8.0:
                lines.append((x0, y0, x1, y1, length, ang, e))
                continue
            lines.append((x0, y0, x1, y1, length, ang, e))
        elif e.dxftype() == "ARC":
            try:
                arcs.append((float(e.dxf.center.x), float(e.dxf.center.y), float(e.dxf.radius)))
            except Exception:  # noqa: BLE001
                continue

    def _angdiff(a: float, b: float) -> float:
        d = abs(a - b) % 180.0
        return min(d, 180.0 - d)

    diags = [t for t in lines if _angdiff(t[5], 0.0) > 8.0 and _angdiff(t[5], 90.0) > 8.0 and 900.0 <= t[4] <= 1800.0]
    raw: list[list[tuple[float, float]]] = []
    leaf_ids: set[int] = set()
    for i, t in enumerate(diags):
        dx, dy = t[2] - t[0], t[3] - t[1]
        ux, uy = dx / t[4], dy / t[4]
        px, py = -uy, ux
        for u in diags[i + 1 :]:
            if _angdiff(t[5], u[5]) > 8.0:
                continue
            s1 = (u[0] - t[0]) * ux + (u[1] - t[1]) * uy
            d1 = (u[0] - t[0]) * px + (u[1] - t[1]) * py
            s2 = (u[2] - t[0]) * ux + (u[3] - t[1]) * uy
            d2 = (u[2] - t[0]) * px + (u[3] - t[1]) * py
            if abs(d1 - d2) > 30.0:
                continue
            gap = abs((d1 + d2) * 0.5)
            if not (40.0 <= gap <= 220.0):
                continue
            overlap = min(max(s1, s2), t[4]) - max(min(s1, s2), 0.0)
            if overlap < 0.7 * min(t[4], u[4]):
                continue
            ends = [(t[0], t[1]), (t[2], t[3]), (u[0], u[1]), (u[2], u[3])]
            swung = any(
                0.75 * min(t[4], u[4]) <= r <= 1.25 * max(t[4], u[4])
                and any(math.hypot(cx - x, cy - y) < 150.0 for x, y in ends)
                for cx, cy, r in arcs
            )
            if not swung:
                continue
            raw.append(ends)
            leaf_ids.add(id(t[6]))
            leaf_ids.add(id(u[6]))

    seen: list[tuple[float, float]] = []
    n_demote = 0
    n_promote = 0
    promoted: set[int] = set()
    for ends in raw:
        cx = sum(p[0] for p in ends) / 4.0
        cy = sum(p[1] for p in ends) / 4.0
        if any(abs(cx - ox) < 400.0 and abs(cy - oy) < 400.0 for ox, oy in seen):
            continue
        clusters: list[list[tuple[float, float]]] = []
        for pt in ends:
            placed = False
            for group in clusters:
                if any(math.hypot(pt[0] - g[0], pt[1] - g[1]) < 220.0 for g in group):
                    group.append(pt)
                    placed = True
                    break
            if not placed:
                clusters.append([pt])
        if len(clusters) != 2:
            continue
        flanks: list[tuple[int, Any]] = []
        for ln in lines:
            if not (180.0 <= ln[4] <= 550.0):
                continue
            if id(ln[6]) in leaf_ids:
                continue
            ends_ln = ((ln[0], ln[1]), (ln[2], ln[3]))
            touch = None
            for idx, pt in enumerate(ends_ln):
                if any(math.hypot(pt[0] - ex, pt[1] - ey) < 80.0 for ex, ey in ends):
                    touch = idx
                    break
            if touch is None:
                continue
            other = ends_ln[1 - touch]
            host = any(
                h[4] >= 800.0
                and id(h[6]) not in leaf_ids
                and (
                    math.hypot(other[0] - h[0], other[1] - h[1]) < 80.0
                    or math.hypot(other[0] - h[2], other[1] - h[3]) < 80.0
                )
                for h in lines
            )
            if not host:
                continue
            side = 0 if any(math.hypot(ends_ln[touch][0] - g[0], ends_ln[touch][1] - g[1]) < 80.0 for g in clusters[0]) else 1
            flanks.append((side, ln[6]))
        if not any(side == 0 for side, _e in flanks) or not any(side == 1 for side, _e in flanks):
            continue
        seen.append((cx, cy))
        for ent in msp:
            if id(ent) not in leaf_ids:
                continue
            if getattr(ent.dxf, "layer", None) != WALL_LAYER:
                continue
            ent.dxf.layer = BASE_LAYER
            try:
                ent.dxf.color = 8
            except Exception:  # noqa: BLE001
                pass
            n_demote += 1
            leaf_ids.discard(id(ent))
        for _side, ent in flanks:
            eid = id(ent)
            if eid in promoted or getattr(ent.dxf, "layer", None) == WALL_LAYER:
                continue
            promoted.add(eid)
            ent.dxf.layer = WALL_LAYER
            try:
                ent.dxf.color = WALL_COLOR
            except Exception:  # noqa: BLE001
                pass
            n_promote += 1
    return n_demote, n_promote


def correct_split_wall_door(msp) -> tuple[int, int]:
    """이중벽이 문 구간에서 끊긴 문. 문짝은 내리고 양옆 벽만 올린다.

    투시영상 검사실7 왼쪽처럼 문 구간에만 속 선이 있고, 위·아래(또는 좌·우)는 빈 이중벽이다.
    """
    lines: list[tuple[str, float, float, float, float, str, Any]] = []
    for e in msp:
        axis = _axis_line(e)
        if axis is None:
            continue
        ori, coord, a, b, length = axis
        if length < 40.0:
            continue
        lines.append((ori, coord, a, b, length, str(getattr(e.dxf, "layer", "") or ""), e))

    def _interiors(ori: str, c0: float, c1: float, a0: float, a1: float, min_len: float) -> int:
        return sum(
            1
            for o, c, a, b, length, _layer, _e in lines
            if o == ori
            and c0 + 20.0 < c < c1 - 20.0
            and length >= min_len
            and min(b, a1) - max(a, a0) > min_len * 0.8
        )

    seen: set[tuple] = set()
    doors: list[tuple[str, float, float, float, float, list[Any]]] = []
    for ori in ("H", "V"):
        segs = [t for t in lines if t[0] == ori and 1000.0 <= t[4] <= 1600.0]
        segs.sort(key=lambda t: t[1])
        for i, t in enumerate(segs):
            for u in segs[i + 1 :]:
                gap = u[1] - t[1]
                if gap < 150.0:
                    continue
                if gap > 250.0:
                    break
                if abs(t[2] - u[2]) > 60.0 or abs(t[3] - u[3]) > 60.0:
                    continue
                lo, hi = max(t[2], u[2]), min(t[3], u[3])
                if not (1000.0 <= hi - lo <= 1600.0):
                    continue
                n_leaf = _interiors(ori, t[1], u[1], lo, hi, 0.75 * (hi - lo))
                if n_leaf < 2:
                    continue
                key = (ori, round((t[1] + u[1]) / 100.0), round((lo + hi) / 200.0))
                if key in seen:
                    continue

                def _side(which: int) -> list[Any]:
                    out = []
                    for o, c, a, b, length, _layer, ent in lines:
                        if o != ori or not (500.0 <= length <= 1800.0):
                            continue
                        if min(abs(c - t[1]), abs(c - u[1])) > 30.0:
                            continue
                        if which < 0:
                            if b > lo + 40.0 or lo - b > 150.0:
                                continue
                        elif a < hi - 40.0 or a - hi > 150.0:
                            continue
                        if _interiors(ori, t[1], u[1], a, b, 500.0) >= 2:
                            continue
                        out.append((c, ent))
                    return out

                below, above = _side(-1), _side(1)

                def _both_faces(group: list[tuple[float, Any]]) -> bool:
                    return any(abs(c - t[1]) <= 30.0 for c, _e in group) and any(
                        abs(c - u[1]) <= 30.0 for c, _e in group
                    )

                if not _both_faces(below) or not _both_faces(above):
                    continue
                seen.add(key)
                doors.append((ori, t[1], u[1], lo, hi, [ent for _c, ent in below + above]))

    n_demote = 0
    n_promote = 0
    touched: set[int] = set()
    for ori, c0, c1, lo, hi, flanks in doors:
        for o, c, a, b, length, layer, ent in lines:
            if o != ori or layer != WALL_LAYER:
                continue
            if not (c0 - 30.0 <= c <= c1 + 30.0):
                continue
            overlap = min(b, hi) - max(a, lo)
            if overlap < length * 0.75 or length > (hi - lo) + 150.0:
                continue
            eid = id(ent)
            if eid in touched:
                continue
            touched.add(eid)
            ent.dxf.layer = BASE_LAYER
            try:
                ent.dxf.color = 8
            except Exception:  # noqa: BLE001
                pass
            n_demote += 1
        for ent in flanks:
            eid = id(ent)
            if eid in touched or getattr(ent.dxf, "layer", None) == WALL_LAYER:
                continue
            touched.add(eid)
            ent.dxf.layer = WALL_LAYER
            try:
                ent.dxf.color = WALL_COLOR
            except Exception:  # noqa: BLE001
                pass
            n_promote += 1
    return n_demote, n_promote


def promote_room_door_sides(msp) -> tuple[int, int]:
    """문짝은 내리고, 문 바로 옆의 벽만 올린다.

    일반영상검사실2 오른쪽처럼 문 구간에 속 선이 있고 한쪽 면만 벽인 경우,
    그리고 중앙의 사선 문처럼 양끝이 짧은 벽으로 이어진 경우.
    """
    lines: list[tuple[float, float, float, float, float, float, bool, str, Any]] = []
    for e in msp:
        if e.dxftype() != "LINE":
            continue
        try:
            x0, y0 = float(e.dxf.start.x), float(e.dxf.start.y)
            x1, y1 = float(e.dxf.end.x), float(e.dxf.end.y)
        except Exception:  # noqa: BLE001
            continue
        length = math.hypot(x1 - x0, y1 - y0)
        if length < 40.0:
            continue
        ang = math.degrees(math.atan2(y1 - y0, x1 - x0)) % 180.0
        axis = min(ang, abs(ang - 90.0), abs(ang - 180.0)) < 8.0
        lines.append((x0, y0, x1, y1, length, ang, axis, str(getattr(e.dxf, "layer", "") or ""), e))

    def _angdiff(a: float, b: float) -> float:
        d = abs(a - b) % 180.0
        return min(d, 180.0 - d)

    n_demote = 0
    n_promote = 0
    touched: set[int] = set()

    def _set(ent, wall: bool) -> bool:
        nonlocal n_demote, n_promote
        eid = id(ent)
        if eid in touched:
            return False
        layer = str(getattr(ent.dxf, "layer", "") or "")
        if wall and layer == WALL_LAYER:
            return False
        if not wall and layer != WALL_LAYER:
            return False
        touched.add(eid)
        ent.dxf.layer = WALL_LAYER if wall else BASE_LAYER
        try:
            ent.dxf.color = WALL_COLOR if wall else 8
        except Exception:  # noqa: BLE001
            pass
        if wall:
            n_promote += 1
        else:
            n_demote += 1
        return True

    verts = [t for t in lines if _angdiff(t[5], 90.0) <= 8.0]
    faces = [t for t in verts if 1700.0 <= t[4] <= 2100.0]
    faces.sort(key=lambda t: (t[0] + t[2]) * 0.5)
    seen_door: set[tuple] = set()
    for i, t in enumerate(faces):
        tx = (t[0] + t[2]) * 0.5
        t0, t1 = min(t[1], t[3]), max(t[1], t[3])
        for u in faces[i + 1 :]:
            ux = (u[0] + u[2]) * 0.5
            gap = ux - tx
            if gap < 120.0:
                continue
            if gap > 250.0:
                break
            u0, u1 = min(u[1], u[3]), max(u[1], u[3])
            if abs(t0 - u0) > 60.0 or abs(t1 - u1) > 60.0:
                continue
            lo, hi = max(t0, u0), min(t1, u1)
            n_leaf = sum(
                1
                for x0, y0, x1, y1, length, ang, _axis, _layer, _e in verts
                if tx + 15.0 < (x0 + x1) * 0.5 < ux - 15.0
                and length >= 700.0
                and min(max(y0, y1), hi) - max(min(y0, y1), lo) > 500.0
            )
            if n_leaf < 4:
                continue
            key = (round((tx + ux) / 80.0), round(lo / 100.0))
            if key in seen_door:
                continue
            seen_door.add(key)
            _set(t[8], False)
            _set(u[8], False)
            for x0, y0, x1, y1, length, ang, _axis, layer, ent in verts:
                if tx + 15.0 < (x0 + x1) * 0.5 < ux - 15.0 and layer == WALL_LAYER:
                    a, b = min(y0, y1), max(y0, y1)
                    ov = min(b, hi) - max(a, lo)
                    if ov >= length * 0.7 and length <= (hi - lo) + 200.0:
                        _set(ent, False)
            for x0, y0, x1, y1, length, _ang, _axis, layer, ent in verts:
                if layer == WALL_LAYER or not (300.0 <= length <= 1400.0):
                    continue
                x = (x0 + x1) * 0.5
                if min(abs(x - tx), abs(x - ux)) > 45.0 or tx + 20.0 < x < ux - 20.0:
                    continue
                a, b = min(y0, y1), max(y0, y1)
                ov = min(b, hi) - max(a, lo)
                near = min(abs(b - lo), abs(a - hi), abs(a - lo), abs(b - hi)) <= 200.0
                outside = ov < length * 0.45 and near
                other = ux if abs(x - tx) <= abs(x - ux) else tx
                offset = min(abs(x - tx), abs(x - ux))
                mate = offset >= 15.0 and any(
                    abs((w[0] + w[2]) * 0.5 - other) <= 45.0
                    and w[7] == WALL_LAYER
                    and min(max(w[1], w[3]), b) - max(min(w[1], w[3]), a) >= length * 0.7
                    for w in verts
                )
                if outside or (mate and ov > length * 0.3):
                    _set(ent, True)

    diags = [t for t in lines if not t[6] and 900.0 <= t[4] <= 1500.0]
    seen_ang: list[tuple[float, float]] = []
    for i, t in enumerate(diags):
        dx, dy = t[2] - t[0], t[3] - t[1]
        ux, uy = dx / t[4], dy / t[4]
        px, py = -uy, ux
        for u in diags[i + 1 :]:
            if _angdiff(t[5], u[5]) > 8.0:
                continue
            s1 = (u[0] - t[0]) * ux + (u[1] - t[1]) * uy
            d1 = (u[0] - t[0]) * px + (u[1] - t[1]) * py
            s2 = (u[2] - t[0]) * ux + (u[3] - t[1]) * uy
            d2 = (u[2] - t[0]) * px + (u[3] - t[1]) * py
            if abs(d1 - d2) > 30.0:
                continue
            gap = abs((d1 + d2) * 0.5)
            if not (40.0 <= gap <= 220.0):
                continue
            ov = min(max(s1, s2), t[4]) - max(min(s1, s2), 0.0)
            if ov < 0.7 * min(t[4], u[4]):
                continue
            ends = [(t[0], t[1]), (t[2], t[3]), (u[0], u[1]), (u[2], u[3])]
            cx = sum(p[0] for p in ends) / 4.0
            cy = sum(p[1] for p in ends) / 4.0
            if any(abs(cx - ox) < 500.0 and abs(cy - oy) < 500.0 for ox, oy in seen_ang):
                continue
            clusters: list[list[tuple[float, float]]] = []
            for pt in ends:
                placed = False
                for group in clusters:
                    if any(math.hypot(pt[0] - q[0], pt[1] - q[1]) < 250.0 for q in group):
                        group.append(pt)
                        placed = True
                        break
                if not placed:
                    clusters.append([pt])
            if len(clusters) != 2:
                continue
            stubs: list[tuple[int, Any]] = []
            for ln in lines:
                if not (150.0 <= ln[4] <= 600.0) or _angdiff(ln[5], t[5]) > 25.0:
                    continue
                pts = ((ln[0], ln[1]), (ln[2], ln[3]))
                touch = None
                for idx, pt in enumerate(pts):
                    if any(math.hypot(pt[0] - ex, pt[1] - ey) < 80.0 for ex, ey in ends):
                        touch = idx
                        break
                if touch is None:
                    continue
                other = pts[1 - touch]
                host = any(
                    h[4] >= 80.0
                    and id(h[8]) != id(ln[8])
                    and (
                        math.hypot(other[0] - h[0], other[1] - h[1]) < 80.0
                        or math.hypot(other[0] - h[2], other[1] - h[3]) < 80.0
                    )
                    for h in lines
                )
                if not host:
                    continue
                side = 0 if any(math.hypot(pts[touch][0] - g[0], pts[touch][1] - g[1]) < 120.0 for g in clusters[0]) else 1
                stubs.append((side, ln[8]))
            if not any(side == 0 for side, _e in stubs) or not any(side == 1 for side, _e in stubs):
                continue
            seen_ang.append((cx, cy))
            _set(t[8], False)
            _set(u[8], False)
            for _side, ent in stubs:
                _set(ent, True)
    return n_demote, n_promote


def correct_inwall_swing_doors(msp) -> tuple[int, int]:
    """긴 벽 한가운데에 겹쳐 그린 여닫이문. 문짝은 내리고 양옆만 올린다.

    실장실 상단처럼 벽 두께 안에 문 폭과 같은 평행선이 있고, 한쪽 끝에서
    스윙 호가 나온다. 양옆이 짧은 문틀뿐인 문짝(개구 안의 스윙)은 건드리지 않는다.
    같은 벽의 문 두 개가 가까이 있으면 그 사이 벽도 올린다.
    """
    parsed: list[tuple[str, float, float, float, float, str, Any]] = []
    arcs: list[tuple[float, float, float]] = []
    for e in msp:
        if e.dxftype() == "LINE":
            a, b = e.dxf.start, e.dxf.end
            dx, dy = abs(b.x - a.x), abs(b.y - a.y)
            length = math.hypot(b.x - a.x, b.y - a.y)
            if length < 40.0:
                continue
            tol = max(15.0, 0.08 * length)
            if dy <= tol:
                parsed.append(("H", (a.y + b.y) / 2.0, min(a.x, b.x), max(a.x, b.x), length, e.dxf.layer, e))
            elif dx <= tol:
                parsed.append(("V", (a.x + b.x) / 2.0, min(a.y, b.y), max(a.y, b.y), length, e.dxf.layer, e))
        elif e.dxftype() == "ARC":
            radius = float(e.dxf.radius)
            if 650.0 <= radius <= 950.0:
                arcs.append((float(e.dxf.center.x), float(e.dxf.center.y), radius))

    doors: list[tuple[str, float, float, float, float, list[Any]]] = []
    seen: set[tuple] = set()
    for cx, cy, radius in arcs:
        leaf = None
        for ori, coord, a, b, length, _layer, _ent in parsed:
            if not (0.9 * radius <= length <= 1.12 * radius):
                continue
            ends = ((a, coord), (b, coord)) if ori == "H" else ((coord, a), (coord, b))
            if any(math.hypot(ex - cx, ey - cy) < 40.0 for ex, ey in ends):
                leaf = (ori, coord, a, b, length)
                break
        if leaf is None:
            continue
        ori, coord, a, b, length = leaf
        mates = [
            h
            for h in parsed
            if h[0] == ori
            and abs(h[1] - coord) <= 220.0
            and min(b, h[3]) - max(a, h[2]) >= 0.75 * length
            and 0.8 * length <= h[4] <= 1.2 * length
        ]
        if len(mates) < 3:
            continue
        coords = sorted(h[1] for h in mates)
        if not (100.0 <= coords[-1] - coords[0] <= 220.0):
            continue
        span0 = min(h[2] for h in mates)
        span1 = max(h[3] for h in mates)
        key = (ori, round((span0 + span1) / 100.0), round((coords[0] + coords[-1]) / 100.0))
        if key in seen:
            continue
        face0, face1 = coords[0], coords[-1]

        def _side(face: float, which: int) -> list[tuple[float, str, Any]]:
            found = []
            for o, c, aa, bb, ln, ly, ent in parsed:
                if o != ori or abs(c - face) > 40.0 or not (100.0 <= ln <= 4000.0):
                    continue
                if min(bb, span1) - max(aa, span0) > 80.0:
                    continue
                if which < 0 and bb <= span0 + 40.0 and span0 - bb <= 80.0:
                    found.append((ln, ly, ent))
                elif which > 0 and aa >= span1 - 40.0 and aa - span1 <= 80.0:
                    found.append((ln, ly, ent))
            return found

        left = _side(face0, -1) + _side(face1, -1)
        right = _side(face0, 1) + _side(face1, 1)
        if not left or not right:
            continue
        if max(ln for ln, _ly, _e in left + right) < 600.0:
            continue
        seen.add(key)
        doors.append((ori, face0, face1, span0, span1, [h[6] for h in mates]))

    n_demote = 0
    n_promote = 0
    touched: set[int] = set()

    def _paint(ent, wall: bool) -> bool:
        eid = id(ent)
        if eid in touched:
            return False
        layer = WALL_LAYER if wall else BASE_LAYER
        if getattr(ent.dxf, "layer", None) == layer:
            return False
        touched.add(eid)
        ent.dxf.layer = layer
        try:
            ent.dxf.color = WALL_COLOR if wall else BASE_COLOR
        except Exception:  # noqa: BLE001
            pass
        return True

    def _covered(ori: str, face: float, p0: float, p1: float) -> bool:
        return any(
            o == ori
            and abs(c - face) <= 40.0
            and ly == WALL_LAYER
            and min(bb, p1) - max(aa, p0) >= (p1 - p0) * 0.8
            for o, c, aa, bb, _ln, ly, _e in parsed
        )

    def _add(ori: str, face: float, p0: float, p1: float) -> None:
        nonlocal n_promote
        if p1 - p0 < 80.0 or _covered(ori, face, p0, p1):
            return
        if ori == "H":
            msp.add_line((p0, face), (p1, face), dxfattribs={"layer": WALL_LAYER, "color": WALL_COLOR})
        else:
            msp.add_line((face, p0), (face, p1), dxfattribs={"layer": WALL_LAYER, "color": WALL_COLOR})
        n_promote += 1

    for ori, face0, face1, span0, span1, mates in doors:
        for ent in mates:
            if getattr(ent.dxf, "layer", None) == WALL_LAYER and _paint(ent, False):
                n_demote += 1
        for face in (face0, face1):
            for o, c, aa, bb, ln, ly, ent in parsed:
                if o != ori or abs(c - face) > 40.0 or not (100.0 <= ln <= 2500.0):
                    continue
                if min(bb, span1) - max(aa, span0) > 80.0:
                    continue
                abuts = (bb <= span0 + 40.0 and span0 - bb <= 80.0) or (
                    aa >= span1 - 40.0 and aa - span1 <= 80.0
                )
                if abuts and ly != WALL_LAYER and _paint(ent, True):
                    n_promote += 1
            for which in (-1, 1):
                best = None
                for o, c, aa, bb, ln, _ly, _ent in parsed:
                    if o != ori or abs(c - face) > 40.0 or ln < 600.0:
                        continue
                    if min(bb, span1) - max(aa, span0) > 80.0:
                        continue
                    if which < 0 and bb < span0 - 40.0 and span0 - bb <= 400.0:
                        gap = (bb, span0)
                    elif which > 0 and aa > span1 + 40.0 and aa - span1 <= 400.0:
                        gap = (span1, aa)
                    else:
                        continue
                    if best is None or gap[1] - gap[0] < best[1] - best[0]:
                        best = gap
                if best is not None:
                    _add(ori, face, best[0], best[1])

    doors.sort(key=lambda d: (d[0], round(d[1] / 50.0), round(d[2] / 50.0), d[3]))
    for i, (ori, face0, face1, span0, span1, _mates) in enumerate(doors):
        for ori2, f0, f1, s0, s1, _m in doors[i + 1 :]:
            if ori2 != ori or abs(f0 - face0) > 40.0 or abs(f1 - face1) > 40.0:
                break
            gap0, gap1 = (span1, s0) if span1 <= s0 else (s1, span0)
            if not (120.0 <= gap1 - gap0 <= 900.0):
                continue
            _add(ori, face0, gap0, gap1)
            _add(ori, face1, gap0, gap1)
    return n_demote, n_promote


def correct_return_sided_doors(msp) -> tuple[int, int]:
    """양끝이 짧은 벽으로 막힌 개구와, 기둥 위에 선 세로 문.

    대기실12 바닥처럼 이중벽이 문 구간을 가로지르고 양끝에 짧은 벽이 있으면
    가로지른 선과 문짝은 내리고 양끝 벽만 올린다. 왼쪽의 세로 문짝도 내린다.
    """
    parsed: list[tuple[str, float, float, float, float, str, Any]] = []
    arcs: list[tuple[float, float, float]] = []
    for e in msp:
        if e.dxftype() == "LINE":
            a, b = e.dxf.start, e.dxf.end
            dx, dy = abs(b.x - a.x), abs(b.y - a.y)
            length = math.hypot(b.x - a.x, b.y - a.y)
            if length < 40.0:
                continue
            tol = max(15.0, 0.08 * length)
            if dy <= tol:
                parsed.append(("H", (a.y + b.y) / 2.0, min(a.x, b.x), max(a.x, b.x), length, e.dxf.layer, e))
            elif dx <= tol:
                parsed.append(("V", (a.x + b.x) / 2.0, min(a.y, b.y), max(a.y, b.y), length, e.dxf.layer, e))
        elif e.dxftype() == "ARC":
            radius = float(e.dxf.radius)
            if 800.0 <= radius <= 1000.0:
                arcs.append((float(e.dxf.center.x), float(e.dxf.center.y), radius))

    n_demote = 0
    n_promote = 0
    touched: set[int] = set()

    def _paint(ent, wall: bool) -> bool:
        eid = id(ent)
        if eid in touched:
            return False
        layer = WALL_LAYER if wall else BASE_LAYER
        if getattr(ent.dxf, "layer", None) == layer:
            return False
        touched.add(eid)
        ent.dxf.layer = layer
        try:
            ent.dxf.color = WALL_COLOR if wall else BASE_COLOR
        except Exception:  # noqa: BLE001
            pass
        return True

    def _add(ori: str, face: float, p0: float, p1: float) -> None:
        nonlocal n_promote
        if p1 - p0 < 80.0:
            return
        covered = any(
            o == ori
            and abs(c - face) <= 3.0
            and ly == WALL_LAYER
            and hlen <= (p1 - p0) + 200.0
            and min(bb, p1) - max(aa, p0) >= (p1 - p0) * 0.8
            for o, c, aa, bb, hlen, ly, _e in parsed
        )
        if covered:
            return
        if ori == "H":
            msp.add_line((p0, face), (p1, face), dxfattribs={"layer": WALL_LAYER, "color": WALL_COLOR})
        else:
            msp.add_line((face, p0), (face, p1), dxfattribs={"layer": WALL_LAYER, "color": WALL_COLOR})
        n_promote += 1

    faces = [h for h in parsed if 2400.0 <= h[4] <= 4000.0]
    faces.sort(key=lambda h: (h[0], h[1], h[2]))
    seen: set[tuple] = set()
    for i, t in enumerate(faces):
        for u in faces[i + 1 :]:
            if u[0] != t[0] or u[1] - t[1] > 250.0:
                break
            gap = u[1] - t[1]
            if not (100.0 <= gap <= 220.0):
                continue
            lo = max(t[2], u[2])
            hi = min(t[3], u[3])
            if hi - lo < 2000.0:
                continue
            key = (t[0], round(t[1]), round(lo))
            if key in seen:
                continue
            face_len = min(t[4], u[4])
            ints = [
                h
                for h in parsed
                if h[0] == t[0]
                and t[1] + 20.0 < h[1] < u[1] - 20.0
                and 500.0 <= h[4] <= face_len * 0.55
                and min(h[3], hi) - max(h[2], lo) >= h[4] * 0.7
            ]
            if len(ints) < 4:
                continue

            def _returns(face: float) -> dict[str, tuple[float, float]]:
                found: dict[str, tuple[float, float]] = {}
                for o, c, a, b, length, _ly, _ent in parsed:
                    if o != t[0] or abs(c - face) > 3.0 or not (400.0 <= length <= 1100.0):
                        continue
                    if min(b, hi) - max(a, lo) < 200.0:
                        continue
                    if abs(a - lo) < 80.0 and lo + 250.0 < b < hi - 250.0:
                        found["L"] = (a, b)
                    elif abs(b - hi) < 80.0 and lo + 250.0 < a < hi - 250.0:
                        found["R"] = (a, b)
                return found

            ret = _returns(t[1])
            if "L" not in ret or "R" not in ret:
                ret = _returns(u[1])
            if "L" not in ret or "R" not in ret:
                continue
            seen.add(key)
            open0, open1 = ret["L"][1], ret["R"][0]
            if open1 - open0 < 800.0:
                continue
            for h in ints:
                if h[5] == WALL_LAYER and _paint(h[6], False):
                    n_demote += 1
            for h in parsed:
                if h[0] != t[0] or h[5] != WALL_LAYER or h[4] < 1500.0:
                    continue
                if abs(h[1] - t[1]) > 3.0 and abs(h[1] - u[1]) > 3.0:
                    continue
                if min(h[3], open1) - max(h[2], open0) < h[4] * 0.5:
                    continue
                if _paint(h[6], False):
                    n_demote += 1
            for face in (t[1], u[1]):
                _add(t[0], face, max(lo, ret["L"][0]), open0)
                _add(t[0], face, open1, min(hi, ret["R"][1]))
            for o, c, a, b, length, ly, ent in parsed:
                if o == t[0] or ly == WALL_LAYER or not (100.0 <= length <= 400.0):
                    continue
                at_end = min(abs(c - open0), abs(c - open1), abs(c - lo), abs(c - hi)) <= 40.0
                on_face = min(abs(a - t[1]), abs(b - t[1]), abs(a - u[1]), abs(b - u[1])) <= 40.0
                if at_end and on_face and _paint(ent, True):
                    n_promote += 1
            for h in parsed:
                if h[0] != t[0] or h[5] == WALL_LAYER or not (400.0 <= h[4] <= 1100.0):
                    continue
                if abs(h[1] - t[1]) > 3.0 and abs(h[1] - u[1]) > 3.0:
                    continue
                if (abs(h[2] - ret["L"][0]) < 5.0 and abs(h[3] - ret["L"][1]) < 5.0) or (
                    abs(h[2] - ret["R"][0]) < 5.0 and abs(h[3] - ret["R"][1]) < 5.0
                ):
                    if _paint(h[6], True):
                        n_promote += 1

    seen_swing: set[tuple] = set()
    for cx, cy, radius in arcs:
        leaf = None
        for ori, coord, a, b, length, _ly, _ent in parsed:
            if not (0.9 * radius <= length <= 1.12 * radius):
                continue
            ends = ((a, coord), (b, coord)) if ori == "H" else ((coord, a), (coord, b))
            if any(math.hypot(ex - cx, ey - cy) < 40.0 for ex, ey in ends):
                leaf = (ori, coord, a, b, length)
                break
        if leaf is None:
            continue
        ori, coord, a, b, length = leaf
        mates = [
            h
            for h in parsed
            if h[0] == ori
            and abs(h[1] - coord) <= 200.0
            and min(b, h[3]) - max(a, h[2]) >= 0.75 * length
            and 0.85 * length <= h[4] <= 1.15 * length
        ]
        if len(mates) < 3 or not any(h[5] == WALL_LAYER for h in mates):
            continue
        coords = sorted(h[1] for h in mates)
        if not (100.0 <= coords[-1] - coords[0] <= 220.0):
            continue
        span0 = min(h[2] for h in mates)
        span1 = max(h[3] for h in mates)
        skey = (ori, round(span0 / 50.0), round(coords[0] / 50.0))
        if skey in seen_swing:
            continue

        def _supported(end: float) -> bool:
            for o, c, aa, bb, ln, _ly, _ent in parsed:
                if ln < 400.0:
                    continue
                if o == ori:
                    if min(abs(c - coords[0]), abs(c - coords[-1])) > 45.0:
                        continue
                    if min(bb, span1) - max(aa, span0) > 80.0:
                        continue
                    if (bb <= end + 40.0 and end - bb <= 120.0) or (aa >= end - 40.0 and aa - end <= 120.0):
                        return True
                elif abs(c - end) <= 150.0 and min(bb, coords[-1]) - max(aa, coords[0]) >= 80.0:
                    if abs(ln - radius) / radius > 0.2:
                        return True
            return False

        if not _supported(span0) or not _supported(span1):
            continue
        seen_swing.add(skey)
        for h in mates:
            if h[5] == WALL_LAYER and _paint(h[6], False):
                n_demote += 1
        for o, c, aa, bb, ln, ly, ent in parsed:
            if o != ori or ly == WALL_LAYER or not (300.0 <= ln <= 2500.0):
                continue
            if min(abs(c - coords[0]), abs(c - coords[-1])) > 45.0:
                continue
            if min(bb, span1) - max(aa, span0) > 80.0:
                continue
            abuts = (bb <= span0 + 40.0 and span0 - bb <= 120.0) or (aa >= span1 - 40.0 and aa - span1 <= 120.0)
            if abuts and _paint(ent, True):
                n_promote += 1
    return n_demote, n_promote


def correct_band_leaf_doors(msp) -> tuple[int, int]:
    """벽 띠 안의 문짝. 양옆 벽은 두고 문짝만 내린다.

    탈의실(여) 하단처럼 간격 150mm 이중면 안에 700mm 문짝이 여러 줄이고,
    양끝은 별도 벽으로 이어진다. 면 바로 바깥의 통과선도 문이다.
    """
    parsed: list[tuple[str, float, float, float, float, str, Any]] = []
    for e in msp:
        if e.dxftype() != "LINE":
            continue
        a, b = e.dxf.start, e.dxf.end
        dx, dy = abs(b.x - a.x), abs(b.y - a.y)
        length = math.hypot(b.x - a.x, b.y - a.y)
        if length < 40.0:
            continue
        tol = max(15.0, 0.08 * length)
        if dy <= tol:
            parsed.append(("H", (a.y + b.y) / 2.0, min(a.x, b.x), max(a.x, b.x), length, e.dxf.layer, e))
        elif dx <= tol:
            parsed.append(("V", (a.x + b.x) / 2.0, min(a.y, b.y), max(a.y, b.y), length, e.dxf.layer, e))

    n_demote = 0
    n_promote = 0
    touched: set[int] = set()

    def _paint(ent, wall: bool) -> bool:
        eid = id(ent)
        if eid in touched:
            return False
        layer = WALL_LAYER if wall else BASE_LAYER
        if getattr(ent.dxf, "layer", None) == layer:
            return False
        touched.add(eid)
        ent.dxf.layer = layer
        try:
            ent.dxf.color = WALL_COLOR if wall else BASE_COLOR
        except Exception:  # noqa: BLE001
            pass
        return True

    faces = [h for h in parsed if 1500.0 <= h[4] <= 2000.0]
    faces.sort(key=lambda h: (h[0], h[1], h[2]))
    seen: set[tuple] = set()
    for i, t in enumerate(faces):
        for u in faces[i + 1 :]:
            if u[0] != t[0] or u[1] - t[1] > 200.0:
                break
            gap = u[1] - t[1]
            if not (130.0 <= gap <= 170.0):
                continue
            lo = max(t[2], u[2])
            hi = min(t[3], u[3])
            if hi - lo < 1400.0:
                continue
            key = (t[0], round(lo / 50.0), round(t[1] / 50.0))
            if key in seen:
                continue
            ints = [
                h
                for h in parsed
                if h[0] == t[0]
                and t[1] + 15.0 < h[1] < u[1] - 15.0
                and 600.0 <= h[4] <= 900.0
                and min(h[3], hi) - max(h[2], lo) >= h[4] * 0.7
            ]
            if len(ints) < 6:
                continue

            def _sides(which: int) -> list[Any]:
                found = []
                for o, c, a, b, length, ly, ent in parsed:
                    if o != t[0] or not (300.0 <= length <= 2200.0):
                        continue
                    if min(abs(c - t[1]), abs(c - u[1])) > 80.0:
                        continue
                    if min(b, hi) - max(a, lo) > 80.0:
                        continue
                    if which < 0 and b <= lo + 40.0 and lo - b <= 80.0:
                        found.append(ent)
                    elif which > 0 and a >= hi - 40.0 and a - hi <= 80.0:
                        found.append(ent)
                return found

            left, right = _sides(-1), _sides(1)
            if not left or not right:
                continue
            seen.add(key)
            for h in parsed:
                if h[0] != t[0] or h[5] != WALL_LAYER:
                    continue
                on_face = abs(h[1] - t[1]) <= 3.0 or abs(h[1] - u[1]) <= 3.0
                interior = t[1] + 15.0 < h[1] < u[1] - 15.0 and 600.0 <= h[4] <= 900.0
                if on_face and 1500.0 <= h[4] <= 2000.0:
                    overlap = min(h[3], hi) - max(h[2], lo)
                    if overlap >= h[4] * 0.8 and _paint(h[6], False):
                        n_demote += 1
                elif interior and min(h[3], hi) - max(h[2], lo) >= h[4] * 0.7:
                    if _paint(h[6], False):
                        n_demote += 1
                else:
                    near = (t[1] - 80.0 <= h[1] <= t[1] - 30.0) or (u[1] + 30.0 <= h[1] <= u[1] + 80.0)
                    overlap = min(h[3], hi) - max(h[2], lo)
                    if near and h[4] >= 1500.0 and overlap >= h[4] * 0.75 and h[4] <= (hi - lo) + 300.0:
                        if _paint(h[6], False):
                            n_demote += 1
            for ent in left + right:
                if _paint(ent, True):
                    n_promote += 1
    return n_demote, n_promote


def correct_narrow_face_doors(msp) -> tuple[int, int]:
    """간격 약 100mm인 이중면 문과, 그 옆 기둥에서 이어진 평행 이중선.

    탈의실(여)·(남) 상단은 면 간격 100mm 안에 980mm 문짝이 있다. 문짝은
    내리고 두 문 사이는 벽으로 둔다. 조정실 기둥에서 이어진 세로 이중면은 벽이다.
    """
    parsed: list[tuple[str, float, float, float, float, str, Any]] = []
    for e in msp:
        if e.dxftype() != "LINE":
            continue
        a, b = e.dxf.start, e.dxf.end
        dx, dy = abs(b.x - a.x), abs(b.y - a.y)
        length = math.hypot(b.x - a.x, b.y - a.y)
        if length < 40.0:
            continue
        tol = max(15.0, 0.08 * length)
        if dy <= tol:
            parsed.append(("H", (a.y + b.y) / 2.0, min(a.x, b.x), max(a.x, b.x), length, e.dxf.layer, e))
        elif dx <= tol:
            parsed.append(("V", (a.x + b.x) / 2.0, min(a.y, b.y), max(a.y, b.y), length, e.dxf.layer, e))

    n_demote = 0
    n_promote = 0
    touched: set[int] = set()

    def _paint(ent, wall: bool) -> bool:
        eid = id(ent)
        if eid in touched:
            return False
        layer = WALL_LAYER if wall else BASE_LAYER
        if getattr(ent.dxf, "layer", None) == layer:
            return False
        touched.add(eid)
        ent.dxf.layer = layer
        if wall:
            ent.dxf.color = WALL_COLOR
        return True

    faces = [h for h in parsed if 1700.0 <= h[4] <= 2300.0]
    faces.sort(key=lambda h: (h[0], h[1], h[2]))
    doors: list[tuple[str, float, float, float, float]] = []
    seen_door: set[tuple] = set()
    for i, t in enumerate(faces):
        for u in faces[i + 1 :]:
            if u[0] != t[0] or u[1] - t[1] > 140.0:
                if u[0] != t[0] or u[1] - t[1] > 140.0:
                    break
                continue
            gap = u[1] - t[1]
            if not (90.0 <= gap <= 120.0):
                continue
            lo, hi = max(t[2], u[2]), min(t[3], u[3])
            if hi - lo < 1500.0:
                continue
            interiors = [
                h
                for h in parsed
                if h[0] == t[0]
                and t[1] + 10.0 < h[1] < u[1] - 10.0
                and 800.0 <= h[4] <= 1100.0
                and min(h[3], hi) - max(h[2], lo) >= 0.7 * h[4]
            ]
            if len(interiors) < 4:
                continue
            if not any(h[5] == WALL_LAYER for h in (t, u, *interiors)):
                continue
            key = (t[0], round(lo / 40.0), round(t[1] / 20.0))
            if key in seen_door:
                continue
            seen_door.add(key)
            doors.append((t[0], t[1], u[1], lo, hi))

    groups: dict[tuple, dict] = {}
    for ori, c0, c1, lo, hi in doors:
        key = None
        for k, g in groups.items():
            if k[0] == ori and abs(g["c0"] - c0) < 15.0 and abs(g["c1"] - c1) < 15.0:
                key = k
                break
        if key is None:
            key = (ori, round(c0, 1), round(c1, 1))
            groups[key] = {"c0": c0, "c1": c1, "spans": []}
        groups[key]["spans"].append((lo, hi))

    for (ori, _c0, _c1), g in groups.items():
        c0, c1 = g["c0"], g["c1"]
        spans = g["spans"]
        spans.sort()

        def _inside(a: float, b: float) -> float:
            total = 0.0
            for lo, hi in spans:
                total += max(0.0, min(b, hi) - max(a, lo))
            return total

        for h in parsed:
            if h[0] != ori or h[5] != WALL_LAYER:
                continue
            on_face = abs(h[1] - c0) <= 8.0 or abs(h[1] - c1) <= 8.0
            interior = c0 + 10.0 < h[1] < c1 - 10.0 and 800.0 <= h[4] <= 1100.0
            inside = _inside(h[2], h[3])
            if inside <= 0.0:
                continue
            if on_face and 1500.0 <= h[4] <= 2800.0 and inside >= h[4] * 0.55:
                if _paint(h[6], False):
                    n_demote += 1
            elif interior and inside >= h[4] * 0.7:
                if _paint(h[6], False):
                    n_demote += 1
        for (lo0, hi0), (lo1, hi1) in zip(spans, spans[1:]):
            gap0, gap1 = hi0, lo1
            if not (300.0 <= gap1 - gap0 <= 1500.0):
                continue
            for face in (c0, c1):
                covered = any(
                    h[0] == ori
                    and getattr(h[6].dxf, "layer", None) == WALL_LAYER
                    and abs(h[1] - face) <= 8.0
                    and min(h[3], gap1) - max(h[2], gap0) >= 0.7 * (gap1 - gap0)
                    for h in parsed
                )
                if covered:
                    continue
                if ori == "H":
                    msp.add_line((gap0, face), (gap1, face), dxfattribs={"layer": WALL_LAYER, "color": WALL_COLOR})
                else:
                    msp.add_line((face, gap0), (face, gap1), dxfattribs={"layer": WALL_LAYER, "color": WALL_COLOR})
                n_promote += 1

    longs = [h for h in parsed if h[5] == BASE_LAYER and 1750.0 <= h[4] <= 2100.0]
    longs.sort(key=lambda h: (h[0], h[1], h[2]))
    seen_pair: set[tuple] = set()

    def _stub(ori: str, coord: float, end: float, outward: int) -> bool:
        for w in parsed:
            if w[0] != ori or w[5] != WALL_LAYER or not (300.0 <= w[4] <= 900.0):
                continue
            if not (15.0 <= abs(w[1] - coord) <= 45.0):
                continue
            if outward > 0 and abs(w[2] - end) <= 80.0 and w[3] > end:
                return True
            if outward < 0 and abs(w[3] - end) <= 80.0 and w[2] < end:
                return True
        for w in parsed:
            if w[0] != ori or w[5] == WALL_LAYER or not (150.0 <= w[4] <= 450.0):
                continue
            if not (15.0 <= abs(w[1] - coord) <= 45.0):
                continue
            if outward > 0 and abs(w[2] - end) <= 80.0 and w[3] > end:
                far = w[3]
            elif outward < 0 and abs(w[3] - end) <= 80.0 and w[2] < end:
                far = w[2]
            else:
                continue
            for host in parsed:
                if host[5] != WALL_LAYER or host[4] < 400.0:
                    continue
                if host[0] == ori and abs(host[1] - w[1]) <= 40.0 and min(abs(host[2] - far), abs(host[3] - far)) <= 40.0:
                    return True
                if host[0] != ori and abs(host[1] - far) <= 40.0 and host[2] - 40.0 <= w[1] <= host[3] + 40.0:
                    return True
        return False

    for i, t in enumerate(longs):
        for u in longs[i + 1 :]:
            if u[0] != t[0] or u[1] - t[1] > 250.0:
                if u[0] != t[0] or u[1] - t[1] > 250.0:
                    break
                continue
            gap = u[1] - t[1]
            if not (120.0 <= gap <= 180.0):
                continue
            if abs(t[2] - u[2]) > 30.0 or abs(t[3] - u[3]) > 30.0:
                continue
            interiors = [
                h
                for h in parsed
                if h[0] == t[0]
                and t[1] + 15.0 < h[1] < u[1] - 15.0
                and 700.0 <= h[4] <= 1100.0
                and min(h[3], t[3]) - max(h[2], t[2]) >= 0.6 * h[4]
            ]
            if len(interiors) < 4:
                continue
            key = (t[0], round(t[1] / 30.0), round(t[2] / 30.0))
            if key in seen_pair:
                continue
            covered = any(
                h[5] == WALL_LAYER
                and h[0] == t[0]
                and abs(h[1] - t[1]) < 15.0
                and min(h[3], t[3]) - max(h[2], t[2]) >= 0.8 * t[4]
                for h in parsed
            )
            if covered:
                continue
            top = _stub(t[0], t[1], t[3], 1) and _stub(t[0], u[1], u[3], 1)
            bot = _stub(t[0], t[1], t[2], -1) and _stub(t[0], u[1], u[2], -1)
            if not top and not bot:
                continue
            seen_pair.add(key)
            for h in parsed:
                if h[0] != t[0] or h[5] == WALL_LAYER:
                    continue
                on_face = abs(h[1] - t[1]) <= 8.0 or abs(h[1] - u[1]) <= 8.0
                if not on_face or not (1750.0 <= h[4] <= 2100.0):
                    continue
                if abs(h[2] - t[2]) > 40.0 or abs(h[3] - t[3]) > 40.0:
                    continue
                if _paint(h[6], True):
                    n_promote += 1
            for end, outward in ((t[3], 1), (t[2], -1)):
                for h in parsed:
                    if h[0] != t[0] or h[5] == WALL_LAYER or not (150.0 <= h[4] <= 450.0):
                        continue
                    if min(abs(h[1] - t[1]), abs(h[1] - u[1])) > 45.0:
                        continue
                    meets = (outward > 0 and abs(h[2] - end) <= 80.0) or (outward < 0 and abs(h[3] - end) <= 80.0)
                    if meets and _paint(h[6], True):
                        n_promote += 1
    return n_demote, n_promote


def correct_filled_opening_doors(msp) -> tuple[int, int]:
    """문짝이 이중면을 채운 개구. 문짝은 내리고 양옆 벽만 올린다.

    골밀도영상 검사실6 아래·오른쪽처럼 면 안에 짧은 문짝이 겹쳐 있다.
    양끝이 이미 벽인 기둥 옆 이중선은 그대로 둔다. 벽이 반만 올라간
    정사각 기둥의 남은 변도 벽으로 둔다.
    """
    parsed: list[tuple[str, float, float, float, float, str, Any]] = []
    for e in msp:
        if e.dxftype() != "LINE":
            continue
        a, b = e.dxf.start, e.dxf.end
        dx, dy = abs(b.x - a.x), abs(b.y - a.y)
        length = math.hypot(b.x - a.x, b.y - a.y)
        if length < 40.0:
            continue
        tol = max(15.0, 0.08 * length)
        if dy <= tol:
            parsed.append(("H", (a.y + b.y) / 2.0, min(a.x, b.x), max(a.x, b.x), length, e.dxf.layer, e))
        elif dx <= tol:
            parsed.append(("V", (a.x + b.x) / 2.0, min(a.y, b.y), max(a.y, b.y), length, e.dxf.layer, e))

    n_demote = 0
    n_promote = 0
    touched: set[int] = set()

    def _paint(ent, wall: bool) -> bool:
        eid = id(ent)
        if eid in touched:
            return False
        layer = WALL_LAYER if wall else BASE_LAYER
        if getattr(ent.dxf, "layer", None) == layer:
            return False
        touched.add(eid)
        ent.dxf.layer = layer
        if wall:
            ent.dxf.color = WALL_COLOR
        return True

    faces = [h for h in parsed if 1500.0 <= h[4] <= 4000.0]
    faces.sort(key=lambda h: (h[0], h[1], h[2]))
    seen: set[tuple] = set()
    for i, t in enumerate(faces):
        for u in faces[i + 1 :]:
            if u[0] != t[0] or u[1] - t[1] > 200.0:
                if u[0] != t[0] or u[1] - t[1] > 200.0:
                    break
                continue
            gap = u[1] - t[1]
            if not (120.0 <= gap <= 180.0):
                continue
            lo, hi = max(t[2], u[2]), min(t[3], u[3])
            span = hi - lo
            if span < 1500.0:
                continue
            interiors = [
                h
                for h in parsed
                if h[0] == t[0]
                and t[1] + 12.0 < h[1] < u[1] - 12.0
                and 600.0 <= h[4] <= 1200.0
                and min(h[3], hi) - max(h[2], lo) >= 0.7 * h[4]
            ]
            if len(interiors) < 4:
                continue
            segs = sorted((max(h[2], lo), min(h[3], hi)) for h in interiors)
            covered = 0.0
            end = -1.0e18
            for a, b in segs:
                if b <= end:
                    continue
                covered += b - max(a, end)
                end = max(end, b)
            if covered < 0.85 * span:
                continue
            if not any(h[5] == WALL_LAYER for h in parsed if h[0] == t[0] and (abs(h[1] - t[1]) <= 8.0 or abs(h[1] - u[1]) <= 8.0) and min(h[3], hi) - max(h[2], lo) >= 0.7 * span):
                continue
            key = (t[0], round(lo / 50.0), round(t[1] / 20.0))
            if key in seen:
                continue

            def _end_sides(end_at: float, outward: int):
                found: dict[int, Any] = {}
                for h in parsed:
                    if h[0] != t[0] or not (400.0 <= h[4] <= 2500.0):
                        continue
                    which = -1
                    if 15.0 <= abs(h[1] - t[1]) <= 50.0:
                        which = 0
                    elif 15.0 <= abs(h[1] - u[1]) <= 50.0:
                        which = 1
                    if which < 0:
                        continue
                    if outward > 0:
                        if abs(h[2] - end_at) > 80.0 or h[2] < end_at - 100.0:
                            continue
                    elif abs(h[3] - end_at) > 80.0 or h[3] > end_at + 100.0:
                        continue
                    prev = found.get(which)
                    if prev is None or h[4] > prev[4]:
                        found[which] = h
                if len(found) < 2:
                    return None
                return found[0], found[1]

            low = _end_sides(lo, -1)
            high = _end_sides(hi, 1)
            long_opening = span >= 2500.0
            if low and high:
                if all(s[5] == WALL_LAYER for s in low + high):
                    continue
            elif not long_opening:
                continue
            seen.add(key)
            for h in parsed:
                if h[0] != t[0] or h[5] != WALL_LAYER:
                    continue
                on_face = abs(h[1] - t[1]) <= 8.0 or abs(h[1] - u[1]) <= 8.0
                interior = t[1] + 12.0 < h[1] < u[1] - 12.0 and 600.0 <= h[4] <= 1200.0
                overlap = min(h[3], hi) - max(h[2], lo)
                if overlap < h[4] * 0.7:
                    continue
                if (on_face or interior) and _paint(h[6], False):
                    n_demote += 1
            for pair in (low, high):
                if not pair:
                    continue
                for s in pair:
                    if s[5] != WALL_LAYER and _paint(s[6], True):
                        n_promote += 1
                    for h in parsed:
                        if h[0] != s[0] or h[5] == WALL_LAYER:
                            continue
                        if abs(h[1] - s[1]) > 8.0 or abs(h[2] - s[2]) > 40.0 or abs(h[3] - s[3]) > 40.0:
                            continue
                        if _paint(h[6], True):
                            n_promote += 1
            if long_opening:
                for end_at in (lo, hi):
                    for h in parsed:
                        if h[0] == t[0] or h[5] == WALL_LAYER or not (400.0 <= h[4] <= 4000.0):
                            continue
                        if abs(h[1] - end_at) > 280.0:
                            continue
                        if min(h[3], u[1]) - max(h[2], t[1]) < 80.0:
                            continue
                        if _paint(h[6], True):
                            n_promote += 1

    arcs: list[tuple[float, float, float]] = []
    for e in msp:
        if e.dxftype() != "ARC":
            continue
        radius = float(e.dxf.radius)
        if 900.0 <= radius <= 1600.0:
            center = e.dxf.center
            arcs.append((center.x, center.y, radius))
    for cx, cy, radius in arcs:
        leaf = None
        for h in parsed:
            if not (0.9 * radius <= h[4] <= 1.12 * radius):
                continue
            ends = ((h[2], h[1]), (h[3], h[1])) if h[0] == "H" else ((h[1], h[2]), (h[1], h[3]))
            if any(abs(ex - cx) < 50.0 and abs(ey - cy) < 50.0 for ex, ey in ends):
                leaf = h
                break
        if leaf is None:
            continue
        ori, coord, a, b = leaf[0], leaf[1], leaf[2], leaf[3]
        faces = [
            h
            for h in parsed
            if h[0] == ori
            and h[5] == WALL_LAYER
            and abs(h[1] - coord) <= 600.0
            and min(h[3], b) - max(h[2], a) >= 0.6 * (b - a)
        ]
        faces.sort(key=lambda h: h[1])
        pair = None
        for i, t in enumerate(faces):
            for u in faces[i + 1 :]:
                if 80.0 <= u[1] - t[1] <= 250.0:
                    pair = (t, u)
                    break
            if pair:
                break
        if pair is None:
            continue
        coords = {round(face[1], 1) for face in pair}
        for h in parsed:
            if h[0] != ori or h[5] != WALL_LAYER:
                continue
            if round(h[1], 1) not in coords and min(abs(h[1] - c) for c in coords) > 8.0:
                continue
            if min(h[3], b) - max(h[2], a) < 0.6 * (b - a):
                continue
            if _paint(h[6], False):
                n_demote += 1
        for face in pair:
            for p0, p1 in ((face[2], a), (b, face[3])):
                if p1 - p0 < 200.0:
                    continue
                covered = any(
                    h[0] == ori
                    and h is not face
                    and getattr(h[6].dxf, "layer", None) == WALL_LAYER
                    and abs(h[1] - face[1]) <= 15.0
                    and min(h[3], p1) - max(h[2], p0) >= 0.8 * (p1 - p0)
                    for h in parsed
                )
                if covered:
                    continue
                if ori == "H":
                    msp.add_line((p0, face[1]), (p1, face[1]), dxfattribs={"layer": WALL_LAYER, "color": WALL_COLOR})
                else:
                    msp.add_line((face[1], p0), (face[1], p1), dxfattribs={"layer": WALL_LAYER, "color": WALL_COLOR})
                n_promote += 1

    verts = [h for h in parsed if h[0] == "V" and 450.0 <= h[4] <= 750.0]
    verts.sort(key=lambda h: (h[1], h[2]))
    seen_sq: set[tuple] = set()
    for i, t in enumerate(verts):
        for u in verts[i + 1 :]:
            if u[1] - t[1] > 800.0:
                break
            width = u[1] - t[1]
            if not (450.0 <= width <= 750.0):
                continue
            if abs(t[2] - u[2]) > 40.0 or abs(t[3] - u[3]) > 40.0:
                continue
            if abs(t[4] - width) > 80.0 or abs(u[4] - width) > 80.0:
                continue
            edges = []
            for y_edge, x0, x1 in ((t[3], t[1], u[1]), (t[2], t[1], u[1])):
                hits = [
                    h
                    for h in parsed
                    if h[0] == "H"
                    and abs(h[1] - y_edge) <= 30.0
                    and abs(h[2] - x0) <= 40.0
                    and abs(h[3] - x1) <= 40.0
                    and 450.0 <= h[4] <= 750.0
                ]
                if not hits:
                    edges = []
                    break
                edges.append(hits)
            if len(edges) < 2:
                continue
            def _edge_copies(ori: str, coord: float, a0: float, b0: float) -> list[Any]:
                return [
                    h
                    for h in parsed
                    if h[0] == ori
                    and abs(h[1] - coord) <= 8.0
                    and abs(h[2] - a0) <= 40.0
                    and abs(h[3] - b0) <= 40.0
                    and 450.0 <= h[4] <= 750.0
                ]

            v_left = _edge_copies("V", t[1], t[2], t[3])
            v_right = _edge_copies("V", u[1], u[2], u[3])
            groups = [v_left, v_right, edges[0], edges[1]]
            if sum(1 for group in groups if any(h[5] == WALL_LAYER for h in group)) < 1:
                continue
            sq_key = (round(t[1] / 40.0), round(t[2] / 40.0))
            if sq_key in seen_sq:
                continue
            seen_sq.add(sq_key)
            for group in groups:
                for h in group:
                    if h[5] != WALL_LAYER and _paint(h[6], True):
                        n_promote += 1
    return n_demote, n_promote


def promote_angled_end_door_jambs(msp) -> tuple[int, int]:
    """사선 벽 끝에 붙은 직선 문. 문짝은 내리고 양옆 짧은 벽만 올린다.

    일반영상검사실2·4처럼 사선 벽 양끝에 가로·세로 문이 있다.
    """
    parsed: list[tuple[str, float, float, float, float, str, Any]] = []
    arcs: list[tuple[float, float, float]] = []
    diags: list[tuple[float, float, float, float]] = []
    for e in msp:
        if e.dxftype() == "ARC":
            radius = float(e.dxf.radius)
            if 800.0 <= radius <= 1100.0:
                center = e.dxf.center
                arcs.append((center.x, center.y, radius))
            continue
        if e.dxftype() != "LINE":
            continue
        a, b = e.dxf.start, e.dxf.end
        dx, dy = b.x - a.x, b.y - a.y
        length = math.hypot(dx, dy)
        if length < 40.0:
            continue
        ang = math.degrees(math.atan2(dy, dx)) % 180.0
        tol = max(15.0, 0.08 * length)
        if abs(dy) <= tol:
            parsed.append(("H", (a.y + b.y) / 2.0, min(a.x, b.x), max(a.x, b.x), length, e.dxf.layer, e))
        elif abs(dx) <= tol:
            parsed.append(("V", (a.x + b.x) / 2.0, min(a.y, b.y), max(a.y, b.y), length, e.dxf.layer, e))
        elif 800.0 <= length <= 2000.0 and 12.0 <= min(ang, 180.0 - ang) <= 78.0:
            diags.append((a.x, a.y, b.x, b.y))

    n_demote = 0
    n_promote = 0
    touched: set[int] = set()

    def _paint(ent, wall: bool) -> bool:
        eid = id(ent)
        if eid in touched:
            return False
        layer = WALL_LAYER if wall else BASE_LAYER
        if getattr(ent.dxf, "layer", None) == layer:
            return False
        touched.add(eid)
        ent.dxf.layer = layer
        if wall:
            ent.dxf.color = WALL_COLOR
        return True

    def _near_diag(x: float, y: float) -> bool:
        for x0, y0, x1, y1 in diags:
            if min(math.hypot(x - x0, y - y0), math.hypot(x - x1, y - y1)) <= 750.0:
                return True
        return False

    seen: set[tuple] = set()
    for cx, cy, radius in arcs:
        leaf = None
        for h in parsed:
            if not (0.85 * radius <= h[4] <= 1.15 * radius):
                continue
            ends = ((h[2], h[1]), (h[3], h[1])) if h[0] == "H" else ((h[1], h[2]), (h[1], h[3]))
            if any(abs(ex - cx) < 60.0 and abs(ey - cy) < 60.0 for ex, ey in ends):
                leaf = h
                break
        if leaf is None:
            continue
        ori, coord, a, b = leaf[0], leaf[1], leaf[2], leaf[3]
        mates = [
            h
            for h in parsed
            if h[0] == ori
            and 80.0 <= abs(h[1] - coord) <= 170.0
            and abs(h[2] - a) < 80.0
            and abs(h[3] - b) < 80.0
            and 0.8 * leaf[4] <= h[4] <= 1.2 * leaf[4]
        ]
        if not mates:
            continue
        key = (ori, round(a / 40.0), round(coord / 30.0))
        if key in seen:
            continue
        seen.add(key)
        span0 = min([a] + [m[2] for m in mates])
        span1 = max([b] + [m[3] for m in mates])
        faces = [coord] + [m[1] for m in mates]
        face0, face1 = min(faces), max(faces)
        mid = (face0 + face1) / 2.0
        ends = [(span0, mid), (span1, mid)] if ori == "H" else [(mid, span0), (mid, span1)]
        if not any(_near_diag(x, y) for x, y in ends):
            continue
        for h in parsed:
            if h[0] != ori or h[5] != WALL_LAYER:
                continue
            if min(abs(h[1] - c) for c in faces) > 8.0:
                continue
            overlap = min(h[3], span1) - max(h[2], span0)
            if overlap >= 0.7 * (span1 - span0) and _paint(h[6], False):
                n_demote += 1
        for h in parsed:
            if h[5] == WALL_LAYER or not (100.0 <= h[4] <= 450.0):
                continue
            if h[0] == ori:
                if min(h[3], span1) - max(h[2], span0) > 80.0:
                    continue
                abuts = (abs(h[3] - span0) <= 90.0 and h[2] < span0 + 20.0) or (
                    abs(h[2] - span1) <= 90.0 and h[3] > span1 - 20.0
                )
                near = min(abs(h[1] - c) for c in faces) <= 220.0
            else:
                abuts = abs(h[1] - span0) <= 90.0 or abs(h[1] - span1) <= 90.0
                near = min(h[3], face1 + 40.0) - max(h[2], face0 - 40.0) > 20.0
            if abuts and near and _paint(h[6], True):
                n_promote += 1
    return n_demote, n_promote


def correct_offset_swing_opening(msp) -> tuple[int, int]:
    """스윙 옆에 한 면만 벽인 개구. 문 구간은 내리고 양옆은 벽으로 둔다.

    MRI1과 조정실 사이처럼 면 간격 150mm 안을 같은 길이의 선이 채우고,
    한쪽 면만 벽이다.
    """
    parsed: list[tuple[str, float, float, float, float, str, Any]] = []
    arcs: list[tuple[float, float, float]] = []
    for e in msp:
        if e.dxftype() == "ARC":
            radius = float(e.dxf.radius)
            if 900.0 <= radius <= 1600.0:
                center = e.dxf.center
                arcs.append((center.x, center.y, radius))
            continue
        if e.dxftype() != "LINE":
            continue
        a, b = e.dxf.start, e.dxf.end
        dx, dy = abs(b.x - a.x), abs(b.y - a.y)
        length = math.hypot(dx, dy)
        if length < 40.0:
            continue
        tol = max(15.0, 0.08 * length)
        if dy <= tol:
            parsed.append(("H", (a.y + b.y) / 2.0, min(a.x, b.x), max(a.x, b.x), length, e.dxf.layer, e))
        elif dx <= tol:
            parsed.append(("V", (a.x + b.x) / 2.0, min(a.y, b.y), max(a.y, b.y), length, e.dxf.layer, e))

    n_demote = 0
    n_promote = 0
    touched: set[int] = set()

    def _paint(ent, wall: bool) -> bool:
        eid = id(ent)
        if eid in touched:
            return False
        layer = WALL_LAYER if wall else BASE_LAYER
        if getattr(ent.dxf, "layer", None) == layer:
            return False
        touched.add(eid)
        ent.dxf.layer = layer
        if wall:
            ent.dxf.color = WALL_COLOR
        return True

    faces = [h for h in parsed if 1500.0 <= h[4] <= 2000.0]
    faces.sort(key=lambda h: (h[0], h[1], h[2]))
    seen: set[tuple] = set()
    for i, t in enumerate(faces):
        for u in faces[i + 1 :]:
            if u[0] != t[0] or u[1] - t[1] > 220.0:
                if u[0] != t[0] or u[1] - t[1] > 220.0:
                    break
                continue
            gap = u[1] - t[1]
            if not (100.0 <= gap <= 200.0):
                continue
            if abs(t[2] - u[2]) > 80.0 or abs(t[3] - u[3]) > 80.0:
                continue
            if t[5] == u[5]:
                continue
            interiors = [
                h
                for h in parsed
                if h[0] == t[0]
                and t[1] + 8.0 < h[1] < u[1] - 8.0
                and abs(h[4] - t[4]) < 200.0
                and abs(h[2] - t[2]) < 80.0
            ]
            if len(interiors) < 2 or not any(h[5] == WALL_LAYER for h in interiors):
                continue
            key = (t[0], round(t[2] / 50.0), round(t[1] / 30.0))
            if key in seen:
                continue
            if t[0] == "V":
                ends = [(t[1], t[2]), (t[1], t[3]), (u[1], t[2]), (u[1], t[3])]
            else:
                ends = [(t[2], t[1]), (t[3], t[1]), (t[2], u[1]), (t[3], u[1])]
            if not any(math.hypot(cx - x, cy - y) < 800.0 for cx, cy, _r in arcs for x, y in ends):
                continue
            seen.add(key)
            span0, span1 = t[2], t[3]
            for h in parsed:
                if h[0] != t[0] or h[5] != WALL_LAYER:
                    continue
                inside = t[1] - 8.0 <= h[1] <= u[1] + 8.0
                if not inside:
                    continue
                overlap = min(h[3], span1) - max(h[2], span0)
                if overlap >= 0.8 * h[4] and abs(h[4] - t[4]) < 250.0 and _paint(h[6], False):
                    n_demote += 1
            for h in parsed:
                if h[5] == WALL_LAYER or not (100.0 <= h[4] <= 900.0):
                    continue
                if h[0] == t[0]:
                    if min(abs(h[1] - t[1]), abs(h[1] - u[1])) > 160.0:
                        continue
                    if min(h[3], span1) - max(h[2], span0) > 80.0:
                        continue
                    abuts = (abs(h[3] - span0) <= 80.0 and h[2] < span0) or (
                        abs(h[2] - span1) <= 80.0 and h[3] > span1
                    )
                else:
                    abuts = abs(h[1] - span0) <= 80.0 or abs(h[1] - span1) <= 80.0
                    covers = h[2] - 20.0 <= t[1] and h[3] + 20.0 >= u[1] and h[4] <= gap + 80.0
                    abuts = abuts and covers
                if abuts and _paint(h[6], True):
                    n_promote += 1
    return n_demote, n_promote


def correct_runthrough_frame_doors(msp) -> tuple[int, int]:
    """옆 벽이 문 안에서 시작해 문 끝만 조금 넘는 문. 문 구간은 내리고 양옆은 벽으로 둔다.

    일반영상검사실2 오른쪽 아래처럼 문짝이 채워진 이중면이 벽으로 올라갔고,
    25mm 밖 벽이 문 한가운데에서 시작해 문 끝을 40–500mm만 넘긴다.
    """
    parsed: list[tuple[str, float, float, float, float, str, Any]] = []
    for e in msp:
        if e.dxftype() != "LINE":
            continue
        a, b = e.dxf.start, e.dxf.end
        dx, dy = abs(b.x - a.x), abs(b.y - a.y)
        length = math.hypot(dx, dy)
        if length < 40.0:
            continue
        tol = max(15.0, 0.08 * length)
        if dy <= tol:
            parsed.append(("H", (a.y + b.y) / 2.0, min(a.x, b.x), max(a.x, b.x), length, e.dxf.layer, e))
        elif dx <= tol:
            parsed.append(("V", (a.x + b.x) / 2.0, min(a.y, b.y), max(a.y, b.y), length, e.dxf.layer, e))

    n_demote = 0
    n_promote = 0
    touched: set[int] = set()

    def _paint(ent, wall: bool) -> bool:
        eid = id(ent)
        if eid in touched:
            return False
        layer = WALL_LAYER if wall else BASE_LAYER
        if getattr(ent.dxf, "layer", None) == layer:
            return False
        touched.add(eid)
        ent.dxf.layer = layer
        if wall:
            ent.dxf.color = WALL_COLOR
        return True

    faces = [h for h in parsed if h[5] == WALL_LAYER and 1750.0 <= h[4] <= 2100.0]
    faces.sort(key=lambda h: (h[0], h[1], h[2]))
    seen: set[tuple] = set()
    for i, t in enumerate(faces):
        for u in faces[i + 1 :]:
            if u[0] != t[0]:
                continue
            if u[1] - t[1] > 200.0:
                break
            gap = u[1] - t[1]
            if not (120.0 <= gap <= 180.0):
                continue
            if abs(t[2] - u[2]) > 80.0 or abs(t[3] - u[3]) > 80.0:
                continue
            leaves = [
                h
                for h in parsed
                if h[0] == t[0] and t[1] + 8.0 < h[1] < u[1] - 8.0 and 700.0 <= h[4] <= 1100.0
            ]
            if len(leaves) < 4:
                continue
            span0, span1 = t[2], t[3]
            key = (t[0], round(t[1]), round(span0 / 20.0))
            if key in seen:
                continue
            overs = []
            for h in parsed:
                if h[5] != WALL_LAYER or h[0] != t[0]:
                    continue
                offset = min(abs(h[1] - t[1]), abs(h[1] - u[1]))
                if not (15.0 <= offset <= 50.0) or t[1] - 5.0 <= h[1] <= u[1] + 5.0:
                    continue
                overlap = min(h[3], span1) - max(h[2], span0)
                if overlap < 400.0:
                    continue
                starts_inside = span0 + 200.0 <= h[2] <= span1 - 200.0
                ends_inside = span0 + 200.0 <= h[3] <= span1 - 200.0
                past_end = span1 + 20.0 <= h[3] <= span1 + 500.0
                past_start = span0 - 500.0 <= h[2] <= span0 - 20.0
                if (starts_inside and past_end) or (ends_inside and past_start):
                    overs.append(h)
            if not overs:
                continue
            seen.add(key)
            for h in parsed:
                if h[0] != t[0] or h[5] != WALL_LAYER:
                    continue
                if min(abs(h[1] - t[1]), abs(h[1] - u[1])) > 8.0:
                    continue
                overlap = min(h[3], span1) - max(h[2], span0)
                if overlap >= 0.7 * h[4] and abs(h[4] - t[4]) < 250.0 and _paint(h[6], False):
                    n_demote += 1
            side_coords = [t[1], u[1]] + [h[1] for h in overs]
            for h in parsed:
                if h[5] == WALL_LAYER or h[0] != t[0] or not (150.0 <= h[4] <= 900.0):
                    continue
                if min(abs(h[1] - c) for c in side_coords) > 8.0:
                    continue
                if min(h[3], span1) - max(h[2], span0) > 80.0:
                    continue
                abuts = (abs(h[3] - span0) <= 90.0 and h[2] < span0) or (
                    abs(h[2] - span1) <= 90.0 and h[3] > span1
                )
                if abuts and _paint(h[6], True):
                    n_promote += 1
    return n_demote, n_promote


def correct_room_header_doors(msp) -> tuple[int, int]:
    """방 위쪽 벽의 문. 문 구간은 내리고 맞닿은 양옆은 벽으로 둔다.

    조정실 상단처럼 문짝이 이중면을 채운 긴 구간, 준비실 상단처럼
    가까운 스윙 두 개 사이의 짧은 문머리를 문으로 본다.
    """
    parsed: list[tuple[str, float, float, float, float, str, Any]] = []
    arcs: list[tuple[float, float, float]] = []
    for e in msp:
        if e.dxftype() == "ARC":
            radius = float(e.dxf.radius)
            if 850.0 <= radius <= 1050.0:
                center = e.dxf.center
                arcs.append((center.x, center.y, radius))
            continue
        if e.dxftype() != "LINE":
            continue
        a, b = e.dxf.start, e.dxf.end
        dx, dy = abs(b.x - a.x), abs(b.y - a.y)
        length = math.hypot(dx, dy)
        if length < 40.0:
            continue
        tol = max(15.0, 0.08 * length)
        if dy <= tol:
            parsed.append(("H", (a.y + b.y) / 2.0, min(a.x, b.x), max(a.x, b.x), length, e.dxf.layer, e))
        elif dx <= tol:
            parsed.append(("V", (a.x + b.x) / 2.0, min(a.y, b.y), max(a.y, b.y), length, e.dxf.layer, e))

    n_demote = 0
    n_promote = 0
    touched: set[int] = set()

    def _paint(ent, wall: bool) -> bool:
        eid = id(ent)
        if eid in touched:
            return False
        layer = WALL_LAYER if wall else BASE_LAYER
        if getattr(ent.dxf, "layer", None) == layer:
            return False
        touched.add(eid)
        ent.dxf.layer = layer
        if wall:
            ent.dxf.color = WALL_COLOR
        return True

    faces = [h for h in parsed if 2000.0 <= h[4] <= 3000.0]
    faces.sort(key=lambda h: (h[0], h[1], h[2]))
    seen: set[tuple] = set()
    for i, t in enumerate(faces):
        for u in faces[i + 1 :]:
            if u[0] != t[0]:
                continue
            if u[1] - t[1] > 200.0:
                break
            gap = u[1] - t[1]
            if not (120.0 <= gap <= 180.0):
                continue
            lo, hi = max(t[2], u[2]), min(t[3], u[3])
            span = hi - lo
            if not (2000.0 <= span <= 2800.0):
                continue
            interiors = [
                h
                for h in parsed
                if h[0] == t[0]
                and t[1] + 12.0 < h[1] < u[1] - 12.0
                and 700.0 <= h[4] <= 1400.0
                and min(h[3], hi) - max(h[2], lo) >= 0.7 * h[4]
            ]
            if len(interiors) < 4:
                continue
            segs = sorted((max(h[2], lo), min(h[3], hi)) for h in interiors)
            covered = 0.0
            end = -1.0e18
            for a, b in segs:
                if b <= end:
                    continue
                covered += b - max(a, end)
                end = max(end, b)
            if covered < 0.8 * span:
                continue
            face_wall = [
                h
                for h in parsed
                if h[0] == t[0]
                and h[5] == WALL_LAYER
                and (abs(h[1] - t[1]) <= 8.0 or abs(h[1] - u[1]) <= 8.0)
                and min(h[3], hi) - max(h[2], lo) >= 0.7 * span
                and abs(h[4] - span) < 250.0
            ]
            if not face_wall:
                continue
            key = (t[0], round(lo / 50.0), round(t[1] / 20.0))
            if key in seen:
                continue

            def _sides(end_at: float, outward: int):
                found = []
                for h in parsed:
                    if h[0] != t[0] or min(abs(h[1] - t[1]), abs(h[1] - u[1])) > 8.0:
                        continue
                    if not (250.0 <= h[4] <= 2200.0):
                        continue
                    if min(h[3], hi) - max(h[2], lo) > 80.0:
                        continue
                    if outward > 0 and 0.0 <= h[2] - end_at <= 220.0:
                        found.append(h)
                    elif outward < 0 and 0.0 <= end_at - h[3] <= 220.0:
                        found.append(h)
                return found

            if not _sides(lo, -1) or not _sides(hi, 1):
                continue
            seen.add(key)
            for h in face_wall:
                if _paint(h[6], False):
                    n_demote += 1
            for h in _sides(lo, -1) + _sides(hi, 1):
                if h[5] != WALL_LAYER and _paint(h[6], True):
                    n_promote += 1

    for i, (x0, y0, r0) in enumerate(arcs):
        for x1, y1, r1 in arcs[i + 1 :]:
            if abs(r0 - r1) > 30.0:
                continue
            dx, dy = abs(x1 - x0), abs(y1 - y0)
            if min(dx, dy) > 40.0:
                continue
            dist = math.hypot(x1 - x0, y1 - y0)
            if not (300.0 <= dist <= 500.0):
                continue
            ori = "H" if dy <= dx else "V"
            lo, hi = (min(x0, x1), max(x0, x1)) if ori == "H" else (min(y0, y1), max(y0, y1))
            coord = (y0 + y1) / 2.0 if ori == "H" else (x0 + x1) / 2.0

            def _flank(side: int):
                found = []
                for h in parsed:
                    if h[0] != ori or not (700.0 <= h[4] <= 1200.0):
                        continue
                    if abs(h[1] - coord) > 180.0:
                        continue
                    if min(h[3], hi) - max(h[2], lo) > 40.0:
                        continue
                    if side > 0 and abs(h[2] - hi) <= 50.0 and h[2] >= hi - 20.0:
                        found.append(h)
                    elif side < 0 and abs(h[3] - lo) <= 50.0 and h[3] <= lo + 20.0:
                        found.append(h)
                return found

            left, right = _flank(-1), _flank(1)
            if not left or not right:
                continue
            if not any(h[5] == WALL_LAYER for h in left + right):
                continue
            if not any(h[5] != WALL_LAYER for h in left + right):
                continue
            for h in parsed:
                if h[0] != ori or h[5] != WALL_LAYER or abs(h[1] - coord) > 180.0:
                    continue
                if h[2] < lo - 20.0 or h[3] > hi + 20.0:
                    continue
                if h[4] < 150.0:
                    continue
                if _paint(h[6], False):
                    n_demote += 1
            for h in left + right:
                if h[5] == WALL_LAYER:
                    continue
                mate = any(
                    s[5] == WALL_LAYER and abs(s[1] - h[1]) <= 15.0
                    for s in (right if h in left else left)
                )
                if mate and _paint(h[6], True):
                    n_promote += 1
    return n_demote, n_promote


def correct_split_face_doors(msp) -> tuple[int, int]:
    """한쪽 면만 벽인 이중 개구. 그 면은 내리고 양옆은 벽으로 둔다.

    MRI2 왼쪽처럼 같은 길이의 두 면 중 한 면만 벽이고, 회색 면은
    문 밖에서 벽으로 이어진다. 바로 아래의 회색 문은 두고, 비어 있는 옆면만 채운다.
    스윙 반지름과 같은 문선은 옆벽으로 복사하지 않는다.
    """
    parsed: list[tuple[str, float, float, float, float, str, Any]] = []
    arcs: list[tuple[float, float, float]] = []
    for e in msp:
        if e.dxftype() == "ARC":
            radius = float(e.dxf.radius)
            if 900.0 <= radius <= 1600.0:
                center = e.dxf.center
                arcs.append((center.x, center.y, radius))
        if e.dxftype() != "LINE":
            continue
        a, b = e.dxf.start, e.dxf.end
        dx, dy = abs(b.x - a.x), abs(b.y - a.y)
        length = math.hypot(dx, dy)
        if length < 40.0:
            continue
        tol = max(15.0, 0.08 * length)
        if dy <= tol:
            parsed.append(("H", (a.y + b.y) / 2.0, min(a.x, b.x), max(a.x, b.x), length, e.dxf.layer, e))
        elif dx <= tol:
            parsed.append(("V", (a.x + b.x) / 2.0, min(a.y, b.y), max(a.y, b.y), length, e.dxf.layer, e))

    n_demote = 0
    n_promote = 0
    touched: set[int] = set()

    def _paint(ent, wall: bool) -> bool:
        eid = id(ent)
        if eid in touched:
            return False
        layer = WALL_LAYER if wall else BASE_LAYER
        if getattr(ent.dxf, "layer", None) == layer:
            return False
        touched.add(eid)
        ent.dxf.layer = layer
        if wall:
            ent.dxf.color = WALL_COLOR
        return True

    def _same(ori: str, coord: float, s0: float, s1: float, layer: str):
        return [
            h
            for h in parsed
            if h[0] == ori
            and abs(h[1] - coord) <= 8.0
            and h[5] == layer
            and abs(h[2] - s0) < 80.0
            and abs(h[3] - s1) < 80.0
            and abs(h[4] - (s1 - s0)) < 250.0
        ]

    def _covered(ori: str, coord: float, a: float, b: float) -> bool:
        for h in parsed:
            if h[0] != ori or abs(h[1] - coord) > 8.0:
                continue
            if min(h[3], b) - max(h[2], a) >= 0.7 * (b - a):
                return True
        return False

    faces = [h for h in parsed if 1500.0 <= h[4] <= 2100.0]
    faces.sort(key=lambda h: (h[0], h[1], h[2]))
    seen: set[tuple] = set()
    for i, t in enumerate(faces):
        for u in faces[i + 1 :]:
            if u[0] != t[0]:
                continue
            if u[1] - t[1] > 200.0:
                break
            gap = u[1] - t[1]
            if not (120.0 <= gap <= 180.0):
                continue
            if abs(t[2] - u[2]) > 80.0 or abs(t[3] - u[3]) > 80.0:
                continue
            s0, s1 = max(t[2], u[2]), min(t[3], u[3])
            key = (t[0], round(t[1]), round(s0 / 50.0))
            if key in seen:
                continue
            tw, tb = _same(t[0], t[1], s0, s1, WALL_LAYER), _same(t[0], t[1], s0, s1, BASE_LAYER)
            uw, ub = _same(u[0], u[1], s0, s1, WALL_LAYER), _same(u[0], u[1], s0, s1, BASE_LAYER)
            if tw and not tb and ub and not uw:
                wall_c, base_c = t[1], u[1]
            elif uw and not ub and tb and not tw:
                wall_c, base_c = u[1], t[1]
            else:
                continue
            continued = any(
                h[0] == t[0]
                and h[5] == WALL_LAYER
                and abs(h[1] - base_c) <= 8.0
                and h[4] >= 800.0
                and min(h[3], s1) - max(h[2], s0) <= 100.0
                and (0.0 <= h[2] - s1 <= 80.0 or 0.0 <= s0 - h[3] <= 80.0)
                for h in parsed
            )
            if not continued:
                continue
            seen.add(key)
            lo_c, hi_c = min(wall_c, base_c), max(wall_c, base_c)
            for h in parsed:
                if h[0] != t[0] or h[5] != WALL_LAYER:
                    continue
                if abs(h[1] - base_c) <= 8.0 or not (lo_c - 8.0 <= h[1] <= hi_c + 8.0):
                    continue
                overlap = min(h[3], s1) - max(h[2], s0)
                if overlap >= 0.8 * h[4] and abs(h[4] - (s1 - s0)) < 250.0 and _paint(h[6], False):
                    n_demote += 1
            for h in parsed:
                if h[0] != t[0] or h[5] == WALL_LAYER or abs(h[1] - wall_c) > 8.0:
                    continue
                if not (200.0 <= h[4] <= 900.0):
                    continue
                if min(h[3], s1) - max(h[2], s0) > 40.0:
                    continue
                abuts = (0.0 <= s0 - h[3] <= 80.0) or (0.0 <= h[2] - s1 <= 80.0)
                if abuts and _paint(h[6], True):
                    n_promote += 1
            for n0, n1 in (
                (s0 - 2200.0, s0),
                (s1, s1 + 2200.0),
            ):
                mates = []
                for h in parsed:
                    if h[0] != t[0] or h[5] != BASE_LAYER or not (1400.0 <= h[4] <= 2000.0):
                        continue
                    if min(abs(h[1] - wall_c), abs(h[1] - base_c)) > 8.0:
                        continue
                    if n1 <= s0:
                        if abs(h[3] - s0) > 100.0 or h[2] >= s0:
                            continue
                    else:
                        if abs(h[2] - s1) > 100.0 or h[3] <= s1:
                            continue
                    mates.append(h)
                if not any(abs(h[1] - wall_c) <= 8.0 for h in mates):
                    continue
                if not any(abs(h[1] - base_c) <= 8.0 for h in mates):
                    continue
                far = min(h[2] for h in mates) if n1 <= s0 else max(h[3] for h in mates)
                for h in parsed:
                    if h[0] != t[0] or h[5] != WALL_LAYER or not (400.0 <= h[4] <= 2500.0):
                        continue
                    if min(abs(h[1] - wall_c), abs(h[1] - base_c)) > 8.0:
                        continue
                    if n1 <= s0:
                        if not (0.0 <= far - h[3] <= 80.0 and h[2] < far):
                            continue
                    elif not (0.0 <= h[2] - far <= 80.0 and h[3] > far):
                        continue
                    near_hinge = False
                    for cx, cy, radius in arcs:
                        if not (0.9 * radius <= h[4] <= 1.15 * radius):
                            continue
                        ends = ((h[1], h[2]), (h[1], h[3])) if h[0] == "V" else ((h[2], h[1]), (h[3], h[1]))
                        if any(math.hypot(ex - cx, ey - cy) <= 200.0 for ex, ey in ends):
                            near_hinge = True
                            break
                    if near_hinge:
                        continue
                    other = base_c if abs(h[1] - wall_c) <= 8.0 else wall_c
                    if _covered(t[0], other, h[2], h[3]):
                        continue
                    if t[0] == "V":
                        msp.add_line(
                            (other, h[2]),
                            (other, h[3]),
                            dxfattribs={"layer": WALL_LAYER, "color": WALL_COLOR},
                        )
                    else:
                        msp.add_line(
                            (h[2], other),
                            (h[3], other),
                            dxfattribs={"layer": WALL_LAYER, "color": WALL_COLOR},
                        )
                    parsed.append((t[0], other, h[2], h[3], h[3] - h[2], WALL_LAYER, None))
                    n_promote += 1
    return n_demote, n_promote


def correct_hinge_panel_doors(msp) -> tuple[int, int]:
    """가까운 스윙 힌지에 붙은 짧은 면은 문이다. 문은 내리고 바깥 벽은 둔다.

    준비실·물품창고 상단처럼 반지름 약 940mm인 스윙 두 개가 가깝고,
    그 힌지 밖에 700–1200mm 면이 붙어 있다.
    """
    parsed: list[tuple[str, float, float, float, float, str, Any]] = []
    arcs: list[tuple[float, float, float]] = []
    for e in msp:
        if e.dxftype() == "ARC":
            radius = float(e.dxf.radius)
            if 850.0 <= radius <= 1050.0:
                center = e.dxf.center
                arcs.append((center.x, center.y, radius))
            continue
        if e.dxftype() != "LINE":
            continue
        a, b = e.dxf.start, e.dxf.end
        dx, dy = abs(b.x - a.x), abs(b.y - a.y)
        length = math.hypot(dx, dy)
        if length < 40.0:
            continue
        tol = max(15.0, 0.08 * length)
        if dy <= tol:
            parsed.append(("H", (a.y + b.y) / 2.0, min(a.x, b.x), max(a.x, b.x), length, e.dxf.layer, e))
        elif dx <= tol:
            parsed.append(("V", (a.x + b.x) / 2.0, min(a.y, b.y), max(a.y, b.y), length, e.dxf.layer, e))

    n_demote = 0
    n_promote = 0
    touched: set[int] = set()

    def _paint(ent, wall: bool) -> bool:
        eid = id(ent)
        if eid in touched:
            return False
        layer = WALL_LAYER if wall else BASE_LAYER
        if getattr(ent.dxf, "layer", None) == layer:
            return False
        touched.add(eid)
        ent.dxf.layer = layer
        if wall:
            ent.dxf.color = WALL_COLOR
        return True

    for i, (x0, y0, r0) in enumerate(arcs):
        for x1, y1, r1 in arcs[i + 1 :]:
            if abs(r0 - r1) > 30.0:
                continue
            dx, dy = abs(x1 - x0), abs(y1 - y0)
            if min(dx, dy) > 40.0:
                continue
            dist = math.hypot(dx, dy)
            if not (300.0 <= dist <= 500.0):
                continue
            ori = "H" if dy <= dx else "V"
            lo, hi = (min(x0, x1), max(x0, x1)) if ori == "H" else (min(y0, y1), max(y0, y1))
            coord = (y0 + y1) / 2.0 if ori == "H" else (x0 + x1) / 2.0
            panels = []
            for h in parsed:
                if h[0] != ori or abs(h[1] - coord) > 180.0 or not (700.0 <= h[4] <= 1200.0):
                    continue
                if min(h[3], hi) - max(h[2], lo) > 40.0:
                    continue
                abuts_lo = abs(h[3] - lo) <= 60.0 and h[2] < lo
                abuts_hi = abs(h[2] - hi) <= 60.0 and h[3] > hi
                if not (abuts_lo or abuts_hi):
                    continue
                panels.append(h)
                if h[5] == WALL_LAYER and h[6] is not None and _paint(h[6], False):
                    n_demote += 1
            for h in panels:
                outer = h[2] if h[3] <= lo + 60.0 else h[3]
                for s in parsed:
                    if s[0] != ori or s[5] == WALL_LAYER or not (800.0 <= s[4] <= 6000.0):
                        continue
                    if abs(s[1] - h[1]) > 30.0:
                        continue
                    if min(s[3], h[3]) - max(s[2], h[2]) > 40.0:
                        continue
                    if outer == h[2] and 0.0 <= outer - s[3] <= 80.0 and s[2] < outer:
                        if _paint(s[6], True):
                            n_promote += 1
                    elif outer == h[3] and 0.0 <= s[2] - outer <= 80.0 and s[3] > outer:
                        if _paint(s[6], True):
                            n_promote += 1
    return n_demote, n_promote


def correct_radius_jamb_doors(msp) -> tuple[int, int]:
    """스윙 힌지에서 문 폭만큼 올라간 세로선은 문이다. 문은 내리고 양옆은 벽으로 둔다.

    MRI2 왼쪽 아래처럼 문짝과 수직이고 길이가 반지름과 같은 선이 힌지에 붙어 있다.
    """
    parsed: list[tuple[str, float, float, float, float, str, Any]] = []
    arcs: list[tuple[float, float, float]] = []
    for e in msp:
        if e.dxftype() == "ARC":
            radius = float(e.dxf.radius)
            if 850.0 <= radius <= 1600.0:
                center = e.dxf.center
                arcs.append((center.x, center.y, radius))
            continue
        if e.dxftype() != "LINE":
            continue
        a, b = e.dxf.start, e.dxf.end
        dx, dy = abs(b.x - a.x), abs(b.y - a.y)
        length = math.hypot(dx, dy)
        if length < 40.0:
            continue
        tol = max(15.0, 0.08 * length)
        if dy <= tol:
            parsed.append(("H", (a.y + b.y) / 2.0, min(a.x, b.x), max(a.x, b.x), length, e.dxf.layer, e))
        elif dx <= tol:
            parsed.append(("V", (a.x + b.x) / 2.0, min(a.y, b.y), max(a.y, b.y), length, e.dxf.layer, e))

    n_demote = 0
    n_promote = 0
    touched: set[int] = set()

    def _paint(ent, wall: bool) -> bool:
        if ent is None:
            return False
        eid = id(ent)
        if eid in touched:
            return False
        layer = WALL_LAYER if wall else BASE_LAYER
        if getattr(ent.dxf, "layer", None) == layer:
            return False
        touched.add(eid)
        ent.dxf.layer = layer
        if wall:
            ent.dxf.color = WALL_COLOR
        return True

    for cx, cy, radius in arcs:
        leaves = []
        for h in parsed:
            if h[5] != BASE_LAYER or not (0.9 * radius <= h[4] <= 1.15 * radius):
                continue
            ends = ((h[2], h[1]), (h[3], h[1])) if h[0] == "H" else ((h[1], h[2]), (h[1], h[3]))
            if any(abs(ex - cx) <= 50.0 and abs(ey - cy) <= 50.0 for ex, ey in ends):
                leaves.append(h)
        if not leaves:
            continue
        for leaf_ori in list(dict.fromkeys(h[0] for h in leaves)):
            door_ori = "V" if leaf_ori == "H" else "H"
            seeds = []
            for h in parsed:
                if h[0] != door_ori or h[5] != WALL_LAYER:
                    continue
                if not (0.9 * radius <= h[4] <= 1.15 * radius):
                    continue
                if door_ori == "V":
                    near = (abs(h[2] - cy) <= 40.0 and 80.0 <= abs(h[1] - cx) <= 180.0) or (
                        abs(h[3] - cy) <= 40.0 and 80.0 <= abs(h[1] - cx) <= 180.0
                    )
                else:
                    near = (abs(h[2] - cx) <= 40.0 and 80.0 <= abs(h[1] - cy) <= 180.0) or (
                        abs(h[3] - cx) <= 40.0 and 80.0 <= abs(h[1] - cy) <= 180.0
                    )
                if near:
                    seeds.append(h)
            if not seeds:
                continue
            span0 = min(h[2] for h in seeds)
            span1 = max(h[3] for h in seeds)
            coords = [h[1] for h in seeds]
            victims = list(seeds)
            for h in parsed:
                if h[0] != door_ori or h[5] != WALL_LAYER or h in victims:
                    continue
                if min(abs(h[1] - c) for c in coords) > 160.0:
                    continue
                if abs(h[4] - seeds[0][4]) > 200.0:
                    continue
                if abs(h[2] - span0) > 80.0 or abs(h[3] - span1) > 80.0:
                    continue
                victims.append(h)
                coords.append(h[1])
            for h in victims:
                if _paint(h[6], False):
                    n_demote += 1
            span0 = min(h[2] for h in victims)
            span1 = max(h[3] for h in victims)
            for h in parsed:
                if h[0] != door_ori or h[5] == WALL_LAYER or not (200.0 <= h[4] <= 2000.0):
                    continue
                if min(abs(h[1] - c) for c in coords) > 12.0:
                    continue
                if min(h[3], span1) - max(h[2], span0) > 40.0:
                    continue
                abuts = (0.0 <= span0 - h[3] <= 80.0) or (0.0 <= h[2] - span1 <= 80.0)
                if not abuts:
                    continue
                if _paint(h[6], True):
                    n_promote += 1
                for mate in parsed:
                    if mate[0] != h[0] or mate[5] == WALL_LAYER:
                        continue
                    gap = abs(mate[1] - h[1])
                    if not (120.0 <= gap <= 180.0):
                        continue
                    if abs(mate[2] - h[2]) > 40.0 or abs(mate[3] - h[3]) > 40.0:
                        continue
                    if _paint(mate[6], True):
                        n_promote += 1
    return n_demote, n_promote


def promote_partition_extensions(msp) -> int:
    """이중벽이 끝에서만 회색이면 그 연장도 벽이다.

    준비실과 물품창고 사이처럼 두 면이 같이 끊기고, 회색 선이 그 끝으로 이어진다.
    힌지에서 방 안으로 내려온 문짝은 그 연장과 겹쳐도 벽으로 올리지 않는다.
    """
    parsed: list[tuple[str, float, float, float, float, str, Any]] = []
    arcs: list[tuple[float, float, float]] = []
    for e in msp:
        if e.dxftype() == "ARC":
            radius = float(e.dxf.radius)
            if 700.0 <= radius <= 1200.0:
                center = e.dxf.center
                arcs.append((center.x, center.y, radius))
            continue
        if e.dxftype() != "LINE":
            continue
        a, b = e.dxf.start, e.dxf.end
        dx, dy = abs(b.x - a.x), abs(b.y - a.y)
        length = math.hypot(dx, dy)
        if length < 80.0:
            continue
        tol = max(15.0, 0.08 * length)
        if dy <= tol:
            parsed.append(("H", (a.y + b.y) / 2.0, min(a.x, b.x), max(a.x, b.x), length, e.dxf.layer, e))
        elif dx <= tol:
            parsed.append(("V", (a.x + b.x) / 2.0, min(a.y, b.y), max(a.y, b.y), length, e.dxf.layer, e))

    n_promote = 0
    touched: set[int] = set()

    def _paint(ent) -> bool:
        eid = id(ent)
        if eid in touched or getattr(ent.dxf, "layer", None) == WALL_LAYER:
            return False
        touched.add(eid)
        ent.dxf.layer = WALL_LAYER
        ent.dxf.color = WALL_COLOR
        return True

    walls = [h for h in parsed if h[5] == WALL_LAYER and h[4] >= 1500.0]
    walls.sort(key=lambda h: (h[0], h[1], h[2]))
    seen: set[tuple] = set()
    for i, t in enumerate(walls):
        for u in walls[i + 1 :]:
            if u[0] != t[0]:
                continue
            if u[1] - t[1] > 200.0:
                break
            gap = u[1] - t[1]
            if not (120.0 <= gap <= 180.0):
                continue
            for end in ("hi", "lo"):
                if end == "hi" and abs(t[3] - u[3]) > 40.0:
                    continue
                if end == "lo" and abs(t[2] - u[2]) > 40.0:
                    continue
                stop = max(t[3], u[3]) if end == "hi" else min(t[2], u[2])
                key = (t[0], round(t[1] / 40.0), round(stop / 40.0), end)
                if key in seen:
                    continue
                extensions = []
                for face in (t, u):
                    for h in parsed:
                        if h[5] != BASE_LAYER or h[0] != t[0] or abs(h[1] - face[1]) > 8.0:
                            continue
                        if end == "hi" and abs(h[2] - face[2]) < 40.0 and h[3] > stop + 700.0:
                            extensions.append(h)
                        elif end == "lo" and abs(h[3] - face[3]) < 40.0 and h[2] < stop - 700.0:
                            extensions.append(h)
                if len(extensions) < 2:
                    continue
                lengths = [(h[3] - stop) if end == "hi" else (stop - h[2]) for h in extensions]
                if not all(700.0 <= length <= 1100.0 for length in lengths):
                    continue
                if max(lengths) - min(lengths) > 80.0:
                    continue
                seen.add(key)
                ext = lengths[0]
                band0, band1 = (stop - 40.0, stop + ext + 40.0) if end == "hi" else (stop - ext - 40.0, stop + 40.0)
                for h in extensions:
                    if _paint(h[6]):
                        n_promote += 1
                for h in parsed:
                    if h[0] != t[0] or h[5] != BASE_LAYER or not (700.0 <= h[4] <= 1100.0):
                        continue
                    if min(abs(h[1] - t[1]), abs(h[1] - u[1])) > 200.0:
                        continue
                    if h[2] < band0 or h[3] > band1:
                        continue
                    ends = ((h[1], h[2]), (h[1], h[3])) if h[0] == "V" else ((h[2], h[1]), (h[3], h[1]))
                    if any(
                        abs(h[4] - radius) <= 0.12 * radius
                        and abs(px - cx) <= 50.0
                        and abs(py - cy) <= 50.0
                        for cx, cy, radius in arcs
                        for px, py in ends
                    ):
                        continue
                    if _paint(h[6]):
                        n_promote += 1
    return n_promote


def promote_one_sided_swing_gaps(msp) -> int:
    """긴 벽이 스윙 힌지 쪽에서만 문 폭만큼 회색이고 반대쪽은 이미 벽이면 그 구간도 벽이다.

    MRI2 아래처럼 회색은 벽선 자체이고, 문짝은 그 선에서 떨어진 짧은 선이다.
    """
    parsed: list[tuple[str, float, float, float, float, str, Any]] = []
    arcs: list[tuple[float, float, float]] = []
    for e in msp:
        if e.dxftype() == "ARC":
            radius = float(e.dxf.radius)
            if 900.0 <= radius <= 1600.0:
                center = e.dxf.center
                arcs.append((center.x, center.y, radius))
            continue
        if e.dxftype() != "LINE":
            continue
        a, b = e.dxf.start, e.dxf.end
        dx, dy = abs(b.x - a.x), abs(b.y - a.y)
        length = math.hypot(dx, dy)
        if length < 80.0:
            continue
        tol = max(15.0, 0.08 * length)
        if dy <= tol:
            parsed.append(("H", (a.y + b.y) / 2.0, min(a.x, b.x), max(a.x, b.x), length, e.dxf.layer, e))
        elif dx <= tol:
            parsed.append(("V", (a.x + b.x) / 2.0, min(a.y, b.y), max(a.y, b.y), length, e.dxf.layer, e))

    n_promote = 0
    touched: set[int] = set()
    for base in parsed:
        if base[5] != BASE_LAYER or base[4] < 2500.0:
            continue
        covers = sorted(
            (h[2], h[3])
            for h in parsed
            if h[5] == WALL_LAYER
            and h[0] == base[0]
            and abs(h[1] - base[1]) <= 8.0
            and min(h[3], base[3]) - max(h[2], base[2]) > 200.0
        )
        if not covers:
            continue
        cursor = base[2]
        gaps: list[tuple[float, float]] = []
        for start, end in covers:
            if start > cursor + 80.0:
                gaps.append((cursor, start))
            cursor = max(cursor, end)
        if base[3] > cursor + 80.0:
            gaps.append((cursor, base[3]))
        for gap0, gap1 in gaps:
            gap_length = gap1 - gap0
            if not (1000.0 <= gap_length <= 1600.0):
                continue

            def _long_side(at: float, before: bool) -> bool:
                return any(
                    h[5] == WALL_LAYER
                    and h[0] == base[0]
                    and abs(h[1] - base[1]) <= 8.0
                    and h[4] >= 1000.0
                    and (
                        (before and abs(h[3] - at) <= 40.0 and h[2] < at)
                        or (not before and abs(h[2] - at) <= 40.0 and h[3] > at)
                    )
                    for h in parsed
                )

            left = _long_side(gap0, True)
            right = _long_side(gap1, False)
            if left == right:
                continue
            open_end = gap0 if not left else gap1
            matched = False
            for center_x, center_y, radius in arcs:
                if abs(gap_length - radius) > 0.15 * max(gap_length, radius):
                    continue
                if base[0] == "H" and abs(center_x - open_end) <= 200.0 and abs(center_y - base[1]) <= 600.0:
                    matched = True
                elif base[0] == "V" and abs(center_y - open_end) <= 200.0 and abs(center_x - base[1]) <= 600.0:
                    matched = True
            if not matched:
                continue
            entity = base[6]
            if id(entity) in touched:
                continue
            touched.add(id(entity))
            entity.dxf.layer = WALL_LAYER
            entity.dxf.color = WALL_COLOR
            n_promote += 1
    return n_promote


def promote_opposed_door_sides(msp) -> tuple[int, int]:
    """위·아래 방의 문 사이 벽과 문 양옆은 벽이다.

    초음파검사실과 진료실2처럼 공유벽이 문 앞에서만 회색이고, 문짝이 양쪽에 있다.
    그 회색 벽과 짧은 문설주는 올리고, 개구 안의 문짝은 내린다.
    """
    parsed: list[tuple[str, float, float, float, float, str, Any]] = []
    for e in msp:
        if e.dxftype() != "LINE":
            continue
        a, b = e.dxf.start, e.dxf.end
        dx, dy = abs(b.x - a.x), abs(b.y - a.y)
        length = math.hypot(dx, dy)
        if length < 80.0:
            continue
        tol = max(15.0, 0.08 * length)
        if dy <= tol:
            parsed.append(("H", (a.y + b.y) / 2.0, min(a.x, b.x), max(a.x, b.x), length, e.dxf.layer, e))
        elif dx <= tol:
            parsed.append(("V", (a.x + b.x) / 2.0, min(a.y, b.y), max(a.y, b.y), length, e.dxf.layer, e))

    n_demote = 0
    n_promote = 0
    touched: set[int] = set()

    def _set(ent, wall: bool) -> bool:
        eid = id(ent)
        if eid in touched:
            return False
        layer = WALL_LAYER if wall else BASE_LAYER
        if getattr(ent.dxf, "layer", None) == layer:
            return False
        touched.add(eid)
        ent.dxf.layer = layer
        ent.dxf.color = WALL_COLOR if wall else BASE_COLOR
        return True

    seen: set[tuple] = set()
    for base in parsed:
        if base[5] != BASE_LAYER or base[4] < 2500.0:
            continue
        for wall in parsed:
            if wall[5] != WALL_LAYER or wall[0] != base[0] or abs(wall[1] - base[1]) > 8.0:
                continue
            if abs(wall[2] - base[2]) <= 40.0 and 700.0 <= base[3] - wall[3] <= 1100.0:
                stop, far = wall[3], base[3]
            elif abs(wall[3] - base[3]) <= 40.0 and 700.0 <= wall[2] - base[2] <= 1100.0:
                stop, far = wall[2], base[2]
            else:
                continue
            perp = any(
                h[5] == WALL_LAYER
                and h[0] != base[0]
                and abs(h[1] - far) <= 250.0
                and h[4] >= 400.0
                and h[2] - 40.0 <= base[1] <= h[3] + 40.0
                for h in parsed
            )
            leaves = []
            for h in parsed:
                if h[0] != base[0] or h[5] != BASE_LAYER or not (800.0 <= h[4] <= 1100.0):
                    continue
                offset = h[1] - base[1]
                if not (180.0 <= abs(offset) <= 500.0):
                    continue
                if min(abs(h[2] - stop), abs(h[3] - stop)) > 60.0:
                    continue
                if min(abs(h[2] - far), abs(h[3] - far)) > 80.0:
                    continue
                leaves.append(h[1])
            if not perp or len({1 if coord > base[1] else -1 for coord in leaves}) < 2:
                continue
            key = (base[0], round(base[1] / 10.0), round(far))
            if key in seen:
                continue
            seen.add(key)
            if _set(base[6], True):
                n_promote += 1
            for h in parsed:
                if h[0] == base[0] or h[5] != BASE_LAYER or not (150.0 <= h[4] <= 500.0):
                    continue
                if abs(h[1] - far) > 30.0:
                    continue
                if not any(abs(end - base[1]) <= 40.0 for end in (h[2], h[3])):
                    continue
                if not any(abs(end - coord) <= 80.0 for end in (h[2], h[3]) for coord in leaves):
                    continue
                mate = any(
                    m[5] == WALL_LAYER
                    and m[0] != base[0]
                    and 120.0 <= abs(m[1] - h[1]) <= 250.0
                    and min(m[3], h[3]) - max(m[2], h[2]) >= 0.8 * h[4]
                    for m in parsed
                )
                if mate and _set(h[6], True):
                    n_promote += 1
            for h in parsed:
                if h[5] != WALL_LAYER or h[0] == base[0] or not (800.0 <= h[4] <= 1100.0):
                    continue
                if not (30.0 <= abs(h[1] - far) <= 220.0):
                    continue
                near = [end for end in (h[2], h[3]) if any(abs(end - coord) <= 40.0 for coord in leaves)]
                if not near:
                    continue
                if min(abs(end - base[1]) for end in near) > min(abs(h[2] - base[1]), abs(h[3] - base[1])):
                    continue
                if _set(h[6], False):
                    n_demote += 1
    return n_demote, n_promote


def promote_aligned_wall_gaps(msp) -> int:
    """두 면이 같은 구간만 회색이면 그 구간도 벽이다.

    조혈모세포 처치보관실 왼쪽처럼 긴 이중벽의 회색 틈이 양쪽 면에서 맞는다.
    """
    parsed: list[tuple[str, float, float, float, float, str, Any]] = []
    for e in msp:
        if e.dxftype() != "LINE":
            continue
        a, b = e.dxf.start, e.dxf.end
        dx, dy = abs(b.x - a.x), abs(b.y - a.y)
        length = math.hypot(dx, dy)
        if length < 80.0:
            continue
        tol = max(15.0, 0.08 * length)
        if dy <= tol:
            parsed.append(("H", (a.y + b.y) / 2.0, min(a.x, b.x), max(a.x, b.x), length, e.dxf.layer, e))
        elif dx <= tol:
            parsed.append(("V", (a.x + b.x) / 2.0, min(a.y, b.y), max(a.y, b.y), length, e.dxf.layer, e))

    def gaps_of(line) -> list[tuple[float, float]]:
        covers = sorted(
            (h[2], h[3])
            for h in parsed
            if h[5] == WALL_LAYER
            and h[0] == line[0]
            and abs(h[1] - line[1]) <= 8.0
            and min(h[3], line[3]) - max(h[2], line[2]) > 100.0
        )
        cursor = line[2]
        found: list[tuple[float, float]] = []
        for start, end in covers:
            if start > cursor + 80.0:
                found.append((cursor, start))
            cursor = max(cursor, end)
        if line[3] > cursor + 80.0:
            found.append((cursor, line[3]))
        return [gap for gap in found if 700.0 <= gap[1] - gap[0] <= 1200.0]

    n_promote = 0
    touched: set[int] = set()
    bases = [h for h in parsed if h[5] == BASE_LAYER and h[4] >= 3000.0]
    bases.sort(key=lambda h: (h[0], h[1], h[2]))
    seen: set[tuple] = set()
    for i, left in enumerate(bases):
        for right in bases[i + 1 :]:
            if right[0] != left[0]:
                break
            if right[1] - left[1] > 250.0:
                break
            if not (150.0 <= right[1] - left[1] <= 220.0):
                continue
            if abs(left[2] - right[2]) > 40.0 or abs(left[3] - right[3]) > 40.0:
                continue
            aligned = []
            for a0, a1 in gaps_of(left):
                for b0, b1 in gaps_of(right):
                    if abs(a0 - b0) <= 50.0 and abs(a1 - b1) <= 50.0:
                        aligned.append((min(a0, b0), max(a1, b1)))
            if not aligned:
                continue
            key = (left[0], round(left[1] / 40.0), round(aligned[0][0] / 40.0))
            if key in seen:
                continue
            seen.add(key)
            for face in (left, right):
                for h in parsed:
                    if h[0] != face[0] or h[5] != BASE_LAYER or abs(h[1] - face[1]) > 40.0:
                        continue
                    if h[4] <= 1200.0:
                        continue
                    if not any(min(h[3], g1) - max(h[2], g0) >= 200.0 for g0, g1 in aligned):
                        continue
                    eid = id(h[6])
                    if eid in touched or getattr(h[6].dxf, "layer", None) == WALL_LAYER:
                        continue
                    touched.add(eid)
                    h[6].dxf.layer = WALL_LAYER
                    h[6].dxf.color = WALL_COLOR
                    n_promote += 1
    return n_promote


def promote_return_door_sides(msp) -> tuple[int, int]:
    """스윙 문짝은 내리고, 문 끝과 벽 사이의 짧은 면은 벽으로 둔다.

    조혈모세포 처치보관실 왼쪽 방의 가운데 문처럼, 문짝은 회색이고
    양옆의 짧은 벽만 빨갛다.
    """
    parsed: list[tuple[str, float, float, float, float, str, Any]] = []
    arcs: list[tuple[float, float, float]] = []
    for e in msp:
        if e.dxftype() == "ARC":
            radius = float(e.dxf.radius)
            if 850.0 <= radius <= 1050.0:
                center = e.dxf.center
                arcs.append((center.x, center.y, radius))
            continue
        if e.dxftype() != "LINE":
            continue
        a, b = e.dxf.start, e.dxf.end
        dx, dy = abs(b.x - a.x), abs(b.y - a.y)
        length = math.hypot(dx, dy)
        if length < 40.0:
            continue
        tol = max(15.0, 0.08 * length)
        if dy <= tol:
            parsed.append(("H", (a.y + b.y) / 2.0, min(a.x, b.x), max(a.x, b.x), length, e.dxf.layer, e))
        elif dx <= tol:
            parsed.append(("V", (a.x + b.x) / 2.0, min(a.y, b.y), max(a.y, b.y), length, e.dxf.layer, e))

    n_demote = 0
    n_promote = 0
    touched: set[int] = set()

    def _set(ent, wall: bool) -> bool:
        eid = id(ent)
        if eid in touched:
            return False
        layer = WALL_LAYER if wall else BASE_LAYER
        if getattr(ent.dxf, "layer", None) == layer:
            touched.add(eid)
            return False
        touched.add(eid)
        ent.dxf.layer = layer
        ent.dxf.color = WALL_COLOR if wall else BASE_COLOR
        return True

    for center_x, center_y, radius in arcs:
        leaves = [
            h
            for h in parsed
            if h[5] == BASE_LAYER
            and abs(h[4] - radius) <= 0.12 * radius
            and (
                (h[0] == "H" and abs(h[1] - center_y) <= 40.0 and (abs(h[2] - center_x) <= 50.0 or abs(h[3] - center_x) <= 50.0))
                or (h[0] == "V" and abs(h[1] - center_x) <= 40.0 and (abs(h[2] - center_y) <= 50.0 or abs(h[3] - center_y) <= 50.0))
            )
        ]
        for leaf in leaves:
            mates = [
                h
                for h in parsed
                if h[5] == BASE_LAYER
                and h[0] == leaf[0]
                and h is not leaf
                and 120.0 <= abs(h[1] - leaf[1]) <= 180.0
                and abs(h[2] - leaf[2]) <= 40.0
                and abs(h[3] - leaf[3]) <= 40.0
                and abs(h[4] - leaf[4]) <= 80.0
            ]
            if not mates:
                continue
            span0, span1 = leaf[2], leaf[3]
            faces = [leaf[1], mates[0][1]]
            for h in parsed:
                if h[5] != WALL_LAYER or h[0] != leaf[0]:
                    continue
                if min(abs(h[1] - face) for face in faces) > 8.0:
                    continue
                if abs(h[4] - leaf[4]) > 250.0:
                    continue
                overlap = min(h[3], span1) - max(h[2], span0)
                if overlap < 0.7 * h[4]:
                    continue
                if _set(h[6], False):
                    n_demote += 1
            for h in parsed:
                if h[5] != BASE_LAYER or h[0] != leaf[0]:
                    continue
                if min(abs(h[1] - face) for face in faces) > 8.0:
                    continue
                if not (80.0 <= h[4] <= 280.0):
                    continue
                if min(h[3], span1) - max(h[2], span0) > 20.0:
                    continue
                near = min(abs(h[2] - span0), abs(h[3] - span0), abs(h[2] - span1), abs(h[3] - span1))
                if near > 50.0:
                    continue
                far = h[3] if min(abs(h[2] - span0), abs(h[2] - span1)) <= 50.0 else h[2]
                perp = any(
                    p[5] == WALL_LAYER
                    and p[0] != h[0]
                    and abs(p[1] - far) <= 45.0
                    and p[4] >= 800.0
                    and p[2] - 50.0 <= h[1] <= p[3] + 50.0
                    for p in parsed
                )
                collinear = any(
                    p[5] == WALL_LAYER
                    and p[0] == h[0]
                    and abs(p[1] - h[1]) <= 8.0
                    and p[4] >= 800.0
                    and (abs(p[3] - far) <= 180.0 or abs(p[2] - far) <= 180.0)
                    and (p[3] < span0 + 20.0 or p[2] > span1 - 20.0)
                    for p in parsed
                )
                if (perp or collinear) and _set(h[6], True):
                    n_promote += 1
    return n_demote, n_promote


def promote_corner_wall_squares(msp) -> int:
    """벽이 한 변에서 끊기는 회색 정사각은 기둥이다. 네 변을 벽으로 둔다."""
    parsed: list[tuple[str, float, float, float, float, str, Any]] = []
    arcs: list[tuple[float, float, float]] = []
    for e in msp:
        if e.dxftype() == "ARC":
            start = float(e.dxf.start_angle)
            end = float(e.dxf.end_angle)
            sweep = (end - start) % 360.0
            if 60.0 <= sweep <= 120.0:
                center = e.dxf.center
                arcs.append((center.x, center.y, sweep))
            continue
        if e.dxftype() != "LINE":
            continue
        a, b = e.dxf.start, e.dxf.end
        dx, dy = abs(b.x - a.x), abs(b.y - a.y)
        length = math.hypot(dx, dy)
        if length < 40.0:
            continue
        tol = max(15.0, 0.08 * length)
        if dy <= tol:
            parsed.append(("H", (a.y + b.y) / 2.0, min(a.x, b.x), max(a.x, b.x), length, e.dxf.layer, e))
        elif dx <= tol:
            parsed.append(("V", (a.x + b.x) / 2.0, min(a.y, b.y), max(a.y, b.y), length, e.dxf.layer, e))

    def stops(edge: float, span0: float, span1: float, horizontal_edge: bool, outward_low: bool) -> bool:
        ends: list[float] = []
        for h in parsed:
            if h[5] != WALL_LAYER or h[4] < 2000.0:
                continue
            if horizontal_edge:
                if h[0] != "V" or not (span0 - 40.0 <= h[1] <= span1 + 40.0):
                    continue
            elif h[0] != "H" or not (span0 - 40.0 <= h[1] <= span1 + 40.0):
                continue
            if outward_low and abs(h[3] - edge) <= 45.0 and h[2] < edge - 200.0:
                ends.append(h[1])
            elif not outward_low and abs(h[2] - edge) <= 45.0 and h[3] > edge + 200.0:
                ends.append(h[1])
        ends = sorted(set(round(value) for value in ends))
        return any(80.0 <= ends[j] - ends[i] <= 250.0 for i in range(len(ends)) for j in range(i + 1, len(ends)))

    n_promote = 0
    touched: set[int] = set()
    bottoms = [h for h in parsed if h[0] == "H" and h[5] == BASE_LAYER and 500.0 <= h[4] <= 700.0]
    for bottom in bottoms:
        y0, x0, x1 = bottom[1], bottom[2], bottom[3]
        for top in bottoms:
            height = top[1] - y0
            if not (500.0 <= height <= 700.0):
                continue
            if abs(top[2] - x0) > 25.0 or abs(top[3] - x1) > 25.0:
                continue
            if abs((x1 - x0) - height) > 80.0:
                continue
            sides = [
                h
                for h in parsed
                if h[0] == "V"
                and h[5] == BASE_LAYER
                and abs(h[2] - y0) <= 30.0
                and abs(h[3] - top[1]) <= 30.0
                and (abs(h[1] - x0) <= 25.0 or abs(h[1] - x1) <= 25.0)
            ]
            if not any(abs(h[1] - x0) <= 25.0 for h in sides) or not any(abs(h[1] - x1) <= 25.0 for h in sides):
                continue
            if any(x0 + 40.0 <= cx <= x1 - 40.0 and y0 + 40.0 <= cy <= top[1] - 40.0 for cx, cy, _sweep in arcs):
                continue
            if not (
                stops(y0, x0, x1, True, True)
                or stops(top[1], x0, x1, True, False)
                or stops(x0, y0, top[1], False, True)
                or stops(x1, y0, top[1], False, False)
            ):
                continue
            for h in (bottom, top, *sides):
                eid = id(h[6])
                if eid in touched or getattr(h[6].dxf, "layer", None) == WALL_LAYER:
                    continue
                touched.add(eid)
                h[6].dxf.layer = WALL_LAYER
                h[6].dxf.color = WALL_COLOR
                n_promote += 1
    return n_promote


def promote_collinear_swing_sides(msp) -> tuple[int, int]:
    """스윙 문짝과 같은 선의 위·아래는 벽이다. 문짝은 내리고 그 양옆만 올린다.

    조혈모세포 처치보관실 옆방의 세로 문처럼, 문 폭의 이중면은 문이고
    같은 면에서 문 밖으로 이어진 선은 벽이다.
    """
    parsed: list[tuple[str, float, float, float, float, str, Any]] = []
    arcs: list[tuple[float, float, float]] = []
    for e in msp:
        if e.dxftype() == "ARC":
            radius = float(e.dxf.radius)
            sweep = (float(e.dxf.end_angle) - float(e.dxf.start_angle)) % 360.0
            if 850.0 <= radius <= 1050.0 and 70.0 <= sweep <= 110.0:
                center = e.dxf.center
                arcs.append((center.x, center.y, radius))
            continue
        if e.dxftype() != "LINE":
            continue
        a, b = e.dxf.start, e.dxf.end
        dx, dy = abs(b.x - a.x), abs(b.y - a.y)
        length = math.hypot(dx, dy)
        if length < 40.0:
            continue
        tol = max(15.0, 0.08 * length)
        if dy <= tol:
            parsed.append(("H", (a.y + b.y) / 2.0, min(a.x, b.x), max(a.x, b.x), length, e.dxf.layer, e))
        elif dx <= tol:
            parsed.append(("V", (a.x + b.x) / 2.0, min(a.y, b.y), max(a.y, b.y), length, e.dxf.layer, e))

    def covered(item) -> bool:
        return any(
            other[5] == WALL_LAYER
            and other[0] == item[0]
            and abs(other[1] - item[1]) <= 8.0
            and min(other[3], item[3]) - max(other[2], item[2]) > 0.8 * item[4]
            for other in parsed
        )

    n_demote = 0
    n_promote = 0
    touched: set[int] = set()
    seen: set[tuple] = set()

    def _set(ent, wall: bool) -> bool:
        eid = id(ent)
        if eid in touched:
            return False
        layer = WALL_LAYER if wall else BASE_LAYER
        if getattr(ent.dxf, "layer", None) == layer:
            touched.add(eid)
            return False
        touched.add(eid)
        ent.dxf.layer = layer
        ent.dxf.color = WALL_COLOR if wall else BASE_COLOR
        return True

    for center_x, center_y, radius in arcs:
        leaves = [
            h
            for h in parsed
            if h[5] == BASE_LAYER
            and abs(h[4] - radius) <= 40.0
            and (
                (h[0] == "H" and abs(h[1] - center_y) <= 40.0 and (abs(h[2] - center_x) <= 40.0 or abs(h[3] - center_x) <= 40.0))
                or (h[0] == "V" and abs(h[1] - center_x) <= 40.0 and (abs(h[2] - center_y) <= 40.0 or abs(h[3] - center_y) <= 40.0))
            )
        ]
        for leaf in leaves:
            mates = [
                h
                for h in parsed
                if h[5] == BASE_LAYER
                and h[0] == leaf[0]
                and h is not leaf
                and 120.0 <= abs(h[1] - leaf[1]) <= 180.0
                and abs(h[2] - leaf[2]) <= 40.0
                and abs(h[3] - leaf[3]) <= 40.0
                and abs(h[4] - radius) <= 40.0
            ]
            if not mates:
                continue
            key = (leaf[0], round(min(leaf[1], mates[0][1]) / 30.0), round(leaf[2] / 40.0))
            if key in seen:
                continue
            seen.add(key)
            span0, span1 = leaf[2], leaf[3]
            faces = [leaf[1], mates[0][1]]
            lo, hi = min(faces), max(faces)
            for h in parsed:
                if h[5] != WALL_LAYER or h[0] != leaf[0]:
                    continue
                on_face = min(abs(h[1] - face) for face in faces) <= 8.0
                between = lo + 15.0 < h[1] < hi - 15.0
                if not on_face and not between:
                    continue
                if abs(h[4] - radius) > 80.0:
                    continue
                overlap = min(h[3], span1) - max(h[2], span0)
                if overlap < 0.7 * h[4]:
                    continue
                if _set(h[6], False):
                    n_demote += 1
            sides = []
            for h in parsed:
                if h[5] != BASE_LAYER or h[0] != leaf[0] or covered(h):
                    continue
                if min(abs(h[1] - face) for face in faces) > 8.0:
                    continue
                if not (400.0 <= h[4] <= 6000.0):
                    continue
                if min(abs(h[3] - span0), abs(h[2] - span1)) > 80.0:
                    continue
                if min(h[3], span1) - max(h[2], span0) > 40.0:
                    continue
                sides.append(h)
            for h in sides:
                if _set(h[6], True):
                    n_promote += 1
            for h in parsed:
                if h[5] != BASE_LAYER or h[0] != leaf[0] or covered(h):
                    continue
                gap = min(abs(h[1] - face) for face in faces)
                if not (8.0 < gap <= 40.0):
                    continue
                if min(h[3], span1) - max(h[2], span0) > 40.0:
                    continue
                if any(min(h[3], side[3]) - max(h[2], side[2]) > 0.7 * min(h[4], side[4]) for side in sides):
                    if _set(h[6], True):
                        n_promote += 1
    return n_demote, n_promote


def promote_wall_face_continuations(msp) -> int:
    """문 너머의 회색 벽면과, 복도에서 긴 벽과 이어진 이중면은 벽이다.

    회의실·교육실 오른쪽처럼 한쪽 면만 회색인 구간과,
    그 아래 복도처럼 두 면이 같이 회색인 구간을 올린다. 문짝은 둔다.
    """
    parsed: list[tuple[str, float, float, float, float, str, Any]] = []
    arcs: list[tuple[float, float, float]] = []
    for e in msp:
        if e.dxftype() == "ARC":
            radius = float(e.dxf.radius)
            sweep = (float(e.dxf.end_angle) - float(e.dxf.start_angle)) % 360.0
            if 800.0 <= radius <= 1600.0 and 60.0 <= sweep <= 120.0:
                center = e.dxf.center
                arcs.append((center.x, center.y, radius))
            continue
        if e.dxftype() != "LINE":
            continue
        a, b = e.dxf.start, e.dxf.end
        dx, dy = abs(b.x - a.x), abs(b.y - a.y)
        length = math.hypot(dx, dy)
        if length < 40.0:
            continue
        tol = max(15.0, 0.08 * length)
        if dy <= tol:
            parsed.append(("H", (a.y + b.y) / 2.0, min(a.x, b.x), max(a.x, b.x), length, e.dxf.layer, e))
        elif dx <= tol:
            parsed.append(("V", (a.x + b.x) / 2.0, min(a.y, b.y), max(a.y, b.y), length, e.dxf.layer, e))

    def is_leaf(item) -> bool:
        for center_x, center_y, radius in arcs:
            if abs(item[4] - radius) > 80.0:
                continue
            if item[0] == "V" and abs(item[1] - center_x) <= 60.0 and (abs(item[2] - center_y) <= 60.0 or abs(item[3] - center_y) <= 60.0):
                return True
            if item[0] == "H" and abs(item[1] - center_y) <= 60.0 and (abs(item[2] - center_x) <= 60.0 or abs(item[3] - center_x) <= 60.0):
                return True
        return False

    def covered(item) -> bool:
        return any(
            other[5] == WALL_LAYER
            and other[0] == item[0]
            and abs(other[1] - item[1]) <= 8.0
            and min(other[3], item[3]) - max(other[2], item[2]) > 0.8 * item[4]
            for other in parsed
        )

    def continues_wall(item, min_length: float) -> bool:
        for other in parsed:
            if other[5] != WALL_LAYER or other[0] != item[0] or abs(other[1] - item[1]) > 8.0:
                continue
            if other[4] < min_length:
                continue
            if min(other[3], item[3]) - max(other[2], item[2]) > 40.0:
                continue
            gap = min(abs(other[3] - item[2]), abs(item[3] - other[2]))
            if 150.0 <= gap <= 250.0:
                return True
        return False

    n_promote = 0
    touched: set[int] = set()

    def _paint(item) -> None:
        nonlocal n_promote
        eid = id(item[6])
        if eid in touched or item[5] == WALL_LAYER:
            return
        touched.add(eid)
        item[6].dxf.layer = WALL_LAYER
        item[6].dxf.color = WALL_COLOR
        n_promote += 1

    for item in parsed:
        if item[5] != BASE_LAYER or covered(item) or is_leaf(item):
            continue
        if not (700.0 <= item[4] <= 2200.0):
            continue
        if not continues_wall(item, 2000.0):
            continue
        mate_wall = any(
            other[5] == WALL_LAYER
            and other[0] == item[0]
            and 150.0 <= abs(other[1] - item[1]) <= 250.0
            and min(other[3], item[3]) - max(other[2], item[2]) > 0.7 * item[4]
            for other in parsed
        )
        if mate_wall:
            _paint(item)

    bases = [item for item in parsed if item[5] == BASE_LAYER and 1200.0 <= item[4] <= 2200.0 and not is_leaf(item) and not covered(item)]
    seen: set[tuple] = set()
    for item in bases:
        mates = [
            other
            for other in bases
            if other[0] == item[0]
            and other is not item
            and 150.0 <= abs(other[1] - item[1]) <= 220.0
            and abs(other[2] - item[2]) <= 40.0
            and abs(other[3] - item[3]) <= 40.0
        ]
        if not mates:
            continue
        key = (item[0], round(min(item[1], mates[0][1]) / 20.0), round(item[2] / 20.0))
        if key in seen:
            continue
        if continues_wall(item, 3000.0) and continues_wall(mates[0], 3000.0):
            seen.add(key)
            _paint(item)
            _paint(mates[0])
    return n_promote


def correct_offset_hinge_leaves(msp) -> tuple[int, int]:
    """힌지에서 조금 비껴 선 문 폭의 선은 문짝이다. 내리고 양옆 벽은 올린다.

    기계실2 오른쪽 아래처럼, 힌지 면의 회색 문짝 옆에 같은 길이의 빨간 선이 있다.
    """
    parsed: list[tuple[str, float, float, float, float, str, Any]] = []
    arcs: list[tuple[float, float, float]] = []
    for e in msp:
        if e.dxftype() == "ARC":
            radius = float(e.dxf.radius)
            sweep = (float(e.dxf.end_angle) - float(e.dxf.start_angle)) % 360.0
            if 850.0 <= radius <= 1050.0 and 70.0 <= sweep <= 110.0:
                center = e.dxf.center
                arcs.append((center.x, center.y, radius))
            continue
        if e.dxftype() != "LINE":
            continue
        a, b = e.dxf.start, e.dxf.end
        dx, dy = abs(b.x - a.x), abs(b.y - a.y)
        length = math.hypot(dx, dy)
        if length < 40.0:
            continue
        tol = max(15.0, 0.08 * length)
        if dy <= tol:
            parsed.append(("H", (a.y + b.y) / 2.0, min(a.x, b.x), max(a.x, b.x), length, e.dxf.layer, e))
        elif dx <= tol:
            parsed.append(("V", (a.x + b.x) / 2.0, min(a.y, b.y), max(a.y, b.y), length, e.dxf.layer, e))

    def covered(item) -> bool:
        return any(
            other[5] == WALL_LAYER
            and other[0] == item[0]
            and abs(other[1] - item[1]) <= 8.0
            and min(other[3], item[3]) - max(other[2], item[2]) > 0.8 * item[4]
            for other in parsed
        )

    n_demote = 0
    n_promote = 0
    touched: set[int] = set()

    def _set(ent, wall: bool) -> bool:
        eid = id(ent)
        if eid in touched:
            return False
        layer = WALL_LAYER if wall else BASE_LAYER
        if getattr(ent.dxf, "layer", None) == layer:
            touched.add(eid)
            return False
        touched.add(eid)
        ent.dxf.layer = layer
        ent.dxf.color = WALL_COLOR if wall else BASE_COLOR
        return True

    for center_x, center_y, radius in arcs:
        leaves = [
            item
            for item in parsed
            if item[5] == WALL_LAYER
            and abs(item[4] - radius) <= 40.0
            and (
                (item[0] == "V" and 15.0 <= abs(item[1] - center_x) <= 70.0 and min(abs(item[2] - center_y), abs(item[3] - center_y)) <= 40.0)
                or (item[0] == "H" and 15.0 <= abs(item[1] - center_y) <= 70.0 and min(abs(item[2] - center_x), abs(item[3] - center_x)) <= 40.0)
            )
        ]
        for leaf in leaves:
            mate = any(
                item[5] == BASE_LAYER
                and item[0] == leaf[0]
                and abs(item[4] - radius) <= 40.0
                and min(item[3], leaf[3]) - max(item[2], leaf[2]) > 0.7 * leaf[4]
                and (
                    (item[0] == "V" and abs(item[1] - center_x) <= 15.0 and min(abs(item[2] - center_y), abs(item[3] - center_y)) <= 40.0)
                    or (item[0] == "H" and abs(item[1] - center_y) <= 15.0 and min(abs(item[2] - center_x), abs(item[3] - center_x)) <= 40.0)
                )
                for item in parsed
            )
            if not mate:
                continue
            if _set(leaf[6], False):
                n_demote += 1
            hosts = [
                item
                for item in parsed
                if item[5] == BASE_LAYER
                and item[0] != leaf[0]
                and abs(item[4] - radius) <= 40.0
                and (
                    (item[0] == "H" and abs(item[1] - center_y) <= 20.0 and (abs(item[2] - center_x) <= 40.0 or abs(item[3] - center_x) <= 40.0))
                    or (item[0] == "V" and abs(item[1] - center_x) <= 20.0 and (abs(item[2] - center_y) <= 40.0 or abs(item[3] - center_y) <= 40.0))
                )
            ]
            if not hosts:
                continue
            span0, span1 = hosts[0][2], hosts[0][3]
            faces = [hosts[0][1]]
            faces.extend(
                item[1]
                for item in parsed
                if item[5] == BASE_LAYER
                and item[0] == hosts[0][0]
                and abs(item[2] - span0) <= 40.0
                and abs(item[3] - span1) <= 40.0
                and 120.0 <= abs(item[1] - hosts[0][1]) <= 180.0
            )
            for item in parsed:
                if item[5] != BASE_LAYER or item[0] != hosts[0][0] or covered(item):
                    continue
                if min(abs(item[1] - face) for face in faces) > 8.0:
                    continue
                if min(item[3], span1) - max(item[2], span0) > 40.0:
                    continue
                if min(abs(item[3] - span0), abs(item[2] - span1)) > 80.0:
                    continue
                if not (80.0 <= item[4] <= 4000.0):
                    continue
                if _set(item[6], True):
                    n_promote += 1
    return n_demote, n_promote


def correct_locker_partition(msp) -> tuple[int, int]:
    """탈의실 사이 벽은 칸막이까지 넓히고, 옆 문짝은 내리고 문 위·아래는 벽으로 둔다.

    짧은 빨간 구간 양옆의 회색 면은 벽이다. 문 높이의 빨간 면은 문짝이다.
    """
    parsed: list[tuple[str, float, float, float, float, str, Any]] = []
    for e in msp:
        if e.dxftype() != "LINE":
            continue
        a, b = e.dxf.start, e.dxf.end
        dx, dy = abs(b.x - a.x), abs(b.y - a.y)
        length = math.hypot(dx, dy)
        if length < 40.0:
            continue
        tol = max(15.0, 0.08 * length)
        if dy <= tol:
            parsed.append(("H", (a.y + b.y) / 2.0, min(a.x, b.x), max(a.x, b.x), length, e.dxf.layer, e))
        elif dx <= tol:
            parsed.append(("V", (a.x + b.x) / 2.0, min(a.y, b.y), max(a.y, b.y), length, e.dxf.layer, e))

    def covered(item) -> bool:
        return any(
            other[5] == WALL_LAYER
            and other[0] == item[0]
            and abs(other[1] - item[1]) <= 8.0
            and min(other[3], item[3]) - max(other[2], item[2]) > 0.8 * item[4]
            for other in parsed
        )

    n_demote = 0
    n_promote = 0
    touched: set[int] = set()

    def _set(ent, wall: bool) -> bool:
        eid = id(ent)
        if eid in touched:
            return False
        layer = WALL_LAYER if wall else BASE_LAYER
        if getattr(ent.dxf, "layer", None) == layer:
            touched.add(eid)
            return False
        touched.add(eid)
        ent.dxf.layer = layer
        ent.dxf.color = WALL_COLOR if wall else BASE_COLOR
        return True

    seen_wall: set[tuple] = set()
    for cap in parsed:
        if cap[5] != WALL_LAYER or not (500.0 <= cap[4] <= 900.0):
            continue
        meets = any(
            other[5] == WALL_LAYER
            and other[0] != cap[0]
            and other[4] >= 1500.0
            and cap[2] - 20.0 <= other[1] <= cap[3] + 20.0
            and min(abs(other[3] - cap[1]), abs(other[2] - cap[1])) <= 150.0
            for other in parsed
        )
        if not meets:
            continue
        mate = next(
            (
                other
                for other in parsed
                if other[5] == WALL_LAYER
                and other[0] == cap[0]
                and 80.0 <= abs(other[1] - cap[1]) <= 120.0
                and abs(other[2] - cap[2]) <= 40.0
                and abs(other[3] - cap[3]) <= 40.0
            ),
            None,
        )
        if mate is None:
            continue
        key = (cap[0], round(min(cap[1], mate[1]) / 20.0), round(cap[2] / 20.0))
        if key in seen_wall:
            continue
        flanks = []
        for face in (cap, mate):
            flanks.extend(
                item
                for item in parsed
                if item[5] == BASE_LAYER
                and item[0] == face[0]
                and abs(item[1] - face[1]) <= 8.0
                and 1200.0 <= item[4] <= 2500.0
                and min(item[3], face[3]) - max(item[2], face[2]) <= 80.0
                and min(abs(item[3] - face[2]), abs(item[2] - face[3])) <= 40.0
            )
        left = any(item[3] <= cap[2] + 40.0 for item in flanks)
        right = any(item[2] >= cap[3] - 40.0 for item in flanks)
        if not (left and right):
            continue
        seen_wall.add(key)
        for item in flanks:
            if _set(item[6], True):
                n_promote += 1

    walls = [item for item in parsed if item[5] == WALL_LAYER and 1800.0 <= item[4] <= 2100.0]
    seen_door: set[tuple] = set()
    for index, face in enumerate(walls):
        for mate in walls[index + 1 :]:
            if face[0] != mate[0] or not (140.0 <= abs(face[1] - mate[1]) <= 170.0):
                continue
            if abs(face[2] - mate[2]) > 40.0 or abs(face[3] - mate[3]) > 40.0:
                continue
            low, high = min(face[1], mate[1]), max(face[1], mate[1])
            leaves = [
                item
                for item in parsed
                if item[5] == BASE_LAYER
                and item[0] == face[0]
                and low + 10.0 < item[1] < high - 10.0
                and 900.0 <= item[4] <= 1100.0
                and min(item[3], face[3]) - max(item[2], face[2]) > 700.0
            ]
            if len(leaves) < 4:
                continue
            key = (face[0], round(low / 20.0), round(face[2] / 20.0))
            if key in seen_door:
                continue
            seen_door.add(key)
            span0, span1 = face[2], face[3]
            for item in parsed:
                if item[5] != WALL_LAYER or item[0] != face[0]:
                    continue
                if min(abs(item[1] - face[1]), abs(item[1] - mate[1])) > 8.0:
                    continue
                if abs(item[2] - span0) > 40.0 or abs(item[3] - span1) > 40.0:
                    continue
                if _set(item[6], False):
                    n_demote += 1
            sides = []
            for door_face, outward in ((low, -1.0), (high, 1.0)):
                for item in parsed:
                    if item[5] != BASE_LAYER or item[0] != face[0] or covered(item):
                        continue
                    if not (15.0 <= (item[1] - door_face) * outward <= 45.0):
                        continue
                    if min(item[3], span1) - max(item[2], span0) > 40.0:
                        continue
                    if item[4] < 600.0:
                        continue
                    near_wall = any(
                        other[5] == WALL_LAYER
                        and other[0] == item[0]
                        and abs(other[1] - item[1]) <= 8.0
                        and min(other[3], item[3]) - max(other[2], item[2]) < 40.0
                        and min(abs(other[3] - item[2]), abs(other[2] - item[3])) <= 80.0
                        for other in parsed
                    )
                    if near_wall:
                        sides.append(item)
                        if _set(item[6], True):
                            n_promote += 1
            for item in parsed:
                if item[5] != BASE_LAYER or item[0] != face[0] or covered(item):
                    continue
                if not (low + 10.0 < item[1] < high - 10.0):
                    continue
                if min(item[3], span1) - max(item[2], span0) > 40.0:
                    continue
                if item[4] < 600.0:
                    continue
                if any(abs(item[2] - side[2]) <= 40.0 and abs(item[3] - side[3]) <= 40.0 for side in sides):
                    if _set(item[6], True):
                        n_promote += 1
    return n_demote, n_promote


def correct_panel_face_doors(msp) -> tuple[int, int]:
    """벽면이 문짝 구간을 덮고 있으면 그 구간은 문이다. 내리고 문 끝의 짧은 면은 벽으로 둔다.

    투시영상 검사실7 오른쪽처럼, 이중벽 안의 문짝을 따라 빨간 면이 이어진다.
    """
    parsed: list[tuple[str, float, float, float, float, str, Any]] = []
    for e in msp:
        if e.dxftype() != "LINE":
            continue
        a, b = e.dxf.start, e.dxf.end
        dx, dy = abs(b.x - a.x), abs(b.y - a.y)
        length = math.hypot(dx, dy)
        if length < 40.0:
            continue
        tol = max(15.0, 0.08 * length)
        if dy <= tol:
            parsed.append(("H", (a.y + b.y) / 2.0, min(a.x, b.x), max(a.x, b.x), length, e.dxf.layer, e))
        elif dx <= tol:
            parsed.append(("V", (a.x + b.x) / 2.0, min(a.y, b.y), max(a.y, b.y), length, e.dxf.layer, e))

    n_demote = 0
    n_promote = 0
    touched: set[int] = set()
    seen: set[tuple] = set()

    def _set(ent, wall: bool) -> bool:
        eid = id(ent)
        if eid in touched:
            return False
        layer = WALL_LAYER if wall else BASE_LAYER
        if getattr(ent.dxf, "layer", None) == layer:
            touched.add(eid)
            return False
        touched.add(eid)
        ent.dxf.layer = layer
        ent.dxf.color = WALL_COLOR if wall else BASE_COLOR
        return True

    panels = [item for item in parsed if item[5] == BASE_LAYER and 750.0 <= item[4] <= 1000.0]
    for panel in panels:
        mates = [
            other
            for other in panels
            if other is not panel
            and other[0] == panel[0]
            and 30.0 <= abs(other[1] - panel[1]) <= 50.0
            and abs(other[2] - panel[2]) <= 20.0
            and abs(other[3] - panel[3]) <= 20.0
        ]
        if not mates:
            continue
        low, high = sorted((panel[1], mates[0][1]))
        span0, span1 = panel[2], panel[3]
        for other in panels:
            if other[0] != panel[0] or min(abs(other[1] - low), abs(other[1] - high)) > 8.0:
                continue
            if abs(other[2] - span1) <= 40.0 and other[3] > span1:
                span1 = max(span1, other[3])
            if abs(other[3] - span0) <= 40.0 and other[2] < span0:
                span0 = min(span0, other[2])
        faces = [
            item
            for item in parsed
            if item[0] == panel[0]
            and item[5] == WALL_LAYER
            and (40.0 <= low - item[1] <= 80.0 or 40.0 <= item[1] - high <= 80.0)
            and min(item[3], span1) - max(item[2], span0) > 0.75 * item[4]
            and item[4] <= (span1 - span0) + 400.0
        ]
        if len({round(item[1]) for item in faces}) < 2:
            continue
        key = (panel[0], round(span0 / 50.0), round(min(item[1] for item in faces) / 20.0))
        if key in seen:
            continue
        seen.add(key)
        coords = {item[1] for item in faces}
        for item in faces:
            if _set(item[6], False):
                n_demote += 1
        for item in parsed:
            if item[5] != BASE_LAYER or item[0] != panel[0]:
                continue
            if not any(abs(item[1] - coord) <= 12.0 for coord in coords):
                continue
            if min(item[3], span1) - max(item[2], span0) > 30.0:
                continue
            if not (80.0 <= item[4] <= 300.0):
                continue
            if min(abs(item[2] - span1), abs(item[3] - span0)) > 100.0:
                continue
            if _set(item[6], True):
                n_promote += 1
    return n_demote, n_promote


def promote_locker_floor_walls(msp) -> int:
    """바닥 벽에 닿은 회색 면은 벽이다.

    탈의실(여) 왼쪽 면과 탈의실(남) 칸막이 옆의 이중면처럼,
    긴 바닥 벽에서 올라온 선이다.
    """
    parsed: list[tuple[str, float, float, float, float, str, Any]] = []
    for e in msp:
        if e.dxftype() != "LINE":
            continue
        a, b = e.dxf.start, e.dxf.end
        dx, dy = abs(b.x - a.x), abs(b.y - a.y)
        length = math.hypot(dx, dy)
        if length < 40.0:
            continue
        tol = max(15.0, 0.08 * length)
        if dy <= tol:
            parsed.append(("H", (a.y + b.y) / 2.0, min(a.x, b.x), max(a.x, b.x), length, e.dxf.layer, e))
        elif dx <= tol:
            parsed.append(("V", (a.x + b.x) / 2.0, min(a.y, b.y), max(a.y, b.y), length, e.dxf.layer, e))

    def covered(item) -> bool:
        return any(
            other[5] == WALL_LAYER
            and other[0] == item[0]
            and abs(other[1] - item[1]) <= 8.0
            and min(other[3], item[3]) - max(other[2], item[2]) > 0.8 * item[4]
            for other in parsed
        )

    def on_floor(item) -> bool:
        return any(
            other[5] == WALL_LAYER
            and other[0] != item[0]
            and other[4] >= 3000.0
            and abs(other[1] - item[2]) <= 40.0
            and other[2] - 40.0 <= item[1] <= other[3] + 40.0
            for other in parsed
        )

    n_promote = 0
    touched: set[int] = set()

    def _paint(item) -> None:
        nonlocal n_promote
        eid = id(item[6])
        if eid in touched or item[5] == WALL_LAYER:
            return
        touched.add(eid)
        item[6].dxf.layer = WALL_LAYER
        item[6].dxf.color = WALL_COLOR
        n_promote += 1

    for item in parsed:
        if item[5] != BASE_LAYER or covered(item) or not (1400.0 <= item[4] <= 1600.0):
            continue
        if not on_floor(item):
            continue
        nearer = [
            other[1]
            for other in parsed
            if other[5] == WALL_LAYER
            and other[0] == item[0]
            and other[1] != item[1]
            and min(other[3], item[3]) - max(other[2], item[2]) > 0.8 * item[4]
        ]
        if not nearer:
            continue
        gap = min(abs(coord - item[1]) for coord in nearer)
        if 150.0 <= gap <= 250.0:
            _paint(item)

    bases = [item for item in parsed if item[5] == BASE_LAYER and 1400.0 <= item[4] <= 1700.0 and not covered(item)]
    seen: set[tuple] = set()
    for item in bases:
        mates = [
            other
            for other in bases
            if other is not item
            and other[0] == item[0]
            and 80.0 <= abs(other[1] - item[1]) <= 120.0
            and abs(other[2] - item[2]) <= 40.0
            and abs(other[3] - item[3]) <= 80.0
        ]
        if not mates or not on_floor(item):
            continue
        key = (item[0], round(min(item[1], mates[0][1]) / 20.0), round(item[2] / 20.0))
        if key in seen:
            continue
        beside = any(
            other[5] == WALL_LAYER
            and other[0] == item[0]
            and other[4] >= 1500.0
            and 250.0 <= abs(other[1] - item[1]) <= 600.0
            and abs(other[2] - item[3]) <= 150.0
            for other in parsed
        )
        if not beside:
            continue
        seen.add(key)
        _paint(item)
        _paint(mates[0])
    return n_promote


def promote_grid_door_flanks(msp) -> int:
    """겹치는 격자로 그린 여닫이문. 격자(문짝)는 두고 양옆 벽만 WALL로 둔다.

    전사실 아래처럼 스윙 호 없이, 이중벽 두께 안에 긴 평행선이 겹쳐 있다.
    """
    parsed: list[tuple[str, float, float, float, float, str, Any]] = []
    for e in msp:
        axis = _axis_line(e)
        if axis is None:
            continue
        ori, coord, a, b, length = axis
        if length < 300.0:
            continue
        parsed.append((ori, coord, a, b, length, str(getattr(e.dxf, "layer", "") or ""), e))

    n = 0
    seen: set[tuple] = set()
    for ori in ("H", "V"):
        faces = [t for t in parsed if t[0] == ori and t[4] >= 2000.0]
        faces.sort(key=lambda t: t[1])
        for i, (_, c0, a0, b0, _l0, _y0, _e0) in enumerate(faces):
            for _, c1, a1, b1, _l1, _y1, _e1 in faces[i + 1 :]:
                gap = c1 - c0
                if gap < 100.0:
                    continue
                if gap > 200.0:
                    break
                lo, hi = max(a0, a1), min(b0, b1)
                if hi - lo < 2000.0:
                    continue
                interiors = [
                    (a, b)
                    for o, c, a, b, length, _layer, _e in parsed
                    if o == ori
                    and length >= 800.0
                    and c0 + 15.0 < c < c1 - 15.0
                    and min(b, hi) - max(a, lo) >= 500.0
                ]
                if len(interiors) < 5:
                    continue
                iv = sorted(interiors)
                clusters: list[tuple[float, float, int]] = []
                cs, ce, cnt = iv[0][0], iv[0][1], 1
                for a, b in iv[1:]:
                    if a <= ce + 400.0:
                        ce = max(ce, b)
                        cnt += 1
                    else:
                        clusters.append((cs, ce, cnt))
                        cs, ce, cnt = a, b, 1
                clusters.append((cs, ce, cnt))
                for s0, s1, cnt in clusters:
                    if not (2100.0 <= s1 - s0 <= 2600.0) or cnt < 5:
                        continue
                    key = (ori, round(c0), round(c1), round(s0 / 50.0), round(s1 / 50.0))
                    if key in seen:
                        continue
                    seen.add(key)

                    def flank(side: int) -> list[Any]:
                        picked: dict[int, Any] = {}
                        for o, c, a, b, length, layer, e in parsed:
                            if o != ori or not (1200.0 <= length <= 2200.0):
                                continue
                            face = 0 if abs(c - c0) <= 25.0 else 1 if abs(c - c1) <= 25.0 else -1
                            if face < 0:
                                continue
                            if min(b, s1) - max(a, s0) > 80.0:
                                continue
                            if side < 0:
                                if b > s0 + 80.0 or s0 - b > 350.0:
                                    continue
                            elif a < s1 - 80.0 or a - s1 > 350.0:
                                continue
                            inside = sum(
                                1
                                for ia, ib in interiors
                                if min(ib, b) - max(ia, a) >= 400.0
                            )
                            if inside >= 3:
                                continue
                            prev = picked.get(face)
                            if prev is None or length > prev[4]:
                                picked[face] = (o, c, a, b, length, layer, e)
                        if len(picked) < 2:
                            return []
                        return list(picked.values())

                    chosen = flank(-1) + flank(1)
                    if len(flank(-1)) < 2 or len(flank(1)) < 2:
                        continue
                    for _o, _c, _a, _b, _length, layer, e in chosen:
                        if layer == WALL_LAYER:
                            continue
                        e.dxf.layer = WALL_LAYER
                        try:
                            e.dxf.color = WALL_COLOR
                        except Exception:  # noqa: BLE001
                            pass
                        n += 1
    return n


def promote_marked_door_flanks(msp) -> int:
    """대각선으로 표시된 문. 문이 놓인 이중벽의 양옆만 WALL로 둔다.

    배식대#2 뒤쪽 문처럼 스윙 호가 없고 사각형에 대각선만 있는 문.
    """
    segs = iter_axis_segs(msp, min_len_mm=200.0)
    hors = [s for s in segs if not s.is_v]
    diags: list[tuple[float, float, float, float]] = []
    seen_d: set[tuple[int, int]] = set()
    for e in msp:
        if e.dxftype() != "LINE":
            continue
        try:
            x0, y0 = float(e.dxf.start.x), float(e.dxf.start.y)
            x1, y1 = float(e.dxf.end.x), float(e.dxf.end.y)
        except Exception:  # noqa: BLE001
            continue
        dx, dy = abs(x1 - x0), abs(y1 - y0)
        length = math.hypot(dx, dy)
        if dx <= 200.0 or dy <= 200.0 or not (1400.0 <= length <= 1900.0):
            continue
        bx0, bx1 = min(x0, x1), max(x0, x1)
        by0, by1 = min(y0, y1), max(y0, y1)
        if not (650.0 <= bx1 - bx0 <= 1100.0 and 1200.0 <= by1 - by0 <= 1800.0):
            continue
        key = (round(bx0 / 40.0), round(by0 / 40.0))
        if key in seen_d:
            continue
        seen_d.add(key)
        diags.append((bx0, bx1, by0, by1))

    added: set[tuple[int, int, int]] = set()
    n = 0

    def _covered(ortho: float, a0: float, a1: float) -> bool:
        span = a1 - a0
        if span < 100.0:
            return True
        for s in hors:
            if s.layer != WALL_LAYER or abs(s.ortho - ortho) > 40.0:
                continue
            if min(s.along1, a1) - max(s.along0, a0) >= span * 0.8:
                return True
        return False

    def _add(ortho: float, a0: float, a1: float) -> None:
        nonlocal n
        if a1 - a0 < 100.0 or _covered(ortho, a0, a1):
            return
        key = (round(ortho), round(a0), round(a1))
        if key in added:
            return
        added.add(key)
        msp.add_line(
            (a0, ortho), (a1, ortho),
            dxfattribs={"layer": WALL_LAYER, "color": WALL_COLOR},
        )
        n += 1

    for bx0, bx1, by0, by1 in diags:
        for edge in (by0, by1):
            hosts = [
                s for s in hors
                if s.length >= 1800.0 and abs(s.ortho - edge) <= 80.0
                and s.along0 <= bx0 + 80.0 and s.along1 >= bx1 - 80.0
            ]
            if not hosts:
                continue
            host = max(hosts, key=lambda s: s.length)
            if bx0 - host.along0 < 600.0 and host.along1 - bx1 < 600.0:
                continue
            mates = [
                s for s in hors
                if 120.0 <= abs(s.ortho - host.ortho) <= 350.0
                and min(host.along1, s.along1) - max(host.along0, s.along0) >= 1500.0
                and s.length >= 1500.0
            ]
            faces = [host] + mates
            seen_o: set[int] = set()
            for face in faces:
                oy = round(face.ortho)
                if oy in seen_o:
                    continue
                seen_o.add(oy)
                if bx0 - face.along0 >= 100.0:
                    _add(face.ortho, face.along0, min(face.along1, bx0))
                if face.along1 - bx1 >= 100.0:
                    _add(face.ortho, max(face.along0, bx1), face.along1)
            # 문 오른쪽이 같은 선에서 끊기고, 조금 떨어진 이중선으로 이어지면 그 선도 벽이다.
            for s in hors:
                if s.along0 < bx1 - 40.0 or s.along0 > bx1 + 400.0:
                    continue
                if not (400.0 <= s.length <= 2500.0):
                    continue
                if not any(abs(s.ortho - face.ortho) <= 80.0 for face in faces):
                    continue
                mate = next(
                    (
                        o for o in hors
                        if o is not s and 120.0 <= abs(o.ortho - s.ortho) <= 350.0
                        and o.along0 <= s.along0 + 80.0 and o.along1 >= s.along1 - 80.0
                        and 400.0 <= o.length <= 2500.0
                    ),
                    None,
                )
                if mate is None:
                    continue
                _add(s.ortho, s.along0, s.along1)
                _add(mate.ortho, s.along0, s.along1)
            break
    return n


def promote_serving_end_door_flanks(msp) -> int:
    """배식대 아래쪽 끝의 문. 문짝은 두고, 그 양옆 벽만 WALL로 둔다."""
    labels = [(x, y) for x, y, s in _iter_text_labels(msp) if _SERVING_RE.search(s)]
    if not labels:
        return 0
    segs = iter_axis_segs(msp, min_len_mm=200.0)
    hors = [s for s in segs if not s.is_v]
    swings: list[tuple[float, float, float, float, float]] = []
    seen_h: set[tuple[int, int]] = set()
    for e in msp:
        if e.dxftype() != "ARC":
            continue
        try:
            r = float(e.dxf.radius)
            cx, cy = float(e.dxf.center.x), float(e.dxf.center.y)
            sweep = (float(e.dxf.end_angle) - float(e.dxf.start_angle)) % 360.0
            sa, ea = float(e.dxf.start_angle), float(e.dxf.end_angle)
        except Exception:  # noqa: BLE001
            continue
        if not (900.0 <= r <= 1200.0) or not (70.0 <= sweep <= 110.0):
            continue
        key = (round(cx / 40.0), round(cy / 40.0))
        if key in seen_h:
            continue
        seen_h.add(key)
        swings.append((cx, cy, r, sa, ea))

    added: set[tuple[int, int, int]] = set()
    n = 0

    def _covered(ortho: float, a0: float, a1: float) -> bool:
        span = a1 - a0
        if span < 100.0:
            return True
        return any(
            (not s.is_v) and s.layer == WALL_LAYER and abs(s.ortho - ortho) <= 40.0
            and min(s.along1, a1) - max(s.along0, a0) >= span * 0.8
            for s in segs
        )

    def _add(ortho: float, a0: float, a1: float) -> None:
        nonlocal n
        if a1 - a0 < 100.0 or _covered(ortho, a0, a1):
            return
        key = (round(ortho), round(a0), round(a1))
        if key in added:
            return
        added.add(key)
        msp.add_line(
            (a0, ortho), (a1, ortho),
            dxfattribs={"layer": WALL_LAYER, "color": WALL_COLOR},
        )
        n += 1

    for lx, ly in labels:
        bottoms = [
            s for s in hors
            if s.length >= 2500.0 and ly - 8000.0 < s.ortho < ly - 1500.0
            and s.along0 - 500.0 <= lx <= s.along1 + 500.0
        ]
        if not bottoms:
            continue
        for cx, cy, r, sa, ea in swings:
            if abs(cx - lx) > 8000.0 or not (ly - 8000.0 < cy < ly - 800.0):
                continue
            along = None
            for ang in (sa, ea):
                rad = math.radians(ang)
                ex, ey = cx + r * math.cos(rad), cy + r * math.sin(rad)
                if abs(ex - cx) > 0.7 * r and abs(ey - cy) < 0.35 * r:
                    along = (ex, ey)
            if along is None:
                continue
            ex, ey = along
            span0, span1 = min(cx, ex), max(cx, ex)
            if not (800.0 <= span1 - span0 <= 1200.0):
                continue
            hosts = [
                s for s in bottoms
                if 80.0 <= abs(s.ortho - ey) <= 220.0
                and s.along0 <= span0 + 80.0 and s.along1 >= span1 - 80.0
                and min(abs(cx - s.along0), abs(cx - s.along1)) <= 200.0
            ]
            if not hosts:
                continue
            host = min(hosts, key=lambda s: abs(s.ortho - ey))
            far = ex
            jamb = (far - host.along0) if far < cx else (host.along1 - far)
            if jamb < 1500.0:
                continue
            mates = [
                s for s in hors
                if 120.0 <= abs(s.ortho - host.ortho) <= 350.0
                and min(host.along1, s.along1) - max(host.along0, s.along0) >= 1500.0
                and s.length >= 1500.0
            ]
            faces = [host] + mates
            seen_o: set[int] = set()
            for face in faces:
                oy = round(face.ortho)
                if oy in seen_o:
                    continue
                seen_o.add(oy)
                if span0 - face.along0 >= 100.0:
                    _add(face.ortho, face.along0, min(face.along1, span0))
                if face.along1 - span1 >= 100.0:
                    _add(face.ortho, max(face.along0, span1), face.along1)
    return n


def promote_stacked_room_side_wall(msp) -> int:
    """위·아래로 붙은 식당창고와 배식대의 같은 쪽 벽. 두 실을 잇는 이중선은 벽이다."""
    labels = [(x, y, s) for x, y, s in _iter_text_labels(msp)]
    stores = [(x, y) for x, y, s in labels if "식당창고" in s]
    servings = [(x, y) for x, y, s in labels if _SERVING_RE.search(s)]
    if not stores or not servings:
        return 0
    segs = iter_axis_segs(msp, min_len_mm=400.0)
    verts = [s for s in segs if s.is_v and s.length >= 1200.0]
    n = 0
    added: set[tuple[int, int, int]] = set()

    def _covered(ortho: float, a0: float, a1: float) -> bool:
        span = max(a1 - a0, 1.0)
        return any(
            s.is_v and s.layer == WALL_LAYER and abs(s.ortho - ortho) <= 40.0
            and min(s.along1, a1) - max(s.along0, a0) >= span * 0.8
            for s in verts
        )

    for sx, sy in stores:
        for bx, by in servings:
            if abs(sx - bx) > 2500.0 or not (3000.0 < abs(sy - by) < 12000.0):
                continue
            y_lo, y_hi = min(sy, by), max(sy, by)
            span0, span1 = y_lo - 6000.0, y_hi + 4000.0
            best = None
            for side in (-1, 1):
                cands = [
                    s for s in verts
                    if side * (s.ortho - sx) > 400.0
                    and abs(s.ortho - sx) < 5000.0
                    and s.along1 > span0 and s.along0 < span1
                    and s.length >= 5000.0
                ]
                orthos = sorted({round(s.ortho) for s in cands})
                for i, o0 in enumerate(orthos):
                    for o1 in orthos[i + 1:]:
                        if not (150 <= o1 - o0 <= 350):
                            continue
                        faces = [s for s in cands if abs(s.ortho - o0) <= 60 or abs(s.ortho - o1) <= 60]
                        base_len = sum(s.length for s in faces if s.layer != WALL_LAYER)
                        if best is None or base_len > best[0]:
                            best = (base_len, o0, o1)
            if best is None or best[0] < 4000.0:
                continue
            _, o0, o1 = best
            for s in verts:
                if abs(s.ortho - o0) > 60.0 and abs(s.ortho - o1) > 60.0:
                    continue
                if s.along1 < span0 or s.along0 > span1 or s.length < 1200.0:
                    continue
                a0, a1 = max(s.along0, span0), min(s.along1, span1)
                if a1 - a0 < 1200.0 or _covered(s.ortho, a0, a1):
                    continue
                key = (round(s.ortho), round(a0), round(a1))
                if key in added:
                    continue
                added.add(key)
                msp.add_line(
                    (s.ortho, a0), (s.ortho, a1),
                    dxfattribs={"layer": WALL_LAYER, "color": WALL_COLOR},
                )
                n += 1
    return n


def open_serving_corner_door(msp) -> int:
    """배식대 모서리 문. 옆벽 이중선에서 문 높이만 개구로 두고 위·아래는 벽이다."""
    labels = [(x, y) for x, y, s in _iter_text_labels(msp) if _SERVING_RE.search(s)]
    if not labels:
        return 0
    swings: list[tuple[float, float, float, float, float]] = []
    seen_h: set[tuple[int, int]] = set()
    for e in msp:
        if e.dxftype() != "ARC":
            continue
        try:
            r = float(e.dxf.radius)
            cx, cy = float(e.dxf.center.x), float(e.dxf.center.y)
            sweep = (float(e.dxf.end_angle) - float(e.dxf.start_angle)) % 360.0
            sa, ea = float(e.dxf.start_angle), float(e.dxf.end_angle)
        except Exception:  # noqa: BLE001
            continue
        if not (900.0 <= r <= 1200.0) or not (70.0 <= sweep <= 110.0):
            continue
        key = (round(cx / 40.0), round(cy / 40.0))
        if key in seen_h:
            continue
        seen_h.add(key)
        swings.append((cx, cy, r, sa, ea))

    n = 0
    for lx, ly in labels:
        for cx, cy, r, sa, ea in swings:
            if abs(cx - lx) > 8000.0 or not (ly - 8000.0 < cy < ly - 800.0):
                continue
            up = None
            for ang in (sa, ea):
                rad = math.radians(ang)
                ex = cx + r * math.cos(rad)
                ey = cy + r * math.sin(rad)
                if abs(ex - cx) < 0.35 * r and abs(ey - cy) > 0.7 * r:
                    up = ey
            if up is None:
                continue
            y0, y1 = (cy, up) if cy < up else (up, cy)
            hits = [
                s for s in iter_axis_segs(msp, min_len_mm=80.0)
                if s.is_v and s.layer == WALL_LAYER and s.entity.dxftype() == "LINE"
                and abs(s.ortho - cx) <= 280.0
                and min(s.along1, y1) - max(s.along0, y0) > 80.0
            ]
            seen_e: set[int] = set()
            for s in hits:
                eid = id(s.entity)
                if eid in seen_e:
                    continue
                seen_e.add(eid)
                a0, a1, x = s.along0, s.along1, s.ortho
                msp.delete_entity(s.entity)
                if y0 - a0 >= 100.0:
                    msp.add_line(
                        (x, a0), (x, y0),
                        dxfattribs={"layer": WALL_LAYER, "color": WALL_COLOR},
                    )
                    n += 1
                if a1 - y1 >= 100.0:
                    msp.add_line(
                        (x, y1), (x, a1),
                        dxfattribs={"layer": WALL_LAYER, "color": WALL_COLOR},
                    )
                    n += 1
    return n


def promote_control_booth_bottom(msp) -> int:
    """소강당 조정실이 홀을 보는 아래쪽 이중선은 벽이다.

    조각으로 끊겨 양 끝이 측벽에 직접 물리지 않으면 강당 demote가 지운다.
    라벨 바로 아래의 벽두께 쌍만 다시 올린다.
    """
    labels = [
        (x, y)
        for x, y, s in _iter_text_labels(msp)
        if "소강당" in s and "조정실" in s
    ]
    if not labels:
        return 0
    segs = iter_axis_segs(msp, min_len_mm=150.0)
    n = 0
    for lx, ly in labels:
        hs = [
            s
            for s in segs
            if s.is_h
            and s.length >= 1500.0
            and ly - 5000.0 < s.ortho < ly - 500.0
            and s.along0 < lx + 12000.0
            and s.along1 > lx - 12000.0
        ]
        orthos = sorted({round(s.ortho) for s in hs})
        best = None
        for i, o0 in enumerate(orthos):
            for o1 in orthos[i + 1 :]:
                if not (120.0 <= o1 - o0 <= 350.0):
                    continue
                a = [s for s in hs if abs(s.ortho - o0) <= 40.0]
                b = [s for s in hs if abs(s.ortho - o1) <= 40.0]
                if not a or not b:
                    continue
                ov0 = max(min(s.along0 for s in a), min(s.along0 for s in b))
                ov1 = min(max(s.along1 for s in a), max(s.along1 for s in b))
                if ov1 - ov0 < 6000.0 or not (ov0 - 1500.0 <= lx <= ov1 + 1500.0):
                    continue
                dist = ly - o1
                if dist < 400.0:
                    continue
                score = (dist, -(ov1 - ov0))
                if best is None or score < best[0]:
                    best = (score, o0, o1, ov0, ov1)
        if best is None:
            continue
        _, o0, o1, ov0, ov1 = best
        walls = [
            s
            for s in segs
            if s.is_h
            and s.layer == WALL_LAYER
            and (abs(s.ortho - o0) <= 40.0 or abs(s.ortho - o1) <= 40.0)
        ]

        def _covered(ortho: float, a0: float, a1: float) -> bool:
            span = max(a1 - a0, 1.0)
            return any(
                abs(w.ortho - ortho) <= 40.0
                and min(w.along1, a1) - max(w.along0, a0) >= span * 0.85
                for w in walls
            )

        for s in segs:
            if not s.is_h or s.layer == WALL_LAYER:
                continue
            if abs(s.ortho - o0) > 40.0 and abs(s.ortho - o1) > 40.0:
                continue
            a0 = max(s.along0, ov0 - 300.0)
            a1 = min(s.along1, ov1 + 300.0)
            if a1 - a0 < 150.0 or _covered(s.ortho, a0, a1):
                continue
            e = s.entity
            full = abs((s.along1 - s.along0) - (a1 - a0)) < 30.0
            if (
                e is not None
                and e.dxftype() == "LINE"
                and getattr(e.dxf, "layer", None) == BASE_LAYER
                and full
            ):
                e.dxf.layer = WALL_LAYER
                try:
                    e.dxf.color = WALL_COLOR
                except Exception:  # noqa: BLE001
                    pass
            else:
                msp.add_line(
                    (a0, s.ortho),
                    (a1, s.ortho),
                    dxfattribs={"layer": WALL_LAYER, "color": WALL_COLOR},
                )
            n += 1
    return n


def promote_stair_eps_party_wall(msp) -> int:
    """계단실과 바로 옆 EPS 사이 이중선은 벽이다.

    같은 x가 객석 문벽으로 지워져도, 두 실이 맞닿은 구간만 다시 올린다.
    """
    labels = list(_iter_text_labels(msp))
    stairs = [(x, y) for x, y, s in labels if "계단실" in s]
    epss = [(x, y) for x, y, s in labels if "EPS" in s]
    if not stairs or not epss:
        return 0
    segs = iter_axis_segs(msp, min_len_mm=400.0)
    n = 0
    for sx, sy in stairs:
        for ex, ey in epss:
            if not (1500.0 < ex - sx < 8000.0) or abs(sy - ey) > 8000.0:
                continue
            y_lo, y_hi = min(sy, ey) - 8000.0, max(sy, ey) + 3500.0
            verts = [
                s
                for s in segs
                if s.is_v
                and s.length >= 2000.0
                and sx + 1200.0 < s.ortho < ex + 800.0
                and s.along1 > y_lo
                and s.along0 < y_hi
            ]
            orthos = sorted({round(s.ortho) for s in verts})
            best = None
            for i, o0 in enumerate(orthos):
                for o1 in orthos[i + 1 :]:
                    if not (150.0 <= o1 - o0 <= 400.0):
                        continue
                    if not (sx + 800.0 < o0 < ex and sx + 800.0 < o1 < ex + 400.0):
                        continue
                    a = [s for s in verts if abs(s.ortho - o0) <= 40.0]
                    b = [s for s in verts if abs(s.ortho - o1) <= 40.0]
                    if not a or not b:
                        continue
                    base_len = sum(s.length for s in a + b if s.layer != WALL_LAYER)
                    if best is None or base_len > best[0]:
                        best = (base_len, o0, o1)
            if best is None or best[0] < 2000.0:
                continue
            _, o0, o1 = best
            walls = [
                s
                for s in segs
                if s.is_v and s.layer == WALL_LAYER and (abs(s.ortho - o0) <= 40.0 or abs(s.ortho - o1) <= 40.0)
            ]

            def _covered(ortho: float, a0: float, a1: float) -> bool:
                span = max(a1 - a0, 1.0)
                return any(
                    abs(w.ortho - ortho) <= 40.0
                    and min(w.along1, a1) - max(w.along0, a0) >= span * 0.85
                    for w in walls
                )

            for s in segs:
                if not s.is_v or s.layer == WALL_LAYER or s.length < 800.0:
                    continue
                if abs(s.ortho - o0) > 40.0 and abs(s.ortho - o1) > 40.0:
                    continue
                if s.along1 < y_lo or s.along0 > y_hi:
                    continue
                a0, a1 = max(s.along0, y_lo), min(s.along1, y_hi)
                if a1 - a0 < 800.0 or _covered(s.ortho, a0, a1):
                    continue
                e = s.entity
                full = abs((s.along1 - s.along0) - (a1 - a0)) < 30.0
                if (
                    e is not None
                    and e.dxftype() == "LINE"
                    and getattr(e.dxf, "layer", None) == BASE_LAYER
                    and full
                ):
                    e.dxf.layer = WALL_LAYER
                    try:
                        e.dxf.color = WALL_COLOR
                    except Exception:  # noqa: BLE001
                        pass
                else:
                    msp.add_line(
                        (s.ortho, a0),
                        (s.ortho, a1),
                        dxfattribs={"layer": WALL_LAYER, "color": WALL_COLOR},
                    )
                n += 1
    return n


def promote_waiting_store_wall(msp) -> int:
    """대기실과 옆 창고 사이 이중선은 벽이다.

    강당 demote가 짧은 칸막이로 보고 지운다. 두 실 라벨 사이의 벽두께만 다시 올린다.
    """
    labels = list(_iter_text_labels(msp))
    waits = [(x, y) for x, y, s in labels if "대기실" in s]
    stores = [(x, y) for x, y, s in labels if "창고" in s]
    if not waits or not stores:
        return 0
    segs = iter_axis_segs(msp, min_len_mm=400.0)
    n = 0
    for wx, wy in waits:
        for sx, sy in stores:
            if not (2500.0 < sx - wx < 14000.0) or abs(sy - wy) > 4000.0:
                continue
            y_lo, y_hi = min(wy, sy) - 6000.0, max(wy, sy) + 4000.0
            verts = [
                s
                for s in segs
                if s.is_v
                and s.length >= 3000.0
                and wx + 1500.0 < s.ortho < sx - 500.0
                and s.along1 > y_lo
                and s.along0 < y_hi
            ]
            orthos = sorted({round(s.ortho) for s in verts})
            best = None
            for i, o0 in enumerate(orthos):
                for o1 in orthos[i + 1 :]:
                    if not (100.0 <= o1 - o0 <= 350.0):
                        continue
                    a = [s for s in verts if abs(s.ortho - o0) <= 40.0]
                    b = [s for s in verts if abs(s.ortho - o1) <= 40.0]
                    if not a or not b:
                        continue
                    base_len = sum(s.length for s in a + b if s.layer != WALL_LAYER)
                    if best is None or base_len > best[0]:
                        best = (base_len, o0, o1)
            if best is None or best[0] < 3000.0:
                continue
            _, o0, o1 = best
            walls = [
                s
                for s in segs
                if s.is_v
                and s.layer == WALL_LAYER
                and (abs(s.ortho - o0) <= 40.0 or abs(s.ortho - o1) <= 40.0)
            ]

            def _covered(ortho: float, a0: float, a1: float) -> bool:
                span = max(a1 - a0, 1.0)
                return any(
                    abs(w.ortho - ortho) <= 40.0
                    and min(w.along1, a1) - max(w.along0, a0) >= span * 0.85
                    for w in walls
                )

            for s in segs:
                if not s.is_v or s.layer == WALL_LAYER or s.length < 1500.0:
                    continue
                if abs(s.ortho - o0) > 40.0 and abs(s.ortho - o1) > 40.0:
                    continue
                if s.along1 < y_lo or s.along0 > y_hi:
                    continue
                a0, a1 = max(s.along0, y_lo), min(s.along1, y_hi)
                if a1 - a0 < 1500.0 or _covered(s.ortho, a0, a1):
                    continue
                e = s.entity
                full = abs((s.along1 - s.along0) - (a1 - a0)) < 30.0
                if (
                    e is not None
                    and e.dxftype() == "LINE"
                    and getattr(e.dxf, "layer", None) == BASE_LAYER
                    and full
                ):
                    e.dxf.layer = WALL_LAYER
                    try:
                        e.dxf.color = WALL_COLOR
                    except Exception:  # noqa: BLE001
                        pass
                else:
                    msp.add_line(
                        (s.ortho, a0),
                        (s.ortho, a1),
                        dxfattribs={"layer": WALL_LAYER, "color": WALL_COLOR},
                    )
                n += 1
    return n


def promote_hall_beam_span(msp) -> int:
    """HALL#2 아래 H-Beam 사이를 잇는 가로선은 벽이다.

    같은 높이의 컨베이어와 한 줄로 보여도, 라벨 아래 기둥 사이만 다시 올린다.
    """
    halls = [(x, y) for x, y, s in _iter_text_labels(msp) if "HALL#2" in s]
    if not halls:
        return 0
    segs = iter_axis_segs(msp, min_len_mm=200.0)
    cols = [
        s
        for s in segs
        if s.is_v and s.layer == WALL_LAYER and 600.0 <= s.length <= 2500.0
    ]
    n = 0
    added: set[tuple[int, int, int]] = set()
    for lx, ly in halls:
        seeds = []
        for s in segs:
            if not s.is_h or not (4000.0 <= s.length <= 20000.0):
                continue
            if not (ly - 6000.0 < s.ortho < ly - 800.0):
                continue
            if not (s.along0 < lx < s.along1):
                continue
            butt_l = any(
                abs(c.ortho - s.along0) <= 500.0
                and c.along0 - 1500.0 <= s.ortho <= c.along1 + 1500.0
                for c in cols
            )
            butt_r = any(
                abs(c.ortho - s.along1) <= 500.0
                and c.along0 - 1500.0 <= s.ortho <= c.along1 + 1500.0
                for c in cols
            )
            if butt_l and butt_r:
                seeds.append(s)
        if not seeds:
            continue
        for s in segs:
            if not s.is_h or s.layer == WALL_LAYER or s.length < 4000.0:
                continue
            if not any(abs(s.ortho - seed.ortho) <= 400.0 for seed in seeds):
                continue
            if not any(
                min(s.along1, seed.along1) - max(s.along0, seed.along0) >= seed.length * 0.7
                for seed in seeds
            ):
                continue
            key = (round(s.ortho), round(s.along0), round(s.along1))
            if key in added:
                continue
            covered = any(
                w.is_h
                and w.layer == WALL_LAYER
                and abs(w.ortho - s.ortho) <= 40.0
                and min(w.along1, s.along1) - max(w.along0, s.along0) >= s.length * 0.85
                for w in segs
            )
            if covered:
                continue
            added.add(key)
            msp.add_line(
                (s.along0, s.ortho),
                (s.along1, s.ortho),
                dxfattribs={"layer": WALL_LAYER, "color": WALL_COLOR},
            )
            n += 1
    return n


def promote_tbd_beam_span(msp) -> int:
    """TBD#2 아래 H-Beam 사이를 잇는 가로선은 벽이다.

    실 안에 있는 기둥 사이만 올리고, 칸 밖 컨베이어는 그대로 둔다.
    """
    labels = [(x, y) for x, y, s in _iter_text_labels(msp) if s.strip() == "TBD#2"]
    if not labels:
        return 0
    segs = iter_axis_segs(msp, min_len_mm=200.0)
    n = 0
    added: set[tuple[int, int, int]] = set()
    for lx, ly in labels:
        sides = [
            s
            for s in segs
            if s.is_v
            and s.layer == WALL_LAYER
            and s.length >= 8000.0
            and s.along0 - 500.0 <= ly <= s.along1 + 500.0
            and abs(s.ortho - lx) < 25000.0
        ]
        lefts = [s.ortho for s in sides if s.ortho < lx - 1500.0]
        rights = [s.ortho for s in sides if s.ortho > lx + 1500.0]
        if not lefts or not rights:
            continue
        x_lo, x_hi = max(lefts), min(rights)
        faces = [
            s
            for s in segs
            if s.is_v
            and s.layer == WALL_LAYER
            and 1000.0 <= s.length <= 2200.0
            and x_lo - 800.0 <= s.ortho <= x_hi + 800.0
            and s.along1 > ly - 12000.0
            and s.along0 < ly - 3000.0
        ]
        xs = sorted({round(s.ortho) for s in faces})
        if len(xs) < 4:
            continue
        groups: list[list[int]] = [[xs[0]]]
        for x in xs[1:]:
            if x - groups[-1][-1] <= 2200:
                groups[-1].append(x)
            else:
                groups.append([x])
        cols = [(g[0], g[-1]) for g in groups]
        cols = [c for c in cols if x_lo - 400.0 <= (c[0] + c[1]) / 2.0 <= x_hi + 400.0]
        if len(cols) < 2:
            continue
        bottoms = []
        for c0, c1 in cols:
            ys = [s.along0 for s in faces if c0 - 5 <= s.ortho <= c1 + 5]
            if ys:
                bottoms.append(min(ys))
        if not bottoms:
            continue
        y0, y1 = min(bottoms) - 200.0, max(bottoms) + 200.0
        gaps = [
            (a1, b0)
            for (a0, a1), (b0, b1) in zip(cols, cols[1:])
            if 2000.0 <= b0 - a1 <= 12000.0
        ]
        for s in segs:
            if not s.is_h or s.layer == WALL_LAYER or s.length < 4000.0:
                continue
            if not (y0 <= s.ortho <= y1):
                continue
            for g0, g1 in gaps:
                a0 = max(s.along0, float(g0))
                a1 = min(s.along1, float(g1))
                if a1 - a0 < (g1 - g0) * 0.7:
                    continue
                key = (round(s.ortho), round(a0), round(a1))
                if key in added:
                    continue
                covered = any(
                    w.is_h
                    and w.layer == WALL_LAYER
                    and abs(w.ortho - s.ortho) <= 40.0
                    and min(w.along1, a1) - max(w.along0, a0) >= (a1 - a0) * 0.85
                    for w in segs
                )
                if covered:
                    continue
                added.add(key)
                msp.add_line(
                    (a0, s.ortho),
                    (a1, s.ortho),
                    dxfattribs={"layer": WALL_LAYER, "color": WALL_COLOR},
                )
                n += 1
    return n


def promote_bottom_column_run(msp) -> int:
    """아래쪽 H-Beam 열을 잇는 가로선은 모두 벽이다.

    기둥 면에 물린 긴 선만 올린다. 롤러 눈금처럼 짧은 선은 그대로 둔다.
    """
    segs = iter_axis_segs(msp, min_len_mm=200.0)
    faces = [
        s
        for s in segs
        if s.is_v and s.layer == WALL_LAYER and 1000.0 <= s.length <= 2200.0
    ]
    rows: list[dict] = []
    for s in sorted(faces, key=lambda s: s.along0):
        placed = False
        for row in rows:
            if min(s.along1, row["y1"]) - max(s.along0, row["y0"]) >= 800.0:
                row["faces"].append(s)
                row["y0"] = min(row["y0"], s.along0)
                row["y1"] = max(row["y1"], s.along1)
                placed = True
                break
        if not placed:
            rows.append({"y0": s.along0, "y1": s.along1, "faces": [s]})
    target = None
    for row in rows:
        xs = sorted({round(s.ortho) for s in row["faces"]})
        if len(xs) < 8 or xs[-1] - xs[0] < 60000.0:
            continue
        if target is None or row["y0"] < target["y0"]:
            target = row
            target["xs"] = xs
    if target is None:
        return 0
    cols: list[list[int]] = [[target["xs"][0]]]
    for x in target["xs"][1:]:
        if x - cols[-1][-1] <= 2500:
            cols[-1].append(x)
        else:
            cols.append([x])
    if len(cols) < 6:
        return 0
    bounds = [(c[0], c[-1]) for c in cols]
    y0, y1 = target["y0"] - 200.0, target["y1"] + 200.0

    def _near_face(x: float) -> bool:
        return any(abs(x - a) <= 1200.0 or abs(x - b) <= 1200.0 for a, b in bounds)

    cands = [
        s
        for s in segs
        if s.is_h
        and 2500.0 <= s.length <= 20000.0
        and y0 <= s.ortho <= y1
        and _near_face(s.along0)
        and _near_face(s.along1)
    ]
    orthos = sorted({round(s.ortho) for s in cands})
    clusters: list[list[int]] = []
    for o in orthos:
        if not clusters or o - clusters[-1][-1] > 15:
            clusters.append([o])
        else:
            clusters[-1].append(o)

    def _cluster_stat(grp: list[int]) -> tuple[float, float]:
        keys: dict[tuple[int, int], bool] = {}
        for s in cands:
            if not any(abs(s.ortho - o) <= 15.0 for o in grp):
                continue
            key = (round(s.along0), round(s.along1))
            keys[key] = keys.get(key, False) or s.layer == WALL_LAYER
        total = sum(b - a for a, b in keys)
        covered = sum(b - a for (a, b), is_wall in keys.items() if is_wall)
        return total, covered

    chord: list[int] | None = None
    for i, _grp in enumerate(clusters):
        window = [clusters[i]]
        for grp in clusters[i + 1 :]:
            if grp[0] - clusters[i][0] > 400:
                break
            window.append(grp)
        if len(window) < 3:
            continue
        stats = [_cluster_stat(g) for g in window]
        if any(total < 40000.0 for total, _cov in stats):
            continue
        # 평행선이 세 줄 이상 아직 회색일 때만 올린다. 이미 벽인 층은 건너뛴다.
        gray_groups = sum(1 for total, cov in stats if total > 0 and cov / total < 0.40)
        if gray_groups < 3:
            continue
        if chord is None or clusters[i][0] < min(chord):
            chord = [o for g in window for o in g]
    if not chord:
        return 0

    wall = [s for s in segs if s.is_h and s.layer == WALL_LAYER]
    extra: list[tuple[float, float, float]] = []

    def _uncovered(ortho: float, a0: float, a1: float) -> float:
        iv: list[list[float]] = []
        spans = [(w.ortho, w.along0, w.along1) for w in wall] + extra
        for oy, b0, b1 in spans:
            if abs(oy - ortho) > 15.0:
                continue
            lo, hi = max(b0, a0), min(b1, a1)
            if hi - lo > 0:
                iv.append([lo, hi])
        iv.sort()
        merged: list[list[float]] = []
        for lo, hi in iv:
            if not merged or lo > merged[-1][1]:
                merged.append([lo, hi])
            else:
                merged[-1][1] = max(merged[-1][1], hi)
        covered = sum(hi - lo for lo, hi in merged)
        return (a1 - a0) - covered

    def _in_chord(ortho: float) -> bool:
        return any(abs(ortho - o) <= 15.0 for o in chord)

    def _missing_face(s) -> bool:
        return any(
            w.is_h
            and w.layer == WALL_LAYER
            and 150.0 <= abs(w.ortho - s.ortho) <= 350.0
            and min(w.along1, s.along1) - max(w.along0, s.along0) >= s.length * 0.8
            for w in wall
        )

    n = 0
    seen: set[tuple[int, int, int]] = set()
    for s in cands:
        if s.layer == WALL_LAYER:
            continue
        if not _in_chord(s.ortho) and not _missing_face(s):
            continue
        key = (round(s.ortho), round(s.along0), round(s.along1))
        if key in seen:
            continue
        if _uncovered(s.ortho, s.along0, s.along1) < 800.0:
            continue
        seen.add(key)
        extra.append((s.ortho, s.along0, s.along1))
        e = s.entity
        if e is not None and e.dxftype() == "LINE" and getattr(e.dxf, "layer", None) == BASE_LAYER:
            e.dxf.layer = WALL_LAYER
            try:
                e.dxf.color = WALL_COLOR
            except Exception:  # noqa: BLE001
                pass
        elif e is not None and e.dxftype() == "LWPOLYLINE":
            try:
                pts = list(e.get_points("xy"))
            except Exception:  # noqa: BLE001
                pts = []
            if len(pts) == 2 and getattr(e.dxf, "layer", None) == BASE_LAYER:
                e.dxf.layer = WALL_LAYER
                try:
                    e.dxf.color = WALL_COLOR
                except Exception:  # noqa: BLE001
                    pass
            else:
                msp.add_line(
                    (s.along0, s.ortho),
                    (s.along1, s.ortho),
                    dxfattribs={"layer": WALL_LAYER, "color": WALL_COLOR},
                )
        else:
            msp.add_line(
                (s.along0, s.ortho),
                (s.along1, s.ortho),
                dxfattribs={"layer": WALL_LAYER, "color": WALL_COLOR},
            )
        n += 1
    return n


def open_waiting_room_top_door(msp) -> int:
    """대기실 위쪽 문은 개구이고 양옆은 벽이다.

    문짝과 문을 가로지르는 벽선은 지우고, 끊겨 회색인 양옆은 다시 올린다.
    """
    labels = [(x, y) for x, y, s in _iter_text_labels(msp) if s.strip() == "대기실"]
    if not labels:
        return 0
    swings: list[tuple[float, float, float, float]] = []
    seen_h: set[tuple[int, int]] = set()
    for e in msp:
        if e.dxftype() != "ARC":
            continue
        try:
            r = float(e.dxf.radius)
            cx, cy = float(e.dxf.center.x), float(e.dxf.center.y)
            sweep = (float(e.dxf.end_angle) - float(e.dxf.start_angle)) % 360.0
            sa, ea = float(e.dxf.start_angle), float(e.dxf.end_angle)
        except Exception:  # noqa: BLE001
            continue
        if not (900.0 <= r <= 1200.0) or not (70.0 <= sweep <= 110.0):
            continue
        key = (round(cx / 40.0), round(cy / 40.0))
        if key in seen_h:
            continue
        seen_h.add(key)
        leaf = None
        for ang in (sa, ea):
            rad = math.radians(ang)
            ex = cx + r * math.cos(rad)
            ey = cy + r * math.sin(rad)
            if abs(ey - cy) < 0.35 * r and abs(ex - cx) > 0.7 * r:
                leaf = ex
        if leaf is None:
            continue
        swings.append((cx, cy, r, leaf))

    n = 0
    for lx, ly in labels:
        for cx, cy, _r, leaf in swings:
            if not (ly + 400.0 < cy < ly + 8000.0) or abs(cx - lx) > 12000.0:
                continue
            o0, o1 = (cx, leaf) if cx < leaf else (leaf, cx)
            segs = iter_axis_segs(msp, min_len_mm=80.0)
            orthos = sorted(
                {
                    round(s.ortho)
                    for s in segs
                    if s.is_h and s.length >= 2000.0 and abs(s.ortho - cy) <= 300.0
                }
            )
            faces: list[float] = []
            for i, a in enumerate(orthos):
                for b in orthos[i + 1 :]:
                    if not (120.0 <= b - a <= 350.0):
                        continue
                    if min(abs(a - cy), abs(b - cy)) <= 80.0:
                        faces = [float(a), float(b)]
            if len(faces) < 2:
                continue
            crossing = [
                s
                for s in segs
                if s.is_h
                and s.layer == WALL_LAYER
                and s.entity is not None
                and any(abs(s.ortho - f) <= 45.0 for f in faces)
                and min(s.along1, o1) - max(s.along0, o0) > 200.0
            ]
            if not crossing:
                continue

            def _on_face(ortho: float) -> bool:
                return any(abs(ortho - f) <= 45.0 for f in faces)

            def _covered(ortho: float, a0: float, a1: float) -> bool:
                span = max(a1 - a0, 1.0)
                return any(
                    w.is_h
                    and w.layer == WALL_LAYER
                    and abs(w.ortho - ortho) <= 40.0
                    and min(w.along1, a1) - max(w.along0, a0) >= span * 0.85
                    for w in iter_axis_segs(msp, min_len_mm=80.0)
                )

            def _add(ortho: float, a0: float, a1: float) -> None:
                nonlocal n
                if a1 - a0 < 100.0 or _covered(ortho, a0, a1):
                    return
                msp.add_line(
                    (a0, ortho),
                    (a1, ortho),
                    dxfattribs={"layer": WALL_LAYER, "color": WALL_COLOR},
                )
                n += 1

            seen_e: set[int] = set()
            for s in crossing:
                eid = id(s.entity)
                if eid in seen_e:
                    continue
                seen_e.add(eid)
                e = s.entity
                if e.dxftype() == "LWPOLYLINE":
                    try:
                        pts = list(e.get_points("xy"))
                    except Exception:  # noqa: BLE001
                        continue
                    if len(pts) != 2:
                        continue
                elif e.dxftype() != "LINE":
                    continue
                a0, a1, y = s.along0, s.along1, s.ortho
                msp.delete_entity(e)
                if o0 - a0 >= 100.0:
                    _add(y, a0, o0)
                if a1 - o1 >= 100.0:
                    _add(y, o1, a1)

            for s in segs:
                if not s.is_h or s.layer == WALL_LAYER or not _on_face(s.ortho):
                    continue
                ov = min(s.along1, o1) - max(s.along0, o0)
                if ov > 80.0:
                    if o0 - s.along0 >= 100.0:
                        _add(s.ortho, s.along0, o0)
                    if s.along1 - o1 >= 100.0:
                        _add(s.ortho, o1, s.along1)
                    continue
                gap = 0.0 if ov > 0 else min(abs(s.along1 - o0), abs(s.along0 - o1))
                door_w = o1 - o0
                if gap > 500.0 or s.length < 100.0 or s.length < door_w * 1.2:
                    continue
                if _covered(s.ortho, s.along0, s.along1):
                    continue
                e = s.entity
                if (
                    e is not None
                    and e.dxftype() == "LINE"
                    and getattr(e.dxf, "layer", None) == BASE_LAYER
                ):
                    e.dxf.layer = WALL_LAYER
                    try:
                        e.dxf.color = WALL_COLOR
                    except Exception:  # noqa: BLE001
                        pass
                    n += 1
                else:
                    _add(s.ortho, s.along0, s.along1)
    return n


def promote_wash_room_edges(msp) -> int:
    """세척실 오른쪽 세로 이중선과 아래쪽 가로선은 벽이다.

    컨베이어 가장자리는 올리지 않는다.
    """
    labels = [(x, y) for x, y, s in _iter_text_labels(msp) if "세척실" in s]
    if not labels:
        return 0
    segs = iter_axis_segs(msp, min_len_mm=400.0)
    n = 0
    seen: set[tuple[int, int, int]] = set()

    def _covered(ortho: float, a0: float, a1: float, horizontal: bool) -> bool:
        span = max(a1 - a0, 1.0)
        return any(
            w.is_h == horizontal
            and w.layer == WALL_LAYER
            and abs(w.ortho - ortho) <= 40.0
            and min(w.along1, a1) - max(w.along0, a0) >= span * 0.85
            for w in segs
        )

    def _promote(s) -> None:
        nonlocal n
        key = (round(s.ortho), round(s.along0), round(s.along1))
        if key in seen or _covered(s.ortho, s.along0, s.along1, s.is_h):
            return
        seen.add(key)
        e = s.entity
        if (
            e is not None
            and e.dxftype() in ("LINE", "LWPOLYLINE")
            and getattr(e.dxf, "layer", None) == BASE_LAYER
        ):
            if e.dxftype() == "LWPOLYLINE":
                try:
                    pts = list(e.get_points("xy"))
                except Exception:  # noqa: BLE001
                    pts = []
                if len(pts) != 2:
                    msp.add_line(
                        (s.x0, s.y0),
                        (s.x1, s.y1),
                        dxfattribs={"layer": WALL_LAYER, "color": WALL_COLOR},
                    )
                    n += 1
                    return
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
        n += 1

    for lx, ly in labels:
        verts = [
            s
            for s in segs
            if s.is_v
            and s.length >= 5000.0
            and lx + 2000.0 < s.ortho < lx + 12000.0
            and s.along0 - 500.0 <= ly <= s.along1 + 500.0
        ]
        orthos = sorted({round(s.ortho) for s in verts})
        pair = None
        for i, a in enumerate(orthos):
            for b in orthos[i + 1 :]:
                if 120.0 <= b - a <= 400.0 and (pair is None or a < pair[0]):
                    pair = (float(a), float(b))
        if pair is not None:
            for s in verts:
                if s.layer == WALL_LAYER:
                    continue
                if abs(s.ortho - pair[0]) <= 40.0 or abs(s.ortho - pair[1]) <= 40.0:
                    _promote(s)
        bottoms = [
            s
            for s in segs
            if s.is_h
            and s.length >= 8000.0
            and ly - 4000.0 < s.ortho < ly - 400.0
            and s.along0 < lx < s.along1
        ]
        for s in bottoms:
            if s.layer == WALL_LAYER:
                continue
            if any(
                w.is_h
                and w.layer == WALL_LAYER
                and 120.0 <= abs(w.ortho - s.ortho) <= 350.0
                and min(w.along1, s.along1) - max(w.along0, s.along0) >= s.length * 0.7
                for w in segs
            ):
                _promote(s)
    return n


def promote_equipment_store_walls(msp) -> int:
    """기물 창고 상단과 하단 왼쪽의 끊긴 벽선을 잇는다."""
    labels = [(x, y) for x, y, s in _iter_text_labels(msp) if s.strip() == "기물 창고"]
    if not labels:
        return 0
    segs = iter_axis_segs(msp, min_len_mm=400.0)
    n = 0
    seen: set[tuple[int, int, int]] = set()

    def _covered(s) -> bool:
        span = max(s.length, 1.0)
        return any(
            w.is_h
            and w.layer == WALL_LAYER
            and abs(w.ortho - s.ortho) <= 40.0
            and min(w.along1, s.along1) - max(w.along0, s.along0) >= span * 0.85
            for w in segs
        )

    def _promote(s) -> None:
        nonlocal n
        key = (round(s.ortho), round(s.along0), round(s.along1))
        if key in seen or _covered(s):
            return
        seen.add(key)
        e = s.entity
        if e is not None and e.dxftype() == "LINE" and getattr(e.dxf, "layer", None) == BASE_LAYER:
            e.dxf.layer = WALL_LAYER
            try:
                e.dxf.color = WALL_COLOR
            except Exception:  # noqa: BLE001
                pass
        elif e is not None and e.dxftype() == "LWPOLYLINE" and getattr(e.dxf, "layer", None) == BASE_LAYER:
            try:
                pts = list(e.get_points("xy"))
            except Exception:  # noqa: BLE001
                pts = []
            if len(pts) == 2:
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
        else:
            msp.add_line(
                (s.x0, s.y0),
                (s.x1, s.y1),
                dxfattribs={"layer": WALL_LAYER, "color": WALL_COLOR},
            )
        n += 1

    for lx, ly in labels:
        sides = [
            s
            for s in segs
            if s.is_v
            and s.layer == WALL_LAYER
            and s.length >= 8000.0
            and abs(s.ortho - lx) < 12000.0
            and s.along0 < ly < s.along1
        ]
        lefts = [s.ortho for s in sides if s.ortho < lx - 2000.0]
        rights = [s.ortho for s in sides if s.ortho > lx + 2000.0]
        if not lefts or not rights:
            continue
        x_left, x_right = min(lefts), max(rights)
        x_inner = max(lefts)
        cols = [
            s
            for s in segs
            if s.is_v
            and s.layer == WALL_LAYER
            and 1000.0 <= s.length <= 1600.0
            and x_inner < s.ortho < lx
        ]
        for s in segs:
            if not s.is_h or s.layer == WALL_LAYER:
                continue
            if not (1500.0 <= s.length <= 3500.0):
                continue
            if not (ly - 4000.0 < s.ortho < ly - 1500.0):
                continue
            if min(abs(s.along0 - x_inner), abs(s.along0 - x_left)) > 400.0:
                continue
            if not any(abs(s.along1 - c.ortho) <= 200.0 for c in cols):
                continue
            if not any(
                w.is_h
                and w.layer == WALL_LAYER
                and abs(w.ortho - s.ortho) <= 40.0
                and w.along0 >= s.along1 - 200.0
                and w.length >= 3000.0
                for w in segs
            ):
                continue
            _promote(s)
        for s in segs:
            if not s.is_h or s.layer == WALL_LAYER or s.length < 3000.0:
                continue
            if not (ly + 400.0 < s.ortho < ly + 2000.0):
                continue
            if s.along0 < x_left - 300.0 or s.along1 > x_right + 300.0:
                continue
            _promote(s)
    return n


def promote_small_meeting_bottom(msp) -> int:
    """소회의실#17 아래쪽 이중선은 벽이다. 회의 테이블 선은 올리지 않는다."""
    labels = [(x, y) for x, y, s in _iter_text_labels(msp) if s.strip() == "소회의실#17"]
    if not labels:
        return 0
    segs = iter_axis_segs(msp, min_len_mm=400.0)
    n = 0
    seen: set[tuple[int, int, int]] = set()
    for lx, ly in labels:
        # 같은 실명 위층은 별도 도면이다. 9층 좌표대만 올린다.
        if ly > 600000.0:
            continue
        sides = [
            s
            for s in segs
            if s.is_v
            and s.layer == WALL_LAYER
            and s.length >= 3000.0
            and abs(s.ortho - lx) < 8000.0
            and s.along0 < ly < s.along1
        ]
        lefts = [s for s in sides if s.ortho < lx - 500.0]
        rights = [s for s in sides if s.ortho > lx + 500.0]
        if not lefts or not rights:
            continue
        x_inner = max(s.ortho for s in lefts)
        x_outer_right = max(s.ortho for s in rights)
        y_bot = min(s.along0 for s in lefts + rights)
        for s in segs:
            if not s.is_h or s.layer == WALL_LAYER or s.entity is None:
                continue
            if s.length < 1500.0:
                continue
            if not (y_bot - 80.0 <= s.ortho <= y_bot + 400.0):
                continue
            if s.along0 < x_inner - 250.0 or s.along1 > x_outer_right + 250.0:
                continue
            key = (round(s.ortho), round(s.along0), round(s.along1))
            if key in seen:
                continue
            seen.add(key)
            e = s.entity
            if e.dxftype() == "LINE" and getattr(e.dxf, "layer", None) == BASE_LAYER:
                e.dxf.layer = WALL_LAYER
                try:
                    e.dxf.color = WALL_COLOR
                except Exception:  # noqa: BLE001
                    pass
            elif e.dxftype() == "LWPOLYLINE" and getattr(e.dxf, "layer", None) == BASE_LAYER:
                try:
                    pts = list(e.get_points("xy"))
                except Exception:  # noqa: BLE001
                    pts = []
                if len(pts) == 2:
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
            else:
                msp.add_line(
                    (s.x0, s.y0),
                    (s.x1, s.y1),
                    dxfattribs={"layer": WALL_LAYER, "color": WALL_COLOR},
                )
            n += 1
    return n


def promote_exec_meeting_right(msp) -> int:
    """회의실#1(임원) 오른쪽은 벽이다. 기둥 위·아래의 끊긴 이중선을 잇는다."""
    labels = [(x, y) for x, y, s in _iter_text_labels(msp) if s.strip() == "회의실#1"]
    if not labels:
        return 0
    imwon = [(x, y) for x, y, s in _iter_text_labels(msp) if s.strip() == "(임원)"]
    segs = iter_axis_segs(msp, min_len_mm=400.0)
    n = 0
    seen: set[int] = set()
    for lx, ly in labels:
        # 같은 실명 위층은 별도 도면이다. 9층 좌표대만 올린다.
        if not (500000.0 < ly < 560000.0):
            continue
        if not any(abs(x - lx) < 4000.0 and abs(y - ly) < 2500.0 for x, y in imwon):
            continue
        faces = [
            s
            for s in segs
            if s.is_v
            and s.layer == WALL_LAYER
            and 1000.0 <= s.length <= 1600.0
            and lx + 2000.0 < s.ortho < lx + 5000.0
            and s.along1 > ly - 1500.0
            and s.along0 < ly + 3000.0
        ]
        if not faces:
            continue
        x_right = max(s.ortho for s in faces)
        mates = {
            round(s.ortho)
            for s in segs
            if s.is_v
            and 150.0 <= x_right - s.ortho <= 350.0
            and s.length >= 800.0
        }
        orthos = {round(x_right)} | mates
        y0 = min(s.along0 for s in faces)
        y1 = max(s.along1 for s in faces)
        for s in segs:
            if not s.is_v or s.layer == WALL_LAYER or s.entity is None or s.length < 800.0:
                continue
            if round(s.ortho) not in orthos and not any(abs(s.ortho - o) <= 40.0 for o in orthos):
                continue
            if s.along1 < y0 - 3500.0 or s.along0 > y1 + 2500.0:
                continue
            if min(s.along1, y1 + 80.0) - max(s.along0, y0 - 80.0) < -50.0:
                continue
            # 기둥 면에 닿는 위·아래 조각만.
            if min(abs(s.along0 - y0), abs(s.along0 - y1), abs(s.along1 - y0), abs(s.along1 - y1)) > 80.0:
                continue
            eid = id(s.entity)
            if eid in seen:
                continue
            seen.add(eid)
            e = s.entity
            if e.dxftype() in ("LINE", "LWPOLYLINE") and getattr(e.dxf, "layer", None) == BASE_LAYER:
                try:
                    pts = list(e.get_points("xy")) if e.dxftype() == "LWPOLYLINE" else []
                except Exception:  # noqa: BLE001
                    pts = []
                xs = [p[0] for p in pts]
                if e.dxftype() == "LINE" or (pts and max(xs) - min(xs) <= 400.0):
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
            else:
                msp.add_line(
                    (s.x0, s.y0),
                    (s.x1, s.y1),
                    dxfattribs={"layer": WALL_LAYER, "color": WALL_COLOR},
                )
            n += 1
    return n


def demote_bay_panel_sills(msp) -> int:
    """객실 위쪽 패널 바로 아래의 단일 가로선은 벽이 아니다.

    407호처럼 1500×2000 패널 두 개의 밑변에서 약 110mm 아래 선은
    벽 두께로 오인된 것이다. 같은 줄의 이웃 실도 내린다.
    """
    panels: list[tuple[float, float, float, float]] = []
    for e in msp:
        if e.dxftype() != "LWPOLYLINE":
            continue
        try:
            pts = [(float(p[0]), float(p[1])) for p in e.get_points("xy")]
        except Exception:  # noqa: BLE001
            continue
        if len(pts) < 4:
            continue
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        width, height = max(xs) - min(xs), max(ys) - min(ys)
        if not (1200.0 <= width <= 2200.0 and 1600.0 <= height <= 2500.0):
            continue
        panels.append((min(xs), max(xs), min(ys), max(ys)))
    if len(panels) < 2:
        return 0

    rows: list[tuple[float, float, float]] = []
    used = [False] * len(panels)
    for i, (ax0, ax1, ay0, _ay1) in enumerate(panels):
        if used[i]:
            continue
        group = [i]
        used[i] = True
        for j in range(i + 1, len(panels)):
            if used[j]:
                continue
            bx0, bx1, by0, _by1 = panels[j]
            if abs(by0 - ay0) > 40.0:
                continue
            if min(ax1, bx1) - max(ax0, bx0) < -400.0:
                continue
            group.append(j)
            used[j] = True
        if len(group) < 2:
            continue
        x0 = min(panels[k][0] for k in group)
        x1 = max(panels[k][1] for k in group)
        y0 = sum(panels[k][2] for k in group) / len(group)
        rows.append((x0, x1, y0))

    n = 0
    for e in list(msp):
        if e.dxftype() != "LINE" or getattr(e.dxf, "layer", None) != WALL_LAYER:
            continue
        axis = _axis_line(e)
        if axis is None or axis[0] != "H":
            continue
        _ori, y, a0, a1, length = axis
        if not (2500.0 <= length <= 5000.0):
            continue
        hit = False
        for x0, x1, bottom in rows:
            gap = bottom - y
            if not (60.0 <= gap <= 200.0):
                continue
            if min(a1, x1) - max(a0, x0) < (x1 - x0) * 0.7:
                continue
            hit = True
            break
        if not hit:
            continue
        # 패널 반대편(아래)에 진짜 이중벽 면이 있으면 벽으로 둔다.
        mate = False
        for other in msp:
            if other is e or getattr(other.dxf, "layer", None) != WALL_LAYER:
                continue
            other_axis = _axis_line(other)
            if other_axis is None or other_axis[0] != "H":
                continue
            oy, oa, ob, olen = other_axis[1], other_axis[2], other_axis[3], other_axis[4]
            if olen < 1500.0 or not (80.0 <= y - oy <= 350.0):
                continue
            if min(a1, ob) - max(a0, oa) >= 1500.0:
                mate = True
                break
        if mate:
            continue
        msp.delete_entity(e)
        n += 1
    return n


def promote_end_wall_columns(msp) -> int:
    """긴 이중벽이 끝나는 직사각 기둥을 WALL로 올린다.

    408호·409호 사이 아래쪽처럼 가로 400mm·세로 600mm여도 기둥이다.
    정사각 최소 변(450mm)보다 좁아 벽면 정사각 규칙에 안 걸린다.
    """
    boxes: list[tuple[float, float, float, float, Any]] = []
    for e in msp:
        if e.dxftype() != "LWPOLYLINE" or not e.closed:
            continue
        if getattr(e.dxf, "layer", None) not in (WALL_LAYER, BASE_LAYER):
            continue
        try:
            pts = [(float(p[0]), float(p[1])) for p in e.get_points("xy")]
        except Exception:  # noqa: BLE001
            continue
        if not (4 <= len(pts) <= 6):
            continue
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        x0, x1 = min(xs), max(xs)
        y0, y1 = min(ys), max(ys)
        short, long = min(x1 - x0, y1 - y0), max(x1 - x0, y1 - y0)
        if not (350.0 <= short <= 750.0 and 500.0 <= long <= 850.0):
            continue
        if short / long < 0.55:
            continue
        boxes.append((x0, x1, y0, y1, e))

    # 404·405호 아래쪽처럼 윤곽이 닫힌 폴리선이 아니라 선 네 개인 기둥.
    axis_segs: list[tuple[str, float, float, float, float, str, Any]] = []
    for e in msp:
        axis = _axis_line(e)
        if axis is None:
            continue
        layer = getattr(e.dxf, "layer", None)
        if layer not in (WALL_LAYER, BASE_LAYER):
            continue
        ori, coord, a, b, length = axis
        axis_segs.append((ori, coord, a, b, length, layer, e))
    verts = [s for s in axis_segs if s[0] == "V" and 500.0 <= s[4] <= 750.0]
    hors = [s for s in axis_segs if s[0] == "H" and 350.0 <= s[4] <= 750.0]
    seen_line: set[tuple[int, int]] = set()
    for v in verts:
        for v2 in verts:
            if v2 is v:
                continue
            gap = v2[1] - v[1]
            if not (350.0 <= gap <= 750.0):
                continue
            if abs(v[2] - v2[2]) > 40.0 or abs(v[3] - v2[3]) > 40.0:
                continue
            x0, x1 = v[1], v2[1]
            y0, y1 = v[2], v[3]
            key = (round(x0), round(y0))
            if key in seen_line:
                continue
            top = next(
                (h for h in hors if abs(h[2] - x0) <= 40.0 and abs(h[3] - x1) <= 40.0 and abs(h[1] - y1) <= 40.0),
                None,
            )
            bot = next(
                (h for h in hors if abs(h[2] - x0) <= 40.0 and abs(h[3] - x1) <= 40.0 and abs(h[1] - y0) <= 40.0),
                None,
            )
            if top is None or bot is None:
                continue
            # 대각선이 있는 사각은 R.E.F 표식이다.
            diag = False
            for e in msp:
                if e.dxftype() != "LINE":
                    continue
                ax, ay = float(e.dxf.start.x), float(e.dxf.start.y)
                bx, by = float(e.dxf.end.x), float(e.dxf.end.y)
                if abs(bx - ax) < 80.0 or abs(by - ay) < 80.0:
                    continue
                if (
                    x0 - 30.0 <= min(ax, bx)
                    and max(ax, bx) <= x1 + 30.0
                    and y0 - 30.0 <= min(ay, by)
                    and max(ay, by) <= y1 + 30.0
                ):
                    diag = True
                    break
            if diag:
                continue
            seen_line.add(key)
            boxes.append((x0, x1, y0, y1, v[6]))
            boxes.append((x0, x1, y0, y1, v2[6]))
            boxes.append((x0, x1, y0, y1, top[6]))
            boxes.append((x0, x1, y0, y1, bot[6]))
    if not boxes:
        return 0

    v_walls: list[tuple[float, float, float]] = []
    h_walls: list[tuple[float, float, float]] = []
    for e in msp:
        if getattr(e.dxf, "layer", None) != WALL_LAYER:
            continue
        axis = _axis_line(e)
        if axis is None:
            continue
        ori, coord, a, b, length = axis
        if length < 2000.0:
            continue
        if ori == "V":
            v_walls.append((coord, a, b))
        else:
            h_walls.append((coord, a, b))

    def _pair_stops(vals: list[float]) -> bool:
        vals = sorted(set(round(v, 1) for v in vals))
        for i, va in enumerate(vals):
            for vb in vals[i + 1 :]:
                if 80.0 <= vb - va <= 350.0:
                    return True
        return False

    def _wall_ends(x0: float, x1: float, y0: float, y1: float) -> bool:
        top = [
            x for x, a, b in v_walls
            if x0 - 40.0 <= x <= x1 + 40.0 and abs(a - y1) <= 70.0 and b >= y1 + 1500.0
        ]
        bot = [
            x for x, a, b in v_walls
            if x0 - 40.0 <= x <= x1 + 40.0 and abs(b - y0) <= 70.0 and a <= y0 - 1500.0
        ]
        left = [
            y for y, a, b in h_walls
            if y0 - 40.0 <= y <= y1 + 40.0 and abs(b - x0) <= 70.0 and a <= x0 - 1500.0
        ]
        right = [
            y for y, a, b in h_walls
            if y0 - 40.0 <= y <= y1 + 40.0 and abs(a - x1) <= 70.0 and b >= x1 + 1500.0
        ]
        return _pair_stops(top) or _pair_stops(bot) or _pair_stops(left) or _pair_stops(right)

    chosen = [box for box in boxes if _wall_ends(*box[:4])]
    # 410호 왼쪽 아래처럼, 위쪽 칸막이가 아직 회색이어도
    # 이미 기둥인 줄과 위·아래 높이가 같은 직사각은 같은 기둥이다.
    rows = [(box[2], box[3]) for box in chosen]
    for box in boxes:
        if box in chosen:
            continue
        if any(abs(box[2] - y0) <= 40.0 and abs(box[3] - y1) <= 40.0 for y0, y1 in rows):
            chosen.append(box)
    if not chosen:
        return 0

    def _to_wall(ent) -> bool:
        if getattr(ent.dxf, "layer", None) != BASE_LAYER:
            return False
        ent.dxf.layer = WALL_LAYER
        try:
            ent.dxf.color = WALL_COLOR
        except Exception:  # noqa: BLE001
            pass
        return True

    n = 0
    seen: set[int] = set()
    for x0, x1, y0, y1, ent in chosen:
        if id(ent) not in seen and _to_wall(ent):
            seen.add(id(ent))
            n += 1
        else:
            seen.add(id(ent))
    for e in msp:
        if id(e) in seen or getattr(e.dxf, "layer", None) != BASE_LAYER:
            continue
        axis = _axis_line(e)
        if axis is None:
            continue
        ori, coord, a, b, length = axis
        if length < 80.0:
            continue
        hit = False
        for x0, x1, y0, y1, _ent in chosen:
            if ori == "H" and a >= x0 - 40.0 and b <= x1 + 40.0 and (
                abs(coord - y0) <= 30.0 or abs(coord - y1) <= 30.0
            ):
                hit = True
                break
            if ori == "V" and a >= y0 - 40.0 and b <= y1 + 40.0 and (
                abs(coord - x0) <= 30.0 or abs(coord - x1) <= 30.0
            ):
                hit = True
                break
        if hit and _to_wall(e):
            seen.add(id(e))
            n += 1

    # 기둥에서 끊긴 이중벽과 같은 자리로, 반대편으로 나가는 짧은 쌍도 벽이다.
    # 404·405호 아래 1m 쌍. 기둥 옆의 문 구간은 좌표가 달라 올리지 않는다.
    uniq: list[tuple[float, float, float, float]] = []
    seen_box: set[tuple[int, int]] = set()
    for x0, x1, y0, y1, _ent in chosen:
        key = (round(x0), round(y0))
        if key in seen_box:
            continue
        seen_box.add(key)
        uniq.append((x0, x1, y0, y1))

    def _stop_coords(x0: float, x1: float, y0: float, y1: float) -> list[tuple[str, float]]:
        found: list[tuple[str, float]] = []
        for x, a, b in v_walls:
            if not (x0 - 40.0 <= x <= x1 + 40.0):
                continue
            if (abs(a - y1) <= 70.0 and b >= y1 + 1500.0) or (abs(b - y0) <= 70.0 and a <= y0 - 1500.0):
                found.append(("V", x))
        for y, a, b in h_walls:
            if not (y0 - 40.0 <= y <= y1 + 40.0):
                continue
            if (abs(b - x0) <= 70.0 and a <= x0 - 1500.0) or (abs(a - x1) <= 70.0 and b >= x1 + 1500.0):
                found.append(("H", y))
        return found

    leaving: list[tuple[str, float, float, float, Any, int]] = []
    for e in msp:
        if id(e) in seen or getattr(e.dxf, "layer", None) != BASE_LAYER:
            continue
        axis = _axis_line(e)
        if axis is None:
            continue
        ori, coord, a, b, length = axis
        if not (400.0 <= length <= 2000.0):
            continue
        for i, (x0, x1, y0, y1) in enumerate(uniq):
            stops = [c for o, c in _stop_coords(x0, x1, y0, y1) if o == ori and abs(c - coord) <= 40.0]
            if not stops:
                continue
            away = False
            if ori == "V":
                away = (abs(b - y0) <= 40.0 and a <= y0 - 400.0) or (
                    abs(a - y1) <= 40.0 and b >= y1 + 400.0
                )
            else:
                away = (abs(b - x0) <= 40.0 and a <= x0 - 400.0) or (
                    abs(a - x1) <= 40.0 and b >= x1 + 400.0
                )
            if away:
                leaving.append((ori, coord, a, b, e, i))
                break
    used_leave: set[int] = set()
    for i, (ori, coord, a, b, ent, box_i) in enumerate(leaving):
        if id(ent) in used_leave:
            continue
        for ori2, coord2, a2, b2, ent2, box_j in leaving[i + 1 :]:
            if box_j != box_i or ori2 != ori or id(ent2) in used_leave:
                continue
            if not (80.0 <= abs(coord2 - coord) <= 350.0):
                continue
            if min(b, b2) - max(a, a2) < 400.0:
                continue
            if _to_wall(ent):
                n += 1
            if _to_wall(ent2):
                n += 1
            used_leave.add(id(ent))
            used_leave.add(id(ent2))
            break

    # 기둥 폭 안쪽에 박힌 평행 쌍이 기둥 밖으로 이어지면 벽이다.
    # 409호 오른쪽 끝 600mm 기둥 위의 1500mm 쌍. X 대각선이 있는 문 구간은 제외.
    diags: list[tuple[float, float, float, float]] = []
    for e in msp:
        if e.dxftype() != "LINE":
            continue
        ax, ay = float(e.dxf.start.x), float(e.dxf.start.y)
        bx, by = float(e.dxf.end.x), float(e.dxf.end.y)
        if abs(bx - ax) < 80.0 or abs(by - ay) < 80.0:
            continue
        diags.append((min(ax, bx), max(ax, bx), min(ay, by), max(ay, by)))

    inset: list[tuple[str, float, float, float, Any, int]] = []
    for e in msp:
        if id(e) in seen or id(e) in used_leave or getattr(e.dxf, "layer", None) != BASE_LAYER:
            continue
        axis = _axis_line(e)
        if axis is None:
            continue
        ori, coord, a, b, length = axis
        if not (800.0 <= length <= 2000.0):
            continue
        for i, (x0, x1, y0, y1) in enumerate(uniq):
            away = False
            if ori == "V" and x0 + 60.0 <= coord <= x1 - 60.0:
                away = (abs(b - y0) <= 40.0 and a <= y0 - 400.0) or (
                    abs(a - y1) <= 40.0 and b >= y1 + 400.0
                )
            elif ori == "H" and y0 + 60.0 <= coord <= y1 - 60.0:
                away = (abs(b - x0) <= 40.0 and a <= x0 - 400.0) or (
                    abs(a - x1) <= 40.0 and b >= x1 + 400.0
                )
            if away:
                inset.append((ori, coord, a, b, e, i))
                break
    used_in: set[int] = set()
    for i, (ori, coord, a, b, ent, box_i) in enumerate(inset):
        if id(ent) in used_in:
            continue
        for ori2, coord2, a2, b2, ent2, box_j in inset[i + 1 :]:
            if box_j != box_i or ori2 != ori or id(ent2) in used_in:
                continue
            if not (80.0 <= abs(coord2 - coord) <= 350.0):
                continue
            ov0, ov1 = max(a, a2), min(b, b2)
            if ov1 - ov0 < 700.0:
                continue
            lo, hi = min(coord, coord2), max(coord, coord2)
            if ori == "V":
                crossed = any(
                    lo - 20.0 <= dx0
                    and dx1 <= hi + 20.0
                    and dy0 >= ov0 - 20.0
                    and dy1 <= ov1 + 20.0
                    for dx0, dx1, dy0, dy1 in diags
                )
            else:
                crossed = any(
                    lo - 20.0 <= dy0
                    and dy1 <= hi + 20.0
                    and dx0 >= ov0 - 20.0
                    and dx1 <= ov1 + 20.0
                    for dx0, dx1, dy0, dy1 in diags
                )
            if crossed:
                continue
            if _to_wall(ent):
                n += 1
            if _to_wall(ent2):
                n += 1
            used_in.add(id(ent))
            used_in.add(id(ent2))
            break
    return n


def correct_x_block_doors(msp) -> tuple[int, int]:
    """벽 두께 안의 X자 블록은 문이다. 문은 내리고 양옆 벽면은 올린다.

    407호 아래처럼 대각선 두 개가 벽 두께(80–350mm) 안에서 교차하고
    개구 폭은 650–1700mm다. X와 개구를 가로지르는 선은 BASE로 두고,
    같은 면으로 이어진 양옆은 WALL로 둔다.
    """
    pieces: list[tuple[float, float, float, float, Any]] = []
    for e in msp:
        layer = getattr(e.dxf, "layer", None)
        if layer not in (WALL_LAYER, BASE_LAYER):
            continue
        if e.dxftype() == "LINE":
            try:
                spans = [(
                    (float(e.dxf.start.x), float(e.dxf.start.y)),
                    (float(e.dxf.end.x), float(e.dxf.end.y)),
                )]
            except Exception:  # noqa: BLE001
                continue
        elif e.dxftype() == "LWPOLYLINE":
            try:
                pts = [(float(p[0]), float(p[1])) for p in e.get_points("xy")]
            except Exception:  # noqa: BLE001
                continue
            spans = list(zip(pts, pts[1:]))
        else:
            continue
        for (x0, y0), (x1, y1) in spans:
            dx, dy = abs(x1 - x0), abs(y1 - y0)
            if dx < 80.0 or dy < 60.0:
                continue
            length = math.hypot(x1 - x0, y1 - y0)
            if not (400.0 <= length <= 2200.0):
                continue
            if min(dx, dy) / max(dx, dy) < 0.06:
                continue
            pieces.append((x0, y0, x1, y1, e))

    used = [False] * len(pieces)
    doors: list[dict[str, Any]] = []
    for i, a in enumerate(pieces):
        if used[i]:
            continue
        ax0, ax1 = min(a[0], a[2]), max(a[0], a[2])
        ay0, ay1 = min(a[1], a[3]), max(a[1], a[3])
        group = [i]
        used[i] = True
        for j in range(i + 1, len(pieces)):
            if used[j]:
                continue
            b = pieces[j]
            bx0, bx1 = min(b[0], b[2]), max(b[0], b[2])
            by0, by1 = min(b[1], b[3]), max(b[1], b[3])
            if (
                abs(ax0 - bx0) <= 40.0
                and abs(ax1 - bx1) <= 40.0
                and abs(ay0 - by0) <= 40.0
                and abs(ay1 - by1) <= 40.0
            ):
                used[j] = True
                group.append(j)
        if len(group) < 2:
            continue
        signs: set[bool] = set()
        for k in group:
            x0, y0, x1, y1, _ent = pieces[k]
            signs.add((x1 - x0) * (y1 - y0) > 0.0)
        if len(signs) < 2:
            continue
        width, height = ax1 - ax0, ay1 - ay0
        thick, span = min(width, height), max(width, height)
        if not (80.0 <= thick <= 360.0 and 650.0 <= span <= 1750.0):
            continue
        if thick / span > 0.5:
            continue
        is_h = width >= height
        doors.append(
            {
                "is_h": is_h,
                "faces": (ay0, ay1) if is_h else (ax0, ax1),
                "a0": ax0 if is_h else ay0,
                "a1": ax1 if is_h else ay1,
                "diag_ids": {id(pieces[k][4]) for k in group},
            }
        )
    if not doors:
        return (0, 0)

    def _merge(cuts: list[tuple[float, float]]) -> list[tuple[float, float]]:
        if not cuts:
            return []
        cuts = sorted(cuts)
        merged: list[list[float]] = [[cuts[0][0], cuts[0][1]]]
        for c0, c1 in cuts[1:]:
            if c0 <= merged[-1][1] + 30.0:
                merged[-1][1] = max(merged[-1][1], c1)
            else:
                merged.append([c0, c1])
        return [(c0, c1) for c0, c1 in merged]

    def _openings(is_h: bool, ortho: float, tol: float) -> list[tuple[float, float]]:
        cuts: list[tuple[float, float]] = []
        for door in doors:
            if door["is_h"] != is_h:
                continue
            if any(abs(ortho - face) <= tol for face in door["faces"]):
                cuts.append((door["a0"], door["a1"]))
        return _merge(cuts)

    def _inside(a0: float, a1: float, cuts: list[tuple[float, float]]) -> float:
        total = 0.0
        for c0, c1 in cuts:
            total += max(0.0, min(a1, c1) - max(a0, c0))
        return total

    def _outside(a0: float, a1: float, cuts: list[tuple[float, float]]) -> list[tuple[float, float]]:
        pieces_out: list[tuple[float, float]] = []
        cursor = a0
        for c0, c1 in cuts:
            lo = max(cursor, a0)
            if c0 - lo >= 80.0:
                pieces_out.append((lo, min(c0, a1)))
            cursor = max(cursor, c1)
        if a1 - cursor >= 80.0:
            pieces_out.append((max(cursor, a0), a1))
        return [(p0, p1) for p0, p1 in pieces_out if p1 - p0 >= 80.0]

    segs: list[tuple[Any, bool, float, float, float]] = []
    for e in msp:
        axis = _axis_line(e)
        if axis is None:
            continue
        ori, coord, a, b, length = axis
        if length < 40.0:
            continue
        if getattr(e.dxf, "layer", None) not in (WALL_LAYER, BASE_LAYER):
            continue
        segs.append((e, ori == "H", coord, a, b))

    def _has_mate(is_h: bool, ortho: float, a0: float, a1: float) -> bool:
        for _e, iv, oo, b0, b1 in segs:
            if iv != is_h:
                continue
            gap = abs(oo - ortho)
            if not (70.0 <= gap <= 420.0):
                continue
            if min(a1, b1) - max(a0, b0) >= 180.0:
                return True
        return False

    def _to_base(ent) -> None:
        ent.dxf.layer = BASE_LAYER
        try:
            ent.dxf.color = BASE_COLOR
        except Exception:  # noqa: BLE001
            pass

    def _to_wall(ent) -> None:
        ent.dxf.layer = WALL_LAYER
        try:
            ent.dxf.color = WALL_COLOR
        except Exception:  # noqa: BLE001
            pass

    n_demote = 0
    n_promote = 0
    demoted: set[int] = set()
    for door in doors:
        for ent_id in door["diag_ids"]:
            demoted.add(ent_id)

    split_add: list[tuple[bool, float, float, float]] = []
    for ent, is_h, ortho, a0, a1 in segs:
        if getattr(ent.dxf, "layer", None) != WALL_LAYER:
            continue
        if id(ent) in demoted:
            continue
        cuts = _openings(is_h, ortho, 45.0)
        wide = _openings(is_h, ortho, 90.0)
        length = a1 - a0
        ilen = _inside(a0, a1, cuts)
        ilen_wide = _inside(a0, a1, wide)
        host = 0.0
        for c0, c1 in wide:
            if min(a1, c1) - max(a0, c0) >= 400.0:
                host = max(host, c1 - c0)
        contained = (
            ilen_wide >= 400.0
            and ilen_wide >= length * 0.7
            and host > 0.0
            and length <= host + 280.0
        )
        if contained or (ilen >= 250.0 and _outside(a0, a1, cuts) == []):
            _to_base(ent)
            demoted.add(id(ent))
            n_demote += 1
            continue
        if ilen < 250.0:
            continue
        outside = _outside(a0, a1, cuts)
        if not outside:
            _to_base(ent)
            demoted.add(id(ent))
            n_demote += 1
            continue
        demoted.add(id(ent))
        msp.delete_entity(ent)
        n_demote += 1
        for p0, p1 in outside:
            split_add.append((is_h, ortho, p0, p1))

    seen_diag: set[int] = set()
    diag_ids = {ent_id for door in doors for ent_id in door["diag_ids"]}
    for ent in msp:
        if id(ent) not in diag_ids or id(ent) in seen_diag:
            continue
        seen_diag.add(id(ent))
        if getattr(ent.dxf, "layer", None) != WALL_LAYER:
            continue
        _to_base(ent)
        n_demote += 1

    def _covered(is_h: bool, ortho: float, a0: float, a1: float) -> bool:
        span = a1 - a0
        if span < 80.0:
            return True
        cover = 0.0
        for ent, iv, oo, b0, b1 in segs:
            if iv != is_h or abs(oo - ortho) > 25.0:
                continue
            if id(ent) in demoted:
                continue
            if getattr(ent.dxf, "layer", None) != WALL_LAYER:
                continue
            cover += max(0.0, min(a1, b1) - max(a0, b0))
        return cover >= span * 0.75

    for is_h, ortho, p0, p1 in split_add:
        if _covered(is_h, ortho, p0, p1):
            continue
        if is_h:
            msp.add_line((p0, ortho), (p1, ortho), dxfattribs={"layer": WALL_LAYER, "color": WALL_COLOR})
        else:
            msp.add_line((ortho, p0), (ortho, p1), dxfattribs={"layer": WALL_LAYER, "color": WALL_COLOR})

    flank: list[tuple[Any, bool, float, float, float]] = []
    seen_flank: set[int] = set()
    for ent, is_h, ortho, a0, a1 in segs:
        if id(ent) in demoted or id(ent) in seen_flank:
            continue
        if getattr(ent.dxf, "layer", None) != BASE_LAYER:
            continue
        length = a1 - a0
        if not (180.0 <= length <= 12000.0):
            continue
        cuts = _openings(is_h, ortho, 45.0)
        if not cuts or _inside(a0, a1, cuts) > 80.0:
            continue
        if not _has_mate(is_h, ortho, a0, a1):
            continue
        touches = False
        for c0, c1 in cuts:
            if abs(a1 - c0) <= 80.0 or abs(a0 - c1) <= 80.0:
                touches = True
                break
        if not touches:
            continue
        flank.append((ent, is_h, ortho, a0, a1))
        seen_flank.add(id(ent))

    changed = True
    while changed:
        changed = False
        for ent, is_h, ortho, a0, a1 in segs:
            if id(ent) in demoted or id(ent) in seen_flank:
                continue
            if getattr(ent.dxf, "layer", None) != BASE_LAYER:
                continue
            length = a1 - a0
            if not (180.0 <= length <= 12000.0):
                continue
            cuts = _openings(is_h, ortho, 45.0)
            if _inside(a0, a1, cuts) > 80.0:
                continue
            if not _has_mate(is_h, ortho, a0, a1):
                continue
            linked = False
            for _pent, pis_h, portho, p0, p1 in flank:
                if pis_h != is_h or abs(portho - ortho) > 25.0:
                    continue
                gap = max(a0, p0) - min(a1, p1)
                if gap <= 40.0:
                    linked = True
                    break
            if not linked:
                continue
            flank.append((ent, is_h, ortho, a0, a1))
            seen_flank.add(id(ent))
            changed = True

    for ent, _is_h, _ortho, _a0, _a1 in flank:
        if getattr(ent.dxf, "layer", None) == WALL_LAYER:
            continue
        _to_wall(ent)
        n_promote += 1

    # 개구 끝에서 벽 두께만 막는 짧은 막이선( jamb )도 벽이다.
    closed: set[int] = set()
    for door in doors:
        f0, f1 = min(door["faces"]), max(door["faces"])
        thick = f1 - f0
        for edge in (door["a0"], door["a1"]):
            for ent, seg_h, ortho, s0, s1 in segs:
                if seg_h == door["is_h"] or id(ent) in closed or id(ent) in demoted:
                    continue
                if ent.dxftype() != "LINE":
                    continue
                if abs(ortho - edge) > 25.0:
                    continue
                if s0 < f0 - 30.0 or s1 > f1 + 30.0:
                    continue
                if (s1 - s0) < thick * 0.6:
                    continue
                if getattr(ent.dxf, "layer", None) != BASE_LAYER:
                    continue
                _to_wall(ent)
                closed.add(id(ent))
                n_promote += 1
    return (n_demote, n_promote)


def correct_capped_leaf_doors(msp) -> tuple[int, int]:
    """벽 두께 안에서 양 끝 캡으로 닫힌 문짝은 문이다.

    410호 아래쪽처럼 약 30×50mm 캡 두 개와 그 사이 약 1190mm 선이
    150mm 이중벽 한가운데에 있다. 캡 바깥까지가 개구라 내리고,
    개구에 바로 닿은 양옆 면은 벽으로 둔다.
    """
    caps: list[tuple[float, float, float, float]] = []
    for e in msp:
        if e.dxftype() != "LWPOLYLINE" or not e.closed:
            continue
        try:
            pts = [(float(p[0]), float(p[1])) for p in e.get_points("xy")]
        except Exception:  # noqa: BLE001
            continue
        if len(pts) < 4:
            continue
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        x0, x1 = min(xs), max(xs)
        y0, y1 = min(ys), max(ys)
        if 15.0 <= (x1 - x0) <= 90.0 and 15.0 <= (y1 - y0) <= 90.0:
            caps.append((x0, x1, y0, y1))
    if len(caps) < 2:
        return (0, 0)

    segs: list[tuple[Any, bool, float, float, float]] = []
    for e in msp:
        axis = _axis_line(e)
        if axis is None:
            continue
        if getattr(e.dxf, "layer", None) not in (WALL_LAYER, BASE_LAYER):
            continue
        ori, coord, a, b, _length = axis
        segs.append((e, ori == "H", coord, a, b))

    def _overlap(a0: float, a1: float, b0: float, b1: float) -> float:
        return max(0.0, min(a1, b1) - max(a0, b0))

    doors: list[dict[str, Any]] = []
    seen: set[tuple] = set()
    for i, (ax0, ax1, ay0, ay1) in enumerate(caps):
        acx, acy = (ax0 + ax1) * 0.5, (ay0 + ay1) * 0.5
        for bx0, bx1, by0, by1 in caps[i + 1 :]:
            bcx, bcy = (bx0 + bx1) * 0.5, (by0 + by1) * 0.5
            dx, dy = abs(acx - bcx), abs(acy - bcy)
            if dx >= dy and 1000.0 <= dx <= 1400.0 and dy <= 40.0:
                is_h, across = True, (acy + bcy) * 0.5
                a0, a1 = min(ax0, bx0), max(ax1, bx1)
            elif dy > dx and 1000.0 <= dy <= 1400.0 and dx <= 40.0:
                is_h, across = False, (acx + bcx) * 0.5
                a0, a1 = min(ay0, by0), max(ay1, by1)
            else:
                continue
            key = (is_h, round(across / 20.0), round(a0 / 30.0))
            if key in seen:
                continue
            leaf_span = 0.0
            for _ent, iv, coord, s0, s1 in segs:
                if iv != is_h or abs(coord - across) > 25.0:
                    continue
                if not (800.0 <= s1 - s0 <= 1700.0):
                    continue
                leaf_span = max(leaf_span, _overlap(s0, s1, a0, a1))
            opening = a1 - a0
            if leaf_span < opening * 0.7:
                continue
            pos: list[float] = []
            neg: list[float] = []
            for _ent, iv, coord, s0, s1 in segs:
                if iv != is_h:
                    continue
                delta = coord - across
                if not (40.0 <= abs(delta) <= 120.0):
                    continue
                # 한쪽 면이 이미 끊겨 있어도, 남은 면이 개구를 400mm 이상 덮으면 문이다.
                if _overlap(s0, s1, a0, a1) < 400.0:
                    continue
                (pos if delta > 0.0 else neg).append(coord)
            if not pos or not neg:
                continue
            seen.add(key)
            doors.append(
                {
                    "is_h": is_h,
                    "across": across,
                    "a0": a0,
                    "a1": a1,
                    "faces": (sum(neg) / len(neg), sum(pos) / len(pos)),
                }
            )
    if not doors:
        return (0, 0)

    def _to_base(ent) -> None:
        ent.dxf.layer = BASE_LAYER
        try:
            ent.dxf.color = BASE_COLOR
        except Exception:  # noqa: BLE001
            pass

    def _to_wall(ent) -> None:
        ent.dxf.layer = WALL_LAYER
        try:
            ent.dxf.color = WALL_COLOR
        except Exception:  # noqa: BLE001
            pass

    n_demote = 0
    n_promote = 0
    demoted: set[int] = set()
    split_add: list[tuple[bool, float, float, float]] = []
    for ent, is_h, coord, s0, s1 in segs:
        if getattr(ent.dxf, "layer", None) != WALL_LAYER or id(ent) in demoted:
            continue
        length = s1 - s0
        for door in doors:
            if door["is_h"] != is_h:
                continue
            opening = door["a1"] - door["a0"]
            cover = _overlap(s0, s1, door["a0"], door["a1"])
            on_leaf = abs(coord - door["across"]) <= 25.0 and length <= opening + 80.0
            on_face = any(abs(coord - face) <= 18.0 for face in door["faces"])
            if on_leaf and cover >= length * 0.7:
                _to_base(ent)
                demoted.add(id(ent))
                n_demote += 1
                break
            if not on_face or cover < 250.0:
                continue
            outside: list[tuple[float, float]] = []
            if s0 <= door["a0"] - 80.0:
                outside.append((s0, door["a0"]))
            if s1 >= door["a1"] + 80.0:
                outside.append((door["a1"], s1))
            if not outside:
                _to_base(ent)
            else:
                msp.delete_entity(ent)
                for p0, p1 in outside:
                    if p1 - p0 >= 80.0:
                        split_add.append((is_h, coord, p0, p1))
            demoted.add(id(ent))
            n_demote += 1
            break

    def _covered(is_h: bool, coord: float, p0: float, p1: float) -> bool:
        span = p1 - p0
        cover = 0.0
        for ent, iv, oo, b0, b1 in segs:
            if iv != is_h or abs(oo - coord) > 18.0 or id(ent) in demoted:
                continue
            if getattr(ent.dxf, "layer", None) != WALL_LAYER:
                continue
            cover += _overlap(p0, p1, b0, b1)
        return cover >= span * 0.75

    for is_h, coord, p0, p1 in split_add:
        if _covered(is_h, coord, p0, p1):
            continue
        if is_h:
            msp.add_line((p0, coord), (p1, coord), dxfattribs={"layer": WALL_LAYER, "color": WALL_COLOR})
        else:
            msp.add_line((coord, p0), (coord, p1), dxfattribs={"layer": WALL_LAYER, "color": WALL_COLOR})

    for ent, is_h, coord, s0, s1 in segs:
        if id(ent) in demoted or getattr(ent.dxf, "layer", None) != BASE_LAYER:
            continue
        length = s1 - s0
        if not (180.0 <= length <= 900.0):
            continue
        hit = False
        for door in doors:
            if door["is_h"] != is_h:
                continue
            if not any(abs(coord - face) <= 18.0 for face in door["faces"]):
                continue
            if _overlap(s0, s1, door["a0"], door["a1"]) > 80.0:
                continue
            if min(abs(s1 - door["a0"]), abs(s0 - door["a1"])) > 45.0:
                continue
            mate = False
            for _o, ov, oo, b0, b1 in segs:
                if ov != is_h:
                    continue
                gap = abs(oo - coord)
                if 70.0 <= gap <= 250.0 and _overlap(s0, s1, b0, b1) >= 180.0:
                    mate = True
                    break
            if mate:
                hit = True
                break
        if not hit:
            continue
        _to_wall(ent)
        n_promote += 1

    closed: set[int] = set()
    for door in doors:
        f0, f1 = min(door["faces"]), max(door["faces"])
        thick = f1 - f0
        for edge in (door["a0"], door["a1"]):
            for ent, seg_h, ortho, s0, s1 in segs:
                if seg_h == door["is_h"] or id(ent) in closed or id(ent) in demoted:
                    continue
                if ent.dxftype() != "LINE":
                    continue
                if abs(ortho - edge) > 25.0:
                    continue
                if s0 < f0 - 30.0 or s1 > f1 + 30.0:
                    continue
                if (s1 - s0) < thick * 0.6:
                    continue
                if getattr(ent.dxf, "layer", None) != BASE_LAYER:
                    continue
                _to_wall(ent)
                closed.add(id(ent))
                n_promote += 1
    return (n_demote, n_promote)


def demote_closet_bay_ends(msp) -> int:
    """옷장 칸의 끝선은 벽이 아니다.

    402호 화살표처럼, 약 1.75m 선반 두 개의 끝만 짧은 빨간 선이다.
    같은 칸의 나머지 윤곽은 회색이고, 80–220mm 벽 두께의 짝도 없다.
    """
    segs: list[tuple[str, float, float, float, float, str, Any]] = []
    columns: list[tuple[str, float, float, float]] = []
    for e in msp:
        layer = getattr(e.dxf, "layer", None)
        if layer not in (WALL_LAYER, BASE_LAYER):
            continue
        if e.dxftype() == "LWPOLYLINE" and e.closed:
            try:
                pts = [(float(p[0]), float(p[1])) for p in e.get_points("xy")]
            except Exception:  # noqa: BLE001
                pts = []
            if 4 <= len(pts) <= 6:
                xs = [p[0] for p in pts]
                ys = [p[1] for p in pts]
                x0, x1 = min(xs), max(xs)
                y0, y1 = min(ys), max(ys)
                short, long = min(x1 - x0, y1 - y0), max(x1 - x0, y1 - y0)
                if 350.0 <= short <= 750.0 and 500.0 <= long <= 850.0 and short / long >= 0.55:
                    columns.append(("H", y0, x0, x1))
                    columns.append(("H", y1, x0, x1))
                    columns.append(("V", x0, y0, y1))
                    columns.append(("V", x1, y0, y1))
        axis = _axis_line(e) if e.dxftype() == "LINE" else None
        if axis is None and e.dxftype() == "LWPOLYLINE":
            try:
                pts = [(float(p[0]), float(p[1])) for p in e.get_points("xy")]
            except Exception:  # noqa: BLE001
                pts = []
            spans = list(zip(pts, pts[1:] + (pts[:1] if e.closed else [])))
            for (x0, y0), (x1, y1) in spans:
                dx, dy = abs(x1 - x0), abs(y1 - y0)
                length = math.hypot(x1 - x0, y1 - y0)
                if length < 40.0:
                    continue
                tol = max(20.0, 0.12 * length)
                if dy <= tol and dx >= dy:
                    segs.append(("H", (y0 + y1) * 0.5, min(x0, x1), max(x0, x1), length, layer, e))
                elif dx <= tol and dy >= dx:
                    segs.append(("V", (x0 + x1) * 0.5, min(y0, y1), max(y0, y1), length, layer, e))
            continue
        if axis is None:
            continue
        ori, coord, a, b, length = axis
        segs.append((ori, coord, a, b, length, layer, e))

    def _on_column(ori: str, coord: float, a: float, b: float) -> bool:
        for cori, ccoord, ca, cb in columns:
            if cori != ori or abs(ccoord - coord) > 30.0:
                continue
            if min(b, cb) - max(a, ca) >= (b - a) * 0.8:
                return True
        return False

    n = 0
    seen: set[int] = set()
    for ori, coord, a, b, length, layer, ent in segs:
        if layer != WALL_LAYER or not (480.0 <= length <= 780.0):
            continue
        if id(ent) in seen or _on_column(ori, coord, a, b):
            continue
        duplicated = any(
            s_ori == ori
            and s_layer == BASE_LAYER
            and abs(s_coord - coord) <= 20.0
            and min(b, s_b) - max(a, s_a) >= length * 0.75
            for s_ori, s_coord, s_a, s_b, _s_len, s_layer, _s_ent in segs
        )
        if not duplicated:
            continue
        def _has_shelf(end: float) -> bool:
            for s_ori, s_coord, s_a, s_b, s_len, s_layer, _s_ent in segs:
                if s_layer != BASE_LAYER or s_ori == ori or not (1400.0 <= s_len <= 2200.0):
                    continue
                if abs(s_coord - end) > 40.0:
                    continue
                if not (s_a - 40.0 <= coord <= s_b + 40.0):
                    continue
                toward_min = coord - s_a
                toward_max = s_b - coord
                if toward_min >= 1200.0 and toward_max <= 400.0:
                    return True
                if toward_max >= 1200.0 and toward_min <= 400.0:
                    return True
            return False

        # 위·아래 칸은 바깥 끝이 실 경계에 닿아 선반이 한쪽에만 있다.
        if not (_has_shelf(a) or _has_shelf(b)):
            continue
        mate = any(
            s_ori == ori
            and s_layer == WALL_LAYER
            and 80.0 <= abs(s_coord - coord) <= 220.0
            and min(b, s_b) - max(a, s_a) >= length * 0.6
            for s_ori, s_coord, s_a, s_b, _s_len, s_layer, _s_ent in segs
        )
        if mate:
            continue
        seen.add(id(ent))
        msp.delete_entity(ent)
        n += 1
    return n


def promote_zigzag_door_sides(msp) -> tuple[int, int]:
    """지그재그 X 문은 내리고, 문 끝에 닿은 양옆 이중벽은 올린다.

    410호 화살표처럼 세로 문(두께 80–180mm, 높이 650–1100mm)이
    한 폴리선으로 그려져 있으면 그 폴리선은 문이다.
    문 끝에서 좌우로 이어지고 대각선이 없는 이중선만 벽으로 둔다.
    """
    doors: list[tuple[float, float, float, float, bool, Any]] = []
    for e in msp:
        if e.dxftype() != "LWPOLYLINE" or e.closed:
            continue
        if getattr(e.dxf, "layer", None) not in (WALL_LAYER, BASE_LAYER):
            continue
        try:
            pts = [(float(p[0]), float(p[1])) for p in e.get_points("xy")]
        except Exception:  # noqa: BLE001
            continue
        if len(pts) != 5:
            continue
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        x0, x1 = min(xs), max(xs)
        y0, y1 = min(ys), max(ys)
        thick, span = min(x1 - x0, y1 - y0), max(x1 - x0, y1 - y0)
        if not (80.0 <= thick <= 180.0 and 650.0 <= span <= 1100.0):
            continue
        n_diag = 0
        n_face = 0
        for i in range(4):
            dx = abs(pts[i + 1][0] - pts[i][0])
            dy = abs(pts[i + 1][1] - pts[i][1])
            if dx > 40.0 and dy > 400.0:
                n_diag += 1
            elif (dx <= 25.0 and dy >= 600.0) or (dy <= 25.0 and dx >= 600.0):
                n_face += 1
        if n_diag < 2 or n_face < 2:
            continue
        doors.append((x0, x1, y0, y1, (y1 - y0) >= (x1 - x0), e))
    if not doors:
        return (0, 0)

    diags: list[tuple[float, float, float, float]] = []
    for e in msp:
        if e.dxftype() == "LINE":
            spans = [(
                (float(e.dxf.start.x), float(e.dxf.start.y)),
                (float(e.dxf.end.x), float(e.dxf.end.y)),
            )]
        elif e.dxftype() == "LWPOLYLINE":
            try:
                pts = [(float(p[0]), float(p[1])) for p in e.get_points("xy")]
            except Exception:  # noqa: BLE001
                continue
            spans = list(zip(pts, pts[1:]))
        else:
            continue
        for (ax, ay), (bx, by) in spans:
            if abs(bx - ax) < 40.0 or abs(by - ay) < 40.0:
                continue
            diags.append((min(ax, bx), max(ax, bx), min(ay, by), max(ay, by)))

    lines: list[tuple[str, float, float, float, Any]] = []
    for e in msp:
        if getattr(e.dxf, "layer", None) != BASE_LAYER:
            continue
        axis = _axis_line(e)
        if axis is None:
            continue
        ori, coord, a, b, length = axis
        if not (400.0 <= length <= 4000.0):
            continue
        lines.append((ori, coord, a, b, e))

    spans: list[tuple[str, float, float, float]] = []
    for x0, x1, y0, y1, vertical, _ent in doors:
        ends = (y0, y1) if vertical else (x0, x1)
        f0, f1 = (x0, x1) if vertical else (y0, y1)
        cross = "H" if vertical else "V"
        cands = []
        for ori, coord, a, b, ent in lines:
            if ori != cross:
                continue
            near = any(abs(coord - end) <= 40.0 or 70.0 <= abs(coord - end) <= 220.0 for end in ends)
            if not near:
                continue
            if min(abs(a - f0), abs(a - f1), abs(b - f0), abs(b - f1)) > 80.0:
                continue
            cands.append((coord, a, b, ent))
        used: set[int] = set()
        for i, a in enumerate(cands):
            if i in used:
                continue
            for j in range(i + 1, len(cands)):
                if j in used:
                    continue
                b = cands[j]
                if not (70.0 <= abs(a[0] - b[0]) <= 220.0):
                    continue
                ov0, ov1 = max(a[1], b[1]), min(a[2], b[2])
                if ov1 - ov0 < 400.0:
                    continue
                lo, hi = min(a[0], b[0]), max(a[0], b[0])
                crossed = False
                for dx0, dx1, dy0, dy1 in diags:
                    if cross == "H":
                        along = min(dx1, ov1) - max(dx0, ov0)
                        inside = dy0 >= lo - 30.0 and dy1 <= hi + 30.0
                    else:
                        along = min(dy1, ov1) - max(dy0, ov0)
                        inside = dx0 >= lo - 30.0 and dx1 <= hi + 30.0
                    if inside and along >= 400.0:
                        crossed = True
                        break
                if crossed:
                    continue
                used.add(i)
                used.add(j)
                spans.append((cross, a[0], ov0, ov1))
                spans.append((cross, b[0], ov0, ov1))
                break

    def _to_base(ent) -> bool:
        if getattr(ent.dxf, "layer", None) != WALL_LAYER:
            return False
        ent.dxf.layer = BASE_LAYER
        try:
            ent.dxf.color = BASE_COLOR
        except Exception:  # noqa: BLE001
            pass
        return True

    def _to_wall(ent) -> bool:
        if getattr(ent.dxf, "layer", None) != BASE_LAYER:
            return False
        ent.dxf.layer = WALL_LAYER
        try:
            ent.dxf.color = WALL_COLOR
        except Exception:  # noqa: BLE001
            pass
        return True

    n_demote = 0
    for _x0, _x1, _y0, _y1, _vertical, ent in doors:
        if _to_base(ent):
            n_demote += 1
    n_promote = 0
    seen: set[int] = set()
    for ori, coord, a0, a1 in spans:
        for e_ori, e_coord, ea, eb, ent in lines:
            if id(ent) in seen or e_ori != ori or abs(e_coord - coord) > 25.0:
                continue
            if min(eb, a1) - max(ea, a0) < (eb - ea) * 0.8:
                continue
            if _to_wall(ent):
                seen.add(id(ent))
                n_promote += 1
    return (n_demote, n_promote)


def promote_outside_band_face(msp) -> int:
    """얕은 띠에서 아래 벽과 나란한 변만 벽이다.

    410호 R.E.F와 옷장 사이처럼, 높이 450–600mm·길이 2m 이상인 회색 띠의
    아래 변이 바로 아래 빨간 벽과 80–180mm로 나란하면 그 변만 올린다.
    윗변과 짧은 끝, 안의 X 표식은 벽이 아니다.
    """
    walls: list[tuple[str, float, float, float]] = []
    for e in msp:
        if getattr(e.dxf, "layer", None) != WALL_LAYER:
            continue
        axis = _axis_line(e)
        if axis is None:
            continue
        ori, coord, a, b, length = axis
        if length < 1500.0:
            continue
        walls.append((ori, coord, a, b))

    n = 0
    for e in list(msp):
        if e.dxftype() != "LWPOLYLINE" or not e.closed:
            continue
        if getattr(e.dxf, "layer", None) != BASE_LAYER:
            continue
        try:
            pts = [(float(p[0]), float(p[1])) for p in e.get_points("xy")]
        except Exception:  # noqa: BLE001
            continue
        if len(pts) != 4:
            continue
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        x0, x1 = min(xs), max(xs)
        y0, y1 = min(ys), max(ys)
        width, height = x1 - x0, y1 - y0
        if width < height:
            continue
        if not (450.0 <= height <= 600.0 and 2000.0 <= width <= 4500.0):
            continue
        near = None
        for ori, coord, a, b in walls:
            if ori != "H" or not (y0 - 180.0 <= coord <= y0 - 80.0):
                continue
            if min(x1, b) - max(x0, a) < width * 0.7:
                continue
            near = (coord, a, b)
            break
        if near is None:
            continue
        msp.delete_entity(e)
        msp.add_line((x0, y1), (x1, y1), dxfattribs={"layer": BASE_LAYER, "color": BASE_COLOR})
        msp.add_line((x1, y1), (x1, y0), dxfattribs={"layer": BASE_LAYER, "color": BASE_COLOR})
        msp.add_line((x0, y1), (x0, y0), dxfattribs={"layer": BASE_LAYER, "color": BASE_COLOR})
        msp.add_line((x0, y0), (x1, y0), dxfattribs={"layer": WALL_LAYER, "color": WALL_COLOR})
        n += 1
    return n


def correct_tall_opening_doors(msp) -> tuple[int, int]:
    """세로로 긴 X는 문이다. 문은 내리고 위·아래 벽면은 올린다.

    410호 왼쪽은 두께 200mm·높이 1530mm, 오른쪽은 두께 150mm·높이 2560mm.
    개구 안의 선은 BASE로 두고, 개구 밖에서 같은 면으로 이어진 벽만 WALL로 둔다.
    """
    pieces: list[tuple[float, float, float, float]] = []
    for e in msp:
        if getattr(e.dxf, "layer", None) not in (WALL_LAYER, BASE_LAYER):
            continue
        if e.dxftype() == "LINE":
            spans = [(
                (float(e.dxf.start.x), float(e.dxf.start.y)),
                (float(e.dxf.end.x), float(e.dxf.end.y)),
            )]
        elif e.dxftype() == "LWPOLYLINE":
            try:
                pts = [(float(p[0]), float(p[1])) for p in e.get_points("xy")]
            except Exception:  # noqa: BLE001
                continue
            spans = list(zip(pts, pts[1:]))
        else:
            continue
        for (x0, y0), (x1, y1) in spans:
            dx, dy = abs(x1 - x0), abs(y1 - y0)
            if dx < 60.0 or dy < 400.0:
                continue
            length = math.hypot(dx, dy)
            if not (500.0 <= length <= 4000.0):
                continue
            pieces.append((min(x0, x1), max(x0, x1), min(y0, y1), max(y0, y1)))

    used = [False] * len(pieces)
    doors: list[tuple[float, float, float, float]] = []
    for i, a in enumerate(pieces):
        if used[i]:
            continue
        group = [i]
        used[i] = True
        for j in range(i + 1, len(pieces)):
            if used[j]:
                continue
            b = pieces[j]
            if all(abs(a[k] - b[k]) <= 50.0 for k in range(4)):
                used[j] = True
                group.append(j)
        if len(group) < 2:
            continue
        width, height = a[1] - a[0], a[3] - a[2]
        thick, span = min(width, height), max(width, height)
        if not (100.0 <= thick <= 250.0 and 1200.0 <= span <= 3200.0):
            continue
        if thick / span > 0.4:
            continue
        if height < width:
            continue
        doors.append((a[0], a[1], a[2], a[3]))
    if not doors:
        return (0, 0)

    def _to_base(ent) -> bool:
        if getattr(ent.dxf, "layer", None) != WALL_LAYER:
            return False
        ent.dxf.layer = BASE_LAYER
        try:
            ent.dxf.color = BASE_COLOR
        except Exception:  # noqa: BLE001
            pass
        return True

    def _to_wall(ent) -> bool:
        if getattr(ent.dxf, "layer", None) != BASE_LAYER:
            return False
        ent.dxf.layer = WALL_LAYER
        try:
            ent.dxf.color = WALL_COLOR
        except Exception:  # noqa: BLE001
            pass
        return True

    n_demote = 0
    seen_demote: set[int] = set()
    for e in msp:
        if id(e) in seen_demote or getattr(e.dxf, "layer", None) != WALL_LAYER:
            continue
        axis = _axis_line(e)
        if axis is None or axis[0] != "V":
            continue
        _ori, x, y0, y1, length = axis
        for fx0, fx1, fy0, fy1 in doors:
            if not (fx0 + 40.0 <= x <= fx1 - 40.0):
                continue
            if min(y1, fy1) - max(y0, fy0) < 400.0:
                continue
            if _to_base(e):
                seen_demote.add(id(e))
                n_demote += 1
            break

    n_promote = 0
    for e in msp:
        if e.dxftype() != "LWPOLYLINE" or e.closed:
            continue
        if getattr(e.dxf, "layer", None) != BASE_LAYER:
            continue
        try:
            pts = [(float(p[0]), float(p[1])) for p in e.get_points("xy")]
        except Exception:  # noqa: BLE001
            continue
        if len(pts) != 6:
            continue
        verts: list[tuple[float, float, float]] = []
        for i in range(5):
            dx = abs(pts[i + 1][0] - pts[i][0])
            dy = abs(pts[i + 1][1] - pts[i][1])
            if dx <= 25.0 and 500.0 <= dy <= 2200.0:
                verts.append(
                    (
                        (pts[i][0] + pts[i + 1][0]) / 2.0,
                        min(pts[i][1], pts[i + 1][1]),
                        max(pts[i][1], pts[i + 1][1]),
                    )
                )
        if len(verts) < 2:
            continue
        hit = False
        for fx0, fx1, fy0, fy1 in doors:
            on_face = [
                v for v in verts if abs(v[0] - fx0) <= 40.0 or abs(v[0] - fx1) <= 40.0
            ]
            if len(on_face) < 2:
                continue
            xs = {round(v[0]) for v in on_face}
            if len(xs) < 2:
                continue
            down = all(abs(v[2] - fy0) <= 40.0 and v[1] <= fy0 - 400.0 for v in on_face)
            up = all(abs(v[1] - fy1) <= 40.0 and v[2] >= fy1 + 400.0 for v in on_face)
            if down or up:
                hit = True
                break
        if hit and _to_wall(e):
            n_promote += 1
    return (n_demote, n_promote)


def correct_louver_doors(msp) -> tuple[int, int]:
    """살대가 모인 문은 내리고, 개구 양옆의 벽면은 올린다.

    410호 오른쪽처럼 15–70mm 간격의 평행선이 네 줄 이상, 두께 100–320mm,
    길이 0.7–2.2m로 모이면 문이다. 가로로 놓인 같은 형태도 같다.
    살대는 BASE로 두고, 개구 끝에서 밖으로 이어진 양면만 WALL로 둔다.
    """
    segs: list[tuple[str, float, float, float, float, str, Any]] = []
    for e in msp:
        layer = getattr(e.dxf, "layer", None)
        if layer not in (WALL_LAYER, BASE_LAYER):
            continue
        axis = _axis_line(e)
        if axis is None:
            continue
        ori, coord, a, b, length = axis
        if length < 200.0:
            continue
        segs.append((ori, coord, a, b, length, layer, e))

    packs: list[tuple[str, float, float, float, float, list[Any]]] = []
    for ori in ("V", "H"):
        items = [s for s in segs if s[0] == ori and 700.0 <= s[4] <= 2200.0]
        items.sort(key=lambda s: s[1])
        used = [False] * len(items)
        for i, head in enumerate(items):
            if used[i]:
                continue
            group = [i]
            for j in range(i + 1, len(items)):
                other = items[j]
                if other[1] - head[1] > 350.0:
                    break
                if abs(other[2] - head[2]) > 60.0 or abs(other[3] - head[3]) > 60.0:
                    continue
                if abs(other[4] - head[4]) > 300.0:
                    continue
                if min(abs(other[1] - items[k][1]) for k in group) > 80.0:
                    continue
                group.append(j)
            if len(group) < 4:
                continue
            coords = sorted(items[k][1] for k in group)
            if not (100.0 <= coords[-1] - coords[0] <= 320.0):
                continue
            gaps = [
                coords[t + 1] - coords[t]
                for t in range(len(coords) - 1)
                if coords[t + 1] - coords[t] > 8.0
            ]
            if sum(1 for gap in gaps if 15.0 <= gap <= 70.0) < 3:
                continue
            if sum(1 for k in group if items[k][5] == BASE_LAYER) < len(group) * 0.6:
                continue
            for k in group:
                used[k] = True
            packs.append(
                (
                    ori,
                    coords[0],
                    coords[-1],
                    min(items[k][2] for k in group),
                    max(items[k][3] for k in group),
                    [items[k][6] for k in group],
                )
            )
    if not packs:
        return (0, 0)

    def _to_base(ent) -> bool:
        if getattr(ent.dxf, "layer", None) != WALL_LAYER:
            return False
        ent.dxf.layer = BASE_LAYER
        try:
            ent.dxf.color = BASE_COLOR
        except Exception:  # noqa: BLE001
            pass
        return True

    def _to_wall(ent) -> bool:
        if getattr(ent.dxf, "layer", None) != BASE_LAYER:
            return False
        ent.dxf.layer = WALL_LAYER
        try:
            ent.dxf.color = WALL_COLOR
        except Exception:  # noqa: BLE001
            pass
        return True

    n_demote = 0
    for _ori, _c0, _c1, _s0, _s1, members in packs:
        for ent in members:
            if _to_base(ent):
                n_demote += 1

    n_promote = 0
    seen: set[int] = set()
    for ori, c0, c1, s0, s1, _members in packs:
        for e_ori, coord, a, b, length, layer, ent in segs:
            if e_ori != ori or layer != BASE_LAYER or id(ent) in seen:
                continue
            if not (400.0 <= length <= 2500.0):
                continue
            if not (c0 - 80.0 <= coord <= c1 + 80.0):
                continue
            if min(b, s1) - max(a, s0) > 80.0:
                continue
            away = (abs(b - s0) <= 80.0 and a <= s0 - 400.0) or (
                abs(a - s1) <= 80.0 and b >= s1 + 400.0
            )
            if not away:
                continue
            mate = any(
                m_ori == ori
                and 80.0 <= abs(m_coord - coord) <= 280.0
                and min(b, m_b) - max(a, m_a) >= 400.0
                for m_ori, m_coord, m_a, m_b, _m_len, _m_layer, _m_ent in segs
            )
            if not mate:
                continue
            if _to_wall(ent):
                seen.add(id(ent))
                n_promote += 1
    return (n_demote, n_promote)


def correct_header_slat_doors(msp) -> tuple[int, int]:
    """객실 위쪽 가로 살대 문을 내리고, 개구 양옆 벽면은 올린다.

    408호·409호 위처럼 길이 1.7–2.1m 살대 사이에 짧은 살대가 끼면 문이다.
    살대 간격이 120mm라 15–70mm 규칙에는 안 걸린다.     살대는 BASE로 두고,
    개구 끝에서 밖으로 이어진 150–260mm 이중면만 WALL로 둔다.
    한쪽 면이 이미 벽이면 맞은편 같은 높이의 회색 면도 벽이다.
    """
    segs: list[tuple[str, float, float, float, float, str, Any]] = []
    for e in msp:
        layer = getattr(e.dxf, "layer", None)
        if layer not in (WALL_LAYER, BASE_LAYER):
            continue
        axis = _axis_line(e)
        if axis is None:
            continue
        ori, coord, a, b, length = axis
        if length < 200.0:
            continue
        segs.append((ori, coord, a, b, length, layer, e))

    packs: list[tuple[str, float, float, float, float, list[Any]]] = []
    for ori in ("V", "H"):
        items = [s for s in segs if s[0] == ori and 1750.0 <= s[4] <= 2100.0]
        used = [False] * len(items)
        for i, head in enumerate(items):
            if used[i]:
                continue
            group = [i]
            for j, other in enumerate(items):
                if j == i or used[j]:
                    continue
                if abs(other[2] - head[2]) > 80.0 or abs(other[3] - head[3]) > 80.0:
                    continue
                if abs(other[1] - head[1]) > 280.0:
                    continue
                group.append(j)
            coords = sorted({round(items[k][1], 1) for k in group})
            if len(coords) < 3:
                continue
            thick = coords[-1] - coords[0]
            if not (180.0 <= thick <= 280.0):
                continue
            s0 = min(items[k][2] for k in group)
            s1 = max(items[k][3] for k in group)
            shorts = [
                s
                for s in segs
                if s[0] == ori
                and 700.0 <= s[4] <= 1400.0
                and coords[0] + 8.0 <= s[1] <= coords[-1] - 8.0
                and s[2] >= s0 - 40.0
                and s[3] <= s1 + 40.0
            ]
            if len(shorts) < 3:
                continue
            for k in group:
                used[k] = True
            packs.append((ori, coords[0], coords[-1], s0, s1, [items[k][6] for k in group]))
    if not packs:
        return (0, 0)

    def _to_base(ent) -> bool:
        if getattr(ent.dxf, "layer", None) != WALL_LAYER:
            return False
        ent.dxf.layer = BASE_LAYER
        try:
            ent.dxf.color = BASE_COLOR
        except Exception:  # noqa: BLE001
            pass
        return True

    def _to_wall(ent) -> bool:
        if getattr(ent.dxf, "layer", None) != BASE_LAYER:
            return False
        ent.dxf.layer = WALL_LAYER
        try:
            ent.dxf.color = WALL_COLOR
        except Exception:  # noqa: BLE001
            pass
        return True

    n_demote = 0
    demoted: set[int] = set()
    for ori, c0, c1, s0, s1, members in packs:
        for ent in members:
            if id(ent) in demoted:
                continue
            if _to_base(ent):
                demoted.add(id(ent))
                n_demote += 1
        for e_ori, coord, a, b, length, layer, ent in segs:
            if e_ori != ori or id(ent) in demoted:
                continue
            if not (c0 + 8.0 <= coord <= c1 - 8.0):
                continue
            if a < s0 - 40.0 or b > s1 + 40.0:
                continue
            if length > (s1 - s0) - 300.0:
                continue
            if _to_base(ent):
                demoted.add(id(ent))
                n_demote += 1

    n_promote = 0
    seen: set[int] = set()
    for ori, c0, c1, s0, s1, _members in packs:
        for e_ori, coord, a, b, length, layer, ent in segs:
            if e_ori != ori or layer != BASE_LAYER or id(ent) in seen:
                continue
            if not (500.0 <= length <= 2600.0):
                continue
            if not (c0 - 120.0 <= coord <= c1 + 120.0):
                continue
            if min(b, s1) - max(a, s0) > 80.0:
                continue
            away = (abs(b - s0) <= 80.0 and a <= s0 - 400.0) or (
                abs(a - s1) <= 80.0 and b >= s1 + 400.0
            )
            if not away:
                continue
            mate = any(
                m_ori == ori
                and m_layer in (WALL_LAYER, BASE_LAYER)
                and 140.0 <= abs(m_coord - coord) <= 260.0
                and min(b, m_b) - max(a, m_a) >= 600.0
                and (
                    (abs(m_b - s0) <= 80.0 and m_a <= s0 - 400.0)
                    or (abs(m_a - s1) <= 80.0 and m_b >= s1 + 400.0)
                )
                for m_ori, m_coord, m_a, m_b, _m_len, m_layer, _m_ent in segs
            )
            if not mate:
                continue
            if _to_wall(ent):
                seen.add(id(ent))
                n_promote += 1

    # 한쪽이 이미 벽인 같은 면. 맞은편 회색 면도 벽이다.
    # 직원실 상단처럼 면 간격이 80mm이고 면이 2.6m를 넘겨도 같다.
    for ori, c0, c1, s0, s1, _members in packs:
        for e_ori, coord, a, b, length, layer, ent in segs:
            if e_ori != ori or layer != BASE_LAYER or id(ent) in seen:
                continue
            if not (400.0 <= length <= 6000.0):
                continue
            if not (c0 - 40.0 <= coord <= c1 + 40.0):
                continue
            if min(b, s1) - max(a, s0) > 80.0:
                continue
            away_right = abs(a - s1) <= 80.0 and b >= s1 + 400.0
            away_left = abs(b - s0) <= 80.0 and a <= s0 - 400.0
            if not (away_right or away_left):
                continue
            mirrored = any(
                m_ori == ori
                and m_layer == WALL_LAYER
                and abs(m_coord - coord) <= 30.0
                and min(m_b, s1) - max(m_a, s0) <= 80.0
                and (
                    (away_right and abs(m_b - s0) <= 120.0 and m_a < s0)
                    or (away_left and abs(m_a - s1) <= 120.0 and m_b > s1)
                )
                for m_ori, m_coord, m_a, m_b, _m_len, m_layer, _m_ent in segs
            )
            if not mirrored:
                continue
            if _to_wall(ent):
                seen.add(id(ent))
                n_promote += 1
    return (n_demote, n_promote)


def promote_swing_opposite_walls(msp) -> int:
    """문 스윙의 반대쪽, 힌지 너머 짧은 벽을 WALL로 올린다.

    호가 열리는 끝(문짝 끝)의 작은 칸은 벽이 아니므로 BASE로 되돌린다.
    힌지를 지나 스윙 반대 방향으로 뻗은 짧은 문 막이만 올린다.
    문짝(호 반지름 길이)은 올리지 않는다.
    """
    swings: list[tuple[float, float, float, float]] = []
    for entity in msp:
        if entity.dxftype() != "ARC":
            continue
        try:
            radius = float(entity.dxf.radius)
            cx = float(entity.dxf.center.x)
            cy = float(entity.dxf.center.y)
            start_angle = float(entity.dxf.start_angle)
        except Exception:  # noqa: BLE001
            continue
        sweep = (float(entity.dxf.end_angle) - start_angle) % 360.0
        if not (400.0 <= radius <= 1400.0) or not (70.0 <= sweep <= 110.0):
            continue
        rad = math.radians(start_angle)
        swings.append((cx + radius * math.cos(rad), cy + radius * math.sin(rad), cx, cy))

    if not swings:
        return 0

    def _pts(entity) -> list[tuple[float, float]]:
        kind = entity.dxftype()
        try:
            if kind == "LINE":
                return [
                    (float(entity.dxf.start.x), float(entity.dxf.start.y)),
                    (float(entity.dxf.end.x), float(entity.dxf.end.y)),
                ]
            if kind == "LWPOLYLINE":
                return [(float(p[0]), float(p[1])) for p in entity.get_points("xy")]
        except Exception:  # noqa: BLE001
            return []
        return []

    def _near(points: list[tuple[float, float]], x: float, y: float, tol: float, closed: bool) -> bool:
        if any(math.hypot(px - x, py - y) <= tol for px, py in points):
            return True
        edges = list(zip(points, points[1:]))
        if closed and len(points) >= 2:
            edges.append((points[-1], points[0]))
        for (x0, y0), (x1, y1) in edges:
            vx, vy = x1 - x0, y1 - y0
            length2 = vx * vx + vy * vy
            if length2 < 1.0:
                continue
            t = max(0.0, min(1.0, ((x - x0) * vx + (y - y0) * vy) / length2))
            if math.hypot(x - (x0 + t * vx), y - (y0 + t * vy)) <= tol:
                return True
        return False

    changed = 0
    seen: set[int] = set()
    for entity in msp:
        if id(entity) in seen:
            continue
        layer = getattr(entity.dxf, "layer", None)
        if layer not in (BASE_LAYER, WALL_LAYER):
            continue
        points = _pts(entity)
        if len(points) < 2:
            continue
        closed = bool(getattr(entity, "closed", False))
        xs = [p[0] for p in points]
        ys = [p[1] for p in points]
        span = max(max(xs) - min(xs), max(ys) - min(ys))
        if span < 40.0 or span > 520.0:
            continue
        for sx, sy, hx, hy in swings:
            opening = math.hypot(sx - hx, sy - hy)
            if opening < 200.0:
                continue
            ux, uy = (sx - hx) / opening, (sy - hy) / opening
            projections = [(px - hx) * ux + (py - hy) * uy for px, py in points]
            on_open_tip = _near(points, sx, sy, 25.0, closed) and not _near(points, hx, hy, 40.0, closed)
            if on_open_tip and layer == WALL_LAYER:
                if _paint_layer(entity, BASE_LAYER, BASE_COLOR):
                    changed += 1
                    seen.add(id(entity))
                break
            if layer != BASE_LAYER:
                continue
            if _near(points, sx, sy, 40.0, closed):
                continue
            if not _near(points, hx, hy, 35.0, closed):
                continue
            if max(projections) > 20.0 or min(projections) > -15.0:
                continue
            if _paint_layer(entity, WALL_LAYER, WALL_COLOR):
                changed += 1
                seen.add(id(entity))
            break
    return changed


def _promote_closed_door_flanks(msp, edges, x0: float, y0: float, x1: float, y1: float) -> int:
    """닫힘선 끝에 붙은 평행 이중선을 WALL로 올린다. 문짝은 두지 않는다."""
    swings: list[tuple[float, float, float, tuple]] = []
    for entity in msp:
        if entity.dxftype() != "ARC":
            continue
        try:
            radius = float(entity.dxf.radius)
            hx = float(entity.dxf.center.x)
            hy = float(entity.dxf.center.y)
            start_angle = float(entity.dxf.start_angle)
            end_angle = float(entity.dxf.end_angle)
        except Exception:  # noqa: BLE001
            continue
        sweep = (end_angle - start_angle) % 360.0
        if not (400.0 <= radius <= 1400.0) or not (70.0 <= sweep <= 110.0):
            continue
        dirs = []
        for angle in (start_angle, end_angle):
            rad = math.radians(angle)
            dirs.append((math.cos(rad), math.sin(rad)))
        swings.append((hx, hy, radius, tuple(dirs)))

    def _leaf(ax: float, ay: float, bx: float, by: float) -> bool:
        length = math.hypot(bx - ax, by - ay)
        if length < 80.0:
            return False
        ux, uy = (bx - ax) / length, (by - ay) / length
        mx, my = (ax + bx) / 2.0, (ay + by) / 2.0
        for hx, hy, radius, dirs in swings:
            if not (0.7 * radius <= length <= 1.3 * radius):
                continue
            if math.hypot(mx - hx, my - hy) > radius * 0.8:
                continue
            for dx, dy in dirs:
                if abs(ux * dx + uy * dy) < 0.95:
                    continue
                if abs((mx - hx) * dy - (my - hy) * dx) > 55.0:
                    continue
                along = (mx - hx) * dx + (my - hy) * dy
                if 0.2 * radius <= along <= 0.8 * radius:
                    return True
        return False

    clen = math.hypot(x1 - x0, y1 - y0) or 1.0
    cux, cuy = (x1 - x0) / clen, (y1 - y0) / clen
    c0, c1 = x0 * cux + y0 * cuy, x1 * cux + y1 * cuy
    if c1 < c0:
        c0, c1 = c1, c0
    ends = ((x0, y0), (x1, y1))
    promoted = 0
    seen: set[int] = set()

    def _paint(ent) -> None:
        nonlocal promoted
        if ent is None or ent.dxftype() != "LINE" or id(ent) in seen:
            return
        if getattr(ent.dxf, "layer", None) != BASE_LAYER:
            return
        if _paint_layer(ent, WALL_LAYER, WALL_COLOR):
            promoted += 1
            seen.add(id(ent))

    touching = []
    for ax, ay, bx, by, layer, ent in edges:
        if layer != BASE_LAYER or ent is None:
            continue
        if getattr(ent.dxf, "layer", None) != BASE_LAYER:
            continue
        length = math.hypot(bx - ax, by - ay)
        if not (80.0 <= length <= 2500.0) or _leaf(ax, ay, bx, by):
            continue
        if not any(min(math.hypot(ax - px, ay - py), math.hypot(bx - px, by - py)) <= 35.0 for px, py in ends):
            continue
        aux, auy = (bx - ax) / length, (by - ay) / length
        if abs(aux * cux + auy * cuy) < 0.98:
            continue
        along0, along1 = ax * cux + ay * cuy, bx * cux + by * cuy
        if along1 < along0:
            along0, along1 = along1, along0
        if min(along1, c1) - max(along0, c0) > 0.35 * clen:
            continue
        a0, a1 = ax * aux + ay * auy, bx * aux + by * auy
        if a1 < a0:
            a0, a1 = a1, a0
        touching.append((ax, ay, bx, by, ent, aux, auy, a0, a1))

    for ax, ay, bx, by, ent, aux, auy, a0, a1 in touching:
        partners = []
        for cx, cy, dx, dy, _layer, pent in edges:
            plen = math.hypot(dx - cx, dy - cy)
            if plen < 80.0 or _leaf(cx, cy, dx, dy):
                continue
            if abs(((dx - cx) / plen) * aux + ((dy - cy) / plen) * auy) < 0.98:
                continue
            gap = abs((cx - ax) * (-auy) + (cy - ay) * aux)
            if not (20.0 <= gap <= 250.0):
                continue
            p0, p1 = cx * aux + cy * auy, dx * aux + dy * auy
            if p1 < p0:
                p0, p1 = p1, p0
            if min(a1, p1) - max(a0, p0) < 80.0:
                continue
            partners.append((cx, cy, dx, dy, pent, plen))
        if not partners:
            continue
        _paint(ent)
        for cx, cy, dx, dy, pent, plen in partners:
            if plen > 2500.0 or pent is None:
                continue
            mid_d = min(
                math.hypot((cx + dx) / 2.0 - x0, (cy + dy) / 2.0 - y0),
                math.hypot((cx + dx) / 2.0 - x1, (cy + dy) / 2.0 - y1),
            )
            if mid_d <= clen + 400.0:
                _paint(pent)
    return promoted


def _promote_swing_continuation_doubles(
    msp,
    edges,
    x0: float,
    y0: float,
    x1: float,
    y1: float,
    hx: float,
    hy: float,
) -> int:
    """닫힌 문 밖으로 이어진 평행 이중선을 WALL로 올린다.

    개구를 가로지르는 맞은편 면은 문 밖 구간만 잇는다. 문짝은 두지 않는다.
    """
    clen = math.hypot(x1 - x0, y1 - y0) or 1.0
    cux, cuy = (x1 - x0) / clen, (y1 - y0) / clen
    cnx, cny = -cuy, cux
    c0, c1 = x0 * cux + y0 * cuy, x1 * cux + y1 * cuy
    if c1 < c0:
        c0, c1 = c1, c0
    hinge_off = (hx - x0) * cnx + (hy - y0) * cny

    def _covered(px0: float, py0: float, px1: float, py1: float) -> bool:
        for ax, ay, bx, by, layer, _ent in edges:
            if layer != WALL_LAYER:
                continue
            vx, vy = bx - ax, by - ay
            length2 = vx * vx + vy * vy
            if length2 < 1.0:
                continue
            def _on(px: float, py: float) -> bool:
                t = ((px - ax) * vx + (py - ay) * vy) / length2
                if t < -0.02 or t > 1.02:
                    return False
                return math.hypot(px - (ax + t * vx), py - (ay + t * vy)) <= 8.0
            if _on(px0, py0) and _on(px1, py1):
                return True
        return False

    rows = []
    for ax, ay, bx, by, layer, ent in edges:
        if layer != BASE_LAYER or ent is None:
            continue
        if getattr(ent.dxf, "layer", None) != BASE_LAYER:
            continue
        length = math.hypot(bx - ax, by - ay)
        if not (80.0 <= length <= 4000.0):
            continue
        ux, uy = (bx - ax) / length, (by - ay) / length
        if abs(ux * cux + uy * cuy) < 0.98:
            continue
        off = (ax - x0) * cnx + (ay - y0) * cny
        off_b = (bx - x0) * cnx + (by - y0) * cny
        if abs(off_b - off) > 20.0 or abs(off) > 400.0:
            continue
        if hinge_off * off < 0.0 and abs(off) > 40.0:
            continue
        near = False
        vx, vy = bx - ax, by - ay
        for px, py in ((x0, y0), (x1, y1)):
            t = max(0.0, min(1.0, ((px - ax) * vx + (py - ay) * vy) / (length * length)))
            if math.hypot(px - (ax + t * vx), py - (ay + t * vy)) <= 450.0:
                near = True
                break
        if not near:
            continue
        a0, a1 = ax * cux + ay * cuy, bx * cux + by * cuy
        if a1 < a0:
            a0, a1 = a1, a0
        rows.append((ax, ay, bx, by, ent, off, a0, a1, length))

    def _partner(off: float, a0: float, a1: float, skip: int) -> bool:
        for i, row in enumerate(rows):
            if i == skip:
                continue
            gap = abs(row[5] - off)
            if not (40.0 <= gap <= 360.0):
                continue
            if min(a1, row[7]) - max(a0, row[6]) >= 80.0:
                return True
        return False

    promoted = 0
    seen: set[int] = set()
    for i, (ax, ay, bx, by, ent, off, a0, a1, length) in enumerate(rows):
        if not _partner(off, a0, a1, i):
            continue
        overlap = min(a1, c1) - max(a0, c0)
        if overlap <= 40.0:
            if length > 1600.0 or id(ent) in seen or ent.dxftype() != "LINE":
                continue
            if _paint_layer(ent, WALL_LAYER, WALL_COLOR):
                promoted += 1
                seen.add(id(ent))
                edges.append((ax, ay, bx, by, WALL_LAYER, ent))
            continue
        raw0 = ax * cux + ay * cuy
        raw1 = bx * cux + by * cuy
        denom = raw1 - raw0
        if abs(denom) < 1.0:
            continue
        def _at(along: float) -> tuple[float, float]:
            t = (along - raw0) / denom
            return (ax + t * (bx - ax), ay + t * (by - ay))
        for lo, hi in ((a0, min(a1, c0)), (max(a0, c1), a1)):
            if not (200.0 <= hi - lo <= 1600.0):
                continue
            if not _partner(off, lo, hi, i):
                continue
            p0, p1 = _at(lo), _at(hi)
            if _covered(p0[0], p0[1], p1[0], p1[1]):
                continue
            line = msp.add_line(p0, p1, dxfattribs={"layer": WALL_LAYER, "color": WALL_COLOR})
            edges.append((p0[0], p0[1], p1[0], p1[1], WALL_LAYER, line))
            promoted += 1
    return promoted


def add_closed_door_wall_lines(msp) -> int:
    """문이 닫히는 안쪽 벽면을 개구 너비만큼 잇는다.

    스윙이 열리는 쪽의 반대 면 가운데, 힌지에서 가장 먼 벽면의 끊긴 구간을
    WALL 한 줄로 잇는다. 스윙 현과 문짝은 그대로 둔다.
    """
    # (x0, y0, x1, y1, layer, entity) — 문 맞은편 끝이 아직 BASE여도 개구로 본다.
    edges: list[tuple] = []
    for entity in msp:
        layer = getattr(entity.dxf, "layer", None)
        if layer not in (WALL_LAYER, BASE_LAYER):
            continue
        kind = entity.dxftype()
        try:
            if kind == "LINE":
                pairs = [(
                    (float(entity.dxf.start.x), float(entity.dxf.start.y)),
                    (float(entity.dxf.end.x), float(entity.dxf.end.y)),
                )]
            elif kind == "LWPOLYLINE":
                pts = [(float(p[0]), float(p[1])) for p in entity.get_points("xy")]
                pairs = list(zip(pts, pts[1:]))
                if entity.closed and len(pts) >= 2:
                    pairs.append((pts[-1], pts[0]))
            else:
                continue
        except Exception:  # noqa: BLE001
            continue
        for (x0, y0), (x1, y1) in pairs:
            if math.hypot(x1 - x0, y1 - y0) >= 80.0:
                edges.append((x0, y0, x1, y1, layer, entity if kind == "LINE" else None))

    def _covered(x0: float, y0: float, x1: float, y1: float) -> bool:
        for ax, ay, bx, by, layer, _ent in edges:
            if layer != WALL_LAYER:
                continue
            if math.hypot(ax - x0, ay - y0) < 8.0 and math.hypot(bx - x1, by - y1) < 8.0:
                return True
            if math.hypot(ax - x1, ay - y1) < 8.0 and math.hypot(bx - x0, by - y0) < 8.0:
                return True
            vx, vy = bx - ax, by - ay
            length2 = vx * vx + vy * vy
            if length2 < 1.0:
                continue
            def _on(px: float, py: float) -> bool:
                t = ((px - ax) * vx + (py - ay) * vy) / length2
                if t < -0.02 or t > 1.02:
                    return False
                return math.hypot(px - (ax + t * vx), py - (ay + t * vy)) <= 8.0
            if _on(x0, y0) and _on(x1, y1):
                return True
        return False

    def _parallel_length(hx: float, hy: float, ux: float, uy: float) -> float:
        total = 0.0
        nx, ny = -uy, ux
        for x0, y0, x1, y1, _layer, _ent in edges:
            ex, ey = x1 - x0, y1 - y0
            elen = math.hypot(ex, ey)
            if elen < 800.0:
                continue
            if abs((ex / elen) * ux + (ey / elen) * uy) < 0.98:
                continue
            off = abs((x0 - hx) * nx + (y0 - hy) * ny)
            mid_d = math.hypot((x0 + x1) / 2.0 - hx, (y0 + y1) / 2.0 - hy)
            if off <= 800.0 and mid_d <= 4000.0:
                total += elen
        return total

    added = 0
    for entity in list(msp):
        if entity.dxftype() != "ARC":
            continue
        try:
            radius = float(entity.dxf.radius)
            hx = float(entity.dxf.center.x)
            hy = float(entity.dxf.center.y)
            start_angle = float(entity.dxf.start_angle)
            end_angle = float(entity.dxf.end_angle)
        except Exception:  # noqa: BLE001
            continue
        sweep = (end_angle - start_angle) % 360.0
        if not (400.0 <= radius <= 1400.0) or not (70.0 <= sweep <= 110.0):
            continue
        ends: list[tuple[float, float]] = []
        for angle in (start_angle, end_angle):
            rad = math.radians(angle)
            ends.append((hx + radius * math.cos(rad), hy + radius * math.sin(rad)))
        best_line: tuple[float, float, float, float, float] | None = None
        options: list[tuple[float, float, float, float, float]] = []
        for px, py in ends:
            ux, uy = (px - hx) / radius, (py - hy) / radius
            if abs(ux) >= 0.98:
                ux, uy = (1.0 if ux > 0.0 else -1.0), 0.0
            elif abs(uy) >= 0.98:
                ux, uy = 0.0, (1.0 if uy > 0.0 else -1.0)
            else:
                continue
            ox, oy = ends[0] if (px, py) != ends[0] else ends[1]
            open_ux, open_uy = (ox - hx) / radius, (oy - hy) / radius
            nx, ny = -uy, ux
            swing_side = open_ux * nx + open_uy * ny
            faces: dict[int, list[tuple[float, float, float, float]]] = {}
            for x0, y0, x1, y1, _layer, _ent in edges:
                ex, ey = x1 - x0, y1 - y0
                elen = math.hypot(ex, ey)
                if elen < 80.0:
                    continue
                if abs((ex / elen) * ux + (ey / elen) * uy) < 0.98:
                    continue
                off0 = (x0 - hx) * nx + (y0 - hy) * ny
                off1 = (x1 - hx) * nx + (y1 - hy) * ny
                if abs(off0) > 450.0 or abs(off1 - off0) > 20.0:
                    continue
                for along, x, y in (
                    ((x0 - hx) * ux + (y0 - hy) * uy, x0, y0),
                    ((x1 - hx) * ux + (y1 - hy) * uy, x1, y1),
                ):
                    if -150.0 <= along <= 220.0 or radius - 220.0 <= along <= radius + 220.0:
                        faces.setdefault(round(off0 / 15.0), []).append((along, x, y, off0))
            facing: list[tuple[float, float, float, float, float]] = []
            for pts in faces.values():
                hinge_side = [p for p in pts if p[0] < radius * 0.45]
                strike_side = [p for p in pts if p[0] > radius * 0.55]
                if not hinge_side or not strike_side:
                    continue
                left = max(hinge_side, key=lambda p: p[0])
                right = min(strike_side, key=lambda p: p[0])
                gap = right[0] - left[0]
                if not (0.7 * radius <= gap <= 1.6 * radius):
                    continue
                if left[3] * swing_side >= 0.0 or abs(left[3]) < 35.0:
                    continue
                facing.append((left[3], left[1], left[2], right[1], right[2]))
            if not facing:
                continue
            off, x0, y0, x1, y1 = max(facing, key=lambda g: abs(g[0]))
            strike_dist = min(
                math.hypot(px - x0, py - y0),
                math.hypot(px - x1, py - y1),
            )
            if strike_dist > 420.0:
                continue
            best_line = (strike_dist, x0, y0, x1, y1)
            options.append(best_line)
        options.sort(key=lambda item: item[0])
        covered_dirs: list[tuple[float, float]] = []
        existing_close: tuple[float, float, float, float] | None = None
        drew = False
        for _dist, x0, y0, x1, y1 in options:
            elen = math.hypot(x1 - x0, y1 - y0) or 1.0
            direction = ((x1 - x0) / elen, (y1 - y0) / elen)
            if any(abs(direction[0] * dx + direction[1] * dy) >= 0.98 for dx, dy in covered_dirs):
                continue
            if _covered(x0, y0, x1, y1):
                if existing_close is None:
                    existing_close = (x0, y0, x1, y1)
                covered_dirs.append(direction)
                continue
            promoted_line = False
            for ax, ay, bx, by, layer, ent in edges:
                if layer != BASE_LAYER or ent is None or ent.dxftype() != "LINE":
                    continue
                same = (
                    math.hypot(ax - x0, ay - y0) <= 20.0 and math.hypot(bx - x1, by - y1) <= 20.0
                ) or (
                    math.hypot(ax - x1, ay - y1) <= 20.0 and math.hypot(bx - x0, by - y0) <= 20.0
                )
                if not same:
                    continue
                if _paint_layer(ent, WALL_LAYER, WALL_COLOR):
                    added += 1
                edges.append((ax, ay, bx, by, WALL_LAYER, ent))
                promoted_line = True
                break
            if not promoted_line:
                line = msp.add_line(
                    (x0, y0), (x1, y1), dxfattribs={"layer": WALL_LAYER, "color": WALL_COLOR},
                )
                edges.append((x0, y0, x1, y1, WALL_LAYER, line))
                added += 1
            added += _promote_closed_door_flanks(msp, edges, x0, y0, x1, y1)
            added += _promote_swing_continuation_doubles(msp, edges, x0, y0, x1, y1, hx, hy)
            covered_dirs.append(direction)
            drew = True
        if not drew and existing_close is not None:
            ex0, ey0, ex1, ey1 = existing_close
            added += _promote_swing_continuation_doubles(msp, edges, ex0, ey0, ex1, ey1, hx, hy)
    return added


def finish_pocket_sliding_doors(msp) -> tuple[int, int]:
    """포켓 미닫이의 문짝을 다시 내리고, 벽의 양면은 올린다.

    벽 두께 안에 모인 짧은 평행선은 문짝이다. 그 옆 개구로 나온 같은 길이의
    선도 문짝이다. 뒤 단계가 문짝을 벽으로 올려도 여기서 되돌린다.
    """
    segs: list[tuple] = []
    for entity in msp:
        if entity.dxftype() != "LINE":
            continue
        try:
            x0 = float(entity.dxf.start.x)
            y0 = float(entity.dxf.start.y)
            x1 = float(entity.dxf.end.x)
            y1 = float(entity.dxf.end.y)
        except Exception:  # noqa: BLE001
            continue
        length = math.hypot(x1 - x0, y1 - y0)
        if length < 80.0:
            continue
        if abs(x1 - x0) <= 20.0:
            along0, along1 = min(y0, y1), max(y0, y1)
            perp = (x0 + x1) / 2.0
            vertical = True
        elif abs(y1 - y0) <= 20.0:
            along0, along1 = min(x0, x1), max(x0, x1)
            perp = (y0 + y1) / 2.0
            vertical = False
        else:
            continue
        segs.append((perp, along0, along1, length, vertical, entity))

    n_demote = 0
    n_promote = 0
    used: set[int] = set()
    pocket = [s for s in segs if 600.0 <= s[3] <= 1400.0]
    for vertical in (True, False):
        group = [s for s in pocket if s[4] is vertical and id(s[5]) not in used]
        group.sort(key=lambda s: s[0])
        i = 0
        while i < len(group):
            seed = group[i]
            if id(seed[5]) in used:
                i += 1
                continue
            nearby = []
            for cand in group:
                if abs(cand[0] - seed[0]) > 500.0:
                    continue
                overlap = min(seed[2], cand[2]) - max(seed[1], cand[1])
                if overlap < 0.7 * min(seed[2] - seed[1], cand[2] - cand[1]):
                    continue
                nearby.append(cand)
            nearby.sort(key=lambda s: s[0])
            cluster = []
            for cand in nearby:
                if cluster and cand[0] - cluster[-1][0] < 15.0:
                    continue
                if cluster and cand[0] - cluster[-1][0] > 130.0:
                    break
                cluster.append(cand)
            width = cluster[-1][0] - cluster[0][0] if cluster else 0.0
            if not (len(cluster) >= 4 and 60.0 <= width <= 200.0):
                i += 1
                continue
            for seg in cluster:
                used.add(id(seg[5]))
            c0 = min(s[1] for s in cluster)
            c1 = max(s[2] for s in cluster)
            span0, span1 = cluster[0][0], cluster[-1][0]
            hosts: list[tuple[float, float, float]] = []
            for perp, along0, along1, length, _vert, _ent in segs:
                if _vert is not vertical or length < 800.0:
                    continue
                if span0 - 200.0 <= perp <= span0 - 20.0 or span1 + 20.0 <= perp <= span1 + 200.0:
                    cover = min(along1, c1) - max(along0, c0)
                    if cover >= 400.0:
                        hosts.append((perp, along0, along1))
            low = [h for h in hosts if h[0] < span0]
            high = [h for h in hosts if h[0] > span1]
            if not low or not high:
                i += 1
                continue
            face_lo = max(low, key=lambda h: h[0])[0]
            face_hi = min(high, key=lambda h: h[0])[0]
            gaps: list[tuple[float, float]] = []
            for face in (face_lo, face_hi):
                pieces = sorted(
                    (a0, a1)
                    for perp, a0, a1, length, _vert, _ent in segs
                    if _vert is vertical and abs(perp - face) <= 15.0 and length >= 200.0
                )
                cursor = c0 - 200.0
                for a0, a1 in pieces:
                    if a1 < cursor:
                        continue
                    if a0 > cursor + 200.0:
                        gaps.append((cursor, a0))
                    cursor = max(cursor, a1)
                if cursor < c1 + 1600.0:
                    gaps.append((cursor, c1 + 1600.0))

            def _in_gap(a0: float, a1: float) -> bool:
                return any(min(a1, g1) - max(a0, g0) > 200.0 for g0, g1 in gaps)

            for perp, along0, along1, length, _vert, ent in cluster:
                if _paint_layer(ent, BASE_LAYER, BASE_COLOR):
                    n_demote += 1
            for perp, along0, along1, length, _vert, ent in segs:
                if _vert is not vertical or ent is None:
                    continue
                if not (500.0 <= length <= 1400.0):
                    continue
                if not (face_lo + 20.0 < perp < face_hi - 20.0):
                    continue
                if id(ent) in {id(s[5]) for s in cluster}:
                    continue
                if _in_gap(along0, along1) and _paint_layer(ent, BASE_LAYER, BASE_COLOR):
                    n_demote += 1
            for perp, along0, along1, length, _vert, ent in segs:
                if _vert is not vertical or not (200.0 <= length <= 4000.0):
                    continue
                if min(abs(perp - face_lo), abs(perp - face_hi)) > 15.0:
                    continue
                if min(along1, c1 + 4000.0) - max(along0, c0 - 4000.0) < 200.0:
                    continue
                if _in_gap(along0, along1):
                    continue
                if _paint_layer(ent, WALL_LAYER, WALL_COLOR):
                    n_promote += 1
            i += 1
    return n_demote, n_promote


def separate_stepped_sliding_doors(msp) -> tuple[int, int]:
    """어긋나게 이어진 얇은 문짝은 내리고, 양끝 벽은 올린다.

    두께 20–80 mm, 길이 400–800 mm 문짝이 세 장 이상 한 줄이면 미닫이다.
    문짝은 BASE로 두고, 문 끝에 닿아 밖으로 이어진 벽만 WALL로 올린다.
    """
    panels: list[tuple] = []
    for entity in msp:
        if entity.dxftype() != "LWPOLYLINE" or not entity.closed:
            continue
        pts = _polyline_xy(entity)
        if len(pts) < 4:
            continue
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        width, height = max(xs) - min(xs), max(ys) - min(ys)
        short, long = min(width, height), max(width, height)
        if not (20.0 <= short <= 80.0 and 400.0 <= long <= 800.0):
            continue
        panels.append((min(xs), min(ys), max(xs), max(ys), width >= height, entity))

    used: set[int] = set()
    runs: list[list[int]] = []
    for i, panel in enumerate(panels):
        if i in used:
            continue
        run = [i]
        used.add(i)
        grew = True
        while grew:
            grew = False
            for j, other in enumerate(panels):
                if j in used or other[4] is not panel[4]:
                    continue
                joined = False
                for k in run:
                    host = panels[k]
                    if host[4]:
                        gap = max(host[0], other[0]) - min(host[2], other[2])
                        shift = abs((host[1] + host[3]) / 2.0 - (other[1] + other[3]) / 2.0)
                    else:
                        gap = max(host[1], other[1]) - min(host[3], other[3])
                        shift = abs((host[0] + host[2]) / 2.0 - (other[0] + other[2]) / 2.0)
                    if gap < 120.0 and shift < 80.0:
                        joined = True
                        break
                if joined:
                    used.add(j)
                    run.append(j)
                    grew = True
        if len(run) >= 3:
            runs.append(run)

    def _end_meets_wall(at_along: float, perp0: float, perp1: float, horizontal: bool) -> bool:
        for entity in msp:
            if entity.dxftype() != "LINE":
                continue
            if getattr(entity.dxf, "layer", None) != WALL_LAYER:
                continue
            x0 = float(entity.dxf.start.x)
            y0 = float(entity.dxf.start.y)
            x1 = float(entity.dxf.end.x)
            y1 = float(entity.dxf.end.y)
            length = math.hypot(x1 - x0, y1 - y0)
            if length < 200.0:
                continue
            if horizontal:
                if abs(x1 - x0) > 25.0 or abs((x0 + x1) / 2.0 - at_along) > 80.0:
                    continue
                span0, span1 = min(y0, y1), max(y0, y1)
            else:
                if abs(y1 - y0) > 25.0 or abs((y0 + y1) / 2.0 - at_along) > 80.0:
                    continue
                span0, span1 = min(x0, x1), max(x0, x1)
            if min(span1, perp1 + 80.0) - max(span0, perp0 - 80.0) >= 40.0:
                return True
        return False

    n_demote = 0
    n_promote = 0
    for run in runs:
        members = [panels[i] for i in run]
        horizontal = members[0][4]
        if horizontal:
            along0 = min(item[0] for item in members)
            along1 = max(item[2] for item in members)
            perp0 = min(item[1] for item in members)
            perp1 = max(item[3] for item in members)
        else:
            along0 = min(item[1] for item in members)
            along1 = max(item[3] for item in members)
            perp0 = min(item[0] for item in members)
            perp1 = max(item[2] for item in members)
        # 양 벽에 닿아 칸을 가득 채우면 신발장이다. 문짝으로 내리지 않고 벽으로 닫는다.
        if _end_meets_wall(along0, perp0, perp1, horizontal) and _end_meets_wall(along1, perp0, perp1, horizontal):
            for _x0, _y0, _x1, _y1, _horiz, ent in members:
                if _paint_layer(ent, WALL_LAYER, WALL_COLOR):
                    n_promote += 1
            mid = (perp0 + perp1) / 2.0
            if horizontal:
                x0, y0, x1, y1 = along0, mid, along1, mid
            else:
                x0, y0, x1, y1 = mid, along0, mid, along1
            covered = False
            for entity in msp:
                if entity.dxftype() != "LINE" or getattr(entity.dxf, "layer", None) != WALL_LAYER:
                    continue
                ax, ay = float(entity.dxf.start.x), float(entity.dxf.start.y)
                bx, by = float(entity.dxf.end.x), float(entity.dxf.end.y)
                vx, vy = bx - ax, by - ay
                length2 = vx * vx + vy * vy
                if length2 < 1.0:
                    continue
                def _on(px: float, py: float, ax: float = ax, ay: float = ay, vx: float = vx, vy: float = vy) -> bool:
                    tee = ((px - ax) * vx + (py - ay) * vy) / length2
                    if tee < -0.02 or tee > 1.02:
                        return False
                    return math.hypot(px - (ax + tee * vx), py - (ay + tee * vy)) <= 20.0
                if _on(x0, y0) and _on(x1, y1):
                    covered = True
                    break
            if not covered:
                msp.add_line((x0, y0), (x1, y1), dxfattribs={"layer": WALL_LAYER, "color": WALL_COLOR})
                n_promote += 1
            continue
        for _x0, _y0, _x1, _y1, _horiz, ent in members:
            if _paint_layer(ent, BASE_LAYER, BASE_COLOR):
                n_demote += 1

        for entity in msp:
            if entity.dxftype() != "LINE":
                continue
            if getattr(entity.dxf, "layer", None) != BASE_LAYER:
                continue
            x0 = float(entity.dxf.start.x)
            y0 = float(entity.dxf.start.y)
            x1 = float(entity.dxf.end.x)
            y1 = float(entity.dxf.end.y)
            length = math.hypot(x1 - x0, y1 - y0)
            if horizontal:
                line_along = (x0 + x1) / 2.0
                span0, span1 = min(y0, y1), max(y0, y1)
                perpendicular = abs(x1 - x0) <= 25.0
                parallel = abs(y1 - y0) <= 25.0
            else:
                line_along = (y0 + y1) / 2.0
                span0, span1 = min(x0, x1), max(x0, x1)
                perpendicular = abs(y1 - y0) <= 25.0
                parallel = abs(x1 - x0) <= 25.0
            at_end = min(abs(line_along - along0), abs(line_along - along1)) <= 40.0
            if perpendicular and 200.0 <= length <= 1800.0 and at_end:
                outside = (span0 < perp0 - 150.0) or (span1 > perp1 + 150.0)
                touches = min(span1, perp1 + 80.0) - max(span0, perp0 - 80.0) >= 40.0
                if outside and touches and _paint_layer(entity, WALL_LAYER, WALL_COLOR):
                    n_promote += 1
                continue
            if not parallel or not (80.0 <= length <= 400.0):
                continue
            if horizontal:
                end_a, end_b = min(x0, x1), max(x0, x1)
                face = (y0 + y1) / 2.0
            else:
                end_a, end_b = min(y0, y1), max(y0, y1)
                face = (x0 + x1) / 2.0
            starts = min(
                abs(end_a - along0), abs(end_a - along1),
                abs(end_b - along0), abs(end_b - along1),
            )
            if starts > 40.0:
                continue
            if min(abs(face - perp0), abs(face - perp1)) > 80.0:
                continue
            inside = min(end_b, along1) - max(end_a, along0)
            if inside > 40.0:
                continue
            if _paint_layer(entity, WALL_LAYER, WALL_COLOR):
                n_promote += 1
    return n_demote, n_promote


def add_corner_closed_door_walls(msp) -> int:
    """문틀에서 끊긴 안쪽 면을 직각 힌지 벽까지 잇는다.

    스윙 반대편 면이 문 너비에서 끝나고, 힌지 쪽에 같은 면의 점이 없으면
    그 높이로 문틀에서 힌지 벽까지 WALL 한 줄을 긋는다. 문짝은 그대로 둔다.
    힌지 벽이 확정된 뒤에 호출한다.
    """
    edges: list[tuple] = []
    for entity in msp:
        layer = getattr(entity.dxf, "layer", None)
        if layer not in (WALL_LAYER, BASE_LAYER):
            continue
        kind = entity.dxftype()
        try:
            if kind == "LINE":
                pairs = [(
                    (float(entity.dxf.start.x), float(entity.dxf.start.y)),
                    (float(entity.dxf.end.x), float(entity.dxf.end.y)),
                )]
            elif kind == "LWPOLYLINE":
                pts = [(float(p[0]), float(p[1])) for p in entity.get_points("xy")]
                pairs = list(zip(pts, pts[1:]))
                if entity.closed and len(pts) >= 2:
                    pairs.append((pts[-1], pts[0]))
            else:
                continue
        except Exception:  # noqa: BLE001
            continue
        for (x0, y0), (x1, y1) in pairs:
            if math.hypot(x1 - x0, y1 - y0) >= 80.0:
                edges.append((x0, y0, x1, y1, layer, entity if kind == "LINE" else None))

    def _covered(x0: float, y0: float, x1: float, y1: float) -> bool:
        for ax, ay, bx, by, layer, _ent in edges:
            if layer != WALL_LAYER:
                continue
            vx, vy = bx - ax, by - ay
            length2 = vx * vx + vy * vy
            if length2 < 1.0:
                continue
            def _on(px: float, py: float) -> bool:
                tee = ((px - ax) * vx + (py - ay) * vy) / length2
                if tee < -0.02 or tee > 1.02:
                    return False
                return math.hypot(px - (ax + tee * vx), py - (ay + tee * vy)) <= 12.0
            if _on(x0, y0) and _on(x1, y1):
                return True
        return False

    added = 0
    for entity in list(msp):
        if entity.dxftype() != "ARC":
            continue
        try:
            radius = float(entity.dxf.radius)
            hx = float(entity.dxf.center.x)
            hy = float(entity.dxf.center.y)
            start_angle = float(entity.dxf.start_angle)
            end_angle = float(entity.dxf.end_angle)
        except Exception:  # noqa: BLE001
            continue
        sweep = (end_angle - start_angle) % 360.0
        if not (400.0 <= radius <= 1400.0) or not (70.0 <= sweep <= 110.0):
            continue
        ends: list[tuple[float, float]] = []
        for angle in (start_angle, end_angle):
            rad = math.radians(angle)
            ends.append((hx + radius * math.cos(rad), hy + radius * math.sin(rad)))
        for index, (px, py) in enumerate(ends):
            ux, uy = (px - hx) / radius, (py - hy) / radius
            if abs(ux) >= 0.98:
                ux, uy = (1.0 if ux > 0.0 else -1.0), 0.0
            elif abs(uy) >= 0.98:
                ux, uy = 0.0, (1.0 if uy > 0.0 else -1.0)
            else:
                continue
            ox, oy = ends[1 - index]
            open_ux, open_uy = (ox - hx) / radius, (oy - hy) / radius
            nx, ny = -uy, ux
            swing_side = open_ux * nx + open_uy * ny
            if abs(swing_side) < 0.5:
                continue
            corner_bins: dict[int, list[tuple[float, float, float, float]]] = {}
            hinge_bins: set[int] = set()
            for x0, y0, x1, y1, _layer, _ent in edges:
                ex, ey = x1 - x0, y1 - y0
                elen = math.hypot(ex, ey)
                if elen < 80.0:
                    continue
                if abs((ex / elen) * ux + (ey / elen) * uy) < 0.98:
                    continue
                off0 = (x0 - hx) * nx + (y0 - hy) * ny
                off1 = (x1 - hx) * nx + (y1 - hy) * ny
                if abs(off0) > 450.0 or abs(off1 - off0) > 20.0:
                    continue
                along0 = (x0 - hx) * ux + (y0 - hy) * uy
                along1 = (x1 - hx) * ux + (y1 - hy) * uy
                if min(along0, along1) < radius * 0.45:
                    hinge_bins.add(round(off0 / 15.0))
                if off0 * swing_side >= 0.0 or abs(off0) < 150.0:
                    continue
                if along0 <= along1:
                    near, far, qx, qy = along0, along1, x0, y0
                else:
                    near, far, qx, qy = along1, along0, x1, y1
                if not (0.7 * radius <= near <= 1.35 * radius) or far < near + 200.0:
                    continue
                corner_bins.setdefault(round(off0 / 15.0), []).append((off0, near, qx, qy))
            corner_bins = {
                key: pts for key, pts in corner_bins.items() if key not in hinge_bins
            }
            if not corner_bins:
                continue
            picked = max(corner_bins, key=lambda key: abs(corner_bins[key][0][0]))
            off, _near, qx, qy = min(corner_bins[picked], key=lambda item: item[1])
            xh = hx + off * nx
            yh = hy + off * ny
            meets_jamb = False
            for ax, ay, bx, by, layer, _ent in edges:
                if layer != WALL_LAYER:
                    continue
                ex, ey = bx - ax, by - ay
                elen = math.hypot(ex, ey)
                if elen < 200.0:
                    continue
                if abs((ex / elen) * ux + (ey / elen) * uy) > 0.25:
                    continue
                length2 = ex * ex + ey * ey
                tee = max(0.0, min(1.0, ((xh - ax) * ex + (yh - ay) * ey) / length2))
                if math.hypot(xh - (ax + tee * ex), yh - (ay + tee * ey)) <= 80.0:
                    meets_jamb = True
                    break
            if not meets_jamb or _covered(qx, qy, xh, yh):
                continue
            strike_dist = min(
                math.hypot(px - qx, py - qy),
                math.hypot(px - xh, py - yh),
            )
            if strike_dist > 700.0:
                continue
            line = msp.add_line(
                (qx, qy), (xh, yh), dxfattribs={"layer": WALL_LAYER, "color": WALL_COLOR},
            )
            edges.append((qx, qy, xh, yh, WALL_LAYER, line))
            added += 1
            added += _promote_closed_door_flanks(msp, edges, qx, qy, xh, yh)
            added += _promote_swing_continuation_doubles(msp, edges, qx, qy, xh, yh, hx, hy)
            break
    return added


def remove_swing_squares(msp) -> int:
    """문 스윙 힌지에 붙은 작은 네모(한 변 약 100 mm)를 지운다."""
    hinges: list[tuple[float, float]] = []
    for entity in msp:
        if entity.dxftype() != "ARC":
            continue
        try:
            radius = float(entity.dxf.radius)
            start_angle = float(entity.dxf.start_angle)
            end_angle = float(entity.dxf.end_angle)
        except Exception:  # noqa: BLE001
            continue
        sweep = (end_angle - start_angle) % 360.0
        if not (400.0 <= radius <= 1400.0) or not (70.0 <= sweep <= 110.0):
            continue
        hinges.append((float(entity.dxf.center.x), float(entity.dxf.center.y)))
    if not hinges:
        return 0
    removed = 0
    for entity in list(msp):
        if entity.dxftype() != "LWPOLYLINE" or not entity.closed:
            continue
        try:
            pts = [(float(p[0]), float(p[1])) for p in entity.get_points("xy")]
        except Exception:  # noqa: BLE001
            continue
        if len(pts) < 4:
            continue
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        width = max(xs) - min(xs)
        height = max(ys) - min(ys)
        short = min(width, height)
        long = max(width, height)
        if not (50.0 <= short <= 160.0 and long <= 160.0):
            continue
        cx = sum(xs) / len(xs)
        cy = sum(ys) / len(ys)
        if not any(math.hypot(cx - hx, cy - hy) <= 220.0 for hx, hy in hinges):
            continue
        msp.delete_entity(entity)
        removed += 1
    return removed


def separate_sliding_door_panels(msp) -> int:
    """미닫이 문짝은 BASE로 내리고, 문에 이어진 벽면은 WALL로 올린다.

    벽 두께 안에 모인 짧은 평행선(포켓 속 문짝)만 내린다.
    그 묶음 바깥으로 이어진 벽면은 올린다.
    """
    segs: list[tuple] = []
    for entity in msp:
        if entity.dxftype() != "LINE":
            continue
        try:
            x0 = float(entity.dxf.start.x)
            y0 = float(entity.dxf.start.y)
            x1 = float(entity.dxf.end.x)
            y1 = float(entity.dxf.end.y)
        except Exception:  # noqa: BLE001
            continue
        length = math.hypot(x1 - x0, y1 - y0)
        if length < 80.0:
            continue
        if abs(x1 - x0) <= 20.0:
            along0, along1 = min(y0, y1), max(y0, y1)
            perp = (x0 + x1) / 2.0
            vertical = True
        elif abs(y1 - y0) <= 20.0:
            along0, along1 = min(x0, x1), max(x0, x1)
            perp = (y0 + y1) / 2.0
            vertical = False
        else:
            continue
        segs.append((perp, along0, along1, length, vertical, entity))

    changed = 0
    used: set[int] = set()
    pocket = [s for s in segs if 600.0 <= s[3] <= 1400.0]
    for vertical in (True, False):
        group = [s for s in pocket if s[4] is vertical and id(s[5]) not in used]
        group.sort(key=lambda s: s[0])
        i = 0
        while i < len(group):
            seed = group[i]
            if id(seed[5]) in used:
                i += 1
                continue
            nearby = []
            for cand in group:
                if abs(cand[0] - seed[0]) > 500.0:
                    continue
                overlap = min(seed[2], cand[2]) - max(seed[1], cand[1])
                if overlap < 0.7 * min(seed[2] - seed[1], cand[2] - cand[1]):
                    continue
                nearby.append(cand)
            nearby.sort(key=lambda s: s[0])
            cluster = []
            for cand in nearby:
                if cluster and cand[0] - cluster[-1][0] < 15.0:
                    continue
                if cluster and cand[0] - cluster[-1][0] > 130.0:
                    break
                cluster.append(cand)
            width = cluster[-1][0] - cluster[0][0] if cluster else 0.0
            if len(cluster) >= 4 and 60.0 <= width <= 500.0:
                for seg in cluster:
                    used.add(id(seg[5]))
                c0 = min(s[1] for s in cluster)
                c1 = max(s[2] for s in cluster)
                def _near_face(perp: float) -> bool:
                    return any(abs(perp - s[0]) <= 8.0 for s in cluster)

                span0, span1 = cluster[0][0], cluster[-1][0]
                wall_perps: list[float] = []
                for perp, along0, along1, length, _vert, _ent in segs:
                    if _vert is not vertical or length < 500.0:
                        continue
                    outside = (span0 - 160.0 <= perp <= span0 - 20.0) or (
                        span1 + 20.0 <= perp <= span1 + 160.0
                    )
                    if not _near_face(perp) and not outside:
                        continue
                    reaches = along0 < c0 - 400.0 or along1 > c1 + 400.0
                    gap = 0.0
                    if along1 < c0:
                        gap = c0 - along1
                    elif along0 > c1:
                        gap = along0 - c1
                    if reaches and gap <= 1600.0:
                        wall_perps.append(perp)

                def _is_wall_face(perp: float) -> bool:
                    return any(abs(perp - wp) <= 8.0 for wp in wall_perps)

                for perp, _along0, _along1, _length, _vert, ent in cluster:
                    if _is_wall_face(perp):
                        if _paint_layer(ent, WALL_LAYER, WALL_COLOR):
                            changed += 1
                    elif _paint_layer(ent, BASE_LAYER, BASE_COLOR):
                        changed += 1
                for perp, along0, along1, length, _vert, ent in segs:
                    if _vert is not vertical or not _is_wall_face(perp):
                        continue
                    if not (200.0 <= length <= 4000.0):
                        continue
                    if min(along1, c1) - max(along0, c0) > 40.0:
                        continue
                    gap = c0 - along1 if along1 <= c0 else along0 - c1
                    if 0.0 <= gap <= 1600.0 and _paint_layer(ent, WALL_LAYER, WALL_COLOR):
                        changed += 1
            i += 1
        # 개구에 놓인 문짝 두 줄. 양옆 벽이 그 앞에서 끊긴다.
        leaves = [s for s in pocket if s[4] is vertical and id(s[5]) not in used]
        leaves.sort(key=lambda s: (s[0], s[1]))
        for a, b in zip(leaves, leaves[1:]):
            if id(a[5]) in used or id(b[5]) in used:
                continue
            if not (20.0 <= b[0] - a[0] <= 90.0):
                continue
            overlap = min(a[2], b[2]) - max(a[1], b[1])
            if overlap < 0.8 * min(a[2] - a[1], b[2] - b[1]):
                continue
            c0, c1 = min(a[1], b[1]), max(a[2], b[2])
            side_hits: dict[float, set[str]] = {}
            for perp, along0, along1, length, _vert, _ent in segs:
                if _vert is not vertical or length < 400.0:
                    continue
                if not (a[0] - 220.0 <= perp <= a[0] - 20.0 or b[0] + 20.0 <= perp <= b[0] + 220.0):
                    continue
                cover = min(along1, c1) - max(along0, c0)
                if cover > 0.35 * (c1 - c0):
                    continue
                key = round(perp)
                side_hits.setdefault(key, set())
                if along1 <= c0 + 40.0 and c0 - along0 >= 400.0:
                    side_hits[key].add("below")
                if along0 >= c1 - 40.0 and along1 - c1 >= 400.0:
                    side_hits[key].add("above")
                if length >= 1400.0 and (along1 >= c0 - 40.0 and along0 <= c1 + 40.0):
                    side_hits[key].add("long")
            hosts = [float(k) for k, sides in side_hits.items() if len(sides) >= 2 or "long" in sides]
            if not any(p < a[0] for p in hosts) or not any(p > b[0] for p in hosts):
                continue
            for ent in (a[5], b[5]):
                used.add(id(ent))
                if _paint_layer(ent, BASE_LAYER, BASE_COLOR):
                    changed += 1
            for perp, along0, along1, length, _vert, ent in segs:
                if _vert is not vertical or not (200.0 <= length <= 4000.0):
                    continue
                if not any(abs(perp - h) <= 8.0 for h in hosts):
                    continue
                if min(along1, c1) - max(along0, c0) > 40.0:
                    continue
                gap = c0 - along1 if along1 <= c0 else along0 - c1
                if 0.0 <= gap <= 1600.0 and _paint_layer(ent, WALL_LAYER, WALL_COLOR):
                    changed += 1
    return changed


def promote_swing_attached_doubles(msp) -> int:
    """스윙이 붙은 자리의 평행 이중선을 WALL로 올린다.

    문짝(힌지에서 호 끝까지 반지름 길이로 이어진 선)은 올리지 않는다.
    """
    swings: list[tuple[float, float, float, float, float, float]] = []
    for entity in msp:
        if entity.dxftype() != "ARC":
            continue
        try:
            radius = float(entity.dxf.radius)
            hx = float(entity.dxf.center.x)
            hy = float(entity.dxf.center.y)
            start_angle = float(entity.dxf.start_angle)
            end_angle = float(entity.dxf.end_angle)
        except Exception:  # noqa: BLE001
            continue
        sweep = (end_angle - start_angle) % 360.0
        if not (400.0 <= radius <= 1400.0) or not (70.0 <= sweep <= 110.0):
            continue
        srad = math.radians(start_angle)
        erad = math.radians(end_angle)
        swings.append((
            hx, hy, radius,
            math.cos(srad), math.sin(srad),
            math.cos(erad), math.sin(erad),
        ))
    if not swings:
        return 0

    segs: list[tuple[float, float, float, float, object]] = []
    for entity in msp:
        if getattr(entity.dxf, "layer", None) != BASE_LAYER:
            continue
        kind = entity.dxftype()
        try:
            if kind == "LINE":
                pairs = [(
                    (float(entity.dxf.start.x), float(entity.dxf.start.y)),
                    (float(entity.dxf.end.x), float(entity.dxf.end.y)),
                )]
            elif kind == "LWPOLYLINE":
                pts = [(float(p[0]), float(p[1])) for p in entity.get_points("xy")]
                pairs = list(zip(pts, pts[1:]))
                if entity.closed and len(pts) >= 2:
                    pairs.append((pts[-1], pts[0]))
            else:
                continue
        except Exception:  # noqa: BLE001
            continue
        for (x0, y0), (x1, y1) in pairs:
            if 80.0 <= math.hypot(x1 - x0, y1 - y0) <= 3000.0:
                segs.append((x0, y0, x1, y1, entity))

    def _leaf(x0, y0, x1, y1, hx, hy, radius, dirs) -> bool:
        length = math.hypot(x1 - x0, y1 - y0)
        if length < radius * 0.55:
            return False
        ux, uy = (x1 - x0) / length, (y1 - y0) / length
        mx, my = (x0 + x1) / 2.0, (y0 + y1) / 2.0
        if math.hypot(mx - hx, my - hy) > radius * 1.15:
            return False
        for dx, dy in dirs:
            if abs(ux * dx + uy * dy) < 0.95:
                continue
            if abs((x0 - hx) * dy - (y0 - hy) * dx) <= 140.0:
                return True
        return False

    promoted = 0
    seen: set[int] = set()
    wall_lines = [
        e for e in msp
        if e.dxftype() == "LINE" and getattr(e.dxf, "layer", None) == WALL_LAYER
    ]
    for hx, hy, radius, sx, sy, ex, ey in swings:
        dirs = ((sx, sy), (ex, ey))
        for entity in wall_lines:
            if _leaf(
                float(entity.dxf.start.x), float(entity.dxf.start.y),
                float(entity.dxf.end.x), float(entity.dxf.end.y),
                hx, hy, radius, dirs,
            ):
                _paint_layer(entity, BASE_LAYER, BASE_COLOR)
        local = []
        for x0, y0, x1, y1, entity in segs:
            vx, vy = x1 - x0, y1 - y0
            length2 = vx * vx + vy * vy
            t = max(0.0, min(1.0, ((hx - x0) * vx + (hy - y0) * vy) / length2))
            dist = math.hypot(hx - (x0 + t * vx), hy - (y0 + t * vy))
            if dist <= 360.0 and not _leaf(x0, y0, x1, y1, hx, hy, radius, dirs):
                local.append((x0, y0, x1, y1, entity))
        for i, (ax, ay, bx, by, ent_a) in enumerate(local):
            adx, ady = bx - ax, by - ay
            alen = math.hypot(adx, ady)
            if alen < 80.0:
                continue
            aux, auy = adx / alen, ady / alen
            a0 = ax * aux + ay * auy
            a1 = bx * aux + by * auy
            if a1 < a0:
                a0, a1 = a1, a0
            for cx, cy, dx, dy, ent_b in local[i + 1:]:
                cdx, cdy = dx - cx, dy - cy
                clen = math.hypot(cdx, cdy)
                if clen < 80.0:
                    continue
                if abs((cdx / clen) * aux + (cdy / clen) * auy) < 0.98:
                    continue
                gap = abs((cx - ax) * (-auy) + (cy - ay) * aux)
                if not (40.0 <= gap <= 200.0):
                    continue
                c0 = cx * aux + cy * auy
                c1 = dx * aux + dy * auy
                if c1 < c0:
                    c0, c1 = c1, c0
                if min(a1, c1) - max(a0, c0) < 80.0:
                    continue
                for ent in (ent_a, ent_b):
                    if id(ent) in seen or ent.dxftype() != "LINE":
                        continue
                    if _paint_layer(ent, WALL_LAYER, WALL_COLOR):
                        promoted += 1
                        seen.add(id(ent))
    return promoted


def _load_room_eval():
    """실이 닫혔는지 판정은 drawing-roomevaluator 와 같은 기하를 쓴다."""
    root = Path(__file__).resolve().parents[2] / "drawing-roomevaluator" / "scripts"
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    import evaluate_room

    return evaluate_room


def close_leaking_wall_ends(msp) -> int:
    """벽이 닫히지 않아 면적이 밖으로 새면, 벽의 마지막을 따라 닫는다.

    실명 라벨이 빨간 WALL 면에 들어가지 않을 때, 문 개구가 아닌 WALL 끝에서
    이어진 회색 선을 따라 올린다. 같은 방향이거나, 그 끝에서 한 번만 직각으로
    꺾인 뒤 그 면을 따라 다음 벽에 닿을 때까지다. 이어진 선이 없으면
    빈 곳에 벽을 새로 긋지 않는다.
    """
    ev = _load_room_eval()
    boxes = ev.column_boxes(msp)
    base_walls = ev.wall_segments(msp, boxes)

    def _hv_parts(entity, layer: str) -> list[dict]:
        kind = entity.dxftype()
        pairs: list[tuple[tuple[float, float], tuple[float, float]]] = []
        if kind == "LINE":
            pairs.append((
                (float(entity.dxf.start.x), float(entity.dxf.start.y)),
                (float(entity.dxf.end.x), float(entity.dxf.end.y)),
            ))
        elif kind == "LWPOLYLINE":
            pts = [(float(p[0]), float(p[1])) for p in entity.get_points("xy")]
            pairs = list(zip(pts, pts[1:]))
            if entity.closed and len(pts) >= 2:
                pairs.append((pts[-1], pts[0]))
        else:
            return []
        out: list[dict] = []
        for a, b in pairs:
            length = math.hypot(b[0] - a[0], b[1] - a[1])
            if length < 150.0:
                continue
            ang = abs(math.degrees(math.atan2(b[1] - a[1], b[0] - a[0]))) % 180.0
            if not (ang < 10.0 or abs(ang - 90.0) < 10.0):
                continue
            out.append({"a": a, "b": b, "L": length, "ent": entity, "layer": layer})
        return out

    walls: list[dict] = []
    bases: list[dict] = []
    for entity in msp:
        layer = getattr(entity.dxf, "layer", None)
        if layer == WALL_LAYER:
            walls.extend(_hv_parts(entity, layer))
        elif layer == BASE_LAYER:
            bases.extend(_hv_parts(entity, layer))
    arcs: list[tuple[float, float, float]] = []
    for entity in msp:
        if entity.dxftype() != "ARC":
            continue
        try:
            arcs.append((
                float(entity.dxf.center.x),
                float(entity.dxf.center.y),
                float(entity.dxf.radius),
            ))
        except Exception:  # noqa: BLE001
            continue

    def _near(p, q, tol: float) -> bool:
        return math.hypot(p[0] - q[0], p[1] - q[1]) <= tol

    def _unit(p, q) -> tuple[float, float]:
        dx, dy = q[0] - p[0], q[1] - p[1]
        length = math.hypot(dx, dy) or 1.0
        return dx / length, dy / length

    def _body_hit(p, segs: list[dict], tol: float, ignore: dict | None = None) -> bool:
        for seg in segs:
            if ignore is not None and seg is ignore:
                continue
            if _near(p, seg["a"], tol) or _near(p, seg["b"], tol):
                return True
            vx, vy = seg["b"][0] - seg["a"][0], seg["b"][1] - seg["a"][1]
            length2 = vx * vx + vy * vy
            if length2 < 1.0:
                continue
            u = ((p[0] - seg["a"][0]) * vx + (p[1] - seg["a"][1]) * vy) / length2
            if 0.02 <= u <= 0.98:
                qx, qy = seg["a"][0] + u * vx, seg["a"][1] + u * vy
                if math.hypot(p[0] - qx, p[1] - qy) <= tol:
                    return True
        return False

    def _is_door_tip(p, direction, src) -> bool:
        ux, uy = direction
        for seg in walls:
            if seg is src:
                continue
            if abs(ux) > 0.9:
                if min(abs(seg["a"][1] - p[1]), abs((seg["a"][1] + seg["b"][1]) * 0.5 - p[1])) > 50.0:
                    continue
                xs = sorted((seg["a"][0], seg["b"][0]))
                gap = xs[0] - p[0] if ux > 0.0 else p[0] - xs[1]
            else:
                if min(abs(seg["a"][0] - p[0]), abs((seg["a"][0] + seg["b"][0]) * 0.5 - p[0])) > 50.0:
                    continue
                ys = sorted((seg["a"][1], seg["b"][1]))
                gap = ys[0] - p[1] if uy > 0.0 else p[1] - ys[1]
            if 40.0 < gap <= 2400.0:
                return True
        return False

    def _free_tips() -> list[tuple]:
        tips = []
        for seg in walls:
            for end_name in ("a", "b"):
                point = seg[end_name]
                other = seg["b"] if end_name == "a" else seg["a"]
                if _body_hit(point, walls, 120.0, ignore=seg):
                    continue
                direction = _unit(other, point)
                if _is_door_tip(point, direction, seg):
                    continue
                tips.append((point, direction, seg))
        return tips

    def _door_leaf(seg: dict) -> bool:
        mx = (seg["a"][0] + seg["b"][0]) * 0.5
        my = (seg["a"][1] + seg["b"][1]) * 0.5
        for hx, hy, radius in arcs:
            if not (400.0 <= radius <= 1400.0):
                continue
            if abs(seg["L"] - radius) > max(180.0, 0.35 * radius):
                continue
            if math.hypot(mx - hx, my - hy) <= radius + 250.0:
                return True
        return False

    def _extend(first: dict, tip_at, direction) -> list[dict]:
        used = {id(first)}
        segs = [first]
        cur = tip_at
        direc = direction
        length = first["L"]
        # 첫 선 끝이 이미 벽에 닿으면 더 잇지 않는다.
        if _body_hit(cur, walls, 400.0) and length >= 300.0:
            return segs
        for _step in range(7):
            found = None
            best = 1e9
            for seg in bases:
                if id(seg) in used or seg["L"] > 9000.0 or _door_leaf(seg):
                    continue
                for end_name in ("a", "b"):
                    end = seg[end_name]
                    dist = math.hypot(cur[0] - end[0], cur[1] - end[1])
                    if dist > 250.0:
                        continue
                    nxt = seg["b"] if end_name == "a" else seg["a"]
                    nd = _unit(end, nxt)
                    if direc[0] * nd[0] + direc[1] * nd[1] <= 0.92:
                        continue
                    # 옆 줄로 건너뛰지 않는다.
                    lateral = abs((end[0] - cur[0]) * -direc[1] + (end[1] - cur[1]) * direc[0])
                    if lateral > 45.0:
                        continue
                    if dist < best:
                        best = dist
                        found = (seg, nxt, nd)
            if found is None:
                break
            seg, nxt, nd = found
            used.add(id(seg))
            segs.append(seg)
            length += seg["L"]
            cur = nxt
            direc = nd
            if length > 8000.0 or _body_hit(cur, walls, 350.0):
                break
        if not _body_hit(cur, walls, 400.0) or length < 300.0:
            return []
        return segs

    def _chains_from(tip, direction) -> list[list[dict]]:
        col = None
        perp = None
        best_col = 1e9
        best_perp = 1e9
        for seg in bases:
            if seg["L"] > 9000.0 or _door_leaf(seg):
                continue
            for end_name in ("a", "b"):
                dist = math.hypot(tip[0] - seg[end_name][0], tip[1] - seg[end_name][1])
                if dist > 90.0:
                    continue
                nxt = seg["b"] if end_name == "a" else seg["a"]
                nd = _unit(seg[end_name], nxt)
                align = direction[0] * nd[0] + direction[1] * nd[1]
                if align > 0.92 and dist < best_col:
                    best_col = dist
                    col = (seg, nxt, nd)
                elif abs(align) < 0.2 and dist < best_perp:
                    best_perp = dist
                    perp = (seg, nxt, nd)
        chains = []
        for start in (col, perp):
            if start is None:
                continue
            seg, nxt, nd = start
            chains.append(_extend(seg, nxt, nd))
        return [c for c in chains if c]

    def _face(extra: list[tuple[float, float, float, float]], lx: float, ly: float):
        pad = 22000.0
        h_final, v_final, dsegs, _x, _y = ev.bridged_runs(
            base_walls + extra, (lx, ly), pad + 2000.0
        )
        lines = ev._cap_wall_ends(
            ev._snap_endpoints(ev._lines_from_runs(h_final, v_final, dsegs), ev.JOIN_MM),
            ev.WALL_CAP_MM,
        )
        found = ev._face_at(lines, lx, ly)
        if found is None or ev._touches_window(found, (lx, ly), pad + 2000.0):
            return None
        return found

    def _on_boundary(face, segs: list[dict]) -> bool:
        from shapely.geometry import LineString

        ring = face.exterior
        for seg in segs:
            if ring.distance(LineString([seg["a"], seg["b"]])) > 450.0:
                return False
        return True

    labels: list[tuple[float, float, str]] = []
    seen_labels: set[tuple[str, int, int]] = set()
    for x, y, text in ev.iter_labels(msp):
        if not ev._roomish(text):
            continue
        name = ev.norm_name(text)
        if any(token in name for token in ("도로", "부지", "인승", "ELEV", "elev")):
            continue
        key = (name, round(x), round(y))
        if key in seen_labels:
            continue
        seen_labels.add(key)
        labels.append((x, y, text))

    tips = _free_tips()
    chosen: list[dict] = []
    chosen_keys: set[tuple[int, int, int, int]] = set()
    for lx, ly, _text in labels:
        if _face([], lx, ly) is not None:
            continue
        best: tuple[float, list[dict]] | None = None
        for tip, direction, _src in tips:
            if math.hypot(tip[0] - lx, tip[1] - ly) > 7000.0:
                continue
            for segs in _chains_from(tip, direction):
                if any(
                    math.hypot((s["a"][0] + s["b"][0]) * 0.5 - lx, (s["a"][1] + s["b"][1]) * 0.5 - ly) > 8000.0
                    for s in segs
                ):
                    continue
                extra = [(s["a"][0], s["a"][1], s["b"][0], s["b"][1]) for s in segs]
                face = _face(extra, lx, ly)
                if face is None:
                    continue
                minx, miny, maxx, maxy = face.bounds
                if max(maxx - minx, maxy - miny) > 16000.0:
                    continue
                if face.area < 1.5e6 or not _on_boundary(face, segs):
                    continue
                if best is None or face.area < best[0]:
                    best = (face.area, segs)
        if best is None:
            continue
        for seg in best[1]:
            key = (
                round(seg["a"][0]),
                round(seg["a"][1]),
                round(seg["b"][0]),
                round(seg["b"][1]),
            )
            rev = (key[2], key[3], key[0], key[1])
            if key in chosen_keys or rev in chosen_keys:
                continue
            chosen_keys.add(key)
            chosen.append(seg)

    promoted = 0
    for seg in chosen:
        entity = seg["ent"]
        if entity.dxftype() == "LINE" and getattr(entity.dxf, "layer", None) == BASE_LAYER:
            if _paint_layer(entity, WALL_LAYER, WALL_COLOR):
                promoted += 1
            continue
        msp.add_line(
            seg["a"],
            seg["b"],
            dxfattribs={"layer": WALL_LAYER, "color": WALL_COLOR},
        )
        promoted += 1
    return promoted


def promote_line_swing_door_jambs(msp) -> int:
    """ARC 없이 짧은 사선과 양끝 작은 사각으로 그린 여닫이문.

    문짝·사선은 BASE로 둔다. 개구 양쪽으로 이어진 벽면과, 그 끝의 벽 두께
    막이선만 WALL로 올린다. 개구를 가로지르는 면은 올리지 않는다.
    """
    squares: list[tuple[float, float, float, float]] = []
    segs: list[tuple[tuple[float, float], tuple[float, float], float, object]] = []
    for ent in msp:
        kind = ent.dxftype()
        if kind == "LWPOLYLINE" and ent.closed:
            pts = _polyline_xy(ent)
            if len(pts) < 4:
                continue
            xs = [p[0] for p in pts]
            ys = [p[1] for p in pts]
            w, h = max(xs) - min(xs), max(ys) - min(ys)
            if 60.0 <= w <= 170.0 and 60.0 <= h <= 170.0:
                squares.append((min(xs), min(ys), max(xs), max(ys)))
        if kind not in ("LINE", "LWPOLYLINE"):
            continue
        if kind == "LINE":
            pairs = [(
                (float(ent.dxf.start.x), float(ent.dxf.start.y)),
                (float(ent.dxf.end.x), float(ent.dxf.end.y)),
            )]
        else:
            pts = _polyline_xy(ent)
            n = len(pts)
            pairs = [(pts[i], pts[(i + 1) % n]) for i in range(n - (0 if ent.closed else 1))]
        for a, b in pairs:
            length = math.hypot(b[0] - a[0], b[1] - a[1])
            if length < 40.0:
                continue
            segs.append((a, b, length, ent))

    def _near_square(pt: tuple[float, float], tol: float = 50.0) -> bool:
        return any(
            box[0] - tol <= pt[0] <= box[2] + tol and box[1] - tol <= pt[1] <= box[3] + tol
            for box in squares
        )

    def _dist_point_seg(px: float, py: float, a, b) -> float:
        dx, dy = b[0] - a[0], b[1] - a[1]
        length2 = dx * dx + dy * dy
        if length2 < 1.0:
            return math.hypot(px - a[0], py - a[1])
        t = max(0.0, min(1.0, ((px - a[0]) * dx + (py - a[1]) * dy) / length2))
        return math.hypot(px - (a[0] + t * dx), py - (a[1] + t * dy))

    doors: list[tuple[str, float, float, float]] = []
    for i, (x0, y0, x1, y1) in enumerate(squares):
        cx, cy = (x0 + x1) * 0.5, (y0 + y1) * 0.5
        for x2, y2, x3, y3 in squares[i + 1 :]:
            cx2, cy2 = (x2 + x3) * 0.5, (y2 + y3) * 0.5
            vertical = abs(cx - cx2) < 40.0 and 600.0 < abs(cy - cy2) < 1000.0
            horizontal = abs(cy - cy2) < 40.0 and 600.0 < abs(cx - cx2) < 1000.0
            if not vertical and not horizontal:
                continue
            if vertical:
                axis = (cx + cx2) * 0.5
                lo, hi = min(y0, y2), max(y1, y3)
                leaf = None
                for a, b, length, ent in segs:
                    if ent.dxftype() != "LINE":
                        continue
                    if abs(a[0] - b[0]) > 25.0:
                        continue
                    if abs((a[0] + b[0]) * 0.5 - axis) > 40.0:
                        continue
                    if not (_near_square(a) and _near_square(b)):
                        continue
                    if 500.0 <= length <= 900.0:
                        leaf = (a, b)
                        break
            else:
                axis = (cy + cy2) * 0.5
                lo, hi = min(x0, x2), max(x1, x3)
                leaf = None
                for a, b, length, ent in segs:
                    if ent.dxftype() != "LINE":
                        continue
                    if abs(a[1] - b[1]) > 25.0:
                        continue
                    if abs((a[1] + b[1]) * 0.5 - axis) > 40.0:
                        continue
                    if not (_near_square(a) and _near_square(b)):
                        continue
                    if 500.0 <= length <= 900.0:
                        leaf = (a, b)
                        break
            if leaf is None:
                continue
            swing = False
            for a, b, length, _ent in segs:
                dx, dy = abs(b[0] - a[0]), abs(b[1] - a[1])
                if not (80.0 <= length <= 200.0 and dx > 30.0 and dy > 30.0):
                    continue
                if min(_dist_point_seg(a[0], a[1], leaf[0], leaf[1]), _dist_point_seg(b[0], b[1], leaf[0], leaf[1])) <= 180.0:
                    swing = True
                    break
            if not swing:
                continue
            kind = "V" if vertical else "H"
            key = (kind, round(axis), round(lo), round(hi))
            if key in {(d[0], round(d[1]), round(d[2]), round(d[3])) for d in doors}:
                continue
            doors.append((kind, axis, lo, hi, leaf))

    changed = 0
    promoted_pts: list[tuple[float, float]] = []

    def _wall_covers(a, b) -> bool:
        for c, d, length, ent in segs:
            if getattr(ent.dxf, "layer", None) != WALL_LAYER:
                continue
            if length < 40.0:
                continue
            if (
                math.hypot(c[0] - a[0], c[1] - a[1]) < 20.0
                and math.hypot(d[0] - b[0], d[1] - b[1]) < 20.0
            ) or (
                math.hypot(c[0] - b[0], c[1] - b[1]) < 20.0
                and math.hypot(d[0] - a[0], d[1] - a[1]) < 20.0
            ):
                return True
        return False

    for kind, axis, lo, hi, leaf in doors:
        for a, b, length, ent in segs:
            if getattr(ent.dxf, "layer", None) != WALL_LAYER:
                continue
            on_leaf = (
                math.hypot(a[0] - leaf[0][0], a[1] - leaf[0][1]) < 20.0
                and math.hypot(b[0] - leaf[1][0], b[1] - leaf[1][1]) < 20.0
            ) or (
                math.hypot(a[0] - leaf[1][0], a[1] - leaf[1][1]) < 20.0
                and math.hypot(b[0] - leaf[0][0], b[1] - leaf[0][1]) < 20.0
            )
            dx, dy = abs(b[0] - a[0]), abs(b[1] - a[1])
            on_swing = (
                80.0 <= length <= 200.0
                and dx > 30.0
                and dy > 30.0
                and min(
                    _dist_point_seg(a[0], a[1], leaf[0], leaf[1]),
                    _dist_point_seg(b[0], b[1], leaf[0], leaf[1]),
                )
                <= 180.0
            )
            if on_leaf or on_swing:
                if _paint_layer(ent, BASE_LAYER, BASE_COLOR):
                    changed += 1
        for a, b, length, ent in segs:
            if getattr(ent.dxf, "layer", None) != BASE_LAYER:
                continue
            if ent.dxftype() != "LINE":
                continue
            dx, dy = b[0] - a[0], b[1] - a[1]
            if kind == "V":
                if abs(dx) > 25.0:
                    continue
                perp = abs((a[0] + b[0]) * 0.5 - axis)
                c0, c1 = sorted((a[1], b[1]))
            else:
                if abs(dy) > 25.0:
                    continue
                perp = abs((a[1] + b[1]) * 0.5 - axis)
                c0, c1 = sorted((a[0], b[0]))
            if not (50.0 <= perp <= 400.0):
                continue
            if _near_square(a) and _near_square(b):
                continue
            outside = c1 <= lo + 25.0 or c0 >= hi - 25.0
            if not outside:
                continue
            gap = (lo - c1) if c1 <= lo + 25.0 else (c0 - hi)
            continues_face = False
            if gap > 180.0:
                for c, d, _length, _other in segs:
                    if kind == "V":
                        if abs(c[0] - d[0]) > 25.0:
                            continue
                        if abs((c[0] + d[0]) * 0.5 - (a[0] + b[0]) * 0.5) > 35.0:
                            continue
                        o0, o1 = sorted((c[1], d[1]))
                    else:
                        if abs(c[1] - d[1]) > 25.0:
                            continue
                        if abs((c[1] + d[1]) * 0.5 - (a[1] + b[1]) * 0.5) > 35.0:
                            continue
                        o0, o1 = sorted((c[0], d[0]))
                    if o1 <= lo + 40.0 or o0 >= hi - 40.0:
                        continue
                    near = c1 if c1 <= lo else c0
                    if min(abs(near - o0), abs(near - o1)) <= 200.0:
                        continues_face = True
                        break
            if gap > 180.0 and not continues_face:
                continue
            if length > 2200.0 or length < 80.0:
                continue
            if _wall_covers(a, b):
                continue
            if _paint_layer(ent, WALL_LAYER, WALL_COLOR):
                changed += 1
                promoted_pts.extend((a, b))

        for a, b, length, ent in segs:
            if getattr(ent.dxf, "layer", None) != BASE_LAYER:
                continue
            if ent.dxftype() != "LINE":
                continue
            if not (80.0 <= length <= 280.0):
                continue
            dx, dy = b[0] - a[0], b[1] - a[1]
            if kind == "V":
                if abs(dy) > 25.0:
                    continue
                along = (a[1] + b[1]) * 0.5
                end_off = (a[0] - axis, b[0] - axis)
            else:
                if abs(dx) > 25.0:
                    continue
                along = (a[0] + b[0]) * 0.5
                end_off = (a[1] - axis, b[1] - axis)
            if min(abs(along - lo), abs(along - hi)) > 180.0 and not any(
                math.hypot(a[0] - px, a[1] - py) <= 30.0 or math.hypot(b[0] - px, b[1] - py) <= 30.0
                for px, py in promoted_pts
            ):
                continue
            if lo + 25.0 < along < hi - 25.0:
                continue
            if max(abs(end_off[0]), abs(end_off[1])) > 420.0:
                continue
            if abs(end_off[0] - end_off[1]) < 80.0:
                continue
            if _wall_covers(a, b):
                continue
            if _paint_layer(ent, WALL_LAYER, WALL_COLOR):
                changed += 1
    return changed


def promote_thick_wall_mates(msp) -> int:
    """한쪽 면만 벽인 두꺼운 벽(면 간격 약 500 mm)의 맞은편과 끝단을 올린다.

    길이가 같은 평행 쌍만 본다. 그 두께 안에 있고 양끝으로 이어진 짧은 꺾임도 벽이다.
    """
    rows: list[tuple[str, float, float, float, float, object]] = []
    for ent in msp:
        if ent.dxftype() != "LINE":
            continue
        a = (float(ent.dxf.start.x), float(ent.dxf.start.y))
        b = (float(ent.dxf.end.x), float(ent.dxf.end.y))
        dx, dy = abs(b[0] - a[0]), abs(b[1] - a[1])
        length = math.hypot(b[0] - a[0], b[1] - a[1])
        if length < 80.0:
            continue
        if dx <= 25.0:
            rows.append(("V", (a[0] + b[0]) * 0.5, min(a[1], b[1]), max(a[1], b[1]), length, ent))
        elif dy <= 25.0:
            rows.append(("H", (a[1] + b[1]) * 0.5, min(a[0], b[0]), max(a[0], b[0]), length, ent))

    slabs: list[tuple[str, float, float, float, float]] = []
    for kind, axis, c0, c1, length, ent in rows:
        if getattr(ent.dxf, "layer", None) != WALL_LAYER or length < 2500.0:
            continue
        if kind != "V":
            continue
        for kind2, axis2, d0, d1, length2, ent2 in rows:
            if kind2 != kind or getattr(ent2.dxf, "layer", None) != BASE_LAYER:
                continue
            gap = abs(axis2 - axis)
            if not (480.0 <= gap <= 520.0):
                continue
            ov0, ov1 = max(c0, d0), min(c1, d1)
            overlap = ov1 - ov0
            if overlap < 2500.0:
                continue
            if abs(length - length2) > 200.0 or abs((c0 + c1) - (d0 + d1)) > 400.0:
                continue
            lo, hi = (axis, axis2) if axis < axis2 else (axis2, axis)
            slabs.append((kind, lo, hi, ov0, ov1))

    changed = 0
    seen: set[int] = set()
    for kind, lo, hi, ov0, ov1 in slabs:
        along0, along1 = ov0 - 1300.0, ov1 + 1300.0
        for kind2, axis, c0, c1, length, ent in rows:
            if kind2 != kind or getattr(ent.dxf, "layer", None) != BASE_LAYER:
                continue
            if id(ent) in seen:
                continue
            if not (lo - 30.0 <= axis <= hi + 30.0):
                continue
            if c0 < along0 - 5.0 or c1 > along1 + 5.0:
                continue
            if length > 1400.0 and not (abs(c0 - ov0) < 30.0 and abs(c1 - ov1) < 30.0):
                continue
            if _paint_layer(ent, WALL_LAYER, WALL_COLOR):
                seen.add(id(ent))
                changed += 1
        for kind2, axis, c0, c1, length, ent in rows:
            if kind2 == kind or getattr(ent.dxf, "layer", None) != BASE_LAYER:
                continue
            if id(ent) in seen:
                continue
            if not (80.0 <= length <= 1400.0):
                continue
            # perpendicular return: its along-coordinate is the pair axis, its span is between the faces
            if not (along0 - 5.0 <= axis <= along1 + 5.0):
                continue
            if c0 < lo - 30.0 or c1 > hi + 30.0:
                continue
            if _paint_layer(ent, WALL_LAYER, WALL_COLOR):
                seen.add(id(ent))
                changed += 1
    return changed


def correct_inline_swing_doors(msp) -> tuple[int, int]:
    """작은 사각 두 개와 스윙으로 그린 여닫이문.

    문 표시·스윙·벽 두께 안의 문선은 내린다. 그 양옆 벽면은 올린다.
    """
    squares: list[tuple[float, float, float, float, object]] = []
    segs: list[tuple[float, float, float, float, float, object]] = []
    for entity in msp:
        kind = entity.dxftype()
        if kind == "LWPOLYLINE" and entity.closed:
            pts = _polyline_xy(entity)
            if len(pts) >= 4:
                xs = [p[0] for p in pts]
                ys = [p[1] for p in pts]
                width, height = max(xs) - min(xs), max(ys) - min(ys)
                if 60.0 <= width <= 170.0 and 60.0 <= height <= 170.0:
                    squares.append((min(xs), min(ys), max(xs), max(ys), entity))
        if kind == "LINE":
            pairs = [(
                (float(entity.dxf.start.x), float(entity.dxf.start.y)),
                (float(entity.dxf.end.x), float(entity.dxf.end.y)),
            )]
        elif kind == "LWPOLYLINE":
            pts = _polyline_xy(entity)
            n = len(pts)
            pairs = [(pts[i], pts[(i + 1) % n]) for i in range(n - (0 if entity.closed else 1))]
        else:
            continue
        for (x0, y0), (x1, y1) in pairs:
            length = math.hypot(x1 - x0, y1 - y0)
            if length >= 40.0:
                segs.append((x0, y0, x1, y1, length, entity))

    doors: list[tuple[float, float, float, float, bool]] = []
    for i, (ax0, ay0, ax1, ay1, _) in enumerate(squares):
        for bx0, by0, bx1, by1, _ in squares[i + 1 :]:
            gap_x = max(ax0, bx0) - min(ax1, bx1)
            gap_y = max(ay0, by0) - min(ay1, by1)
            overlap_x = min(ax1, bx1) - max(ax0, bx0)
            overlap_y = min(ay1, by1) - max(ay0, by0)
            side_by_side = gap_x < 40.0 and overlap_y > 70.0
            stacked = gap_y < 40.0 and overlap_x > 70.0
            if not side_by_side and not stacked:
                continue
            box = (min(ax0, bx0), min(ay0, by0), max(ax1, bx1), max(ay1, by1))
            vertical_wall = (box[2] - box[0]) >= (box[3] - box[1])
            cx = (box[0] + box[2]) * 0.5
            cy = (box[1] + box[3]) * 0.5
            swing = False
            for x0, y0, x1, y1, length, _ent in segs:
                dx, dy = abs(x1 - x0), abs(y1 - y0)
                if not (80.0 <= length <= 220.0 and dx > 40.0 and dy > 40.0):
                    continue
                mx, my = (x0 + x1) * 0.5, (y0 + y1) * 0.5
                if math.hypot(mx - cx, my - cy) <= 800.0 or min(
                    math.hypot(x0 - cx, y0 - cy), math.hypot(x1 - cx, y1 - cy)
                ) <= 800.0:
                    swing = True
                    break
            if swing:
                doors.append((*box, vertical_wall))

    n_demote = 0
    n_promote = 0
    demoted: set[int] = set()

    def _demote(entity) -> None:
        nonlocal n_demote
        if entity is None or id(entity) in demoted:
            return
        demoted.add(id(entity))
        if _paint_layer(entity, BASE_LAYER, BASE_COLOR):
            n_demote += 1

    def _promote(entity) -> None:
        nonlocal n_promote
        if entity is None or id(entity) in demoted:
            return
        if _paint_layer(entity, WALL_LAYER, WALL_COLOR):
            n_promote += 1

    for x0, y0, x1, y1, vertical_wall in doors:
        cx, cy = (x0 + x1) * 0.5, (y0 + y1) * 0.5
        if vertical_wall:
            ortho0, ortho1 = x0 - 80.0, x1 + 80.0
            along0, along1 = y0, y1
        else:
            ortho0, ortho1 = y0 - 80.0, y1 + 80.0
            along0, along1 = x0, x1

        groups: dict[int, list[tuple]] = {}
        for sx0, sy0, sx1, sy1, length, entity in segs:
            dx, dy = abs(sx1 - sx0), abs(sy1 - sy0)
            if vertical_wall:
                if dx > 25.0 or length < 400.0:
                    continue
                ortho = (sx0 + sx1) * 0.5
                a0, a1 = min(sy0, sy1), max(sy0, sy1)
            else:
                if dy > 25.0 or length < 400.0:
                    continue
                ortho = (sy0 + sy1) * 0.5
                a0, a1 = min(sx0, sx1), max(sx0, sx1)
            if not (ortho0 <= ortho <= ortho1):
                continue
            if min(a1, along1 + 400.0) - max(a0, along0 - 400.0) < 80.0:
                continue
            groups.setdefault(round(ortho / 10.0) * 10, []).append(
                (ortho, a0, a1, length, entity)
            )
        if len(groups) < 2:
            continue
        keys = sorted(groups)
        face_lo, face_hi = keys[0], keys[-1]
        if not (180.0 <= face_hi - face_lo <= 320.0):
            continue
        if not any(item[3] >= 1000.0 for item in groups[face_lo]):
            continue
        if not any(item[3] >= 1000.0 for item in groups[face_hi]):
            continue

        for sx0, sy0, sx1, sy1, length, entity in segs:
            dx, dy = abs(sx1 - sx0), abs(sy1 - sy0)
            mx, my = (sx0 + sx1) * 0.5, (sy0 + sy1) * 0.5
            diag = 80.0 <= length <= 220.0 and dx > 40.0 and dy > 40.0
            near = math.hypot(mx - cx, my - cy) <= 800.0
            if diag and near:
                _demote(entity)
                for tx0, ty0, tx1, ty1, tlen, tent in segs:
                    if tent is entity or not (300.0 <= tlen <= 700.0):
                        continue
                    ends = ((sx0, sy0), (sx1, sy1))
                    if any(
                        min(math.hypot(px - tx0, py - ty0), math.hypot(px - tx1, py - ty1)) <= 40.0
                        for px, py in ends
                    ):
                        _demote(tent)
            if vertical_wall:
                ortho = (sx0 + sx1) * 0.5
                a0, a1 = min(sy0, sy1), max(sy0, sy1)
                parallel = dx <= 25.0
            else:
                ortho = (sy0 + sy1) * 0.5
                a0, a1 = min(sx0, sx1), max(sx0, sx1)
                parallel = dy <= 25.0
            inside = face_lo + 25.0 < ortho < face_hi - 25.0
            if parallel and inside and min(a1, along1 + 500.0) - max(a0, along0 - 500.0) > 200.0:
                _demote(entity)
            if parallel or not (150.0 <= length <= 320.0):
                continue
            if vertical_wall:
                span0, span1 = min(sx0, sx1), max(sx0, sx1)
                along_mid = (sy0 + sy1) * 0.5
                door_along = cy
            else:
                span0, span1 = min(sy0, sy1), max(sy0, sy1)
                along_mid = (sx0 + sx1) * 0.5
                door_along = cx
            if (
                span0 >= face_lo - 30.0
                and span1 <= face_hi + 30.0
                and abs(along_mid - door_along) <= 80.0
            ):
                _demote(entity)

        for sq in squares:
            scx, scy = (sq[0] + sq[2]) * 0.5, (sq[1] + sq[3]) * 0.5
            if x0 - 20.0 <= scx <= x1 + 20.0 and y0 - 20.0 <= scy <= y1 + 20.0:
                _demote(sq[4])

        along_pad0, along_pad1 = along0 - 4000.0, along1 + 4000.0
        for face_key in (face_lo, face_hi):
            for _ortho, a0, a1, length, entity in groups[face_key]:
                if length < 200.0:
                    continue
                if min(a1, along_pad1) - max(a0, along_pad0) < 200.0:
                    continue
                _promote(entity)

        for sx0, sy0, sx1, sy1, length, entity in segs:
            if not (200.0 <= length <= 2000.0):
                continue
            if getattr(entity.dxf, "layer", None) != BASE_LAYER:
                continue
            dx, dy = abs(sx1 - sx0), abs(sy1 - sy0)
            if vertical_wall:
                if dx > 25.0:
                    continue
                ortho = (sx0 + sx1) * 0.5
                a0, a1 = min(sy0, sy1), max(sy0, sy1)
            else:
                if dy > 25.0:
                    continue
                ortho = (sy0 + sy1) * 0.5
                a0, a1 = min(sx0, sx1), max(sx0, sx1)
            if abs(ortho - face_lo) <= 40.0:
                other = face_hi
            elif abs(ortho - face_hi) <= 40.0:
                other = face_lo
            else:
                continue
            gap = along0 - a1 if a1 < along0 else (a0 - along1 if a0 > along1 else 0.0)
            if gap > 1500.0:
                continue
            mate = False
            for tx0, ty0, tx1, ty1, tlen, tent in segs:
                if getattr(tent.dxf, "layer", None) != WALL_LAYER or tlen < 200.0:
                    continue
                tdx, tdy = abs(tx1 - tx0), abs(ty1 - ty0)
                if vertical_wall:
                    if tdx > 25.0 or abs((tx0 + tx1) * 0.5 - other) > 40.0:
                        continue
                    b0, b1 = min(ty0, ty1), max(ty0, ty1)
                else:
                    if tdy > 25.0 or abs((ty0 + ty1) * 0.5 - other) > 40.0:
                        continue
                    b0, b1 = min(tx0, tx1), max(tx0, tx1)
                if min(a1, b1) - max(a0, b0) >= 0.7 * length:
                    mate = True
                    break
            if mate:
                _promote(entity)

        host_spans = [item for item in groups[face_lo] + groups[face_hi] if item[3] >= 1000.0]
        if host_spans:
            host0 = min(item[1] for item in host_spans)
            host1 = max(item[2] for item in host_spans)
            for sx0, sy0, sx1, sy1, length, entity in segs:
                if not (400.0 <= length <= 1600.0):
                    continue
                if getattr(entity.dxf, "layer", None) != BASE_LAYER:
                    continue
                dx, dy = abs(sx1 - sx0), abs(sy1 - sy0)
                if vertical_wall:
                    if dx > 25.0:
                        continue
                    ortho = (sx0 + sx1) * 0.5
                    a0, a1 = min(sy0, sy1), max(sy0, sy1)
                else:
                    if dy > 25.0:
                        continue
                    ortho = (sy0 + sy1) * 0.5
                    a0, a1 = min(sx0, sx1), max(sx0, sx1)
                if not (face_lo - 80.0 <= ortho <= face_hi + 220.0):
                    continue
                if min(a1, host1) - max(a0, host0) > 80.0:
                    continue
                if a1 <= host0:
                    gap = host0 - a1
                elif a0 >= host1:
                    gap = a0 - host1
                else:
                    continue
                if gap > 250.0:
                    continue
                _promote(entity)

    return n_demote, n_promote


def promote_leaf_side_walls(msp) -> int:
    """얇은 여닫이문짝 양끝에서 같은 면으로 이어진 벽선을 올린다.

    문짝과 겹치는 선은 두지 않는다.
    """
    leaves: list[tuple[float, float, float, bool, object]] = []
    segs: list[tuple[float, float, float, float, float, str, object]] = []
    for entity in msp:
        kind = entity.dxftype()
        layer = getattr(entity.dxf, "layer", None)
        if kind == "LWPOLYLINE" and entity.closed:
            pts = _polyline_xy(entity)
            if len(pts) >= 4:
                xs = [p[0] for p in pts]
                ys = [p[1] for p in pts]
                width, height = max(xs) - min(xs), max(ys) - min(ys)
                short, long = min(width, height), max(width, height)
                if 20.0 <= short <= 80.0 and 650.0 <= long <= 1450.0:
                    horizontal = width >= height
                    along0, along1 = (min(xs), max(xs)) if horizontal else (min(ys), max(ys))
                    ortho = (min(ys) + max(ys)) / 2.0 if horizontal else (min(xs) + max(xs)) / 2.0
                    leaves.append((along0, along1, ortho, horizontal, entity))
        if kind != "LINE" or layer not in (WALL_LAYER, BASE_LAYER):
            continue
        x0, y0 = float(entity.dxf.start.x), float(entity.dxf.start.y)
        x1, y1 = float(entity.dxf.end.x), float(entity.dxf.end.y)
        length = math.hypot(x1 - x0, y1 - y0)
        if length >= 200.0:
            segs.append((x0, y0, x1, y1, length, layer, entity))

    promoted = 0
    for along0, along1, ortho, horizontal, leaf in leaves:
        _paint_layer(leaf, BASE_LAYER, BASE_COLOR)

        def _axis(x0, y0, x1, y1):
            if horizontal:
                if abs(y1 - y0) > 25.0:
                    return None
                return (y0 + y1) / 2.0, min(x0, x1), max(x0, x1)
            if abs(x1 - x0) > 25.0:
                return None
            return (x0 + x1) / 2.0, min(y0, y1), max(y0, y1)

        face_orthos: list[float] = []
        for x0, y0, x1, y1, length, layer, _ent in segs:
            if layer != WALL_LAYER:
                continue
            axis = _axis(x0, y0, x1, y1)
            if axis is None:
                continue
            face, a0, a1 = axis
            if not (40.0 <= abs(face - ortho) <= 250.0):
                continue
            if min(a1, along1) - max(a0, along0) > 80.0:
                continue
            gap = min(abs(a1 - along0), abs(a0 - along1), abs(a0 - along0), abs(a1 - along1))
            if gap <= 200.0:
                face_orthos.append(face)
        if not face_orthos:
            continue
        for x0, y0, x1, y1, length, layer, entity in segs:
            if layer != BASE_LAYER or not (400.0 <= length <= 2500.0):
                continue
            axis = _axis(x0, y0, x1, y1)
            if axis is None:
                continue
            face, a0, a1 = axis
            if not any(abs(face - known) <= 30.0 for known in face_orthos):
                continue
            if min(a1, along1) - max(a0, along0) > 80.0:
                continue
            if a0 >= along1 - 80.0 and a1 > along1:
                gap = a0 - along1
            elif a1 <= along0 + 80.0 and a0 < along0:
                gap = along0 - a1
            else:
                continue
            if gap > 200.0:
                continue
            if _paint_layer(entity, WALL_LAYER, WALL_COLOR):
                promoted += 1
    return promoted


def promote_projection_junctions(msp) -> int:
    """돌출벽이 본벽에서 벽 두께만큼 어긋나 이어지면 그 이음을 WALL로 올린다.

    회색 선이 긴 벽 두 면과 같은 직선이고, 한쪽은 맞닿고 다른 쪽은
    80–350 mm 비어 있으면 그 선은 돌출벽과 만나는 벽이다.
    같은 모서리에서 200–450 mm 떨어진 평행한 다른 면도 같이 올린다.
    """
    walls: list[tuple[tuple[float, float], tuple[float, float], float]] = []
    bases: list[tuple[object, tuple[float, float], tuple[float, float], float]] = []
    for entity in msp:
        layer = entity.dxf.layer
        if layer not in (WALL_LAYER, BASE_LAYER):
            continue
        if entity.dxftype() == "LINE":
            pairs = [
                (
                    (float(entity.dxf.start.x), float(entity.dxf.start.y)),
                    (float(entity.dxf.end.x), float(entity.dxf.end.y)),
                )
            ]
        elif entity.dxftype() == "LWPOLYLINE":
            pts = _polyline_xy(entity)
            span = len(pts) if entity.closed else len(pts) - 1
            pairs = [
                (pts[i], pts[(i + 1) % len(pts)])
                for i in range(span)
            ]
        else:
            continue
        for a, b in pairs:
            length = math.hypot(b[0] - a[0], b[1] - a[1])
            if length < 40.0:
                continue
            if layer == WALL_LAYER:
                walls.append((a, b, length))
            else:
                bases.append((entity, a, b, length))

    def _on_wall(px: float, py: float, tol: float = 50.0) -> bool:
        for (x0, y0), (x1, y1), _length in walls:
            vx, vy = x1 - x0, y1 - y0
            span2 = vx * vx + vy * vy
            if span2 <= 1.0:
                continue
            t = max(0.0, min(1.0, ((px - x0) * vx + (py - y0) * vy) / span2))
            if math.hypot(px - (x0 + t * vx), py - (y0 + t * vy)) <= tol:
                return True
        return False

    changed = 0
    painted: set[int] = set()
    for entity, a, b, length in bases:
        if not (400.0 <= length <= 1200.0):
            continue
        dx, dy = b[0] - a[0], b[1] - a[1]
        ux, uy = dx / length, dy / length
        nx, ny = -uy, ux
        left_gap = None
        right_gap = None
        for (x0, y0), (x1, y1), wall_length in walls:
            if wall_length < 600.0:
                continue
            ex, ey = x1 - x0, y1 - y0
            wall_span = math.hypot(ex, ey)
            if abs(ex / wall_span * ux + ey / wall_span * uy) < 0.99:
                continue
            if abs((x0 - a[0]) * nx + (y0 - a[1]) * ny) > 35.0:
                continue
            w0 = (x0 - a[0]) * ux + (y0 - a[1]) * uy
            w1 = (x1 - a[0]) * ux + (y1 - a[1]) * uy
            w0, w1 = (w0, w1) if w0 <= w1 else (w1, w0)
            if w1 <= 40.0:
                gap = -w1
                if left_gap is None or gap < left_gap:
                    left_gap = gap
            elif w0 >= length - 40.0:
                gap = w0 - length
                if right_gap is None or gap < right_gap:
                    right_gap = gap
        if left_gap is None or right_gap is None:
            continue
        near_gap, far_gap = sorted((left_gap, right_gap))
        if near_gap > 40.0 or not (80.0 <= far_gap <= 350.0):
            continue
        gapped = a if left_gap == far_gap else b
        if id(entity) not in painted and _paint_layer(entity, WALL_LAYER, WALL_COLOR):
            painted.add(id(entity))
            changed += 1
        # 같은 모서리에서 벽 두께만큼 떨어진 다른 면
        for mate, ma, mb, mate_length in bases:
            if not (200.0 <= mate_length <= 900.0):
                continue
            mex, mey = mb[0] - ma[0], mb[1] - ma[1]
            mate_span = math.hypot(mex, mey)
            if abs(mex / mate_span * ux + mey / mate_span * uy) < 0.99:
                continue
            offset = abs((ma[0] - a[0]) * nx + (ma[1] - a[1]) * ny)
            if not (200.0 <= offset <= 450.0):
                continue
            ends = (ma, mb)
            if not any(_on_wall(*end) for end in ends):
                continue
            touching = ma if _on_wall(*ma) else mb
            if math.hypot(touching[0] - gapped[0], touching[1] - gapped[1]) > 500.0:
                continue
            if id(mate) not in painted and _paint_layer(mate, WALL_LAYER, WALL_COLOR):
                painted.add(id(mate))
                changed += 1
    return changed


def promote_shifted_corner_faces(msp) -> int:
    """빨간 벽면과 같은 길이인데 모서리에서 벽 두께만큼 어긋난 회색 면을 올린다.

    침실 모서리처럼 한 면만 빨강이고, 나란한 면이 양끝에서 80–320 mm
    밀려 있으면 그 회색 면도 벽이다.
    """
    walls: list[tuple[tuple[float, float], tuple[float, float], float]] = []
    bases: list[tuple[object, tuple[float, float], tuple[float, float], float]] = []
    for entity in msp:
        layer = entity.dxf.layer
        if layer not in (WALL_LAYER, BASE_LAYER):
            continue
        if entity.dxftype() == "LINE":
            pairs = [
                (
                    (float(entity.dxf.start.x), float(entity.dxf.start.y)),
                    (float(entity.dxf.end.x), float(entity.dxf.end.y)),
                )
            ]
        elif entity.dxftype() == "LWPOLYLINE":
            pts = _polyline_xy(entity)
            span = len(pts) if entity.closed else len(pts) - 1
            pairs = [(pts[i], pts[(i + 1) % len(pts)]) for i in range(span)]
        else:
            continue
        for a, b in pairs:
            length = math.hypot(b[0] - a[0], b[1] - a[1])
            if length < 40.0:
                continue
            if layer == WALL_LAYER:
                walls.append((a, b, length))
            else:
                bases.append((entity, a, b, length))

    def _covered(a, b, length) -> bool:
        for c, d, wall_length in walls:
            if abs(wall_length - length) > 40.0:
                continue
            if (
                math.hypot(c[0] - a[0], c[1] - a[1]) < 25.0
                and math.hypot(d[0] - b[0], d[1] - b[1]) < 25.0
            ):
                return True
            if (
                math.hypot(c[0] - b[0], c[1] - b[1]) < 25.0
                and math.hypot(d[0] - a[0], d[1] - a[1]) < 25.0
            ):
                return True
        return False

    def _near_long_wall(px, py, ux, uy) -> bool:
        for (x0, y0), (x1, y1), wall_length in walls:
            if wall_length < 3000.0:
                continue
            ex, ey = x1 - x0, y1 - y0
            span = math.hypot(ex, ey) or 1.0
            if abs(ex / span * ux + ey / span * uy) > 0.3:
                continue
            vx, vy = ex, ey
            span2 = vx * vx + vy * vy
            t = max(0.0, min(1.0, ((px - x0) * vx + (py - y0) * vy) / span2))
            if math.hypot(px - (x0 + t * vx), py - (y0 + t * vy)) <= 220.0:
                return True
        return False

    changed = 0
    painted: set[int] = set()
    for entity, a, b, length in bases:
        if not (1500.0 <= length <= 4000.0) or _covered(a, b, length):
            continue
        ux, uy = (b[0] - a[0]) / length, (b[1] - a[1]) / length
        nx, ny = -uy, ux
        matched = False
        for (x0, y0), (x1, y1), wall_length in walls:
            if abs(wall_length - length) > 0.15 * max(length, wall_length):
                continue
            ex, ey = (x1 - x0) / wall_length, (y1 - y0) / wall_length
            if abs(ex * ux + ey * uy) < 0.995:
                continue
            offset = abs((x0 - a[0]) * nx + (y0 - a[1]) * ny)
            if not (80.0 <= offset <= 320.0):
                continue

            def _along(px: float, py: float) -> float:
                return (px - a[0]) * ux + (py - a[1]) * uy

            w0, w1 = sorted((_along(x0, y0), _along(x1, y1)))
            shift_start, shift_end = w0, w1 - length
            if abs(shift_start - shift_end) > 80.0:
                continue
            if not (60.0 <= abs(shift_start) <= 350.0):
                continue
            matched = True
            break
        if not matched:
            continue
        if not (_near_long_wall(*a, ux, uy) or _near_long_wall(*b, ux, uy)):
            continue
        if id(entity) not in painted and _paint_layer(entity, WALL_LAYER, WALL_COLOR):
            painted.add(id(entity))
            changed += 1
    return changed


def promote_bay_window_outlines(msp) -> int:
    """45° 볼살과 바깥면으로 된 돌출창 윤곽을 WALL로 올린다."""
    changed = 0
    for entity in msp:
        if entity.dxftype() != "LWPOLYLINE":
            continue
        pts = _polyline_xy(entity)
        if len(pts) < 4:
            continue
        segs = []
        span = len(pts) if entity.closed else len(pts) - 1
        for i in range(span):
            a, b = pts[i], pts[(i + 1) % len(pts)]
            length = math.hypot(b[0] - a[0], b[1] - a[1])
            if length < 80.0:
                continue
            angle = math.degrees(math.atan2(b[1] - a[1], b[0] - a[0])) % 180.0
            segs.append((a, b, length, angle))
        hit = False
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

            def _near(angle: float, target: float) -> bool:
                delta = abs(angle - target) % 180.0
                return min(delta, 180.0 - delta) < 12.0

            axis = min(angle1, abs(angle1 - 90.0), abs(angle1 - 180.0)) < 8.0
            if not axis or _near(angle0, 45.0) == _near(angle2, 45.0):
                continue
            if not (
                (_near(angle0, 45.0) or _near(angle0, 135.0))
                and (_near(angle2, 45.0) or _near(angle2, 135.0))
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
                hit = True
                break
        if hit and _paint_layer(entity, WALL_LAYER, WALL_COLOR):
            changed += 1
    return changed


def demote_thin_swing_leaf_lines(msp) -> int:
    """두께 20–80 mm 여닫이문 잎과 그에 겹친 평행선은 WALL에서 뺀다.

    문끝에 붙은 짧은 벽(60–800 mm)은 그대로 둔다.
    """
    leaves: list[dict] = []
    for entity in msp:
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
                "entity": entity,
                "vertical": vertical,
                "along0": min(ys) if vertical else min(xs),
                "along1": max(ys) if vertical else max(xs),
                "center": (min(xs) + max(xs)) / 2 if vertical else (min(ys) + max(ys)) / 2,
            }
        )
    if not leaves:
        return 0

    def _overlap(a0: float, a1: float, b0: float, b1: float) -> float:
        return max(0.0, min(a1, b1) - max(a0, b0))

    changed = 0
    seen: set[int] = set()
    for leaf in leaves:
        ent = leaf["entity"]
        if id(ent) not in seen and _paint_layer(ent, BASE_LAYER, BASE_COLOR):
            seen.add(id(ent))
            changed += 1
    for entity in msp:
        if id(entity) in seen or entity.dxftype() != "LINE":
            continue
        if getattr(entity.dxf, "layer", None) != WALL_LAYER:
            continue
        x0, y0 = float(entity.dxf.start.x), float(entity.dxf.start.y)
        x1, y1 = float(entity.dxf.end.x), float(entity.dxf.end.y)
        length = math.hypot(x1 - x0, y1 - y0)
        if not (600.0 <= length <= 1600.0):
            continue
        vertical = abs(x1 - x0) <= abs(y1 - y0)
        for leaf in leaves:
            if leaf["vertical"] != vertical:
                continue
            if vertical:
                dist = abs((x0 + x1) / 2 - leaf["center"])
                overlap = _overlap(min(y0, y1), max(y0, y1), leaf["along0"] - 200.0, leaf["along1"] + 1400.0)
            else:
                dist = abs((y0 + y1) / 2 - leaf["center"])
                overlap = _overlap(min(x0, x1), max(x0, x1), leaf["along0"] - 200.0, leaf["along1"] + 1400.0)
            if dist <= 180.0 and overlap >= 0.7 * length:
                if _paint_layer(entity, BASE_LAYER, BASE_COLOR):
                    seen.add(id(entity))
                    changed += 1
                break
    return changed


def promote_aligned_door_mates(msp) -> int:
    """문 옆에서 한쪽 면만 벽인 이중선을 맞춘다.

    길이·끝이 같은 빨간 면이 있으면 회색 면을 올린다. 문짝은 끝이 달라 제외된다.
    """
    squares: list[tuple[float, float, float, float]] = []
    doors: list[tuple[float, float]] = []
    lines: list[tuple[str, float, float, float, float, str, object]] = []
    for entity in msp:
        kind = entity.dxftype()
        layer = getattr(entity.dxf, "layer", None)
        if kind == "LWPOLYLINE" and entity.closed:
            pts = _polyline_xy(entity)
            if len(pts) >= 4:
                xs = [p[0] for p in pts]
                ys = [p[1] for p in pts]
                width, height = max(xs) - min(xs), max(ys) - min(ys)
                short, long = min(width, height), max(width, height)
                if 60.0 <= width <= 170.0 and 60.0 <= height <= 170.0:
                    squares.append((min(xs), min(ys), max(xs), max(ys)))
                if 20.0 <= short <= 80.0 and 650.0 <= long <= 1450.0:
                    doors.append(((min(xs) + max(xs)) / 2.0, (min(ys) + max(ys)) / 2.0))
        if kind != "LINE" or layer not in (WALL_LAYER, BASE_LAYER):
            continue
        x0, y0 = float(entity.dxf.start.x), float(entity.dxf.start.y)
        x1, y1 = float(entity.dxf.end.x), float(entity.dxf.end.y)
        length = math.hypot(x1 - x0, y1 - y0)
        if length < 200.0 or length > 2500.0:
            continue
        if abs(x1 - x0) <= 25.0:
            lines.append(("V", (x0 + x1) / 2.0, min(y0, y1), max(y0, y1), length, layer, entity))
        elif abs(y1 - y0) <= 25.0:
            lines.append(("H", (y0 + y1) / 2.0, min(x0, x1), max(x0, x1), length, layer, entity))

    for i, (ax0, ay0, ax1, ay1) in enumerate(squares):
        for bx0, by0, bx1, by1 in squares[i + 1 :]:
            gap_x = max(ax0, bx0) - min(ax1, bx1)
            gap_y = max(ay0, by0) - min(ay1, by1)
            overlap_x = min(ax1, bx1) - max(ax0, bx0)
            overlap_y = min(ay1, by1) - max(ay0, by0)
            if (gap_x < 40.0 and overlap_y > 70.0) or (gap_y < 40.0 and overlap_x > 70.0):
                doors.append((
                    (min(ax0, bx0) + max(ax1, bx1)) / 2.0,
                    (min(ay0, by0) + max(ay1, by1)) / 2.0,
                ))
    if not doors:
        return 0

    promoted = 0
    seen: set[int] = set()
    for ori, ortho, a0, a1, length, layer, entity in lines:
        if layer != BASE_LAYER or id(entity) in seen:
            continue
        mid = ((a0 + a1) / 2.0, ortho) if ori == "H" else (ortho, (a0 + a1) / 2.0)
        if not any(math.hypot(mid[0] - dx, mid[1] - dy) <= 3000.0 for dx, dy in doors):
            continue
        mate = False
        for ori2, ortho2, b0, b1, _length2, layer2, _ent2 in lines:
            if layer2 != WALL_LAYER or ori2 != ori:
                continue
            dist = abs(ortho2 - ortho)
            if not (180.0 <= dist <= 280.0):
                continue
            if abs(a0 - b0) > 80.0 or abs(a1 - b1) > 80.0:
                continue
            mate = True
            break
        if mate and _paint_layer(entity, WALL_LAYER, WALL_COLOR):
            seen.add(id(entity))
            promoted += 1
    return promoted


def promote_leaf_jambs(msp) -> int:
    """여닫이문 위·아래(또는 좌·우)에서 방 벽까지 이어진 문틀을 올린다.

    문짝을 가로지르는 선, 두 벽면 사이의 문선은 두지 않는다.
    """
    leaves: list[tuple[float, float, float, bool]] = []
    records: list[dict] = []
    polylines: list[tuple[object, list[tuple[float, float]]]] = []
    for entity in msp:
        kind = entity.dxftype()
        layer = getattr(entity.dxf, "layer", None)
        if kind == "LWPOLYLINE":
            pts = _polyline_xy(entity)
            if len(pts) >= 2:
                if entity.closed and len(pts) >= 4:
                    xs = [p[0] for p in pts]
                    ys = [p[1] for p in pts]
                    width, height = max(xs) - min(xs), max(ys) - min(ys)
                    short, long = min(width, height), max(width, height)
                    if 20.0 <= short <= 80.0 and 650.0 <= long <= 1450.0:
                        horizontal = width >= height
                        along0, along1 = (min(xs), max(xs)) if horizontal else (min(ys), max(ys))
                        ortho = (min(ys) + max(ys)) / 2.0 if horizontal else (min(xs) + max(xs)) / 2.0
                        leaves.append((along0, along1, ortho, horizontal))
                elif not entity.closed:
                    polylines.append((entity, pts))
        if kind != "LINE" or layer not in (WALL_LAYER, BASE_LAYER):
            continue
        x0, y0 = float(entity.dxf.start.x), float(entity.dxf.start.y)
        x1, y1 = float(entity.dxf.end.x), float(entity.dxf.end.y)
        length = math.hypot(x1 - x0, y1 - y0)
        if length < 40.0:
            continue
        if abs(x1 - x0) <= 25.0:
            records.append({
                "ori": "V", "ortho": (x0 + x1) / 2.0,
                "a0": min(y0, y1), "a1": max(y0, y1),
                "length": length, "layer": layer, "entity": entity,
            })
        elif abs(y1 - y0) <= 25.0:
            records.append({
                "ori": "H", "ortho": (y0 + y1) / 2.0,
                "a0": min(x0, x1), "a1": max(x0, x1),
                "length": length, "layer": layer, "entity": entity,
            })
    if not leaves:
        return 0

    def _overlap(a0: float, a1: float, b0: float, b1: float) -> float:
        return max(0.0, min(a1, b1) - max(a0, b0))

    def _between_faces(ori: str, face: float, a0: float, a1: float) -> bool:
        left = right = False
        for rec in records:
            if rec["ori"] != ori or rec["layer"] != WALL_LAYER or rec["length"] < 400.0:
                continue
            if abs(rec["ortho"] - face) > 400.0:
                continue
            if _overlap(a0, a1, rec["a0"], rec["a1"]) < 200.0:
                continue
            if rec["ortho"] < face - 40.0:
                left = True
            elif rec["ortho"] > face + 40.0:
                right = True
        return left and right

    def _meets_long_wall(ori: str, face: float, far_along: float) -> bool:
        cross = "H" if ori == "V" else "V"
        for rec in records:
            if rec["ori"] != cross or rec["layer"] != WALL_LAYER or rec["length"] < 1500.0:
                continue
            if rec["a0"] - 80.0 <= face <= rec["a1"] + 80.0 and abs(far_along - rec["ortho"]) <= 80.0:
                return True
        return False

    def _is_jamb(ori: str, face: float, a0: float, a1: float, length: float, along0: float, along1: float, center: float) -> bool:
        if not (400.0 <= length <= 2200.0):
            return False
        if abs(face - center) > 340.0:
            return False
        if _overlap(a0, a1, along0, along1) > 80.0:
            return False
        if a0 >= along1 - 80.0 and a1 > along1:
            gap, far_along = a0 - along1, a1
        elif a1 <= along0 + 80.0 and a0 < along0:
            gap, far_along = along0 - a1, a0
        else:
            return False
        if gap > 80.0:
            return False
        if _between_faces(ori, face, a0, a1):
            return False
        if _meets_long_wall(ori, face, far_along):
            return True
        # 긴 벽에 닿지 않아도, 문짝 양옆 40–250 mm 면은 벽이다.
        return 40.0 <= abs(face - center) <= 250.0

    promoted = 0
    seen: set[int] = set()
    for along0, along1, center, horizontal in leaves:
        want = "H" if horizontal else "V"
        for rec in records:
            if rec["ori"] != want or rec["layer"] != BASE_LAYER or id(rec["entity"]) in seen:
                continue
            if not _is_jamb(rec["ori"], rec["ortho"], rec["a0"], rec["a1"], rec["length"], along0, along1, center):
                continue
            if _paint_layer(rec["entity"], WALL_LAYER, WALL_COLOR):
                seen.add(id(rec["entity"]))
                promoted += 1
        for entity, pts in polylines:
            if id(entity) in seen or getattr(entity.dxf, "layer", None) != BASE_LAYER:
                continue
            matched = False
            crosses = False
            for i in range(len(pts) - 1):
                x0, y0 = pts[i]
                x1, y1 = pts[i + 1]
                length = math.hypot(x1 - x0, y1 - y0)
                if abs(x1 - x0) <= 25.0:
                    ori, face, a0, a1 = "V", (x0 + x1) / 2.0, min(y0, y1), max(y0, y1)
                elif abs(y1 - y0) <= 25.0:
                    ori, face, a0, a1 = "H", (y0 + y1) / 2.0, min(x0, x1), max(x0, x1)
                else:
                    continue
                if ori == want and _overlap(a0, a1, along0, along1) > 80.0 and abs(face - center) <= 340.0:
                    crosses = True
                if ori == want and _is_jamb(ori, face, a0, a1, length, along0, along1, center):
                    matched = True
            if matched and not crosses and _paint_layer(entity, WALL_LAYER, WALL_COLOR):
                seen.add(id(entity))
                promoted += 1
    return promoted


def promote_swing_side_walls(msp) -> int:
    """스윙문에서 한쪽만 벽인 문틀과, 문 바깥으로 이어진 벽면을 올린다.

    문짝이 지나가는 구간은 두지 않는다.
    """
    arcs: list[tuple[float, float, float]] = []
    records: list[dict] = []
    for entity in msp:
        kind = entity.dxftype()
        layer = getattr(entity.dxf, "layer", None)
        if kind == "ARC":
            sweep = abs(float(entity.dxf.end_angle) - float(entity.dxf.start_angle)) % 360.0
            sweep = min(sweep, 360.0 - sweep)
            radius = float(entity.dxf.radius)
            if 70.0 <= sweep <= 110.0 and 500.0 <= radius <= 1500.0:
                center = entity.dxf.center
                arcs.append((float(center.x), float(center.y), radius))
        if kind != "LINE" or layer not in (WALL_LAYER, BASE_LAYER):
            continue
        x0, y0 = float(entity.dxf.start.x), float(entity.dxf.start.y)
        x1, y1 = float(entity.dxf.end.x), float(entity.dxf.end.y)
        length = math.hypot(x1 - x0, y1 - y0)
        if length < 80.0:
            continue
        if abs(x1 - x0) <= 25.0:
            records.append({
                "ori": "V", "ortho": (x0 + x1) / 2.0,
                "a0": min(y0, y1), "a1": max(y0, y1),
                "length": length, "layer": layer, "entity": entity,
            })
        elif abs(y1 - y0) <= 25.0:
            records.append({
                "ori": "H", "ortho": (y0 + y1) / 2.0,
                "a0": min(x0, x1), "a1": max(x0, x1),
                "length": length, "layer": layer, "entity": entity,
            })
    if not arcs:
        return 0

    def _near_arc(x: float, y: float, limit: float) -> tuple[float, float, float] | None:
        best = None
        best_d = limit
        for cx, cy, radius in arcs:
            dist = math.hypot(x - cx, y - cy)
            if dist < best_d:
                best = (cx, cy, radius)
                best_d = dist
        return best

    promoted = 0
    seen: set[int] = set()
    for rec in records:
        if rec["layer"] != BASE_LAYER or id(rec["entity"]) in seen:
            continue
        if not (100.0 <= rec["length"] <= 600.0):
            continue
        mid_along = (rec["a0"] + rec["a1"]) / 2.0
        mirrored = False
        for other in records:
            if other["layer"] != WALL_LAYER or other["ori"] != rec["ori"]:
                continue
            if abs(other["length"] - rec["length"]) > 30.0:
                continue
            if abs(other["a0"] - rec["a0"]) > 40.0 or abs(other["a1"] - rec["a1"]) > 40.0:
                continue
            dist = abs(other["ortho"] - rec["ortho"])
            if not (700.0 <= dist <= 1400.0):
                continue
            if rec["ori"] == "V":
                mid = ((rec["ortho"] + other["ortho"]) / 2.0, mid_along)
            else:
                mid = (mid_along, (rec["ortho"] + other["ortho"]) / 2.0)
            if _near_arc(mid[0], mid[1], 1600.0) is not None:
                mirrored = True
                break
        if not mirrored:
            continue
        if _paint_layer(rec["entity"], WALL_LAYER, WALL_COLOR):
            seen.add(id(rec["entity"]))
            promoted += 1

    for rec in records:
        if rec["layer"] != BASE_LAYER or id(rec["entity"]) in seen:
            continue
        if not (200.0 <= rec["length"] <= 1500.0):
            continue
        joined = False
        for other in records:
            if other["layer"] != WALL_LAYER or other["ori"] != rec["ori"]:
                continue
            if abs(other["ortho"] - rec["ortho"]) > 30.0:
                continue
            gap = max(other["a0"], rec["a0"]) - min(other["a1"], rec["a1"])
            if gap > 40.0:
                continue
            join = rec["a0"] if abs(rec["a0"] - other["a1"]) <= abs(rec["a1"] - other["a0"]) else rec["a1"]
            joined = True
            break
        if not joined:
            continue
        # 문짝이 지나가는 선은 힌지와의 거리가 가깝다.
        if rec["ori"] == "H":
            cx_hit = _near_arc((rec["a0"] + rec["a1"]) / 2.0, rec["ortho"], 2000.0)
        else:
            cx_hit = _near_arc(rec["ortho"], (rec["a0"] + rec["a1"]) / 2.0, 2000.0)
        if cx_hit is None:
            continue
        hx, hy, _radius = cx_hit
        if rec["ori"] == "H":
            if rec["a0"] <= hx <= rec["a1"]:
                closest = abs(hy - rec["ortho"])
            else:
                closest = min(math.hypot(rec["a0"] - hx, rec["ortho"] - hy), math.hypot(rec["a1"] - hx, rec["ortho"] - hy))
        else:
            if rec["a0"] <= hy <= rec["a1"]:
                closest = abs(hx - rec["ortho"])
            else:
                closest = min(math.hypot(rec["ortho"] - hx, rec["a0"] - hy), math.hypot(rec["ortho"] - hx, rec["a1"] - hy))
        if not (250.0 <= closest <= 1500.0):
            continue
        if _paint_layer(rec["entity"], WALL_LAYER, WALL_COLOR):
            seen.add(id(rec["entity"]))
            promoted += 1
    return promoted


def add_swing_leaf_walls(msp) -> int:
    """스윙 힌지 문짝은 벽으로 올리지 않는다.

    방 면적은 문짝이 아니라 스윙 반대편 안쪽 벽면으로 닫는다.
    힌지에 붙은 문짝을 빨강으로 두면 그 선이 실 경계가 된다.
    """
    return 0


def promote_short_connected_doubles(msp) -> int:
    """짧은 이중선이라도 벽 끝에서 같은 두께로 이어지면 벽으로 올린다.

    면 간격 80–350 mm, 길이 180–1700 mm. 두 면이 각각 이미 빨간 벽의
    끝에서 꺾이거나 이어지고, 그 벽 두 면의 간격과 같으면 올린다.
    두 면을 닫는 짧은 막이선도 같이 올린다.
    간격 40–80 mm는 같은 방향으로 이어진 짧은 면이 있을 때, 또는
    두 면이 모두 같은 두께의 벽과 한 줄로 이어진 500–1000 mm 칸일 때, 또는
    벽 끝에서 직각으로 꺾인 다리(300–500 mm)가 다른 벽 끝까지 이어질 때 올린다.
    """
    segs = iter_axis_segs(msp, min_len_mm=80.0)
    walls = [s for s in segs if s.layer == WALL_LAYER and s.length >= 800.0]
    bases = [
        s
        for s in segs
        if s.layer == BASE_LAYER and 180.0 <= s.length <= 1700.0 and not _is_stair_tread_seg(s, segs)
    ]
    caps = [
        s
        for s in segs
        if s.layer == BASE_LAYER and 60.0 <= s.length <= 430.0 and not _is_stair_tread_seg(s, segs)
    ]
    if not walls or not bases:
        return 0

    def _near_end(px: float, py: float, wall: AxisSeg, tol: float = 100.0) -> bool:
        return min(
            math.hypot(px - wall.x0, py - wall.y0),
            math.hypot(px - wall.x1, py - wall.y1),
        ) <= tol

    def _joined_walls(seg: AxisSeg) -> list[AxisSeg]:
        found: list[AxisSeg] = []
        ends = ((seg.x0, seg.y0), (seg.x1, seg.y1))
        for wall in walls:
            if seg.is_h == wall.is_h and abs(seg.ortho - wall.ortho) > 50.0:
                continue
            if any(_near_end(px, py, wall) for px, py in ends):
                found.append(wall)
        return found

    chosen: list[AxisSeg] = []
    seen_seg: set[tuple[float, float, float, float]] = set()

    def _key(seg: AxisSeg) -> tuple[float, float, float, float]:
        return (round(seg.x0, 1), round(seg.y0, 1), round(seg.x1, 1), round(seg.y1, 1))

    def _take(seg: AxisSeg) -> None:
        key = _key(seg)
        if key not in seen_seg:
            seen_seg.add(key)
            chosen.append(seg)

    for i, left in enumerate(bases):
        left_walls = _joined_walls(left)
        if not left_walls:
            continue
        for right in bases[i + 1 :]:
            if left.is_h != right.is_h:
                continue
            gap = abs(left.ortho - right.ortho)
            if not (80.0 <= gap <= 350.0):
                continue
            overlap = min(left.along1, right.along1) - max(left.along0, right.along0)
            shorter = min(left.length, right.length)
            if overlap < 0.65 * shorter:
                continue
            if abs(left.length - right.length) > max(400.0, 0.6 * shorter):
                continue
            right_walls = _joined_walls(right)
            if not right_walls:
                continue
            matched = False
            for wall_a in left_walls:
                for wall_b in right_walls:
                    if wall_a.is_h != wall_b.is_h:
                        continue
                    if abs(abs(wall_a.ortho - wall_b.ortho) - gap) > 60.0:
                        continue
                    matched = True
                    break
                if matched:
                    break
            if not matched:
                continue
            _take(left)
            _take(right)
            far_ends: list[tuple[float, float]] = []
            for seg in (left, right):
                ends = ((seg.x0, seg.y0), (seg.x1, seg.y1))
                anchored = [
                    end
                    for end in ends
                    if any(_near_end(end[0], end[1], wall) for wall in (left_walls + right_walls))
                ]
                free = [end for end in ends if end not in anchored]
                far_ends.append(free[0] if free else ends[0])
            if len(far_ends) == 2:
                fx0, fy0 = far_ends[0]
                fx1, fy1 = far_ends[1]
                for cap in caps:
                    if cap.is_h == left.is_h:
                        continue
                    if not (gap - 40.0 <= cap.length <= gap + 80.0):
                        continue
                    (cx0, cy0), (cx1, cy1) = (cap.x0, cap.y0), (cap.x1, cap.y1)
                    straight = (
                        math.hypot(cx0 - fx0, cy0 - fy0) <= 80.0
                        and math.hypot(cx1 - fx1, cy1 - fy1) <= 80.0
                    )
                    crossed = (
                        math.hypot(cx0 - fx1, cy0 - fy1) <= 80.0
                        and math.hypot(cx1 - fx0, cy1 - fy0) <= 80.0
                    )
                    if straight or crossed:
                        _take(cap)

    # 벽 끝에서 같은 선으로 조금 더 나간 뒤, 거기에 붙은 40–80 mm 이중선.
    # 침실-3 오른쪽 위처럼 간격이 얇아도 벽 면에 이어져 있으면 벽이다.
    all_walls = [s for s in segs if s.layer == WALL_LAYER and s.length >= 80.0]
    short_bases = [
        s
        for s in segs
        if s.layer == BASE_LAYER and 80.0 <= s.length <= 1200.0 and not _is_stair_tread_seg(s, segs)
    ]

    def _end_touch(seg: AxisSeg, others: list[AxisSeg], tol: float = 45.0) -> bool:
        ends = ((seg.x0, seg.y0), (seg.x1, seg.y1))
        for other in others:
            other_ends = ((other.x0, other.y0), (other.x1, other.y1))
            if any(math.hypot(a[0] - b[0], a[1] - b[1]) <= tol for a in ends for b in other_ends):
                return True
        return False

    stubs: list[AxisSeg] = []
    for seg in short_bases:
        if seg.length > 500.0:
            continue
        for wall in all_walls + chosen:
            if seg.is_h != wall.is_h or abs(seg.ortho - wall.ortho) > 25.0:
                continue
            overlap = min(seg.along1, wall.along1) - max(seg.along0, wall.along0)
            if overlap >= 30.0 or not _end_touch(seg, [wall], 30.0):
                continue
            stubs.append(seg)
            _take(seg)
            break
    anchor = list(chosen) + stubs
    pool = [s for s in short_bases if _key(s) not in seen_seg]
    for _step in range(3):
        added: list[AxisSeg] = []
        for i, left in enumerate(pool):
            for right in pool[i + 1 :]:
                if left.is_h != right.is_h:
                    continue
                gap = abs(left.ortho - right.ortho)
                if not (40.0 <= gap <= 80.0):
                    continue
                overlap = min(left.along1, right.along1) - max(left.along0, right.along0)
                shorter = min(left.length, right.length)
                if overlap < 0.7 * shorter or abs(left.length - right.length) > 300.0:
                    continue
                if _end_touch(left, anchor) or _end_touch(right, anchor):
                    added.extend((left, right))
        if not added:
            break
        for seg in added:
            _take(seg)
        anchor = list(chosen)

    # 두 면이 모두 기존 벽과 한 줄이고 두께도 같으면, 500–1000 mm 칸도 벽이다.
    # 한 줄만 이어진 선은 500 mm를 넘기지 않는다. 스윙 안의 문짝은 올리지 않는다.
    swings: list[tuple[float, float, float]] = []
    for entity in msp:
        if entity.dxftype() != "ARC":
            continue
        try:
            radius = float(entity.dxf.radius)
        except Exception:  # noqa: BLE001
            continue
        if not (400.0 <= radius <= 1400.0):
            continue
        swings.append((float(entity.dxf.center.x), float(entity.dxf.center.y), radius))

    def _inside_swing(seg: AxisSeg) -> bool:
        mx = (seg.x0 + seg.x1) * 0.5
        my = (seg.y0 + seg.y1) * 0.5
        return any(
            math.hypot(mx - cx, my - cy) <= radius + 40.0 for cx, cy, radius in swings
        )

    def _collinear_hosts(seg: AxisSeg) -> list[AxisSeg]:
        found: list[AxisSeg] = []
        for wall in all_walls:
            if seg.is_h != wall.is_h or abs(seg.ortho - wall.ortho) > 25.0:
                continue
            overlap = min(seg.along1, wall.along1) - max(seg.along0, wall.along0)
            if overlap >= 30.0 or not _end_touch(seg, [wall], 30.0):
                continue
            found.append(wall)
        return found

    long_faces = [
        s for s in short_bases if 500.0 < s.length <= 1000.0 and not _inside_swing(s)
    ]
    panel_ids: set[int] = set()
    for i, left in enumerate(long_faces):
        left_hosts = _collinear_hosts(left)
        if not left_hosts:
            continue
        for right in long_faces[i + 1 :]:
            if left.is_h != right.is_h:
                continue
            gap = abs(left.ortho - right.ortho)
            if not (40.0 <= gap <= 80.0):
                continue
            overlap = min(left.along1, right.along1) - max(left.along0, right.along0)
            shorter = min(left.length, right.length)
            if overlap < 0.7 * shorter or abs(left.length - right.length) > 300.0:
                continue
            right_hosts = _collinear_hosts(right)
            if not right_hosts:
                continue
            matched = False
            for wall_a in left_hosts:
                for wall_b in right_hosts:
                    if wall_a.is_h != wall_b.is_h:
                        continue
                    if abs(abs(wall_a.ortho - wall_b.ortho) - gap) > 25.0:
                        continue
                    matched = True
                    break
                if matched:
                    break
            if not matched:
                continue
            _take(left)
            _take(right)
            if left.entity is not None:
                panel_ids.add(id(left.entity))
            if right.entity is not None:
                panel_ids.add(id(right.entity))

    # 그 칸 옆에서 같은 방향으로 조금 어긋나 이어진 면도 벽이다.
    # 침실-3 왼쪽처럼 10 mm 어긋나고, 맞은편 벽과 20–80 mm일 때.
    for seg in long_faces:
        continued = False
        for wall in all_walls + chosen:
            if seg.is_h != wall.is_h or abs(seg.ortho - wall.ortho) > 25.0:
                continue
            overlap = min(seg.along1, wall.along1) - max(seg.along0, wall.along0)
            if overlap >= 30.0 or not _end_touch(seg, [wall], 40.0):
                continue
            continued = True
            break
        if not continued:
            continue
        for wall in all_walls:
            if wall.is_h != seg.is_h:
                continue
            gap = abs(wall.ortho - seg.ortho)
            if not (20.0 <= gap <= 80.0):
                continue
            overlap = min(seg.along1, wall.along1) - max(seg.along0, wall.along0)
            shorter = min(seg.length, wall.length)
            if overlap < 0.65 * shorter or abs(seg.length - wall.length) > max(400.0, 0.6 * shorter):
                continue
            _take(seg)
            break

    # 벽 끝에서 직각으로 꺾인 40–80 mm 이중선.
    # 간격 하한만 내리면 같은 선에 붙은 창호선까지 올라가므로,
    # 다리 300–500 mm가 그 벽 두께와 같고 다른 벽 끝까지 이어질 때만 올린다.
    bend_walls = [s for s in segs if s.layer == WALL_LAYER and s.length >= 600.0]
    bend_faces = [s for s in bases if 300.0 <= s.length <= 500.0]
    link_faces = [
        s
        for s in segs
        if s.layer == BASE_LAYER
        and 180.0 <= s.length <= 700.0
        and not _is_stair_tread_seg(s, segs)
    ]
    link_caps = [
        s
        for s in segs
        if s.layer == BASE_LAYER
        and 40.0 <= s.length <= 250.0
        and not _is_stair_tread_seg(s, segs)
    ]

    def _touch_wall_ends(seg: AxisSeg) -> list[tuple[tuple[float, float], AxisSeg]]:
        found: list[tuple[tuple[float, float], AxisSeg]] = []
        for wall in bend_walls:
            if seg.is_h == wall.is_h:
                continue
            for px, py in ((seg.x0, seg.y0), (seg.x1, seg.y1)):
                for qx, qy in ((wall.x0, wall.y0), (wall.x1, wall.y1)):
                    if math.hypot(px - qx, py - qy) <= 40.0:
                        found.append(((qx, qy), wall))
        return found

    def _pair_gap(left: AxisSeg, right: AxisSeg, gap_min: float, gap_max: float) -> float | None:
        if left.is_h != right.is_h:
            return None
        gap = abs(left.ortho - right.ortho)
        if not (gap_min <= gap <= gap_max):
            return None
        overlap = min(left.along1, right.along1) - max(left.along0, right.along0)
        shorter = min(left.length, right.length)
        if overlap < 0.65 * shorter:
            return None
        if abs(left.length - right.length) > max(400.0, 0.6 * shorter):
            return None
        return gap

    def _touches(seg: AxisSeg, group: list[AxisSeg], tol: float) -> bool:
        ends_a = ((seg.x0, seg.y0), (seg.x1, seg.y1))
        for other in group:
            ends_b = ((other.x0, other.y0), (other.x1, other.y1))
            if any(
                math.hypot(a[0] - b[0], a[1] - b[1]) <= tol for a in ends_a for b in ends_b
            ):
                return True
        return False

    bend_seeds: list[tuple[AxisSeg, AxisSeg, tuple[float, float], tuple[float, float]]] = []
    for i, left in enumerate(bend_faces):
        left_hits = _touch_wall_ends(left)
        if not left_hits:
            continue
        for right in bend_faces[i + 1 :]:
            gap = _pair_gap(left, right, 40.0, 80.0)
            if gap is None:
                continue
            right_hits = _touch_wall_ends(right)
            if not right_hits:
                continue
            anchor = None
            for q1, wall_a in left_hits:
                for q2, wall_b in right_hits:
                    if wall_a.is_h != wall_b.is_h:
                        continue
                    host_gap = abs(wall_a.ortho - wall_b.ortho)
                    if host_gap < 40.0 or abs(host_gap - gap) > 25.0:
                        continue
                    if math.hypot(q1[0] - q2[0], q1[1] - q2[1]) > gap + 100.0:
                        continue
                    anchor = (q1, q2)
                    break
                if anchor:
                    break
            if anchor:
                bend_seeds.append((left, right, anchor[0], anchor[1]))

    for left0, right0, q1, q2 in bend_seeds:
        comp: list[AxisSeg] = [left0, right0]
        seen_ids = {id(left0), id(right0)}
        for _step in range(3):
            group = list(comp)
            connectors = [
                cap for cap in link_caps if id(cap) not in seen_ids and _touches(cap, group, 40.0)
            ]
            added_bend: list[AxisSeg] = []
            for i, left in enumerate(link_faces):
                for right in link_faces[i + 1 :]:
                    if _pair_gap(left, right, 40.0, 350.0) is None:
                        continue
                    direct = _touches(left, group, 45.0) or _touches(right, group, 45.0)
                    via = None
                    if not direct:
                        for cap in connectors:
                            if (
                                _touches(cap, [left], 40.0) or _touches(cap, [right], 40.0)
                            ) and _touches(cap, group, 40.0):
                                via = cap
                                break
                    if not direct and via is None:
                        continue
                    for seg in (left, right):
                        if id(seg) not in seen_ids:
                            added_bend.append(seg)
                    if via is not None and id(via) not in seen_ids:
                        added_bend.append(via)
            if not added_bend:
                break
            for seg in added_bend:
                seen_ids.add(id(seg))
                comp.append(seg)
        reaches = False
        for seg in comp:
            for px, py in ((seg.x0, seg.y0), (seg.x1, seg.y1)):
                if math.hypot(px - q1[0], py - q1[1]) <= 80.0 or math.hypot(px - q2[0], py - q2[1]) <= 80.0:
                    continue
                for wall in bend_walls:
                    if math.hypot(px - wall.x0, py - wall.y0) <= 40.0 or math.hypot(px - wall.x1, py - wall.y1) <= 40.0:
                        reaches = True
                        break
                if reaches:
                    break
            if reaches:
                break
        if not reaches:
            continue
        for seg in link_faces:
            if id(seg) in seen_ids or not _touches(seg, comp, 40.0):
                continue
            for mate in list(comp) + bend_walls:
                if mate.is_h != seg.is_h:
                    continue
                gap = abs(seg.ortho - mate.ortho)
                if not (40.0 <= gap <= 350.0):
                    continue
                overlap = min(seg.along1, mate.along1) - max(seg.along0, mate.along0)
                shorter = min(seg.length, mate.length)
                if overlap < 0.65 * shorter:
                    continue
                if abs(seg.length - mate.length) > max(400.0, 0.6 * shorter):
                    continue
                seen_ids.add(id(seg))
                comp.append(seg)
                break
        for i, left in enumerate(list(comp)):
            for right in comp[i + 1 :]:
                gap = _pair_gap(left, right, 40.0, 350.0)
                if gap is None:
                    continue
                for cap in link_caps:
                    if cap.is_h == left.is_h:
                        continue
                    if not (gap - 40.0 <= cap.length <= gap + 80.0):
                        continue
                    if id(cap) in seen_ids:
                        continue
                    ends_c = ((cap.x0, cap.y0), (cap.x1, cap.y1))
                    ends_l = ((left.x0, left.y0), (left.x1, left.y1))
                    ends_r = ((right.x0, right.y0), (right.x1, right.y1))
                    hit_l = any(
                        math.hypot(a[0] - b[0], a[1] - b[1]) <= 40.0
                        for a in ends_c
                        for b in ends_l
                    )
                    hit_r = any(
                        math.hypot(a[0] - b[0], a[1] - b[1]) <= 40.0
                        for a in ends_c
                        for b in ends_r
                    )
                    if hit_l and hit_r:
                        seen_ids.add(id(cap))
                        comp.append(cap)
        for seg in comp:
            _take(seg)

    changed = 0
    painted: set[int] = set()

    def _same_line(entity, seg: AxisSeg) -> bool:
        if entity.dxftype() != "LINE":
            return False
        ax, ay = float(entity.dxf.start.x), float(entity.dxf.start.y)
        bx, by = float(entity.dxf.end.x), float(entity.dxf.end.y)

        def _near(px: float, py: float, qx: float, qy: float) -> bool:
            return math.hypot(px - qx, py - qy) <= 5.0

        straight = _near(ax, ay, seg.x0, seg.y0) and _near(bx, by, seg.x1, seg.y1)
        flipped = _near(ax, ay, seg.x1, seg.y1) and _near(bx, by, seg.x0, seg.y0)
        return straight or flipped

    for seg in chosen:
        entity = seg.entity
        if entity is None:
            continue
        if entity.dxftype() == "LINE" or id(entity) in panel_ids:
            if id(entity) not in painted and _paint_layer(entity, WALL_LAYER, WALL_COLOR):
                painted.add(id(entity))
                changed += 1
            if entity.dxftype() == "LINE":
                continue
        else:
            msp.add_line(
                (seg.x0, seg.y0),
                (seg.x1, seg.y1),
                dxfattribs={"layer": WALL_LAYER, "color": WALL_COLOR},
            )
            changed += 1
        for other in msp:
            if other is entity or id(other) in painted:
                continue
            if getattr(other.dxf, "layer", None) != BASE_LAYER:
                continue
            if _same_line(other, seg) and _paint_layer(other, WALL_LAYER, WALL_COLOR):
                painted.add(id(other))
                changed += 1
    return changed


def demote_swing_hinge_door_leaves(msp) -> int:
    """1/4 스윙 힌지에 붙은 문짝을 WALL에서 뺀다.

    힌지에서 호 끝까지 이어진 얇은 문짝(두께 15–90 mm)과 그 위의 선은
    방 면적 경계가 아니다. 호는 그대로 둔다.
    스윙이 열리는 반대편의 벽면은 내리지 않는다.
    """
    leaves: list[tuple[float, float, float, float, float, float]] = []
    for entity in msp:
        if entity.dxftype() != "ARC":
            continue
        try:
            radius = float(entity.dxf.radius)
            start_angle = float(entity.dxf.start_angle)
            end_angle = float(entity.dxf.end_angle)
            center = entity.dxf.center
        except Exception:  # noqa: BLE001
            continue
        sweep = (end_angle - start_angle) % 360.0
        if not (400.0 <= radius <= 1400.0 and 70.0 <= sweep <= 110.0):
            continue
        hx, hy = float(center.x), float(center.y)
        ends: list[tuple[float, float]] = []
        for angle in (start_angle, end_angle):
            rad = math.radians(angle)
            ux, uy = math.cos(rad), math.sin(rad)
            if abs(ux) >= 0.98:
                ux, uy = (1.0 if ux > 0.0 else -1.0), 0.0
            elif abs(uy) >= 0.98:
                ux, uy = 0.0, (1.0 if uy > 0.0 else -1.0)
            else:
                continue
            ends.append((ux, uy))
        if len(ends) != 2:
            continue
        for index, (ux, uy) in enumerate(ends):
            ox, oy = ends[1 - index]
            swing_side = ox * (-uy) + oy * ux
            if abs(swing_side) < 0.5:
                continue
            leaves.append((hx, hy, radius, ux, uy, 1.0 if swing_side > 0.0 else -1.0))
    if not leaves:
        return 0

    def _along(hx: float, hy: float, ux: float, uy: float, x: float, y: float) -> float:
        return (x - hx) * ux + (y - hy) * uy

    def _side(
        hx: float, hy: float, ux: float, uy: float, swing_side: float, x: float, y: float,
    ) -> float:
        return ((x - hx) * (-uy) + (y - hy) * ux) * swing_side

    changed = 0
    seen: set[int] = set()
    leaf_boxes: list[tuple[float, float, float, float, bool]] = []
    for entity in list(msp):
        if entity.dxftype() != "LWPOLYLINE" or not getattr(entity, "closed", False):
            continue
        if getattr(entity.dxf, "layer", None) != WALL_LAYER:
            continue
        pts = _polyline_xy(entity)
        if len(pts) < 4:
            continue
        xs = [pt[0] for pt in pts]
        ys = [pt[1] for pt in pts]
        width, height = max(xs) - min(xs), max(ys) - min(ys)
        short, long = min(width, height), max(width, height)
        if not (15.0 <= short <= 90.0):
            continue
        horizontal = width >= height
        if horizontal:
            end_pts = (
                (min(xs), (min(ys) + max(ys)) / 2.0),
                (max(xs), (min(ys) + max(ys)) / 2.0),
            )
            direction = (1.0, 0.0)
        else:
            end_pts = (
                ((min(xs) + max(xs)) / 2.0, min(ys)),
                ((min(xs) + max(xs)) / 2.0, max(ys)),
            )
            direction = (0.0, 1.0)
        cx = (min(xs) + max(xs)) / 2.0
        cy = (min(ys) + max(ys)) / 2.0
        for hx, hy, radius, ux, uy, swing_side in leaves:
            if abs(direction[0] * ux + direction[1] * uy) < 0.98:
                continue
            if not (0.55 * radius <= long <= 1.2 * radius):
                continue
            if min(math.hypot(px - hx, py - hy) for px, py in end_pts) > 80.0:
                continue
            along = _along(hx, hy, ux, uy, cx, cy)
            side = _side(hx, hy, ux, uy, swing_side, cx, cy)
            if along < 0.2 * radius or not (-20.0 <= side <= short + 25.0):
                continue
            if _paint_layer(entity, BASE_LAYER, BASE_COLOR):
                seen.add(id(entity))
                changed += 1
            leaf_boxes.append((min(xs) - 15.0, min(ys) - 15.0, max(xs) + 15.0, max(ys) + 15.0, horizontal))
            break

    for entity in list(msp):
        if id(entity) in seen or entity.dxftype() != "LINE":
            continue
        if getattr(entity.dxf, "layer", None) != WALL_LAYER:
            continue
        x0, y0 = float(entity.dxf.start.x), float(entity.dxf.start.y)
        x1, y1 = float(entity.dxf.end.x), float(entity.dxf.end.y)
        length = math.hypot(x1 - x0, y1 - y0)
        if length < 400.0:
            continue
        dx, dy = (x1 - x0) / length, (y1 - y0) / length
        horizontal = abs(y1 - y0) <= abs(x1 - x0)
        hit = False
        for hx, hy, radius, ux, uy, swing_side in leaves:
            if abs(dx * ux + dy * uy) < 0.98:
                continue
            if not (0.55 * radius <= length <= 1.2 * radius):
                continue
            a0 = _along(hx, hy, ux, uy, x0, y0)
            a1 = _along(hx, hy, ux, uy, x1, y1)
            lo, hi = min(a0, a1), max(a0, a1)
            if lo < -80.0 or lo > 100.0 or hi < 0.55 * radius or hi > radius + 180.0:
                continue
            s0 = _side(hx, hy, ux, uy, swing_side, x0, y0)
            s1 = _side(hx, hy, ux, uy, swing_side, x1, y1)
            if abs(s1 - s0) > 20.0:
                continue
            side = (s0 + s1) / 2.0
            if not (-15.0 <= side <= 90.0):
                continue
            hinge_end = (x0, y0) if a0 <= a1 else (x1, y1)
            if math.hypot(hinge_end[0] - hx, hinge_end[1] - hy) > 100.0:
                continue
            hit = True
            break
        if not hit:
            for bx0, by0, bx1, by1, box_h in leaf_boxes:
                if box_h != horizontal:
                    continue
                if (
                    bx0 <= x0 <= bx1 and by0 <= y0 <= by1
                    and bx0 <= x1 <= bx1 and by0 <= y1 <= by1
                ):
                    hit = True
                    break
        if hit and _paint_layer(entity, BASE_LAYER, BASE_COLOR):
            seen.add(id(entity))
            changed += 1
    return changed


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
    # 70 mm: 엘리베이터 문 어깨(리턴)·짧은 문틀까지 후보에 포함
    # (기본 500 mm면 door_return 250 mm 등이 통째로 빠짐)
    segs = iter_axis_segs(msp, min_len_mm=70.0)

    hbeam_ids: set[int] = set()
    if do_column_promote:
        hbeam_ids = find_hbeam_column_entities(msp)
    # 휠체어 표식 등. 이미 WALL인 심볼은 기둥 protect에 넣지 않고 demote한다.
    pictogram_demote = demote_pictogram_columns(msp)
    hbeam_ids -= pictogram_demote

    # promote 후보를 demote 전에 확정 (넓은 demote_bbox가 이웃 WALL을
    # 지우면 갭/연속방 승격 맥락이 사라져 한 칸만 회색으로 남는 문제 방지)
    promote: list[AxisSeg] = []
    if do_gap_promote:
        promote.extend(find_promote_segments(segs))
    if do_corridor_promote:
        promote.extend(promote_corridor_walls(segs))
        promote.extend(promote_corridor_door_flanks(segs))
    promote.extend(promote_collinear_room_walls(segs))
    promote.extend(promote_butt_partitions(segs))
    promote.extend(promote_room_corner_returns(segs))
    promote.extend(promote_wall_mate_faces(segs))
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
        promote.extend(promote_stage_enclosure_walls(msp, segs))
    # 엘리베이터 문·후면은 승격 금지 (측벽만 벽)
    if do_elevator_promote:
        promote = filter_promote_away_from_elevator_doors(promote, msp)
    # LINE 가구 직사각·운동기구·정원/조경은 승격 금지
    if do_box_demote:
        furn_ids = demote_line_furniture_boxes(segs, exclude_ids=hbeam_ids)
        furn_ids |= demote_fitness_equipment(msp, segs, exclude_ids=hbeam_ids)
        furn_ids |= demote_meeting_room_interiors(msp, segs, exclude_ids=hbeam_ids)
        furn_ids |= demote_landscape_walls(msp, segs, exclude_ids=hbeam_ids)
        furn_ids |= demote_serving_counter_walls(msp, segs, exclude_ids=hbeam_ids)
        promote = [s for s in promote if id(s.entity) not in furn_ids]

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
    furniture_box_demote: set[int] = set()
    furniture_line_demote: set[int] = set()
    fitness_demote: set[int] = set()
    landscape_demote: set[int] = set()
    if do_box_demote:
        furniture_box_demote = demote_closed_furniture_boxes(msp, exclude_ids=hbeam_ids)
        furniture_line_demote = demote_line_furniture_boxes(segs, exclude_ids=hbeam_ids)
        fitness_demote = demote_fitness_equipment(msp, segs, exclude_ids=hbeam_ids)
        fitness_demote |= demote_meeting_room_interiors(msp, segs, exclude_ids=hbeam_ids)
        fitness_demote |= demote_serving_counter_walls(msp, segs, exclude_ids=hbeam_ids)
        landscape_demote = demote_landscape_walls(msp, segs, exclude_ids=hbeam_ids)
        demote_ids |= furniture_box_demote
        demote_ids |= furniture_line_demote
        demote_ids |= fitness_demote
        demote_ids |= landscape_demote
    # review demote 는 protect 차감 대상이 아니다.
    # 과대 bbox 는 normalize_bbox_list 에서 이미 skip 되므로,
    # 남은 박스는 Vision 명시 판정 → 복도·계단·엘리베이터·H-Beam protect 보다 우선.
    review_demote = demote_in_bboxes(msp, review.get("demote_bboxes") or [])
    n_protected = len(demote_ids & protect_ids)
    demote_ids -= protect_ids
    demote_ids |= review_demote
    # 오픈홀 중앙·엘리베이터 문/후면·가구·운동기구·정원은 protect보다 우선 demote
    demote_ids |= open_hall_demote
    demote_ids |= demote_stair_treads(segs)
    promote = [
        s
        for s in promote
        if not _is_stair_tread_seg(s, segs)
        and not _is_stair_nosing_seg(s, segs)
        and not _near_seat_door_wall(s.is_v, s.ortho, segs)
    ]
    demote_ids |= pictogram_demote
    demote_ids |= elev_door_demote
    demote_ids |= furniture_box_demote
    demote_ids |= furniture_line_demote
    demote_ids |= fitness_demote
    demote_ids |= landscape_demote

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
        segs_after = iter_axis_segs(msp, min_len_mm=70.0)
        post_elev = demote_elevator_door_back_faces(msp, segs_after)
        for e in list(msp):
            if e.dxf.layer == WALL_LAYER and id(e) in post_elev:
                msp.delete_entity(e)
                n_demoted += 1
                n_elev_door_post += 1

    # promote 로 다시 올라온 LINE 가구·운동기구·정원 제거
    n_furniture_post = 0
    if do_box_demote:
        segs_after = iter_axis_segs(msp, min_len_mm=70.0)
        post_furn = demote_line_furniture_boxes(segs_after, exclude_ids=hbeam_ids)
        post_furn |= demote_closed_furniture_boxes(msp, exclude_ids=hbeam_ids)
        post_furn |= demote_fitness_equipment(msp, segs_after, exclude_ids=hbeam_ids)
        post_furn |= demote_meeting_room_interiors(msp, segs_after, exclude_ids=hbeam_ids)
        post_furn |= demote_serving_counter_walls(msp, segs_after, exclude_ids=hbeam_ids)
        post_furn |= demote_landscape_walls(msp, segs_after, exclude_ids=hbeam_ids)
        for e in list(msp):
            if e.dxf.layer == WALL_LAYER and id(e) in post_furn:
                msp.delete_entity(e)
                n_demoted += 1
                n_furniture_post += 1
    # H-Beam 기둥: demote 이후 BASE→WALL (가구 demote에 안 걸림)
    n_column_promoted = 0
    if do_column_promote:
        n_column_promoted = promote_hbeam_columns(msp)
        n_column_promoted += promote_hbeam_sleeves(msp)
    # 승격으로 다시 빨개진 픽토그램(장애인 표식) 제거
    n_pictogram = len(pictogram_demote)
    post_pic = demote_pictogram_columns(msp)
    for e in list(msp):
        if e.dxf.layer == WALL_LAYER and id(e) in post_pic:
            msp.delete_entity(e)
            n_demoted += 1
            if id(e) not in pictogram_demote:
                n_pictogram += 1

    # 문짝은 벽이 아니고, 개구 양옆은 벽. promote 이후에 개구를 끊는다.
    _n_door_cut, _n_door_flank = correct_walls_around_doors(msp)
    # 문 양옆 승격이 배식대 안을 다시 벽으로 만들지 않는다. H-Beam 은 남긴다.
    if do_box_demote:
        segs_serving = iter_axis_segs(msp, min_len_mm=70.0)
        post_serving = demote_serving_counter_walls(
            msp, segs_serving, exclude_ids=hbeam_ids
        )
        for e in list(msp):
            if e.dxf.layer == WALL_LAYER and id(e) in post_serving:
                msp.delete_entity(e)
                n_demoted += 1

    # 대각선으로만 표시된 문의 양옆. 배식대 demote 뒤에 올려 경계벽이 다시 지워지지 않게 한다.
    n_promoted += promote_marked_door_flanks(msp)
    n_promoted += promote_grid_door_flanks(msp)
    n_promoted += promote_serving_end_door_flanks(msp)
    n_promoted += promote_stacked_room_side_wall(msp)
    n_promoted += open_serving_corner_door(msp)
    n_promoted += promote_control_booth_bottom(msp)
    n_promoted += promote_stair_eps_party_wall(msp)
    n_promoted += promote_waiting_store_wall(msp)
    n_promoted += promote_hall_beam_span(msp)
    n_promoted += promote_tbd_beam_span(msp)
    n_promoted += promote_bottom_column_run(msp)
    n_promoted += open_waiting_room_top_door(msp)
    n_promoted += promote_wash_room_edges(msp)
    n_promoted += promote_equipment_store_walls(msp)
    n_promoted += promote_small_meeting_bottom(msp)
    n_promoted += promote_exec_meeting_right(msp)
    # 벽면 정사각 기둥. H-Beam 집계(`n_column_promoted`)와 분리한다.
    n_wall_square_promoted = promote_wall_square_columns(msp)
    n_promoted += n_wall_square_promoted
    # 조정실 아래 문짝이 문 옆 승격에 같이 올라온 것을 되돌린다.
    n_ctrl_demote, n_ctrl_promote = correct_control_room_bottom_door(msp)
    n_demoted += n_ctrl_demote
    n_promoted += n_ctrl_promote
    n_leaf_demote, n_leaf_promote = correct_leaf_span_door(msp)
    n_demoted += n_leaf_demote
    n_promoted += n_leaf_promote
    n_ang_demote, n_ang_promote = promote_angled_door_flanks(msp)
    n_demoted += n_ang_demote
    n_promoted += n_ang_promote
    n_split_demote, n_split_promote = correct_split_wall_door(msp)
    n_demoted += n_split_demote
    n_promoted += n_split_promote
    n_side_demote, n_side_promote = promote_room_door_sides(msp)
    n_demoted += n_side_demote
    n_promoted += n_side_promote
    n_swing_demote, n_swing_promote = correct_inwall_swing_doors(msp)
    n_demoted += n_swing_demote
    n_promoted += n_swing_promote
    n_ret_demote, n_ret_promote = correct_return_sided_doors(msp)
    n_demoted += n_ret_demote
    n_promoted += n_ret_promote
    n_band_demote, n_band_promote = correct_band_leaf_doors(msp)
    n_demoted += n_band_demote
    n_promoted += n_band_promote
    n_narrow_demote, n_narrow_promote = correct_narrow_face_doors(msp)
    n_demoted += n_narrow_demote
    n_promoted += n_narrow_promote
    n_fill_demote, n_fill_promote = correct_filled_opening_doors(msp)
    n_demoted += n_fill_demote
    n_promoted += n_fill_promote
    n_jamb_demote, n_jamb_promote = promote_angled_end_door_jambs(msp)
    n_demoted += n_jamb_demote
    n_promoted += n_jamb_promote
    n_off_demote, n_off_promote = correct_offset_swing_opening(msp)
    n_demoted += n_off_demote
    n_promoted += n_off_promote
    n_run_demote, n_run_promote = correct_runthrough_frame_doors(msp)
    n_demoted += n_run_demote
    n_promoted += n_run_promote
    n_header_demote, n_header_promote = correct_room_header_doors(msp)
    n_demoted += n_header_demote
    n_promoted += n_header_promote
    n_split_demote, n_split_promote = correct_split_face_doors(msp)
    n_demoted += n_split_demote
    n_promoted += n_split_promote
    n_hinge_demote, n_hinge_promote = correct_hinge_panel_doors(msp)
    n_demoted += n_hinge_demote
    n_promoted += n_hinge_promote
    n_jamb_door_demote, n_jamb_door_promote = correct_radius_jamb_doors(msp)
    n_demoted += n_jamb_door_demote
    n_promoted += n_jamb_door_promote
    n_promoted += promote_partition_extensions(msp)
    n_promoted += promote_one_sided_swing_gaps(msp)
    n_opposed_demote, n_opposed_promote = promote_opposed_door_sides(msp)
    n_demoted += n_opposed_demote
    n_promoted += n_opposed_promote
    n_promoted += promote_aligned_wall_gaps(msp)
    n_return_demote, n_return_promote = promote_return_door_sides(msp)
    n_demoted += n_return_demote
    n_promoted += n_return_promote
    n_promoted += promote_corner_wall_squares(msp)
    n_face_demote, n_face_promote = promote_collinear_swing_sides(msp)
    n_demoted += n_face_demote
    n_promoted += n_face_promote
    n_promoted += promote_wall_face_continuations(msp)
    n_hinge_leaf_demote, n_hinge_leaf_promote = correct_offset_hinge_leaves(msp)
    n_demoted += n_hinge_leaf_demote
    n_promoted += n_hinge_leaf_promote
    n_locker_demote, n_locker_promote = correct_locker_partition(msp)
    n_demoted += n_locker_demote
    n_promoted += n_locker_promote
    n_panel_demote, n_panel_promote = correct_panel_face_doors(msp)
    n_demoted += n_panel_demote
    n_promoted += n_panel_promote
    n_promoted += promote_locker_floor_walls(msp)
    n_x_demote, n_x_promote = correct_x_block_doors(msp)
    n_demoted += n_x_demote
    n_promoted += n_x_promote
    n_end_col = promote_end_wall_columns(msp)
    n_wall_square_promoted += n_end_col
    n_promoted += n_end_col
    n_demoted += demote_bay_panel_sills(msp)
    n_cap_demote, n_cap_promote = correct_capped_leaf_doors(msp)
    n_demoted += n_cap_demote
    n_promoted += n_cap_promote
    n_demoted += demote_closet_bay_ends(msp)
    n_zig_demote, n_zig_promote = promote_zigzag_door_sides(msp)
    n_demoted += n_zig_demote
    n_promoted += n_zig_promote
    n_promoted += promote_outside_band_face(msp)
    n_tall_demote, n_tall_promote = correct_tall_opening_doors(msp)
    n_demoted += n_tall_demote
    n_promoted += n_tall_promote
    n_louver_demote, n_louver_promote = correct_louver_doors(msp)
    n_demoted += n_louver_demote
    n_promoted += n_louver_promote
    n_header_demote, n_header_promote = correct_header_slat_doors(msp)
    n_demoted += n_header_demote
    n_promoted += n_header_promote

    # 가장 중요한 두 원칙. 다른 보정 뒤에 적용해 문 개구와 기둥 주변이 남게 한다.
    # 1) 문은 내리고 양옆은 벽. 2) 기둥은 벽, 기둥에 닿는 이중 평행선도 벽.
    n_h_sym, h_boxes = promote_h_symbol_columns(msp)
    n_column_promoted += n_h_sym
    n_promoted += n_h_sym
    square_boxes: list[tuple[float, float, float, float]] = []
    for ent in msp:
        if ent.dxftype() != "LWPOLYLINE" or not ent.closed:
            continue
        if getattr(ent.dxf, "layer", None) != WALL_LAYER:
            continue
        pts = _polyline_xy(ent)
        if not (4 <= len(pts) <= 5):
            continue
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        bw, bh = max(xs) - min(xs), max(ys) - min(ys)
        if not (400.0 <= bw <= 1500.0 and 400.0 <= bh <= 1500.0):
            continue
        if abs(bw - bh) > max(bw, bh) * 0.22:
            continue
        square_boxes.append((min(xs), min(ys), max(xs), max(ys)))
    n_promoted += promote_column_adjacent_wall_pairs(msp, h_boxes + square_boxes)
    n_leaf_side_demote, n_leaf_side_promote = correct_door_leaves_and_flanks(msp)
    n_demoted += n_leaf_side_demote
    n_promoted += n_leaf_side_promote
    n_promoted += promote_swing_opposite_walls(msp)
    n_promoted += add_closed_door_wall_lines(msp)
    n_demoted += remove_swing_squares(msp)
    n_promoted += promote_swing_attached_doubles(msp)
    n_promoted += separate_sliding_door_panels(msp)
    n_promoted += promote_line_swing_door_jambs(msp)
    n_promoted += promote_thick_wall_mates(msp)
    # 벽이 닫히지 않아 면적이 밖으로 새면, 그 벽 끝을 따라 닫는다.
    n_leak_closed = close_leaking_wall_ends(msp)
    n_promoted += n_leak_closed
    # 앞선 승격이 여닫이문 잎을 다시 벽으로 올린다. 잎만 되돌린다.
    n_demoted += demote_thin_swing_leaf_lines(msp)
    n_promoted += promote_bay_window_outlines(msp)
    n_inline_demote, n_inline_promote = correct_inline_swing_doors(msp)
    n_demoted += n_inline_demote
    n_promoted += n_inline_promote
    n_promoted += promote_leaf_side_walls(msp)
    n_promoted += promote_aligned_door_mates(msp)
    n_promoted += promote_leaf_jambs(msp)
    n_promoted += promote_swing_side_walls(msp)
    n_promoted += add_swing_leaf_walls(msp)
    n_promoted += add_corner_closed_door_walls(msp)
    n_step_demote, n_step_promote = separate_stepped_sliding_doors(msp)
    n_demoted += n_step_demote
    n_promoted += n_step_promote
    n_pocket_demote, n_pocket_promote = finish_pocket_sliding_doors(msp)
    n_demoted += n_pocket_demote
    n_promoted += n_pocket_promote
    # 벽선이 개구를 지나 한 줄로 남아 있으면 앞선 닫기에서 끊김을 못 본다.
    # 선이 나뉜 뒤에 그 구간을 다시 닫는다.
    n_promoted += add_closed_door_wall_lines(msp)
    n_promoted += promote_projection_junctions(msp)
    n_promoted += promote_shifted_corner_faces(msp)
    n_promoted += promote_short_connected_doubles(msp)
    # 앞선 승격이 스윙 힌지 문짝을 다시 올려도, 면적 경계로 남지 않게 마지막에 내린다.
    n_demoted += demote_swing_hinge_door_leaves(msp)

    n_wall = sum(1 for e in msp if e.dxf.layer == WALL_LAYER)
    n_base = sum(1 for e in msp if e.dxf.layer == BASE_LAYER)
    return {
        "n_demoted": n_demoted,
        "n_promoted": n_promoted,
        "n_corridor_protected": n_protected,
        "n_open_hall_demoted": len(open_hall_demote) + n_open_hall_post,
        "n_pictogram_demoted": n_pictogram,
        "n_stair_promote_candidates": n_stair_promote,
        "n_elevator_promote_candidates": n_elev_promote,
        "n_elevator_door_demoted": len(elev_door_demote) + n_elev_door_post,
        "n_column_promoted": n_column_promoted,
        "n_wall_square_promoted": n_wall_square_promoted,
        "n_leak_closed": n_leak_closed,
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
    from matplotlib.patches import Arc, Circle

    doc = ezdxf.readfile(str(dxf_path))
    msp = doc.modelspace()
    base_segs: list[list[tuple[float, float]]] = []
    wall_segs: list[list[tuple[float, float]]] = []
    texts: list[tuple[float, float, str, float, float]] = []
    arcs: list[tuple[float, float, float, float, float]] = []
    circles: list[tuple[float, float, float]] = []
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
            elif t == "ARC":
                c = e.dxf.center
                arcs.append(
                    (
                        float(c.x),
                        float(c.y),
                        float(e.dxf.radius),
                        float(e.dxf.start_angle),
                        float(e.dxf.end_angle),
                    )
                )
                continue
            elif t == "CIRCLE":
                c = e.dxf.center
                circles.append((float(c.x), float(c.y), float(e.dxf.radius)))
                continue
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
    # 문 스윙(ARC)은 벽이 아니지만 입구 표시로 회색으로 남긴다.
    for cx, cy, radius, a0, a1 in arcs:
        ax.add_patch(
            Arc(
                (cx, cy),
                2 * radius,
                2 * radius,
                angle=0,
                theta1=a0,
                theta2=a1,
                color="#555555",
                linewidth=0.6,
            )
        )
    for cx, cy, radius in circles:
        ax.add_patch(
            Circle(
                (cx, cy),
                radius,
                fill=False,
                edgecolor="#555555",
                linewidth=0.45,
            )
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
            fs *= 2.0
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
