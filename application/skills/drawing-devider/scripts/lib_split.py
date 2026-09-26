#!/usr/bin/env python3
"""drawing-devider shared helpers."""

from __future__ import annotations

import json
import math
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

GEOM_TYPES = frozenset(
    {"LINE", "LWPOLYLINE", "POLYLINE", "ARC", "CIRCLE", "ELLIPSE", "SPLINE", "HATCH"}
)
TEXT_TYPES = frozenset({"TEXT", "MTEXT"})

FURNITURE_KEYWORDS = (
    "가구", "chair", "피트니스", "화)", "DOOR", "DOR_", "도어", "자동문",
    "락커", "라커", "샤워", "신발", "러닝", "파우더", "큐비클", "대변기",
    "소변", "객석", "좌석", "모바일", "회의", "업다운", "절취", "RAIN",
    "rain", "입면", "슬라이딩", "접견",
)


def is_furniture(name: str) -> bool:
    lower = name.lower()
    return any(k.lower() in lower for k in FURNITURE_KEYWORDS)


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def ceil_div(a: float, b: float) -> int:
    return max(1, int(math.ceil(a / b)))


def grid_for_span(
    width_m: float,
    height_m: float,
    *,
    max_tile_m: float = 60.0,
    overlap_m: float = 0.0,
) -> dict[str, float | int]:
    """폭·깊이(m)에 대해 max_tile_m 이하 타일 격자 계산.

    중간 타일은 양쪽에 overlap이 붙으므로
    step ≤ max_tile_m - 2*overlap_m 이 되도록 nx/ny를 잡는다.
    """
    if width_m <= 0 or height_m <= 0:
        raise ValueError(f"invalid span: {width_m} x {height_m}")
    ov = max(0.0, float(overlap_m))
    if ov * 2 >= max_tile_m:
        raise ValueError(f"overlap_m too large for max_tile_m={max_tile_m}")
    step_max = max_tile_m - 2 * ov
    nx = ceil_div(width_m, step_max)
    ny = ceil_div(height_m, step_max)
    tile_w = width_m / nx
    tile_h = height_m / ny
    # worst-case with overlap (interior tile)
    worst_w = tile_w + 2 * ov
    worst_h = tile_h + 2 * ov
    if worst_w > max_tile_m + 1e-6 or worst_h > max_tile_m + 1e-6:
        raise RuntimeError(
            f"tile exceeds max even after refine: "
            f"{worst_w:.3f}x{worst_h:.3f} m (max {max_tile_m})"
        )
    return {
        "nx": nx,
        "ny": ny,
        "n_tiles": nx * ny,
        "tile_w_m": tile_w,
        "tile_h_m": tile_h,
        "worst_w_m": worst_w,
        "worst_h_m": worst_h,
        "width_m": width_m,
        "height_m": height_m,
        "max_tile_m": max_tile_m,
        "overlap_m": ov,
        "step_max_m": step_max,
    }


def iter_tile_boxes(
    xmin: float,
    ymin: float,
    xmax: float,
    ymax: float,
    *,
    nx: int,
    ny: int,
    overlap_mm: float = 1000.0,
) -> list[dict[str, Any]]:
    """mm bbox를 nx×ny 그리드로 분할 (overlap 포함)."""
    width = xmax - xmin
    height = ymax - ymin
    step_x = width / nx
    step_y = height / ny
    tiles: list[dict[str, Any]] = []
    for r in range(ny):
        for c in range(nx):
            x0 = xmin + c * step_x - (overlap_mm if c > 0 else 0)
            x1 = xmin + (c + 1) * step_x + (overlap_mm if c < nx - 1 else 0)
            y0 = ymin + r * step_y - (overlap_mm if r > 0 else 0)
            y1 = ymin + (r + 1) * step_y + (overlap_mm if r < ny - 1 else 0)
            x0, y0 = max(xmin, x0), max(ymin, y0)
            x1, y1 = min(xmax, x1), min(ymax, y1)
            tid = f"R{r}C{c}"
            tiles.append(
                {
                    "id": tid,
                    "row": r,
                    "col": c,
                    "xmin": x0,
                    "ymin": y0,
                    "xmax": x1,
                    "ymax": y1,
                    "width_mm": x1 - x0,
                    "height_mm": y1 - y0,
                    "width_m": (x1 - x0) / 1000.0,
                    "height_m": (y1 - y0) / 1000.0,
                }
            )
    return tiles


def assert_tile_limit(tiles: Iterable[dict], max_tile_m: float = 60.0) -> None:
    for t in tiles:
        if t["width_m"] > max_tile_m + 0.05 or t["height_m"] > max_tile_m + 0.05:
            raise RuntimeError(
                f"tile {t['id']} exceeds {max_tile_m}m: "
                f"{t['width_m']:.3f}x{t['height_m']:.3f}"
            )


def _line_centroids(entities) -> tuple[list[float], list[float]]:
    cxs: list[float] = []
    cys: list[float] = []
    for e in entities:
        if e.dxftype() != "LINE":
            continue
        try:
            x0, y0 = float(e.dxf.start.x), float(e.dxf.start.y)
            x1, y1 = float(e.dxf.end.x), float(e.dxf.end.y)
        except Exception:
            continue
        cxs.append((x0 + x1) / 2)
        cys.append((y0 + y1) / 2)
    return cxs, cys


def _primary_x_band(cxs: list[float]) -> tuple[float, float] | None:
    """이중 레이아웃이면 LINE 많은 쪽 X 구간, 아니면 전체 X."""
    if len(cxs) < 30:
        return None
    xs = sorted(cxs)
    gaps = [(xs[i + 1] - xs[i], i) for i in range(len(xs) - 1)]
    gaps.sort(reverse=True)
    gap, idx = gaps[0]
    if gap < 20_000:
        return min(cxs), max(cxs)
    split = (xs[idx] + xs[idx + 1]) / 2
    left = [x for x in cxs if x < split]
    right = [x for x in cxs if x >= split]
    use = right if len(right) >= len(left) else left
    return min(use), max(use)


def find_primary_line_bbox(entities) -> tuple[float, float, float, float] | None:
    """LINE 중심 기준 최대 gap으로 primary 클러스터 bbox (mm).

    clean용 — Y도 LINE만으로 잡는다 (벽 밀도 기준).
    """
    cxs, cys = _line_centroids(entities)
    band = _primary_x_band(cxs)
    if band is None:
        return None
    xmin, xmax = band
    ys = [cys[i] for i, x in enumerate(cxs) if xmin <= x <= xmax]
    if not ys:
        return None
    pad = 1000.0
    return xmin - pad, min(ys) - pad, xmax + pad, max(ys) + pad


def find_primary_floor_bbox(
    entities,
    *,
    core_bbox: tuple[float, float, float, float] | None = None,
    pad_mm: float = 2000.0,
    expand_mm: float = 80_000.0,
) -> tuple[float, float, float, float] | None:
    """원본(floor_original)용 primary bbox.

    core_bbox(평면/코어/기둥 LINE primary)를 핵으로 두고,
    같은 대역의 LWPOLY/ARC/CIRCLE로 Y·X를 expand_mm(기본 80 m)까지 확장.
    그 밖 이상치 좌표는 버린다.
    """
    core = core_bbox or find_primary_line_bbox(entities)
    if core is None:
        return None
    x0, y0, x1, y1 = core
    geom_extra = frozenset({"LWPOLYLINE", "POLYLINE", "ARC", "CIRCLE", "LINE"})
    gxs: list[float] = [x0, x1]
    gys: list[float] = [y0, y1]
    for e in entities:
        if e.dxftype() not in geom_extra:
            continue
        c = entity_centroid(e)
        if c is None:
            continue
        cx, cy = c
        if cx < x0 - expand_mm or cx > x1 + expand_mm:
            continue
        if cy < y0 - expand_mm or cy > y1 + expand_mm:
            continue
        gxs.append(cx)
        gys.append(cy)
    return (
        min(gxs) - pad_mm,
        min(gys) - pad_mm,
        max(gxs) + pad_mm,
        max(gys) + pad_mm,
    )


def expand_bbox_include_outer_walls(
    entities,
    bbox: tuple[float, float, float, float],
    *,
    min_len_mm: float = 30_000.0,
    search_up_mm: float = 30_000.0,
    search_down_mm: float = 15_000.0,
    wall_pad_mm: float = 2500.0,
    min_band_len_mm: float = 80_000.0,
) -> tuple[float, float, float, float]:
    """긴 가로 LINE(외벽)이 crop 밖에 있으면 Y를 확장.

    floor_original PNG에서 상단이 잘리는 경우 방지.
    """
    x0, y0, x1, y1 = bbox
    by_top: dict[int, float] = {}
    by_bot: dict[int, float] = {}
    for e in entities:
        if e.dxftype() != "LINE":
            continue
        try:
            xa, ya = float(e.dxf.start.x), float(e.dxf.start.y)
            xb, yb = float(e.dxf.end.x), float(e.dxf.end.y)
        except Exception:  # noqa: BLE001
            continue
        dx, dy = abs(xb - xa), abs(yb - ya)
        if dy >= 30.0 or dx < min_len_mm:
            continue
        mx = (xa + xb) / 2.0
        if mx < x0 - 10_000 or mx > x1 + 10_000:
            continue
        y = (ya + yb) / 2.0
        key = int(round(y / 100.0) * 100)
        if y1 - 2000 <= y <= y1 + search_up_mm:
            by_top[key] = by_top.get(key, 0.0) + dx
        if y0 - search_down_mm <= y <= y0 + 2000:
            by_bot[key] = by_bot.get(key, 0.0) + dx

    ny0, ny1 = y0, y1
    top_hits = [k for k, L in by_top.items() if L >= min_band_len_mm]
    if top_hits:
        ny1 = max(ny1, float(max(top_hits)) + wall_pad_mm)
    bot_hits = [k for k, L in by_bot.items() if L >= min_band_len_mm]
    if bot_hits:
        ny0 = min(ny0, float(min(bot_hits)) - wall_pad_mm)
    return (x0, ny0, x1, ny1)


def entity_centroid(e) -> tuple[float, float] | None:
    t = e.dxftype()
    try:
        if t == "LINE":
            return (
                (float(e.dxf.start.x) + float(e.dxf.end.x)) / 2,
                (float(e.dxf.start.y) + float(e.dxf.end.y)) / 2,
            )
        if t == "LWPOLYLINE":
            pts = list(e.get_points("xy"))
            if not pts:
                return None
            return (
                sum(float(p[0]) for p in pts) / len(pts),
                sum(float(p[1]) for p in pts) / len(pts),
            )
        if t in {"CIRCLE", "ARC"}:
            return float(e.dxf.center.x), float(e.dxf.center.y)
        if t in TEXT_TYPES:
            return float(e.dxf.insert.x), float(e.dxf.insert.y)
    except Exception:
        return None
    return None


def filter_bbox(entities, xmin, ymin, xmax, ymax):
    out = []
    for e in entities:
        c = entity_centroid(e)
        if c and xmin <= c[0] <= xmax and ymin <= c[1] <= ymax:
            out.append(e)
    return out


def count_types(entities) -> dict[str, int]:
    return dict(Counter(e.dxftype() for e in entities))
