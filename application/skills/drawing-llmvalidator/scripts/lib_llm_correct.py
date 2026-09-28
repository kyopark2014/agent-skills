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
) -> tuple[list[tuple[bool, float, float, float]], list[tuple[bool, float, float, float]]]:
    """외여닫이 문. 1/4 스윙의 벽 방향이 개구, 수직으로 선 문짝은 벽이 아니다.

    반환: (개구 목록, 문짝 목록). 둘 다 (세로인가, ortho, along0, along1).
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
    seen: set[tuple[int, int, int]] = set()
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
                        leaves.append((True, cx, min(cy, perp[1]), max(cy, perp[1])))
            continue
        horizontal, host = chosen
        key = (round(cx / 50.0), round(cy / 50.0), round(r / 50.0))
        if key in seen:
            continue
        seen.add(key)
        if horizontal:
            openings.append((False, host, min(cx, along[0]), max(cx, along[0])))
            leaves.append((True, cx, min(cy, perp[1]), max(cy, perp[1])))
        else:
            openings.append((True, host, min(cy, along[1]), max(cy, along[1])))
            leaves.append((False, cy, min(cx, perp[0]), max(cx, perp[0])))
    return openings, leaves


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
    """
    openings = find_double_door_openings(msp)
    single_openings, door_leaves = find_single_door_openings(msp, openings)
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
        if not pieces or not _has_pair(is_v, ortho, a0, a1, min_thick=min_thick):
            continue
        for p0, p1 in pieces:
            if _already(is_v, ortho, p0, p1):
                continue
            if is_v:
                msp.add_line((ortho, p0), (ortho, p1), dxfattribs={"layer": WALL_LAYER, "color": WALL_COLOR})
            else:
                msp.add_line((p0, ortho), (p1, ortho), dxfattribs={"layer": WALL_LAYER, "color": WALL_COLOR})
            covered.append((is_v, ortho, p0, p1))
            n_flank += 1
    return (n_cut, n_flank)


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
