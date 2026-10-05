#!/usr/bin/env python3
"""실명 라벨이 들어 있는 공간을 빨간 WALL 안쪽 면으로 면적 계산."""

from __future__ import annotations

import argparse
import json
import math
import re
from collections import defaultdict
from pathlib import Path

import ezdxf
import shapely
from PIL import Image, ImageDraw, ImageFont
from shapely.errors import GEOSException
from shapely.geometry import GeometryCollection, LineString, Point, Polygon, box
from shapely.ops import polygonize, unary_union
from shapely.strtree import STRtree

Image.MAX_IMAGE_PIXELS = None

WALL_LAYER = "WALL"
WINDOW_LAYER = "WINDOW"
COLUMN_LAYER = "COLUMN"
DOOR_LAYER = "DOOR"
BOUNDARY_LAYERS = (WALL_LAYER, WINDOW_LAYER, COLUMN_LAYER, DOOR_LAYER)
AXIS_TOL_MM = 20.0
CLUSTER_TOL_MM = 15.0
JOIN_MM = 80.0
DOOR_GAP_MM = 2400.0
DOOR_TOUCH_MM = 450.0
WALL_CAP_MM = 300.0
SNAP_MM = 40.0
COLUMN_MIN_MM = 450.0
COLUMN_MAX_MM = 1500.0
COLUMN_ASPECT = 0.22

# render_wall_dxf_png 가 PNG 저장 뒤 붙이는 여백 (우, 아래, 위)
PNG_PAD_RIGHT = 200
PNG_PAD_BOTTOM = 120
PNG_PAD_TOP = 160


def norm_name(text: str) -> str:
    s = text.replace("＃", "#").replace("\u00a0", " ")
    return re.sub(r"\s+", "", s).strip()


def plain_mtext(raw: str) -> str:
    s = re.sub(r"\{[^;]*;", "", raw or "")
    s = s.replace("}", "").replace("\\P", " ")
    return re.sub(r"\\[A-Za-z][^;]*;", "", s).strip()


def iter_labels(msp):
    for e in msp:
        t = e.dxftype()
        try:
            if t == "TEXT":
                s = str(e.dxf.text or "").strip()
                x, y = float(e.dxf.insert.x), float(e.dxf.insert.y)
            elif t == "MTEXT":
                s = plain_mtext(e.text or "")
                x, y = float(e.dxf.insert.x), float(e.dxf.insert.y)
            else:
                continue
        except Exception:
            continue
        if s:
            yield x, y, s


_AXIS_LABEL = re.compile(r"^[A-Za-z]\d+$")
_NUMBER_LABEL = re.compile(r"^[\d,.\s]+$")


def _roomish(text: str) -> bool:
    if any(token in text for token in ("면적", "천장", ":", "：")):
        return False
    name = norm_name(text)
    if not name or len(name) > 24:
        return False
    return re.search(r"[가-힣]", name) is not None


def is_room_label(text: str) -> bool:
    """한글 실명, 또는 영문 2자 이상에 숫자가 붙은 이름. 축선·치수는 제외."""
    if _roomish(text):
        return True
    if any(token in text for token in ("면적", "천장", ":", "：", "=", "/")):
        return False
    name = norm_name(text)
    if not name or len(name) > 24:
        return False
    if _NUMBER_LABEL.fullmatch(name) or _AXIS_LABEL.fullmatch(name):
        return False
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9#()·.+-]*", name):
        return False
    letters = re.findall(r"[A-Za-z]", name)
    return len(letters) >= 2 and re.search(r"\d", name) is not None


def _label_record(entity) -> tuple[float, float, str, float] | None:
    kind = entity.dxftype()
    try:
        if kind == "TEXT":
            text = str(entity.dxf.text or "").strip()
            height = float(entity.dxf.height or 0)
        elif kind == "MTEXT":
            text = plain_mtext(entity.text or "")
            height = float(entity.dxf.char_height or 0)
        else:
            return None
        x, y = float(entity.dxf.insert.x), float(entity.dxf.insert.y)
    except Exception:
        return None
    if not text or not is_room_label(text):
        return None
    return x, y, text, height or 375.0


def _label_width(text: str, height: float) -> float:
    return max(len(norm_name(text)), 1) * max(height, 1.0)


def _stacked(a: tuple[float, float, str, float], b: tuple[float, float, str, float]) -> bool:
    """위·아래 줄 간격이 글자 높이의 1.8배 안이고 가로로 겹치면 한 실명이다."""
    dy = abs(a[1] - b[1])
    height = max(a[3], b[3], 1.0)
    if dy <= height * 0.6 or dy > height * 1.8:
        return False
    aw, bw = _label_width(a[2], a[3]), _label_width(b[2], b[3])
    overlap = min(a[0] + aw, b[0] + bw) - max(a[0], b[0])
    return overlap >= min(aw, bw) * 0.4


def collect_room_labels(msp) -> list[tuple[float, float, str]]:
    """실명 라벨. 50 mm 안 중복은 하나로, 붙은 두 줄은 위에서부터 잇는다."""
    raw: list[tuple[float, float, str, float]] = []
    for entity in msp:
        rec = _label_record(entity)
        if rec is None:
            continue
        x, y, text, _height = rec
        name = norm_name(text)
        if any(
            norm_name(prev) == name and abs(x - px) <= 50 and abs(y - py) <= 50
            for px, py, prev, _h in raw
        ):
            continue
        raw.append(rec)

    parent = list(range(len(raw)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i, a in enumerate(raw):
        for j in range(i + 1, len(raw)):
            if _stacked(a, raw[j]):
                ri, rj = find(i), find(j)
                if ri != rj:
                    parent[rj] = ri

    groups: dict[int, list[tuple[float, float, str, float]]] = {}
    for i, rec in enumerate(raw):
        groups.setdefault(find(i), []).append(rec)

    found: list[tuple[float, float, str]] = []
    for group in groups.values():
        group.sort(key=lambda item: -item[1])
        if len(group) == 1:
            found.append((group[0][0], group[0][1], group[0][2]))
            continue
        text = norm_name("".join(part[2] for part in group))
        x = sum(part[0] for part in group) / len(group)
        y = sum(part[1] for part in group) / len(group)
        found.append((x, y, text))
    found.sort(key=lambda item: (norm_name(item[2]), item[1], item[0]))
    return found


def find_room_label(
    msp, room: str, at: tuple[float, float] | None = None
) -> tuple[float, float, str]:
    want = norm_name(room)
    hits = [(x, y, s) for x, y, s in collect_room_labels(msp) if norm_name(s) == want]
    unique: list[tuple[float, float, str]] = []
    for x, y, s in hits:
        if any(abs(x - ux) <= 50 and abs(y - uy) <= 50 for ux, uy, _ in unique):
            continue
        unique.append((x, y, s))
    if not unique:
        raise SystemExit(f"실명을 찾지 못했습니다: {room}")
    if at is not None:
        ax, ay = at
        unique.sort(key=lambda h: (h[0] - ax) ** 2 + (h[1] - ay) ** 2)
        return unique[0]
    if len(unique) > 1:
        coords = ", ".join(f"({x:.0f},{y:.0f})" for x, y, _ in unique)
        raise SystemExit(
            f"'{room}' 라벨이 {len(unique)}곳입니다: {coords}. --x 와 --y 로 하나를 지정하세요."
        )
    return unique[0]


def _rect_box(pts: list[tuple[float, float]]) -> tuple[float, float, float, float] | None:
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    w, h = max(xs) - min(xs), max(ys) - min(ys)
    if w < COLUMN_MIN_MM or h < COLUMN_MIN_MM or w > COLUMN_MAX_MM or h > COLUMN_MAX_MM:
        return None
    if abs(w - h) / max(w, h) > COLUMN_ASPECT:
        return None
    x0, y0, x1, y1 = min(xs), min(ys), max(xs), max(ys)
    for x, y in pts:
        on_v = abs(x - x0) <= 5 or abs(x - x1) <= 5
        on_h = abs(y - y0) <= 5 or abs(y - y1) <= 5
        if not (on_v or on_h):
            return None
    return (x0, y0, x1, y1)


def column_boxes(msp) -> list[tuple[float, float, float, float]]:
    """H-Beam: 변 0.45–1.5 m 축평행 정사각. 동심 쌍 또는 중심 짧은 선.

    COLUMN 레이어의 정사각은 이미 기둥으로 저장됐으므로 그대로 쓴다.
    """
    squares: list[tuple[float, float, float, float]] = []
    column_layer_boxes: list[tuple[float, float, float, float]] = []
    for e in msp:
        if e.dxf.layer not in (WALL_LAYER, COLUMN_LAYER) or e.dxftype() != "LWPOLYLINE" or not e.closed:
            continue
        pts = [(float(a), float(b)) for a, b in e.get_points("xy")]
        if len(pts) < 4:
            continue
        box = _rect_box(pts)
        if not box:
            continue
        if e.dxf.layer == COLUMN_LAYER:
            column_layer_boxes.append(box)
        else:
            squares.append(box)

    def center(b):
        return ((b[0] + b[2]) * 0.5, (b[1] + b[3]) * 0.5)

    ticks: list[tuple[float, float, float]] = []
    for e in msp:
        if e.dxf.layer not in (WALL_LAYER, COLUMN_LAYER) or e.dxftype() != "LINE":
            continue
        x0, y0 = float(e.dxf.start.x), float(e.dxf.start.y)
        x1, y1 = float(e.dxf.end.x), float(e.dxf.end.y)
        length = math.hypot(x1 - x0, y1 - y0)
        if 150 <= length <= 800:
            ticks.append(((x0 + x1) * 0.5, (y0 + y1) * 0.5, length))

    def has_tick(box) -> bool:
        cx, cy = center(box)
        half = max(box[2] - box[0], box[3] - box[1]) * 0.5
        for tx, ty, _length in ticks:
            if abs(tx - cx) <= half * 0.45 and abs(ty - cy) <= half * 0.45:
                if box[0] - 5 <= tx <= box[2] + 5 and box[1] - 5 <= ty <= box[3] + 5:
                    return True
        return False

    def has_mate(box) -> bool:
        cx, cy = center(box)
        for other in squares:
            if other is box:
                continue
            ox, oy = center(other)
            if abs(ox - cx) <= 80 and abs(oy - cy) <= 80:
                return True
        return False

    kept: list[tuple[float, float, float, float]] = []
    for box in squares:
        if has_mate(box) or has_tick(box):
            kept.append(box)

    # 동심 쌍은 바깥 사각만 남긴다.
    outers: list[tuple[float, float, float, float]] = []
    for box in kept:
        cx, cy = center(box)
        group = [b for b in kept if abs(center(b)[0] - cx) <= 80 and abs(center(b)[1] - cy) <= 80]
        outer = max(group, key=lambda b: (b[2] - b[0]) * (b[3] - b[1]))
        if not any(abs(outer[0] - u[0]) < 1 and abs(outer[1] - u[1]) < 1 for u in outers):
            outers.append(outer)
    for box in column_layer_boxes:
        if not any(abs(box[0] - u[0]) < 1 and abs(box[1] - u[1]) < 1 for u in outers):
            outers.append(box)
    return outers


def _inside_box(x: float, y: float, boxes) -> bool:
    for x0, y0, x1, y1 in boxes:
        if x0 - 5 <= x <= x1 + 5 and y0 - 5 <= y <= y1 + 5:
            return True
    return False


def _segment_on_open_door(x0: float, y0: float, x1: float, y1: float, open_doors) -> bool:
    """연 문의 문선에서 350 mm 안인 짧은 DOOR 선은 그 개구의 문짝이다."""
    if not open_doors:
        return False
    length = math.hypot(x1 - x0, y1 - y0)
    if length > DOOR_GAP_MM + 200.0:
        return False
    mid = Point((x0 + x1) * 0.5, (y0 + y1) * 0.5)
    for door in open_doors:
        for line in door["lines"]:
            if float(line.distance(mid)) <= 350.0:
                return True
    return False


def wall_segments(msp, boxes, *, include_door: bool = True, open_doors=None) -> list[tuple[float, float, float, float]]:
    segs: list[tuple[float, float, float, float]] = []
    layers = BOUNDARY_LAYERS if include_door else (WALL_LAYER, WINDOW_LAYER, COLUMN_LAYER)
    for e in msp:
        # DOOR 선은 경계다. open_doors 에 있는 문짝만 뺀다.
        if e.dxf.layer not in layers:
            continue
        t = e.dxftype()
        parts: list[tuple[float, float, float, float]] = []
        if t == "LINE":
            parts.append(
                (
                    float(e.dxf.start.x),
                    float(e.dxf.start.y),
                    float(e.dxf.end.x),
                    float(e.dxf.end.y),
                )
            )
        elif t == "LWPOLYLINE":
            pts = [(float(a), float(b)) for a, b in e.get_points("xy")]
            n = len(pts)
            for i in range(n - 1 + (1 if e.closed else 0)):
                a, b = pts[i], pts[(i + 1) % n]
                parts.append((a[0], a[1], b[0], b[1]))
        for x0, y0, x1, y1 in parts:
            if _inside_box((x0 + x1) * 0.5, (y0 + y1) * 0.5, boxes):
                continue
            if e.dxf.layer == DOOR_LAYER and _segment_on_open_door(x0, y0, x1, y1, open_doors):
                continue
            segs.append((x0, y0, x1, y1))
    return segs


def _axis_snapper(values: list[float], tol: float):
    ordered = sorted(values)
    groups: list[list[float]] = []
    for v in ordered:
        if groups and v - groups[-1][-1] <= tol:
            groups[-1].append(v)
        else:
            groups.append([v])
    centers = [sorted(g)[len(g) // 2] for g in groups]

    def snap(v: float) -> float | None:
        best = None
        best_d = tol + 1
        for c in centers:
            d = abs(v - c)
            if d < best_d:
                best, best_d = c, d
        if best is not None and best_d <= tol:
            return best
        return None

    return snap, centers


def _merge_intervals(intervals: list[tuple[float, float]], join: float) -> list[list[float]]:
    iv = sorted((min(a, b), max(a, b)) for a, b in intervals if abs(b - a) > 0.5)
    if not iv:
        return []
    out = [[iv[0][0], iv[0][1]]]
    for a, b in iv[1:]:
        if a <= out[-1][1] + join:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return out


def _bridge(merged: list[list[float]], max_gap: float, allow=None) -> list[list[float]]:
    """allow(gap_start, gap_end) 가 False 인 틈은 잇지 않는다."""
    if not merged:
        return []
    out = [merged[0][:]]
    for a, b in merged[1:]:
        gap_start = out[-1][1]
        gap = a - gap_start
        if 0 < gap <= max_gap and (allow is None or allow(gap_start, a)):
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return out


def _gap_is_open_door(horizontal: bool, axis: float, a: float, b: float, open_doors) -> bool:
    """틈이 연 문의 문선과 같은 축에서 겹치면 그 개구는 잇지 않는다."""
    for door in open_doors or []:
        span = door.get("span")
        if not span:
            continue
        ori, ax, s0, s1 = span
        if horizontal and ori == "h" and abs(axis - ax) <= DOOR_TOUCH_MM:
            if min(b, s1) - max(a, s0) > 80.0:
                return True
        if (not horizontal) and ori == "v" and abs(axis - ax) <= DOOR_TOUCH_MM:
            if min(b, s1) - max(a, s0) > 80.0:
                return True
    return False


def _gap_hits_column(horizontal: bool, axis: float, a: float, b: float, boxes) -> bool:
    """틈의 가운데가 기둥 박스(벽 두께 여유 200 mm) 안에 있으면 기둥이 끊은 벽이다."""
    mid = (a + b) * 0.5
    pad = 200.0
    for x0, y0, x1, y1 in boxes:
        if horizontal:
            if (y0 - pad) <= axis <= (y1 + pad) and (x0 - pad) <= mid <= (x1 + pad):
                return True
        elif (x0 - pad) <= axis <= (x1 + pad) and (y0 - pad) <= mid <= (y1 + pad):
            return True
    return False


def bridged_runs(segs, origin: tuple[float, float], pad: float, *, door: str = "close", boxes=None, open_doors=None):
    """원점 주변 세그만 모아 축별로 문 틈을 잇는다."""
    ox, oy = origin
    near = []
    for s in segs:
        mx, my = (s[0] + s[2]) * 0.5, (s[1] + s[3]) * 0.5
        if abs(mx - ox) <= pad and abs(my - oy) <= pad:
            near.append(s)
    hsegs, vsegs, dsegs = [], [], []
    for s in near:
        if abs(s[1] - s[3]) <= AXIS_TOL_MM:
            hsegs.append(s)
        elif abs(s[0] - s[2]) <= AXIS_TOL_MM:
            vsegs.append(s)
        else:
            dsegs.append(s)
    ysnap, y_axes = _axis_snapper([(s[1] + s[3]) * 0.5 for s in hsegs], CLUSTER_TOL_MM) if hsegs else (lambda _v: None, [])
    xsnap, x_axes = _axis_snapper([(s[0] + s[2]) * 0.5 for s in vsegs], CLUSTER_TOL_MM) if vsegs else (lambda _v: None, [])
    h_int: dict[float, list[tuple[float, float]]] = defaultdict(list)
    v_int: dict[float, list[tuple[float, float]]] = defaultdict(list)
    for s in hsegs:
        y = ysnap((s[1] + s[3]) * 0.5)
        if y is None:
            continue
        h_int[y].append((min(s[0], s[2]), max(s[0], s[2])))
    for s in vsegs:
        x = xsnap((s[0] + s[2]) * 0.5)
        if x is None:
            continue
        v_int[x].append((min(s[1], s[3]), max(s[1], s[3])))
    def _finish(intervals, horizontal: bool, axis: float) -> list[list[float]]:
        merged = _merge_intervals(intervals, JOIN_MM)
        if open_doors:
            return _bridge(
                merged,
                DOOR_GAP_MM,
                allow=lambda a, b, horizontal=horizontal, axis=axis: not _gap_is_open_door(
                    horizontal, axis, a, b, open_doors
                ),
            )
        if door == "open":
            return _bridge(
                merged,
                DOOR_GAP_MM,
                allow=lambda a, b, horizontal=horizontal, axis=axis: _gap_hits_column(
                    horizontal, axis, a, b, boxes or []
                ),
            )
        return _bridge(merged, DOOR_GAP_MM)

    h_final = {y: _finish(iv, True, y) for y, iv in h_int.items()}
    v_final = {x: _finish(iv, False, x) for x, iv in v_int.items()}
    return h_final, v_final, dsegs, x_axes, y_axes


def _lines_from_runs(h_final, v_final, dsegs) -> list[LineString]:
    lines: list[LineString] = []
    for y, ivs in h_final.items():
        for a, b in ivs:
            if abs(b - a) > 0.5:
                lines.append(LineString([(a, y), (b, y)]))
    for x, ivs in v_final.items():
        for a, b in ivs:
            if abs(b - a) > 0.5:
                lines.append(LineString([(x, a), (x, b)]))
    for x0, y0, x1, y1 in dsegs:
        if math.hypot(x1 - x0, y1 - y0) > 0.5:
            lines.append(LineString([(x0, y0), (x1, y1)]))
    return lines


def _snap_endpoints(lines: list[LineString], tol: float) -> list[LineString]:
    """모서리에서 어긋난 끝점을 tol 안에서 한 점으로 모은다. tol보다 큰 군집은 합치지 않는다."""
    endpoints: list[tuple[float, float]] = []
    for ln in lines:
        coords = list(ln.coords)
        endpoints.append((float(coords[0][0]), float(coords[0][1])))
        endpoints.append((float(coords[-1][0]), float(coords[-1][1])))
    n = len(endpoints)
    if n == 0:
        return []
    parent = list(range(n))
    members: list[list[int]] = [[i] for i in range(n)]

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(i: int, j: int) -> None:
        ri, rj = find(i), find(j)
        if ri == rj:
            return
        group = members[ri] + members[rj]
        xs = [endpoints[k][0] for k in group]
        ys = [endpoints[k][1] for k in group]
        if max(xs) - min(xs) > tol or max(ys) - min(ys) > tol:
            return
        parent[rj] = ri
        members[ri] = group

    cell = tol if tol > 0 else 1.0
    buckets: dict[tuple[int, int], list[int]] = defaultdict(list)
    for i, (x, y) in enumerate(endpoints):
        buckets[(math.floor(x / cell), math.floor(y / cell))].append(i)
    for (ix, iy), ids in buckets.items():
        neigh: list[int] = []
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                neigh.extend(buckets.get((ix + dx, iy + dy), []))
        for i in ids:
            x, y = endpoints[i]
            for j in neigh:
                if j <= i:
                    continue
                x2, y2 = endpoints[j]
                if abs(x - x2) <= tol and abs(y - y2) <= tol:
                    union(i, j)

    canon = [(0.0, 0.0)] * n
    seen: dict[int, tuple[float, float]] = {}
    for i in range(n):
        root = find(i)
        if root not in seen:
            xs = sorted(endpoints[k][0] for k in members[root])
            ys = sorted(endpoints[k][1] for k in members[root])
            mid = len(xs) // 2
            seen[root] = (xs[mid], ys[mid])
        canon[i] = seen[root]

    out: list[LineString] = []
    for idx, ln in enumerate(lines):
        a, b = canon[idx * 2], canon[idx * 2 + 1]
        if abs(a[0] - b[0]) <= 0.5 and abs(a[1] - b[1]) <= 0.5:
            continue
        out.append(LineString([a, b]))
    return out


def _cap_wall_ends(lines: list[LineString], tol: float) -> list[LineString]:
    """끝점이 다른 벽선에서 tol 안에 있으면 그 선까지 잇는다. 이중선 벽의 끝막음이다."""
    if not lines:
        return lines
    tree = STRtree(lines)
    extra: list[LineString] = []
    seen: set[tuple[tuple[float, float], tuple[float, float]]] = set()
    for ln in lines:
        for xy in (ln.coords[0], ln.coords[-1]):
            pt = Point(xy)
            for idx in tree.query(pt.buffer(tol)):
                other = lines[int(idx)]
                dist = float(other.distance(pt))
                if dist <= 0.5 or dist > tol:
                    continue
                nearest = other.interpolate(other.project(pt))
                a = (round(pt.x, 1), round(pt.y, 1))
                b = (round(float(nearest.x), 1), round(float(nearest.y), 1))
                if a == b:
                    continue
                key = tuple(sorted((a, b)))
                if key in seen:
                    continue
                seen.add(key)
                extra.append(LineString([(pt.x, pt.y), (float(nearest.x), float(nearest.y))]))
    return lines + extra


def _sanitize_lines(lines: list[LineString], grid: float) -> list[LineString]:
    """끝점을 grid에 맞추고, 길이 0인 선과 같은 선의 중복을 뺀다.

    문 선과 벽 선이 거의 같은 좌표에 겹치면 GEOS noding이 수렴하지 않는다.
    """
    seen: set[tuple] = set()
    out: list[LineString] = []
    for ln in lines:
        coords = list(ln.coords)
        if len(coords) < 2:
            continue
        a = (
            round(float(coords[0][0]) / grid) * grid,
            round(float(coords[0][1]) / grid) * grid,
        )
        b = (
            round(float(coords[-1][0]) / grid) * grid,
            round(float(coords[-1][1]) / grid) * grid,
        )
        if abs(a[0] - b[0]) <= grid * 0.5 and abs(a[1] - b[1]) <= grid * 0.5:
            continue
        key = tuple(sorted((a, b)))
        if key in seen:
            continue
        seen.add(key)
        out.append(LineString([a, b]))
    return out


def _node_lines(lines: list[LineString]):
    """1 mm에서 안 되면 5 mm, 10 mm로 다시 맞춘다."""
    for grid in (1.0, 5.0, 10.0):
        cleaned = _sanitize_lines(lines, grid)
        if len(cleaned) < 3:
            continue
        try:
            noded = shapely.node(GeometryCollection(cleaned))
        except GEOSException:
            continue
        if not noded.is_empty:
            return noded
    return None


def _face_at(lines: list[LineString], x: float, y: float) -> Polygon | None:
    lines = [ln for ln in lines if ln.length > 0.5]
    if len(lines) < 3:
        return None
    noded = _node_lines(lines)
    if noded is None:
        return None
    try:
        faces = [g for g in polygonize(noded) if g.geom_type == "Polygon" and g.area > 1.0]
    except GEOSException:
        return None
    pt = Point(x, y)
    interior = [g for g in faces if g.contains(pt)]
    if interior:
        return min(interior, key=lambda g: g.area)
    touching = [g for g in faces if g.distance(pt) <= 1.0]
    if not touching:
        return None
    return max(touching, key=lambda g: g.area)


def _touches_window(poly: Polygon, origin: tuple[float, float], reach: float, tol: float = 50.0) -> bool:
    ox, oy = origin
    minx, miny, maxx, maxy = poly.bounds
    return (
        minx <= ox - reach + tol
        or maxx >= ox + reach - tol
        or miny <= oy - reach + tol
        or maxy >= oy + reach - tol
    )


def _polygons_of(geom) -> list[Polygon]:
    if geom.is_empty:
        return []
    if geom.geom_type == "Polygon":
        return [geom]
    if geom.geom_type in ("MultiPolygon", "GeometryCollection"):
        out: list[Polygon] = []
        for part in geom.geoms:
            out.extend(_polygons_of(part))
        return out
    return []


def _subtract_columns(face: Polygon, boxes, seed: tuple[float, float]):
    """실 안으로 들어온 H-Beam 박스를 뺀다. 벽 두께 안에만 있는 기둥은 면과 안 겹친다."""
    net = face
    subtracted = []
    pt = Point(seed)
    for x0, y0, x1, y1 in boxes:
        rect = box(x0, y0, x1, y1)
        if not net.intersects(rect):
            continue
        area_m2 = net.intersection(rect).area / 1_000_000.0
        if area_m2 < 0.02:
            continue
        net = net.difference(rect)
        subtracted.append(
            {
                "bbox_mm": [round(x0, 4), round(y0, 4), round(x1, 4), round(y1, 4)],
                "area_m2": round(area_m2, 4),
            }
        )
    parts = [p for p in _polygons_of(net) if p.covers(pt)]
    if not parts:
        raise SystemExit("라벨이 계산된 실 다각형 밖에 있습니다.")
    return min(parts, key=lambda p: p.area), subtracted


def _simplify_ring(coords, x_axes, y_axes) -> list[tuple[float, float]]:
    pts: list[tuple[float, float]] = []
    seq = list(coords)
    if len(seq) >= 2 and seq[0] == seq[-1]:
        seq = seq[:-1]
    for x, y in seq:
        x, y = float(x), float(y)
        if x_axes:
            nx = min(x_axes, key=lambda a: abs(a - x))
            if abs(nx - x) <= SNAP_MM:
                x = nx
        if y_axes:
            ny = min(y_axes, key=lambda a: abs(a - y))
            if abs(ny - y) <= SNAP_MM:
                y = ny
        if not pts or abs(pts[-1][0] - x) > 1 or abs(pts[-1][1] - y) > 1:
            pts.append((x, y))
    if len(pts) >= 2 and abs(pts[0][0] - pts[-1][0]) <= 1 and abs(pts[0][1] - pts[-1][1]) <= 1:
        pts = pts[:-1]
    simplified = []
    n = len(pts)
    for i in range(n):
        a, b, c = pts[(i - 1) % n], pts[i], pts[(i + 1) % n]
        ab = (b[0] - a[0], b[1] - a[1])
        bc = (c[0] - b[0], c[1] - b[1])
        cross = ab[0] * bc[1] - ab[1] * bc[0]
        if abs(cross) > 1.0:
            simplified.append(b)
    if len(simplified) >= 3:
        pts = simplified
    return pts


def shoelace_m2(pts: list[tuple[float, float]]) -> float:
    n = len(pts)
    acc = 0.0
    for i in range(n):
        x0, y0 = pts[i]
        x1, y1 = pts[(i + 1) % n]
        acc += x0 * y1 - x1 * y0
    return abs(acc) * 0.5 / 1_000_000.0


def point_in_poly(x: float, y: float, pts) -> bool:
    inside = False
    n = len(pts)
    for i in range(n):
        x0, y0 = pts[i]
        x1, y1 = pts[(i + 1) % n]
        if (y0 > y) != (y1 > y):
            xinter = (x1 - x0) * (y - y0) / (y1 - y0 + 1e-12) + x0
            if x < xinter:
                inside = not inside
    return inside


def drawing_area_m2(msp, pts) -> float | None:
    best = None
    for x, y, s in iter_labels(msp):
        m = re.search(r"면적\s*[:：]\s*([0-9]+(?:\.[0-9]+)?)", s)
        if not m:
            continue
        if point_in_poly(x, y, pts):
            best = float(m.group(1))
    return best


def png_transform(meta: dict, png_size: tuple[int, int]):
    bb = meta["bbox_mm"]
    cx0, cy0 = float(bb["xmin"]), float(bb["ymin"])
    cx1, cy1 = float(bb["xmax"]), float(bb["ymax"])
    span_y0 = max(cy1 - cy0, 1.0)
    tick = max(span_y0 * 0.025, 1000.0)
    xmin = cx0 - max(tick * 0.8, 1500.0)
    ymin = cy0 - max(tick * 2.5, 4000.0)
    xmax = cx1 + max(tick * 2.0, 3000.0)
    ymax = cy1 + max(tick * 6.5, 9000.0)
    width, height = png_size
    plot_w = width - PNG_PAD_RIGHT
    plot_h = height - PNG_PAD_TOP - PNG_PAD_BOTTOM

    def to_px(x: float, y: float) -> tuple[float, float]:
        c = (x - xmin) / (xmax - xmin) * plot_w
        r = PNG_PAD_TOP + (ymax - y) / (ymax - ymin) * plot_h
        return c, r

    return to_px


# floor PNG 실명과 같은 픽셀 높이. render_wall_dxf_png (dpi 200):
# fs = clamp(char_height / mm_per_inch * 72 * 1.6, 5, 7) * 2
_SHEET_DPI = 200.0


def sheet_label_px(px_per_mm: float, text_height_mm: float) -> int:
    """검증 도면 PNG에 이미 그려진 실명과 같은 글자 높이."""
    mm_per_inch = _SHEET_DPI / max(px_per_mm, 1e-6)
    fs = (max(text_height_mm, 1.0) / mm_per_inch) * 72.0 * 1.6
    fs = min(max(fs, 5.0), 7.0) * 2.0
    return max(22, int(round(fs * _SHEET_DPI / 72.0)))


def _font(size: int) -> ImageFont.ImageFont:
    for path, index in (
        ("/System/Library/Fonts/Supplemental/AppleGothic.ttf", 0),
        ("/System/Library/Fonts/AppleSDGothicNeo.ttc", 0),
        ("/Library/Fonts/Arial Unicode.ttf", 0),
    ):
        if Path(path).is_file():
            try:
                return ImageFont.truetype(path, size, index=index)
            except TypeError:
                return ImageFont.truetype(path, size)
    return ImageFont.load_default()


def render_overlay(png_path: Path, meta: dict, pts, info: dict, out_path: Path) -> None:
    im = Image.open(png_path).convert("RGBA")
    to_px = png_transform(meta, im.size)
    margin = 3200.0
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    c0, r_bottom = to_px(min(xs) - margin, min(ys) - margin)
    c1, r_top = to_px(max(xs) + margin, max(ys) + margin)
    left, top = int(math.floor(min(c0, c1))) - 8, int(math.floor(min(r_top, r_bottom))) - 8
    right, bottom = int(math.ceil(max(c0, c1))) + 8, int(math.ceil(max(r_top, r_bottom))) + 8
    left, top = max(0, left), max(0, top)
    right, bottom = min(im.width, right), min(im.height, bottom)
    crop = im.crop((left, top, right, bottom))
    poly = []
    for x, y in pts:
        c, r = to_px(x, y)
        poly.append((c - left, r - top))
    overlay = Image.new("RGBA", crop.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    draw.polygon(poly, fill=(37, 130, 255, 96))
    draw.line(poly + [poly[0]], fill=(0, 70, 190, 230), width=2)
    composed = Image.alpha_composite(crop, overlay)
    scale = 2
    composed = composed.resize((composed.width * scale, composed.height * scale), Image.Resampling.LANCZOS)
    draw = ImageDraw.Draw(composed)
    poly_s = [(p[0] * scale, p[1] * scale) for p in poly]
    draw.line(poly_s + [poly_s[0]], fill=(0, 70, 190, 255), width=3)

    px_per_mm = abs(to_px(1.0, 0.0)[0] - to_px(0.0, 0.0)[0])
    title_px = sheet_label_px(px_per_mm, float(info.get("text_height_mm") or 375.0)) * scale
    font = _font(title_px)
    font_s = _font(max(22, int(round(title_px * 0.72))))
    lines = [
        info["room"],
        f"벽 안쪽 면적  {info['area_m2']:.2f} m²",
        f"기둥 돌출 제외  {info.get('column_protrusion_m2', 0):.2f} m²",
        f"{info['width_m']:.3f} m × {info['height_m']:.3f} m",
    ]
    if info.get("drawing_area_m2") is not None:
        lines.append(f"도면 표기  {info['drawing_area_m2']:.0f} m²")
    # 실 위쪽 여백에 흰 상자
    x_text, y_text = 24, 24
    widths = []
    heights = []
    for i, line in enumerate(lines):
        fnt = font if i == 0 else font_s
        box = draw.textbbox((0, 0), line, font=fnt)
        widths.append(box[2] - box[0])
        heights.append(box[3] - box[1])
    box_w = max(widths) + 36
    box_h = sum(heights) + 16 * len(lines) + 20
    draw.rounded_rectangle((16, 16, 16 + box_w, 16 + box_h), radius=12, fill=(255, 255, 255, 230), outline=(0, 70, 190, 255), width=2)
    y = y_text
    for i, line in enumerate(lines):
        fnt = font if i == 0 else font_s
        fill = (10, 40, 90, 255) if i == 0 else (30, 30, 30, 255)
        draw.text((x_text + 8, y), line, font=fnt, fill=fill)
        y += heights[i] + 14
    out_path.parent.mkdir(parents=True, exist_ok=True)
    composed.convert("RGB").save(out_path, quality=95)


def safe_stem(room: str) -> str:
    s = norm_name(room).replace("#", "")
    s = re.sub(r"[^\w가-힣.+-]+", "_", s)
    return s or "room"


def _span_of(line: LineString) -> tuple[str, float, float, float]:
    x0, y0 = line.coords[0]
    x1, y1 = line.coords[-1]
    if abs(y1 - y0) <= abs(x1 - x0):
        return ("h", (float(y0) + float(y1)) * 0.5, min(float(x0), float(x1)), max(float(x0), float(x1)))
    return ("v", (float(x0) + float(x1)) * 0.5, min(float(y0), float(y1)), max(float(y0), float(y1)))


def _swing_doors(msp, lines: list[LineString], origin: tuple[float, float], pad: float) -> list[dict]:
    """여닫이 문. 힌지와, 그 개구를 막는 문선을 돌려준다.

    문선은 힌지 뒤의 벽과 문짝 끝의 벽을 문 방향으로 잇는다.
    """
    if not lines:
        return []
    ox, oy = origin
    walls = None
    for grid in (1.0, 5.0, 10.0):
        cleaned = _sanitize_lines(lines, grid)
        if not cleaned:
            continue
        try:
            walls = unary_union(cleaned)
            break
        except GEOSException:
            walls = None
    if walls is None or walls.is_empty:
        return []
    found: dict[tuple[int, int], dict] = {}
    for entity in msp:
        if entity.dxftype() != "ARC" or getattr(entity.dxf, "layer", None) != "DOOR":
            continue
        try:
            radius = float(entity.dxf.radius)
            start_angle = float(entity.dxf.start_angle)
            end_angle = float(entity.dxf.end_angle)
            center = entity.dxf.center
        except Exception:  # noqa: BLE001
            continue
        sweep = (end_angle - start_angle) % 360.0
        if not (600.0 <= radius <= 1600.0 and 70.0 <= sweep <= 110.0):
            continue
        hx, hy = float(center.x), float(center.y)
        if abs(hx - ox) > pad or abs(hy - oy) > pad:
            continue
        for angle in (start_angle, end_angle):
            rad = math.radians(angle)
            ux, uy = math.cos(rad), math.sin(rad)
            ray = LineString(
                [(hx - ux * 800.0, hy - uy * 800.0), (hx + ux * (radius + 200.0), hy + uy * (radius + 200.0))]
            )
            inter = walls.intersection(ray)
            if inter.is_empty:
                continue
            geoms = list(inter.geoms) if hasattr(inter, "geoms") else [inter]
            alongs: list[float] = []
            for geom in geoms:
                coords: list[tuple[float, float]] = []
                if geom.geom_type == "Point":
                    coords = [(float(geom.x), float(geom.y))]
                elif geom.geom_type == "LineString":
                    coords = [(float(x), float(y)) for x, y in geom.coords]
                elif geom.geom_type == "MultiLineString":
                    for part in geom.geoms:
                        coords.extend((float(x), float(y)) for x, y in part.coords)
                for x, y in coords:
                    alongs.append((x - hx) * ux + (y - hy) * uy)
            behind = [value for value in alongs if -700.0 <= value <= -30.0]
            ahead = [value for value in alongs if radius * 0.75 <= value <= radius + 180.0]
            if not behind or not ahead:
                continue
            start_at, end_at = min(behind), min(ahead)
            if end_at - start_at < radius * 0.5:
                continue
            line = LineString(
                [(hx + ux * start_at, hy + uy * start_at), (hx + ux * end_at, hy + uy * end_at)]
            )
            key = (round(hx), round(hy))
            door = found.setdefault(
                key,
                {"kind": "swing", "x": hx, "y": hy, "radius": radius, "lines": [], "span": None},
            )
            door["lines"].append(line)
    doors = list(found.values())
    for door in doors:
        door["span"] = _span_of(min(door["lines"], key=lambda line: line.length))
    return doors


def _door_thresholds(msp, lines: list[LineString], origin: tuple[float, float], pad: float) -> list[LineString]:
    """여닫이 문 개구를 문선으로 막는다. 면적은 그 선에서 멈추고 문 밖으로 넘어가지 않는다."""
    lines_out: list[LineString] = []
    for door in _swing_doors(msp, lines, origin, pad):
        lines_out.extend(door["lines"])
    return lines_out


def _leaf_doors_on_boundary(msp, face: Polygon) -> list[dict]:
    """닫힌 실의 테두리 위에 놓인 DOOR 직선. 스윙 호는 넣지 않는다."""
    ring = face.exterior
    doors: list[dict] = []
    for entity in msp:
        if getattr(entity.dxf, "layer", None) != DOOR_LAYER or entity.dxftype() == "ARC":
            continue
        parts: list[tuple[float, float, float, float]] = []
        if entity.dxftype() == "LINE":
            parts.append(
                (
                    float(entity.dxf.start.x),
                    float(entity.dxf.start.y),
                    float(entity.dxf.end.x),
                    float(entity.dxf.end.y),
                )
            )
        elif entity.dxftype() == "LWPOLYLINE":
            pts = [(float(a), float(b)) for a, b in entity.get_points("xy")]
            count = len(pts)
            for index in range(count - 1 + (1 if entity.closed else 0)):
                start, end = pts[index], pts[(index + 1) % count]
                parts.append((start[0], start[1], end[0], end[1]))
        on_ring: list[LineString] = []
        for x0, y0, x1, y1 in parts:
            if math.hypot(x1 - x0, y1 - y0) < 600.0:
                continue
            line = LineString([(x0, y0), (x1, y1)])
            if float(ring.distance(line.interpolate(0.5, normalized=True))) <= 80.0:
                on_ring.append(line)
        if not on_ring:
            continue
        leaf = max(on_ring, key=lambda line: line.length)
        span = _span_of(leaf)
        doors.append(
            {
                "kind": "leaf",
                "x": (leaf.coords[0][0] + leaf.coords[-1][0]) * 0.5,
                "y": (leaf.coords[0][1] + leaf.coords[-1][1]) * 0.5,
                "radius": leaf.length,
                "lines": on_ring,
                "span": span,
            }
        )
    return doors


def _boundary_doors(doors: list[dict], face: Polygon) -> list[dict]:
    """닫힌 실의 벽 테두리에서 DOOR_TOUCH_MM 안에 문선이 있는 여닫이만 고른다."""
    ring = face.exterior
    kept: list[dict] = []
    for door in doors:
        hinge = Point(door["x"], door["y"])
        nearest = min(door["lines"], key=lambda line: float(ring.distance(line)))
        near = float(ring.distance(nearest)) <= DOOR_TOUCH_MM or float(ring.distance(hinge)) <= DOOR_TOUCH_MM
        if near:
            chosen = dict(door)
            chosen["span"] = _span_of(nearest)
            kept.append(chosen)
    kept.sort(key=lambda door: (round(door["x"], 1), round(door["y"], 1)))
    return kept


def _room_lines(msp, segs, origin, boxes, open_doors):
    """라벨이 들어 있는 닫힌 면. open_doors 의 여닫이는 개구로 남긴다."""
    lx, ly = origin
    pad = 12000.0
    open_keys = {(round(door["x"]), round(door["y"])) for door in open_doors}
    while pad <= 50000:
        h_final, v_final, dsegs, x_axes, y_axes = bridged_runs(
            segs, origin, pad + 2000, boxes=boxes, open_doors=open_doors
        )
        lines = _cap_wall_ends(
            _snap_endpoints(_lines_from_runs(h_final, v_final, dsegs), JOIN_MM),
            WALL_CAP_MM,
        )
        swings = _swing_doors(msp, lines, origin, pad + 2000)
        for door in swings:
            if (round(door["x"]), round(door["y"])) in open_keys:
                continue
            lines.extend(door["lines"])
        found = _face_at(lines, lx, ly)
        if found is not None and not _touches_window(found, origin, pad + 2000):
            return found, x_axes, y_axes, swings
        pad += 8000
    return None, [], [], []


def evaluate(
    dxf_path: Path,
    meta_path: Path,
    png_path: Path,
    room: str,
    out_dir: Path,
    at: tuple[float, float] | None = None,
    door: str = "close",
) -> dict:
    if door not in ("close", "open"):
        raise SystemExit("--door 는 close 또는 open 입니다.")
    doc = ezdxf.readfile(str(dxf_path))
    msp = doc.modelspace()
    lx, ly, label = find_room_label(msp, room, at)
    boxes = column_boxes(msp)
    closed_segs = wall_segments(msp, boxes)
    face, x_axes, y_axes, swings = _room_lines(msp, closed_segs, (lx, ly), boxes, [])
    if face is None:
        raise SystemExit("벽이 실을 닫지 않아 면적이 창 밖으로 새었습니다.")
    boundary = _boundary_doors(swings, face) + _leaf_doors_on_boundary(msp, face)
    boundary.sort(key=lambda door: (round(door["x"], 1), round(door["y"], 1)))
    open_doors = boundary if door == "open" else []
    if open_doors:
        segs = wall_segments(msp, boxes, open_doors=open_doors)
        opened, ox, oy, _swings = _room_lines(msp, segs, (lx, ly), boxes, open_doors)
        if opened is None:
            raise SystemExit("문을 열면 벽이 실을 닫지 않아 면적이 창 밖으로 새었습니다.")
        face, x_axes, y_axes = opened, ox, oy

    gross_m2 = face.area / 1_000_000.0
    net, protrusions = _subtract_columns(face, boxes, (lx, ly))
    pts = _simplify_ring(net.exterior.coords, x_axes, y_axes)
    holes = [_simplify_ring(ring.coords, x_axes, y_axes) for ring in net.interiors]
    holes = [h for h in holes if len(h) >= 3]
    if len(pts) < 3 or not point_in_poly(lx, ly, pts):
        raise SystemExit("라벨이 계산된 실 다각형 밖에 있습니다.")
    area = shoelace_m2(pts) - sum(shoelace_m2(h) for h in holes)
    minx, miny, maxx, maxy = face.bounds
    width_m = (maxx - minx) / 1000.0
    height_m = (maxy - miny) / 1000.0
    drawn = drawing_area_m2(msp, pts)
    rectangular = abs(area - width_m * height_m) / area < 0.01 if area else False
    seen: set[tuple[str, int, int]] = set()
    enclosed = []
    for x, y, text in collect_room_labels(msp):
        if not net.covers(Point(x, y)):
            continue
        key = (norm_name(text), round(x), round(y))
        if key in seen:
            continue
        seen.add(key)
        enclosed.append({"text": text, "x": round(x, 1), "y": round(y, 1)})
    enclosed.sort(key=lambda item: (norm_name(item["text"]), item["x"], item["y"]))
    text_height_mm = 375.0
    nearest = 1e18
    for entity in msp:
        rec = _label_record(entity)
        if rec is None:
            continue
        dist = abs(rec[0] - lx) + abs(rec[1] - ly)
        if dist < nearest:
            nearest = dist
            text_height_mm = rec[3]
    info = {
        "room": label,
        "text_height_mm": text_height_mm,
        "floor_dxf": str(dxf_path),
        "label_mm": {"x": lx, "y": ly},
        "area_m2": round(area, 4),
        "area_before_columns_m2": round(gross_m2, 4),
        "column_protrusion_m2": round(sum(p["area_m2"] for p in protrusions), 4),
        "column_protrusions": protrusions,
        "width_m": round(width_m, 4),
        "height_m": round(height_m, 4),
        "rectangular": rectangular,
        "polygon_mm": [[round(x, 4), round(y, 4)] for x, y in pts],
        "enclosed_labels": enclosed,
        "drawing_area_m2": drawn,
        "boundary_doors": [
            {
                "kind": item["kind"],
                "x": round(item["x"], 1),
                "y": round(item["y"], 1),
                "state": "open" if door == "open" else "close",
            }
            for item in boundary
        ],
        "rules": {
            "boundary": "inner face of WALL, WINDOW, COLUMN, and DOOR lines",
            "door": door,
            "boundary_door_mm": DOOR_TOUCH_MM,
            "door_gap_mm": DOOR_GAP_MM,
            "hbeam": "column protrusion inside the inner face is always subtracted",
            "method": "polygonize",
        },
    }
    stem = safe_stem(label)
    out_dir.mkdir(parents=True, exist_ok=True)
    overlay = out_dir / f"{stem}_overlay.png"
    render_overlay(png_path, json.loads(meta_path.read_text(encoding="utf-8")), pts, info, overlay)
    info["overlay_png"] = str(overlay)
    json_path = out_dir / f"{stem}.json"
    json_path.write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")
    info["json"] = str(json_path)
    return info


def main() -> None:
    parser = argparse.ArgumentParser(description="빨간 WALL로 둘러싼 실 면적")
    parser.add_argument("--dxf", type=Path, required=True)
    parser.add_argument("--meta", type=Path, required=True)
    parser.add_argument("--png", type=Path, required=True)
    parser.add_argument("--room", required=True)
    parser.add_argument(
        "--door",
        choices=("close", "open"),
        default="close",
        help="닫힌 실의 벽 테두리에 놓인 DOOR 직선과, 테두리 450 mm 안의 여닫이만 연다. close(기본)는 그 문도 막는다. 테두리 밖 DOOR는 어느 쪽이든 닫힌 경계로 둔다.",
    )
    parser.add_argument("--x", type=float, default=None, help="중복 라벨일 때 선택할 x (mm)")
    parser.add_argument("--y", type=float, default=None, help="중복 라벨일 때 선택할 y (mm)")
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()
    if (args.x is None) != (args.y is None):
        raise SystemExit("--x 와 --y 는 함께 지정하세요.")
    at = (args.x, args.y) if args.x is not None else None
    # 번호 붙은 room_eval_N 은 만들지 않는다. 항상 DXF 옆 room_eval 에 덮어쓴다.
    out = args.dxf.parent / "room_eval"
    info = evaluate(args.dxf, args.meta, args.png, args.room, out, at, door=args.door)
    print(f"room: {info['room']}")
    print(f"door: {info['rules']['door']}")
    if info["boundary_doors"]:
        shown_doors = ", ".join(
            f"{item['state']} ({item['x']:.0f},{item['y']:.0f})" for item in info["boundary_doors"]
        )
        print(f"boundary_doors: {shown_doors}")
    print(f"area_m2: {info['area_m2']:.2f}")
    print(f"column_protrusion_m2: {info['column_protrusion_m2']:.2f}")
    print(f"width_m: {info['width_m']:.3f}")
    print(f"height_m: {info['height_m']:.3f}")
    if info["enclosed_labels"]:
        shown = ", ".join(
            f"{item['text']} ({item['x']:.0f},{item['y']:.0f})" for item in info["enclosed_labels"]
        )
        print(f"enclosed_labels: {shown}")
    if info["drawing_area_m2"] is not None:
        print(f"drawing_area_m2: {info['drawing_area_m2']:.0f}")
    print(f"overlay: {info['overlay_png']}")
    print(f"json: {info['json']}")


if __name__ == "__main__":
    main()
