#!/usr/bin/env python3
"""실명 라벨이 들어 있는 공간을 빨간 WALL 안쪽 면으로 면적 계산."""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from collections import defaultdict
from pathlib import Path

import cv2
import ezdxf
import numpy as np
from PIL import Image, ImageDraw, ImageFont

Image.MAX_IMAGE_PIXELS = None

WALL_LAYER = "WALL"
AXIS_TOL_MM = 20.0
CLUSTER_TOL_MM = 15.0
JOIN_MM = 80.0
DOOR_GAP_MM = 2400.0
RASTER_MM = 2.0
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


def find_room_label(msp, room: str) -> tuple[float, float, str]:
    want = norm_name(room)
    hits = [(x, y, s) for x, y, s in iter_labels(msp) if norm_name(s) == want]
    if not hits:
        raise SystemExit(f"실명을 찾지 못했습니다: {room}")
    if len(hits) > 1:
        # 같은 이름이면 첫 좌표. 도면에 중복이면 모두 알린다.
        coords = ", ".join(f"({x:.0f},{y:.0f})" for x, y, _ in hits)
        print(f"warning: '{room}' 라벨 {len(hits)}개 — {coords}. 첫 라벨을 사용합니다.", file=sys.stderr)
    return hits[0]


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
    """H-Beam: 변 0.45–1.5 m 축평행 정사각. 동심 쌍 또는 중심 짧은 선."""
    squares: list[tuple[float, float, float, float]] = []
    for e in msp:
        if e.dxf.layer != WALL_LAYER or e.dxftype() != "LWPOLYLINE" or not e.closed:
            continue
        pts = [(float(a), float(b)) for a, b in e.get_points("xy")]
        if len(pts) < 4:
            continue
        box = _rect_box(pts)
        if box:
            squares.append(box)

    def center(b):
        return ((b[0] + b[2]) * 0.5, (b[1] + b[3]) * 0.5)

    ticks: list[tuple[float, float, float]] = []
    for e in msp:
        if e.dxf.layer != WALL_LAYER or e.dxftype() != "LINE":
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
    return outers


def _inside_box(x: float, y: float, boxes) -> bool:
    for x0, y0, x1, y1 in boxes:
        if x0 - 5 <= x <= x1 + 5 and y0 - 5 <= y <= y1 + 5:
            return True
    return False


def wall_segments(msp, boxes) -> list[tuple[float, float, float, float]]:
    segs: list[tuple[float, float, float, float]] = []
    for e in msp:
        if e.dxf.layer != WALL_LAYER:
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


def _bridge(merged: list[list[float]], max_gap: float) -> list[list[float]]:
    if not merged:
        return []
    out = [merged[0][:]]
    for a, b in merged[1:]:
        gap = a - out[-1][1]
        if 0 < gap <= max_gap:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return out


def bridged_runs(segs, origin: tuple[float, float], pad: float):
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
    h_final = {y: _bridge(_merge_intervals(iv, JOIN_MM), DOOR_GAP_MM) for y, iv in h_int.items()}
    v_final = {x: _bridge(_merge_intervals(iv, JOIN_MM), DOOR_GAP_MM) for x, iv in v_int.items()}
    return h_final, v_final, dsegs, x_axes, y_axes


def flood_room(h_final, v_final, dsegs, seed, pad):
    ox, oy = seed
    xmin, xmax = ox - pad, ox + pad
    ymin, ymax = oy - pad, oy + pad
    res = RASTER_MM
    width = int(math.ceil((xmax - xmin) / res)) + 3
    height = int(math.ceil((ymax - ymin) / res)) + 3
    wall = np.zeros((height, width), np.uint8)

    def col(x):
        return int(round((x - xmin) / res))

    def row(y):
        return int(round((ymax - y) / res))

    for y, ivs in h_final.items():
        r = row(y)
        if not 0 <= r < height:
            continue
        for a, b in ivs:
            cv2.line(wall, (col(a), r), (col(b), r), 255, 1)
    for x, ivs in v_final.items():
        c = col(x)
        if not 0 <= c < width:
            continue
        for a, b in ivs:
            cv2.line(wall, (c, row(a)), (c, row(b)), 255, 1)
    for x0, y0, x1, y1 in dsegs:
        cv2.line(wall, (col(x0), row(y0)), (col(x1), row(y1)), 255, 1)

    sc, sr = col(ox), row(oy)
    if wall[sr, sc]:
        found = None
        for rad in range(1, 30):
            for dy in range(-rad, rad + 1):
                for dx in range(-rad, rad + 1):
                    rr, cc = sr + dy, sc + dx
                    if 0 <= rr < height and 0 <= cc < width and wall[rr, cc] == 0:
                        found = (cc, rr)
                        break
                if found:
                    break
            if found:
                break
        if not found:
            raise SystemExit("라벨 위치가 벽 위에 있고 빈 칸을 찾지 못했습니다.")
        sc, sr = found

    free = np.where(wall == 0, 255, 0).astype(np.uint8)
    mask = np.zeros((height + 2, width + 2), np.uint8)
    cv2.floodFill(free, mask, (sc, sr), 128)
    room = free == 128
    touches = bool(room[0].any() or room[-1].any() or room[:, 0].any() or room[:, -1].any())
    return room, touches, xmin, ymax, res


def subtract_column_protrusions(room, xmin, ymax, res, boxes):
    """실 마스크 안에서 H-Beam 박스와 겹치는 칸을 지운다.

    벽 두께 안에만 있는 기둥은 실 마스크 밖이라 빠지지 않는다.
    """
    net = room.copy()
    height, width = net.shape
    subtracted = []

    def col(x: float) -> int:
        return int(round((x - xmin) / res))

    def row(y: float) -> int:
        return int(round((ymax - y) / res))

    for x0, y0, x1, y1 in boxes:
        c0, c1 = sorted((col(x0), col(x1)))
        r0, r1 = sorted((row(y0), row(y1)))
        c0, c1 = max(0, c0), min(width - 1, c1)
        r0, r1 = max(0, r0), min(height - 1, r1)
        if c1 < c0 or r1 < r0:
            continue
        region = net[r0 : r1 + 1, c0 : c1 + 1]
        hit = int(region.sum())
        area_m2 = hit * (res / 1000.0) ** 2
        if area_m2 < 0.02:
            continue
        region[:] = False
        subtracted.append(
            {
                "bbox_mm": [round(x0, 4), round(y0, 4), round(x1, 4), round(y1, 4)],
                "area_m2": round(area_m2, 4),
            }
        )
    return net, subtracted


def polygon_from_mask(room, xmin, ymax, res, x_axes, y_axes):
    mask_u8 = room.astype(np.uint8) * 255
    contours, _ = cv2.findContours(mask_u8, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    if not contours:
        raise SystemExit("실 윤곽을 만들지 못했습니다.")
    contour = max(contours, key=cv2.contourArea)
    epsilon = max(8.0, 20.0 / res)  # 약 20 mm
    approx = cv2.approxPolyDP(contour, epsilon, True)
    pts = []
    for p in approx.reshape(-1, 2):
        c, r = float(p[0]), float(p[1])
        x = xmin + c * res
        y = ymax - r * res
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
    # 같은 직선 위의 중간 점은 뺀다.
    simplified = []
    n = len(pts)
    for i in range(n):
        a, b, c = pts[(i - 1) % n], pts[i], pts[(i + 1) % n]
        ab = (b[0] - a[0], b[1] - a[1])
        bc = (c[0] - b[0], c[1] - b[1])
        cross = ab[0] * bc[1] - ab[1] * bc[0]
        if abs(cross) > 1.0:  # mm², 거의 일직선이면 제거
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


def _font(size: int) -> ImageFont.ImageFont:
    for path, index in (
        ("/System/Library/Fonts/AppleSDGothicNeo.ttc", 0),
        ("/System/Library/Fonts/Supplemental/AppleGothic.ttf", 0),
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

    font = _font(32)
    font_s = _font(22)
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


def evaluate(dxf_path: Path, meta_path: Path, png_path: Path, room: str, out_dir: Path) -> dict:
    doc = ezdxf.readfile(str(dxf_path))
    msp = doc.modelspace()
    lx, ly, label = find_room_label(msp, room)
    boxes = column_boxes(msp)
    segs = wall_segments(msp, boxes)
    pad = 12000.0
    room_mask = None
    xmin = ymax = 0.0
    x_axes: list[float] = []
    y_axes: list[float] = []
    while pad <= 50000:
        h_final, v_final, dsegs, x_axes, y_axes = bridged_runs(segs, (lx, ly), pad + 2000)
        room_mask, touches, xmin, ymax, res = flood_room(h_final, v_final, dsegs, (lx, ly), pad)
        if not touches and int(room_mask.sum()) > 0:
            break
        pad += 8000
    else:
        raise SystemExit("벽이 실을 닫지 않아 면적이 창 밖으로 새었습니다.")

    gross_m2 = float(room_mask.sum()) * (res / 1000.0) ** 2
    room_mask, protrusions = subtract_column_protrusions(room_mask, xmin, ymax, res, boxes)
    pts = polygon_from_mask(room_mask, xmin, ymax, res, x_axes, y_axes)
    if len(pts) < 3 or not point_in_poly(lx, ly, pts):
        raise SystemExit("라벨이 계산된 실 다각형 밖에 있습니다.")
    area = shoelace_m2(pts)
    raster_m2 = float(room_mask.sum()) * (res / 1000.0) ** 2
    if raster_m2 <= 0 or abs(area - raster_m2) / raster_m2 > 0.03:
        raise SystemExit(f"다각형 면적({area:.3f})과 래스터 면적({raster_m2:.3f})이 어긋납니다.")
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    width_m = (max(xs) - min(xs)) / 1000.0
    height_m = (max(ys) - min(ys)) / 1000.0
    drawn = drawing_area_m2(msp, pts)
    rectangular = abs(area - width_m * height_m) / area < 0.01 if area else False
    info = {
        "room": label,
        "floor_dxf": str(dxf_path),
        "label_mm": {"x": lx, "y": ly},
        "area_m2": round(area, 4),
        "area_before_columns_m2": round(gross_m2, 4),
        "column_protrusion_m2": round(sum(p["area_m2"] for p in protrusions), 4),
        "column_protrusions": protrusions,
        "area_raster_m2": round(raster_m2, 4),
        "width_m": round(width_m, 4),
        "height_m": round(height_m, 4),
        "rectangular": rectangular,
        "polygon_mm": [[round(x, 4), round(y, 4)] for x, y in pts],
        "drawing_area_m2": drawn,
        "rules": {
            "boundary": "inner wall face",
            "door_gap_mm": DOOR_GAP_MM,
            "hbeam": "column protrusion inside the inner face is always subtracted",
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
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()
    out = args.out or (args.dxf.parent / "room_eval")
    info = evaluate(args.dxf, args.meta, args.png, args.room, out)
    print(f"room: {info['room']}")
    print(f"area_m2: {info['area_m2']:.2f}")
    print(f"column_protrusion_m2: {info['column_protrusion_m2']:.2f}")
    print(f"width_m: {info['width_m']:.3f}")
    print(f"height_m: {info['height_m']:.3f}")
    if info["drawing_area_m2"] is not None:
        print(f"drawing_area_m2: {info['drawing_area_m2']:.0f}")
    print(f"overlay: {info['overlay_png']}")
    print(f"json: {info['json']}")


if __name__ == "__main__":
    main()
