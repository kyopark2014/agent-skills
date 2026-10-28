#!/usr/bin/env python3
"""floor_wall_validated.dxf 의 실명 라벨마다 벽 안쪽 면을 잡아
floor_label_detected.dxf / .png / .json 으로 쓴다.

면적 계산은 drawing-roomevaluator 와 같다.
면적·영역 제외와 집기·설비 토큰은 drawing-roomevaluator/scripts/label_info.json 을 따른다.
DXF 는 검증 도면을 복사한 뒤 라벨마다 레이어를 더한다.
그 레이어의 HATCH 면적이 그 라벨의 면적이고, 이름과 면적 문자가 같이 있다.
PNG 는 그 DXF 를 검증 도면과 같은 렌더러로 그린 것이다.
"""

from __future__ import annotations

import argparse
import colorsys
import json
import math
import re
import sys
import time
from pathlib import Path

import ezdxf
from ezdxf.colors import float2transparency
from shapely.geometry import LineString, Point, Polygon, box
from shapely.ops import unary_union

_SKILLS_DIR = Path(__file__).resolve().parents[2]
if str(_SKILLS_DIR) not in sys.path:
    sys.path.insert(0, str(_SKILLS_DIR))

from lib_korean_dxf import apply_korean_text  # noqa: E402

ROOM_SCRIPTS = Path(__file__).resolve().parents[2] / "drawing-roomevaluator" / "scripts"
sys.path.insert(0, str(ROOM_SCRIPTS))
import evaluate_room as room  # noqa: E402

_VALIDATOR_SCRIPTS = Path(__file__).resolve().parents[2] / "drawing-llmvalidator" / "scripts"
if str(_VALIDATOR_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_VALIDATOR_SCRIPTS))
from lib_llm_correct import (  # noqa: E402
    bbox_overlaps_xy,
    floor_sheet_mm_per_inch,
    png_text_cap_mm,
    render_wall_dxf_png,
)

_LAYER_BAD = set('<>/\\":;?*=`')
OUT_STEM = "floor_label_detected"
# 라벨이 작은 칸·모서리 주머니에 떨어지면 면적이 이보다 작다(2 m² 이하). 씨앗을 옮겨 다시 잰다.
# 옮겨도 이 이하면 면적 표기에서 버린다.
MIN_LABEL_AREA_M2 = 2.0
TINY_AREA_SKIP_REASON = "면적이 2 m² 이하입니다"
# 가까운 거리부터 왼쪽·오른쪽·위·아래(mm).
NUDGE_STEPS_MM = (100.0, 200.0, 300.0, 400.0, 600.0, 800.0, 1000.0, 1500.0, 2000.0)
# --fill-unlabeled: WALL 래스터 → morph close → connected components → 무명_N.
# --sample-mm 은 래스터 셀 크기(mm). 작을수록 정밀·느림.
DEFAULT_SAMPLE_MM = 100.0
# fill 성분 최소 면적. 실명 라벨의 MIN_LABEL_AREA_M2(2 m²)와 따로 둔다.
FILL_MIN_AREA_M2 = 1.0
# 래스터 contour 를 벽 안쪽 면(detect_one)으로 바꿀 때: CC 와의 겹침·면적 상한.
FILL_REFINE_MIN_OVERLAP = 0.50
FILL_REFINE_MAX_AREA_RATIO = 2.5
FILL_REFINE_MAX_AREA_EXTRA_M2 = 5.0
# 실명 면이 모서리를 비워 둔 칸(가구 WALL 등) — 그 AABB 안 조각은 무명으로 안 붙인다.
FILL_INSIDE_NAMED_BBOX_RATIO = 0.90
FILL_INSIDE_NAMED_BBOX_PAD_MM = 50.0
OCCUPIED_OVERLAP = 0.20
EXTERIOR_AREA_FRAC = 0.85
# 래스터 틈 메움(셀). 문선이 이미 경계에 있으면 작은 값이면 된다.
FILL_CLOSE_CELLS = 3
# 직교 WALL 자유 끝점 L 모서리 갭(mm). 기둥 자리에서 끊긴 코너를 fill 래스터만 잇는다.
FILL_CORNER_GAP_MIN_MM = 200.0
FILL_CORNER_GAP_MAX_MM = 1100.0
FILL_CORNER_JOIN_MM = 50.0
FILL_CORNER_AXIS_MM = 8.0
FILL_MAX_GRID = 8000
FILL_CONTOUR_EPS_PX = 1.5
# llm_review/review.json 의 promote/demote bbox 중 이 글자가 들어가면 무명 제외.
FILL_SKIP_REVIEW_AREA_LABELS = (
    "계단",
    "화장실",
    "WC",
    "승강기",
    "외벽",
    "침대",
)
FILL_SKIP_REVIEW_X_LABELS = ("X", "샤프트")
FILL_SKIP_REVIEW_LABELS = FILL_SKIP_REVIEW_AREA_LABELS + FILL_SKIP_REVIEW_X_LABELS
# 세면대 심볼 → 화장실로 보고 무명 제외.
# 이중 닫힌 LWPOLYLINE(세면기 외곽·내곽) + 배수 CIRCLE, 또는 동심 CIRCLE 타원 세면기.
# 외곽은 llmvalidator 가 WALL 로 올릴 수 있어 BASE/0/WALL 모두 본다.
SINK_DRAIN_R_MIN_MM = 10.0
SINK_DRAIN_R_MAX_MM = 45.0
SINK_OUTER_LONG_MM = (700.0, 950.0)
SINK_OUTER_SHORT_MM = (400.0, 600.0)
SINK_INNER_LONG_MM = (400.0, 600.0)
SINK_INNER_SHORT_MM = (280.0, 480.0)
SINK_NEST_CENTER_MM = 100.0
SINK_DRAIN_NEAR_MM = 350.0
SINK_OVAL_R_MM = (200.0, 250.0)
SINK_OVAL_MATE_DR_MM = (8.0, 20.0)
SINK_OVAL_MATE_CENTER_MM = 15.0
SINK_OVAL_ARC_R_MM = (120.0, 260.0)
SINK_OVAL_ARC_NEAR_MM = 180.0
SINK_OVAL_ARC_MIN = 10
SINK_DEDUPE_MM = 200.0
# WALL 세면기 노치와 칸 사이 틈(경주 부티크 호텔 201호 ≈22 mm).
SINK_NEAR_POLY_MM = 100.0
# 칸 안 TEXT 에 있으면 화장실로 보고 라벨을 붙이지 않음(집기 표기 포함).
FILL_BATHROOM_TEXT = ("세면대", "세면", "화장실", "WC", "W.C", "변기")
# 교차 대각선(X) — 무명 라벨 제외용. 긴 변·대각 상한 없음(크기 무관).
# 문 두께 안 얇은 X 만 빼고, WALL 승격 한도(llmvalidator ≤25 m)와는 별개다.
X_DIAG_LEN_MIN_MM = 200.0
X_DIAG_ASPECT_MIN = 0.08
X_CROSS_SHORT_FRAC = 0.75
X_SPAN_MIN_MM = 499.0
X_DOOR_THICK_MIN_MM = 80.0
X_DOOR_THICK_MAX_MM = 360.0
X_DOOR_SPAN_MIN_MM = 650.0
X_DOOR_SPAN_MAX_MM = 1750.0
X_DOOR_ASPECT_MAX = 0.5
X_BBOX_PAD_MM = 100.0
SYNTHETIC_PREFIX = "무명_"
SYNTHETIC_SOURCE_UNLABELED = "unlabeled_fill"
REVIEW_JSON_REL = Path("llm_review") / "review.json"
def is_fixture_label(text: str) -> bool:
    """집기 이름, 또는 그 뒤에 번호만 붙은 표기. 목록은 drawing-roomevaluator label_info.json."""
    return room.is_fixture_label(text)


def collect_labels(msp) -> list[tuple[float, float, str]]:
    """drawing-roomevaluator 와 같은 실명. 집기 표기는 뺀다."""
    return [item for item in room.collect_room_labels(msp) if not is_fixture_label(item[2])]


def layer_name(label: str, used: set[str]) -> str:
    raw = "".join("_" if ch in _LAYER_BAD else ch for ch in room.norm_name(label)).strip()
    raw = raw[:255] or "LABEL"
    name = raw
    n = 2
    while name.lower() in used or name == "0":
        suffix = f"_{n}"
        name = f"{raw[: 255 - len(suffix)]}{suffix}"
        n += 1
    used.add(name.lower())
    return name


def disambiguate_duplicate_names(detected: list[dict]) -> None:
    """같은 실명이 둘 이상이면 `이름#1`, `이름#2` … 로 나눠 요약 탭에서 따로 끌 수 있게 한다.

    한 번만 나온 이름은 그대로 둔다. 도면에 이미 `이름#1` 이 있으면 그 번호는 건너뛴다.
    """
    by_name: dict[str, list[dict]] = {}
    for inst in detected:
        by_name.setdefault(inst["name"], []).append(inst)

    claimed = {name for name, group in by_name.items() if len(group) == 1}
    for name, group in by_name.items():
        if len(group) < 2:
            continue
        group.sort(key=lambda item: (-float(item.get("y") or 0.0), float(item.get("x") or 0.0)))
        next_n = 1
        for inst in group:
            base = str(inst.get("text") or name)
            while True:
                candidate = f"{base}#{next_n}"
                next_n += 1
                cand_name = room.norm_name(candidate)
                if cand_name not in claimed:
                    break
            inst["text"] = candidate
            inst["name"] = cand_name
            claimed.add(cand_name)


def rgb_for(index: int) -> tuple[int, int, int]:
    hue = (index * 0.618033988749895) % 1.0
    red, green, blue = colorsys.hsv_to_rgb(hue, 0.58, 0.90)
    return int(red * 255), int(green * 255), int(blue * 255)


def _boundary_at(msp, face, swings) -> list[dict]:
    """닫힌 실의 벽 테두리에 닿는 문. drawing-roomevaluator 와 같다."""
    boundary = room._boundary_doors(swings, face) + room._leaf_doors_on_boundary(msp, face)
    boundary.sort(key=lambda door: (round(door["x"], 1), round(door["y"], 1)))
    return boundary


def detect_one(
    msp,
    boxes,
    segs,
    x: float,
    y: float,
    door: str = "close",
) -> tuple[dict | None, str | None]:
    """라벨 좌표를 둘러싼 벽 안쪽 면. 실패하면 (None, 이유).

    door=open 이면 그 실의 벽 테두리에 닿는 문만 연다.
    """
    face, x_axes, y_axes, swings = room._room_lines(msp, segs, (x, y), boxes, [])
    if face is None:
        return None, "벽이 실을 닫지 않습니다"
    boundary = _boundary_at(msp, face, swings)
    if door == "open" and boundary:
        opened_segs = room.wall_segments(msp, boxes, open_doors=boundary)
        opened, ox, oy, _swings = room._room_lines(msp, opened_segs, (x, y), boxes, boundary)
        if opened is None:
            return None, "문을 열면 벽이 실을 닫지 않아 면적이 창 밖으로 새었습니다."
        face, x_axes, y_axes = opened, ox, oy
    try:
        net, protrusions = room._subtract_columns(face, boxes, (x, y))
    except SystemExit as exc:
        return None, str(exc) or "라벨이 계산된 면 밖에 있습니다"
    pts = room._simplify_ring(list(net.exterior.coords), x_axes, y_axes)
    holes = [
        hole
        for hole in (room._simplify_ring(list(ring.coords), x_axes, y_axes) for ring in net.interiors)
        if len(hole) >= 3
    ]
    if len(pts) < 3 or not room.point_in_poly(x, y, pts):
        return None, "라벨이 계산된 면 밖에 있습니다"
    area = room.shoelace_m2(pts) - sum(room.shoelace_m2(hole) for hole in holes)
    minx, miny, maxx, maxy = face.bounds
    return {
        "pts": pts,
        "holes": holes,
        "area_m2": area,
        "column_protrusion_m2": sum(item["area_m2"] for item in protrusions),
        "width_m": (maxx - minx) / 1000.0,
        "height_m": (maxy - miny) / 1000.0,
        "drawing_area_m2": room.drawing_area_m2(msp, pts),
        "boundary_doors": [
            {
                "kind": item["kind"],
                "x": round(item["x"], 1),
                "y": round(item["y"], 1),
                "state": "open" if door == "open" else "close",
            }
            for item in boundary
        ],
        "net": net,
    }, None


def detect_one_with_nudge(
    msp,
    boxes,
    segs,
    x: float,
    y: float,
    door: str = "close",
) -> tuple[dict | None, str | None, float, float]:
    """detect_one. 면적이 2 m² 이하면 씨앗을 상하좌우로 옮겨 다시 잰다.

    같은 거리에서 여러 방향이 되면 면적이 큰 쪽을 고른다.
    옮겨도 2 m²를 넘지 못하면 버린다.
    반환은 (면, 실패 이유, 씨앗 x, 씨앗 y).
    """
    found, reason = detect_one(msp, boxes, segs, x, y, door=door)
    if found is None or found["area_m2"] > MIN_LABEL_AREA_M2:
        return found, reason, x, y
    tiny = found
    for step in NUDGE_STEPS_MM:
        best: dict | None = None
        best_xy: tuple[float, float] | None = None
        best_note = ""
        for dx, dy, note in (
            (-step, 0.0, f"왼쪽 {step:.0f} mm"),
            (step, 0.0, f"오른쪽 {step:.0f} mm"),
            (0.0, step, f"위 {step:.0f} mm"),
            (0.0, -step, f"아래 {step:.0f} mm"),
        ):
            nx, ny = x + dx, y + dy
            moved, _moved_reason = detect_one(msp, boxes, segs, nx, ny, door=door)
            if moved is None or moved["area_m2"] <= MIN_LABEL_AREA_M2:
                continue
            if best is None or moved["area_m2"] > best["area_m2"]:
                best = moved
                best_xy = (nx, ny)
                best_note = note
        if best is not None and best_xy is not None:
            print(
                f"[nudge] ({x:.0f},{y:.0f}) → ({best_xy[0]:.0f},{best_xy[1]:.0f}) "
                f"{best_note}: {tiny['area_m2']:.2f} → {best['area_m2']:.2f} m2",
                flush=True,
            )
            return best, None, best_xy[0], best_xy[1]
    return None, TINY_AREA_SKIP_REASON, x, y


def _drop_tiny_faces(
    detected: list[dict], skipped: list[dict]
) -> tuple[list[dict], list[dict]]:
    """면적이 2 m² 이하인 실을 결과에서 뺀다."""
    kept: list[dict] = []
    for inst in detected:
        if float(inst.get("area_m2") or 0.0) > MIN_LABEL_AREA_M2:
            kept.append(inst)
            continue
        skipped.append(
            {
                "text": inst.get("text") or inst.get("name") or "",
                "x": round(float(inst.get("x") or 0.0), 1),
                "y": round(float(inst.get("y") or 0.0), 1),
                "reason": TINY_AREA_SKIP_REASON,
            }
        )
        print(
            f"[drop] {inst.get('text') or inst.get('name')}  "
            f"{float(inst.get('area_m2') or 0.0):.2f} m2 <= {MIN_LABEL_AREA_M2:.0f} m2",
            flush=True,
        )
    return kept, skipped


def _record_face(msp, boxes, face, x_axes, y_axes, swings, x: float, y: float, door: str) -> dict | None:
    """이미 닫힌 면을 detect_one 과 같은 기록으로 만든다."""
    try:
        net, protrusions = room._subtract_columns(face, boxes, (x, y))
    except SystemExit:
        return None
    pts = room._simplify_ring(list(net.exterior.coords), x_axes, y_axes)
    holes = [
        hole
        for hole in (room._simplify_ring(list(ring.coords), x_axes, y_axes) for ring in net.interiors)
        if len(hole) >= 3
    ]
    if len(pts) < 3 or not room.point_in_poly(x, y, pts):
        return None
    boundary = _boundary_at(msp, face, swings)
    area = room.shoelace_m2(pts) - sum(room.shoelace_m2(hole) for hole in holes)
    minx, miny, maxx, maxy = face.bounds
    return {
        "pts": pts,
        "holes": holes,
        "area_m2": area,
        "column_protrusion_m2": sum(item["area_m2"] for item in protrusions),
        "width_m": (maxx - minx) / 1000.0,
        "height_m": (maxy - miny) / 1000.0,
        "drawing_area_m2": room.drawing_area_m2(msp, pts),
        "boundary_doors": [
            {
                "kind": item["kind"],
                "x": round(item["x"], 1),
                "y": round(item["y"], 1),
                "state": "open" if door == "open" else "close",
            }
            for item in boundary
        ],
        "net": net,
    }


def _detect_open_clusters(
    msp,
    boxes,
    segs,
    open_misses: list[tuple[float, float, str]],
    detected: list[dict],
    skipped: list[dict],
    door: str,
) -> tuple[list[dict], list[dict]]:
    """방안 1·2·단일 씨앗에서 못 잡은 오픈 라벨을 묶음으로 넓혀 면을 만든다."""
    if len(open_misses) < room.CLUSTER_MIN_SEEDS:
        return detected, skipped
    origins = [(x, y) for x, y, _text in open_misses]
    origin_groups = room.cluster_open_origins(origins)
    lines = [LineString([(x0, y0), (x1, y1)]) for x0, y0, x1, y1, *_rest in segs]
    rescued: set[tuple[int, int]] = set()
    for group in origin_groups:
        group_set = {(round(x, 1), round(y, 1)) for x, y in group}
        members = [
            (x, y, text)
            for x, y, text in open_misses
            if (round(x, 1), round(y, 1)) in group_set
        ]
        if len(members) < room.CLUSTER_MIN_SEEDS:
            continue
        seeds = [(x, y) for x, y, _text in members]
        density = room.cluster_hull_wall_density(segs, seeds)
        if density < room.CLUSTER_HULL_WALL_DENSITY_MIN:
            continue
        blocked = []
        for inst in detected:
            poly = _host_polygon(inst)
            if poly is not None:
                blocked.append(poly)
        face = room._seed_cluster_face(lines, seeds, blocked_faces=blocked)
        if face is None:
            continue
        kept = 0
        for x, y, text in members:
            packed = _record_face(msp, boxes, face, [], [], [], x, y, door)
            if packed is None:
                continue
            packed.update({"text": text, "x": x, "y": y, "name": room.norm_name(text)})
            detected.append(packed)
            rescued.add((round(x), round(y)))
            kept += 1
            print(
                f"[cluster] {text}  {packed['area_m2']:.2f} m2 "
                f"(묶음 {len(members)}, 벽밀도 {density:.2f})",
                flush=True,
            )
        if kept:
            print(
                f"[cluster] face {face.area / 1_000_000.0:.2f} m2 shared by {kept} labels",
                flush=True,
            )
    if rescued:
        skipped = [
            row
            for row in skipped
            if (round(row["x"]), round(row["y"])) not in rescued
        ]
    return detected, skipped


def _divide_shared_faces(msp, boxes, segs, detected: list[dict], door: str) -> list[dict]:
    """한 면에 화장실·WC·계단실 글자가 있으면 벽 끝을 이어 그 부분을 면적에서 뺀다.

    WC·화장실·계단실 라벨은 실로 남기지 않는다.
    """
    anchors = [
        (x, y, text)
        for x, y, text in room.iter_labels(msp)
        if room.is_split_anchor(text)
    ]
    if not anchors:
        return detected
    used: set[tuple[int, int]] = set()
    for inst in detected:
        inside = [
            (x, y, text)
            for x, y, text in anchors
            if (round(x), round(y)) not in used and room.point_in_poly(x, y, inst["pts"])
        ]
        if not inside:
            continue
        split = room.split_face_at_wall_end(
            msp, segs, boxes, (inst["x"], inst["y"]), [(x, y) for x, y, _text in inside]
        )
        if split is None:
            continue
        face_a, faces_b, x_axes, y_axes, swings = split
        packed = _record_face(msp, boxes, face_a, x_axes, y_axes, swings, inst["x"], inst["y"], door)
        if packed is None:
            continue
        others = []
        for (x, y, text), face_b in zip(inside, faces_b):
            row = _record_face(msp, boxes, face_b, x_axes, y_axes, swings, x, y, door)
            if row is None:
                others = []
                break
            row.update({"text": text, "x": x, "y": y, "name": room.norm_name(text)})
            others.append(row)
        if len(others) != len(inside):
            continue
        kept = {key: inst[key] for key in ("text", "x", "y", "name")}
        inst.clear()
        inst.update(packed)
        inst.update(kept)
        for x, y, _text in inside:
            used.add((round(x), round(y)))
        print(
            f"split {inst['text']} {inst['area_m2']:.2f} m2, excluded "
            + ", ".join(f"{row['text']} {row['area_m2']:.2f} m2" for row in others),
            flush=True,
        )
    return detected


def _host_polygon(inst: dict):
    poly = Polygon(inst["pts"], inst.get("holes") or [])
    if not poly.is_valid:
        poly = poly.buffer(0)
    if poly.is_empty:
        return None
    if poly.geom_type == "MultiPolygon":
        point = Point(inst["x"], inst["y"])
        holding = [geom for geom in poly.geoms if geom.covers(point)]
        poly = holding[0] if holding else max(poly.geoms, key=lambda geom: geom.area)
    if poly.geom_type != "Polygon":
        return None
    return poly


def _anchor_visibility(segs, x: float, y: float):
    """화장실 글자 주위 벽으로 칸을 잡는다.

    빈 곳을 600 mm 줄였다가 다시 넓혀 문 틈으로 옆 실까지 새지 않게 한다.
    칸막이 너머의 작은 칸도 같이 뺀다.
    """
    reach = 7000.0
    lines = []
    for x0, y0, x1, y1 in segs:
        if min(
            math.hypot(x0 - x, y0 - y),
            math.hypot(x1 - x, y1 - y),
            math.hypot((x0 + x1) * 0.5 - x, (y0 + y1) * 0.5 - y),
        ) > reach:
            continue
        if math.hypot(x1 - x0, y1 - y0) < 200.0:
            continue
        lines.append(LineString([(x0, y0), (x1, y1)]))
    if len(lines) < 3:
        return None
    wall = unary_union([ln.buffer(30.0, cap_style=2, join_style=2) for ln in lines])
    window = box(x - reach, y - reach, x + reach, y + reach)
    void = window.difference(wall)
    if void.is_empty:
        return None
    parts = [void] if void.geom_type == "Polygon" else list(void.geoms)
    origin = Point(x, y)
    holding = [geom for geom in parts if geom.covers(origin)]
    if not holding:
        return None
    part = holding[0]
    eroded = part.buffer(-600.0, join_style=2)
    if eroded.is_empty:
        return None
    pieces = [eroded] if eroded.geom_type == "Polygon" else list(eroded.geoms)
    near = [geom for geom in pieces if geom.distance(origin) <= 680.0]
    if not near:
        return None
    piece = min(near, key=lambda geom: (geom.distance(origin), -geom.area))
    restored = piece.buffer(620.0, join_style=2).intersection(part)
    if restored.is_empty:
        return None
    if restored.geom_type == "MultiPolygon":
        holding = [geom for geom in restored.geoms if geom.covers(origin)]
        if not holding:
            return None
        restored = max(holding, key=lambda geom: geom.area)
    extra = [
        geom
        for geom in parts
        if geom is not part
        and 400_000.0 <= geom.area <= 8_000_000.0
        and restored.distance(geom) <= 280.0
    ]
    if extra:
        restored = unary_union([restored, *extra]).buffer(140.0).buffer(-140.0)
        if restored.is_empty:
            return None
        if restored.geom_type == "MultiPolygon":
            holding = [geom for geom in restored.geoms if geom.covers(origin)]
            if not holding:
                return None
            restored = max(holding, key=lambda geom: geom.area)
    if restored.geom_type != "Polygon" or not restored.covers(origin):
        return None
    if restored.area < 1_200_000.0 or restored.area > 40_000_000.0:
        return None
    minx, miny, maxx, maxy = restored.bounds
    if minx <= x - reach + 80.0 or maxx >= x + reach - 80.0 or miny <= y - reach + 80.0 or maxy >= y + reach - 80.0:
        return None
    return restored


def _apply_room_difference(inst: dict, diff) -> bool:
    point = Point(inst["x"], inst["y"])
    if diff.is_empty:
        return False
    if diff.geom_type == "MultiPolygon":
        holding = [geom for geom in diff.geoms if geom.covers(point)]
        if not holding:
            return False
        diff = max(holding, key=lambda geom: geom.area)
    if diff.geom_type != "Polygon" or not diff.covers(point):
        return False
    pts = room._simplify_ring(list(diff.exterior.coords), [], [])
    holes = [
        hole
        for hole in (room._simplify_ring(list(ring.coords), [], []) for ring in diff.interiors)
        if len(hole) >= 3
    ]
    if len(pts) < 3 or not room.point_in_poly(inst["x"], inst["y"], pts):
        return False
    area = room.shoelace_m2(pts) - sum(room.shoelace_m2(hole) for hole in holes)
    if area <= 0.2:
        return False
    inst["pts"] = pts
    inst["holes"] = holes
    inst["area_m2"] = area
    minx, miny, maxx, maxy = diff.bounds
    inst["width_m"] = (maxx - minx) / 1000.0
    inst["height_m"] = (maxy - miny) / 1000.0
    return True


# 기명 실이 X·/ 표식 칸과 겹치면 그 겹침만 뺀다(전실이 ST X 윗삼각형으로 새던 경우).
X_ROOM_SUBTRACT_MIN_MM2 = 10_000.0  # 0.01 m²
X_ROOM_SUBTRACT_MAX_HOST_FRAC = 0.75


X_LABEL_SKIP_REASON = "라벨이 X·/ 표식 칸 안에 있습니다"


def _subtract_crossed_x_regions(
    msp, detected: list[dict], skipped: list[dict] | None = None
) -> tuple[list[dict], list[dict]]:
    """교차 X·네모+/ 표식 bbox가 기명 실 면에 침범한 부분을 뺀다.

    무명 fill 의 skip_x 와 같은 기하를 쓰되 pad 없이 표식 칸만 자른다.
    라벨 점이 X 안에 있으면 기명 실도 버린다. 겹침이 실의 75%를 넘으면
    그 상자는 자르지 않는다.
    """
    if skipped is None:
        skipped = []
    x_boxes = find_crossed_x_bboxes(msp, pad_mm=0.0) + find_slash_rect_bboxes(
        msp, pad_mm=0.0
    )
    if not x_boxes or not detected:
        return detected, skipped
    kept: list[dict] = []
    for inst in detected:
        host = _host_polygon(inst)
        if host is None:
            kept.append(inst)
            continue
        seed = Point(inst["x"], inst["y"])
        seed_in_x = False
        for xmin, ymin, xmax, ymax in x_boxes:
            if xmax <= xmin or ymax <= ymin:
                continue
            region = box(xmin, ymin, xmax, ymax)
            if region.covers(seed) or region.contains(seed):
                seed_in_x = True
                break
        if seed_in_x:
            skipped.append(
                {
                    "text": inst.get("text") or inst.get("name") or "",
                    "x": round(float(inst.get("x") or 0.0), 1),
                    "y": round(float(inst.get("y") or 0.0), 1),
                    "reason": X_LABEL_SKIP_REASON,
                }
            )
            print(
                f"[drop] {inst.get('text') or inst.get('name')}  "
                f"{float(inst.get('area_m2') or 0.0):.2f} m2: {X_LABEL_SKIP_REASON}",
                flush=True,
            )
            continue
        notes: list[str] = []
        for xmin, ymin, xmax, ymax in x_boxes:
            if xmax <= xmin or ymax <= ymin:
                continue
            region = box(xmin, ymin, xmax, ymax)
            if not host.intersects(region):
                continue
            overlap = host.intersection(region)
            if overlap.is_empty or overlap.area < X_ROOM_SUBTRACT_MIN_MM2:
                continue
            if overlap.area > host.area * X_ROOM_SUBTRACT_MAX_HOST_FRAC:
                continue
            diff = host.difference(overlap)
            if not _apply_room_difference(inst, diff):
                continue
            host = _host_polygon(inst)
            if host is None:
                break
            notes.append(f"X {overlap.area / 1_000_000.0:.2f} m2")
        if notes:
            print(
                f"cut {inst['text']} {inst['area_m2']:.2f} m2, removed "
                + ", ".join(notes),
                flush=True,
            )
        kept.append(inst)
    return kept, skipped


def _subtract_anchor_faces(msp, segs, detected: list[dict]) -> list[dict]:
    """벽 끝으로 못 가른 화장실은 그 글자에서 보이는 칸을 잡아 들어 있는 실에서 뺀다.

    화장실·WC·계단실 글자는 실로 남기지 않는다.
    """
    anchors = [
        (x, y, text)
        for x, y, text in room.iter_labels(msp)
        if room.is_split_anchor(text)
    ]
    if not anchors:
        return detected
    for inst in detected:
        host = _host_polygon(inst)
        if host is None:
            continue
        notes = []
        for x, y, text in anchors:
            if not room.point_in_poly(x, y, inst["pts"]):
                continue
            face = _anchor_visibility(segs, x, y)
            if face is None or face.covers(Point(inst["x"], inst["y"])):
                continue
            if not face.covers(Point(x, y)):
                continue
            overlap = host.intersection(face)
            if overlap.is_empty or overlap.area < 1_200_000.0:
                continue
            if overlap.area < 0.55 * face.area or overlap.area > host.area * 0.75:
                continue
            diff = host.difference(overlap)
            if not _apply_room_difference(inst, diff):
                continue
            host = _host_polygon(inst)
            if host is None:
                break
            notes.append(f"{text} {overlap.area / 1_000_000.0:.2f} m2")
        if notes:
            print(
                f"cut {inst['text']} {inst['area_m2']:.2f} m2, removed " + ", ".join(notes),
                flush=True,
            )
    return detected


def _divide_on_dividers(msp, boxes, segs, detected: list[dict], door: str) -> list[dict]:
    """한 면에 라벨이 여러 개이면 그 사이의 벽이 아닌 선으로 면을 나눈다.

    창고·차고·화장실·방풍실·보관실·퇴식대·샤워실·고압·저압·계단실·승강기·EPS·OPEN·오픈·홀-·홈-·입구·출구·양치공간·청소도구함·청소도구실·실외기실·조경·배경·평면도·비품고을·항습기·화물·휠체어·린넨실·오물처리실·구급자·응급환자·의료가스실·기계실·(여)·(남)·문서고·기울기·자동방화·인)·인승·OTIS·Wlqtnwjd·DA#·DA(·올려옴·내려감·DOOR·패드·기저귀·침례탕약·설치·드라이기·유사용·상하부·하부·(고객)·서비스동선·Drop-off·상부·캐노피선·고장이슈·대)·자쿠지·IN-S·분전함·세척실·탕비실·물품·스트레쳐·E.V·ELEV·ELEV. HALL·ELEV. 홀·WC·DA·유도등·단자함·소화전·다용도실·밸브·(근생)·정화조·피난기·보관소·속도·주거·배수·비상용·탑승대기·ST( 쪽은 실로 남기지 않는다.
    """
    labels = [
        (x, y, text)
        for x, y, text in room.iter_labels(msp)
        if not is_fixture_label(text) and room.is_region_label(text)
    ]
    by_xy = {(round(item["x"]), round(item["y"])): item for item in detected}
    for inst in detected:
        inside = []
        for x, y, text in labels:
            if room.norm_name(text) == inst["name"]:
                continue
            if (x - inst["x"]) ** 2 + (y - inst["y"]) ** 2 < 400.0 ** 2:
                continue
            if room.point_in_poly(x, y, inst["pts"]):
                inside.append((x, y, text))
        if not inside:
            continue
        split = room.split_face_on_divider(
            msp,
            segs,
            boxes,
            (inst["x"], inst["y"]),
            [(x, y) for x, y, _text in inside],
            current_pts=inst["pts"],
        )
        if split is None:
            continue
        face_a, separated, x_axes, y_axes, swings = split
        packed = _record_face(msp, boxes, face_a, x_axes, y_axes, swings, inst["x"], inst["y"], door)
        if packed is None:
            continue
        kept = {key: inst[key] for key in ("text", "x", "y", "name")}
        inst.clear()
        inst.update(packed)
        inst.update(kept)
        notes = []
        for index, face_b in separated:
            x, y, text = inside[index]
            other = by_xy.get((round(x), round(y)))
            if other is None or not room.is_room_label(text):
                area = face_b.area / 1_000_000.0
                notes.append(f"{text} {area:.2f} m2")
                continue
            row = _record_face(msp, boxes, face_b, x_axes, y_axes, swings, x, y, door)
            if row is None:
                notes.append(text)
                continue
            other_kept = {key: other[key] for key in ("text", "x", "y", "name")}
            other.clear()
            other.update(row)
            other.update(other_kept)
            notes.append(f"{other['text']} {other['area_m2']:.2f} m2")
        print(
            f"divide {inst['text']} {inst['area_m2']:.2f} m2 | " + ", ".join(notes),
            flush=True,
        )
    return detected


def _face_key(pts: list[tuple[float, float]]) -> tuple[tuple[int, int], ...]:
    return tuple((round(x), round(y)) for x, y in pts)


def _bbox_dict(bbox: dict) -> tuple[float, float, float, float] | None:
    try:
        return (
            float(bbox["xmin"]),
            float(bbox["ymin"]),
            float(bbox["xmax"]),
            float(bbox["ymax"]),
        )
    except (KeyError, TypeError, ValueError):
        return None


def _occupied_polygons(detected: list[dict]) -> list[Polygon]:
    out: list[Polygon] = []
    for inst in detected:
        poly = _host_polygon(inst)
        if poly is not None and not poly.is_empty:
            out.append(poly)
    return out


def _overlaps_occupied(poly: Polygon, occupied: list[Polygon], ratio: float = OCCUPIED_OVERLAP) -> bool:
    if not occupied or poly.area <= 0.0:
        return False
    shared = 0.0
    for other in occupied:
        if not poly.intersects(other):
            continue
        shared += poly.intersection(other).area
        if shared / poly.area > ratio:
            return True
    return shared / poly.area > ratio


def _named_label_bboxes(
    detected: list[dict],
    pad_mm: float = FILL_INSIDE_NAMED_BBOX_PAD_MM,
) -> list[Polygon]:
    """실명(비-synthetic) 라벨 면의 축정렬 bbox. 모서리 빈 칸 무명 억제에 쓴다."""
    out: list[Polygon] = []
    pad = max(float(pad_mm), 0.0)
    for inst in detected:
        if inst.get("synthetic"):
            continue
        host = _host_polygon(inst)
        if host is None or host.is_empty:
            continue
        minx, miny, maxx, maxy = host.bounds
        out.append(box(minx - pad, miny - pad, maxx + pad, maxy + pad))
    return out


def _mostly_inside_named_bbox(
    poly: Polygon,
    named_bboxes: list[Polygon],
    ratio: float = FILL_INSIDE_NAMED_BBOX_RATIO,
) -> bool:
    if not named_bboxes or poly.area <= 0.0:
        return False
    area = float(poly.area)
    for bbox_poly in named_bboxes:
        if float(poly.intersection(bbox_poly).area) / area >= ratio:
            return True
    return False


def review_json_path(dxf_path: Path) -> Path:
    """층 폴더의 llm_review/review.json."""
    return dxf_path.parent / REVIEW_JSON_REL


def load_skip_bboxes_from_review(
    review_path: Path | None,
    *,
    labels: tuple[str, ...] = FILL_SKIP_REVIEW_LABELS,
) -> list[tuple[float, float, float, float]]:
    """review.json promote/demote bbox 중 계단·화장실·X 등 제외 라벨의 mm 상자."""
    if review_path is None or not review_path.is_file():
        return []
    try:
        data = json.loads(review_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    tokens = tuple(room.norm_name(token) for token in labels if token)
    if not tokens:
        return []
    out: list[tuple[float, float, float, float]] = []
    for key in ("promote_bboxes", "demote_bboxes"):
        rows = data.get(key)
        if not isinstance(rows, list):
            continue
        for item in rows:
            if not isinstance(item, dict):
                continue
            label = room.norm_name(str(item.get("label") or ""))
            if not label or not any(token in label for token in tokens):
                continue
            try:
                xmin = float(item["xmin"])
                ymin = float(item["ymin"])
                xmax = float(item["xmax"])
                ymax = float(item["ymax"])
            except (KeyError, TypeError, ValueError):
                continue
            if xmax <= xmin or ymax <= ymin:
                continue
            # Vision 문 개구·문짝 X 는 무명 제외에 쓰지 않는다.
            x_tokens = tuple(room.norm_name(token) for token in FILL_SKIP_REVIEW_X_LABELS)
            if any(token in label for token in x_tokens):
                if _is_door_x_review_bbox(xmin, ymin, xmax, ymax):
                    continue
            out.append((xmin, ymin, xmax, ymax))
    return out


def _is_door_x_review_bbox(
    xmin: float, ymin: float, xmax: float, ymax: float
) -> bool:
    """문 개구 얇은 X·문짝 정사각 X. llmvalidator door X 제외와 동일."""
    width = abs(float(xmax) - float(xmin))
    height = abs(float(ymax) - float(ymin))
    thick, span = min(width, height), max(width, height)
    if thick <= 0.0 or span <= 0.0:
        return False
    if (
        X_DOOR_THICK_MIN_MM <= thick <= X_DOOR_THICK_MAX_MM
        and X_DOOR_SPAN_MIN_MM <= span <= X_DOOR_SPAN_MAX_MM
        and thick / span <= X_DOOR_ASPECT_MAX
    ):
        return True
    if 400.0 <= thick <= 650.0 and 400.0 <= span <= 650.0:
        return True
    return False


def _bboxes_confirmed_by_geom(
    vision: list[tuple[float, float, float, float]],
    geom: list[tuple[float, float, float, float]],
    *,
    min_overlap: float = 0.10,
) -> list[tuple[float, float, float, float]]:
    """Vision X 상자 중 기하 교차 X 와 겹치는 것만. 문 스윙 오검 X 를 걸러 낸다."""
    if not vision or not geom:
        return []
    geom_boxes = [box(*item) for item in geom]
    out: list[tuple[float, float, float, float]] = []
    for item in vision:
        region = box(*item)
        area = float(region.area)
        if area <= 0.0:
            continue
        for other in geom_boxes:
            if not region.intersects(other):
                continue
            shared = float(region.intersection(other).area)
            if shared / min(area, float(other.area)) >= min_overlap:
                out.append(item)
                break
    return out


def _hits_skip_bbox(
    poly: Polygon,
    seed_x: float,
    seed_y: float,
    skip_bboxes: list[tuple[float, float, float, float]],
    *,
    overlap_ratio: float = 0.15,
    max_poly_to_box: float | None = None,
) -> bool:
    """중심점이 상자 안이거나 면 겹침이 크면 제외.

    max_poly_to_box 가 있으면 성분 면적이 상자보다 그 배수보다 클 때는
    겹침 비율만으로는 제외하지 않는다(큰 홀이 작은 X 에 닿는 경우).
    """
    if not skip_bboxes:
        return False
    pt = Point(seed_x, seed_y)
    for xmin, ymin, xmax, ymax in skip_bboxes:
        if xmin <= seed_x <= xmax and ymin <= seed_y <= ymax:
            return True
        region = box(xmin, ymin, xmax, ymax)
        if not poly.intersects(region):
            continue
        if region.covers(pt):
            return True
        if poly.area <= 0.0:
            continue
        shared = poly.intersection(region).area
        if shared / poly.area < overlap_ratio:
            continue
        if max_poly_to_box is not None and region.area > 0.0:
            if poly.area > region.area * max_poly_to_box:
                continue
        return True
    return False


def _segment_crosses(
    a: tuple[float, float, float, float],
    b: tuple[float, float, float, float],
) -> bool:
    """두 선분이 끝점만 닿지 않고 교차하면 True."""
    ax0, ay0, ax1, ay1 = a
    bx0, by0, bx1, by1 = b

    def orient(px: float, py: float, qx: float, qy: float, rx: float, ry: float) -> float:
        return (qy - py) * (rx - qx) - (qx - px) * (ry - qy)

    def on_seg(px: float, py: float, qx: float, qy: float, rx: float, ry: float) -> bool:
        return (
            min(px, rx) - 1e-6 <= qx <= max(px, rx) + 1e-6
            and min(py, ry) - 1e-6 <= qy <= max(py, ry) + 1e-6
        )

    o1 = orient(ax0, ay0, ax1, ay1, bx0, by0)
    o2 = orient(ax0, ay0, ax1, ay1, bx1, by1)
    o3 = orient(bx0, by0, bx1, by1, ax0, ay0)
    o4 = orient(bx0, by0, bx1, by1, ax1, ay1)
    if o1 * o2 < 0.0 and o3 * o4 < 0.0:
        return True
    if abs(o1) <= 1e-6 and on_seg(ax0, ay0, bx0, by0, ax1, ay1):
        return True
    if abs(o2) <= 1e-6 and on_seg(ax0, ay0, bx1, by1, ax1, ay1):
        return True
    if abs(o3) <= 1e-6 and on_seg(bx0, by0, ax0, ay0, bx1, by1):
        return True
    if abs(o4) <= 1e-6 and on_seg(bx0, by0, ax1, ay1, bx1, by1):
        return True
    return False


def _closed_rect_size(
    pts: list[tuple[float, float]],
) -> tuple[float, float, float, float] | None:
    """닫힌 폴리라인의 중심·가로·세로. 점이 부족하면 None."""
    if len(pts) < 3:
        return None
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    width = max(xs) - min(xs)
    height = max(ys) - min(ys)
    if width <= 0.0 or height <= 0.0:
        return None
    return (
        (min(xs) + max(xs)) * 0.5,
        (min(ys) + max(ys)) * 0.5,
        width,
        height,
    )


def _is_sink_outer(width: float, height: float) -> bool:
    long_side, short_side = max(width, height), min(width, height)
    return (
        SINK_OUTER_LONG_MM[0] <= long_side <= SINK_OUTER_LONG_MM[1]
        and SINK_OUTER_SHORT_MM[0] <= short_side <= SINK_OUTER_SHORT_MM[1]
    )


def _is_sink_inner(width: float, height: float) -> bool:
    long_side, short_side = max(width, height), min(width, height)
    return (
        SINK_INNER_LONG_MM[0] <= long_side <= SINK_INNER_LONG_MM[1]
        and SINK_INNER_SHORT_MM[0] <= short_side <= SINK_INNER_SHORT_MM[1]
    )


def find_sink_centers(
    msp,
) -> list[tuple[float, float, float, float, float, float]]:
    """세면대 심볼 (cx, cy, x0, y0, x1, y1) mm.

    1) BASE/0/WALL 이중 닫힌 LWPOLYLINE(세면기 외곽·내곽) + 배수 CIRCLE
       (llmvalidator 가 외곽을 WALL 로 올려도 동일). x0..y1 은 외곽 bbox.
    2) 동심 CIRCLE 쌍(r 200–250) + 주변 ARC ≥10 (타원 세면기). bbox 는 원 직경.
    """
    rects: list[tuple[float, float, float, float]] = []
    circles: list[tuple[float, float, float]] = []
    arcs: list[tuple[float, float, float]] = []
    for entity in msp:
        kind = entity.dxftype()
        layer = str(getattr(entity.dxf, "layer", "") or "")
        try:
            if kind == "LWPOLYLINE":
                if layer not in ("BASE", "0", "WALL"):
                    continue
                if not bool(entity.closed):
                    continue
                sized = _closed_rect_size(list(entity.get_points("xy")))
                if sized is not None:
                    rects.append(sized)
            elif kind == "CIRCLE":
                center = entity.dxf.center
                circles.append(
                    (float(center.x), float(center.y), float(entity.dxf.radius))
                )
            elif kind == "ARC":
                if layer not in ("BASE", "0", "WALL"):
                    continue
                center = entity.dxf.center
                arcs.append(
                    (float(center.x), float(center.y), float(entity.dxf.radius))
                )
        except Exception:
            continue

    drains = [
        (x, y)
        for x, y, radius in circles
        if SINK_DRAIN_R_MIN_MM <= radius <= SINK_DRAIN_R_MAX_MM
    ]
    outers = [
        (x, y, w, h) for x, y, w, h in rects if _is_sink_outer(w, h)
    ]
    inners = [
        (x, y, w, h) for x, y, w, h in rects if _is_sink_inner(w, h)
    ]
    sinks: list[tuple[float, float, float, float, float, float]] = []
    for ox, oy, ow, oh in outers:
        for ix, iy, _iw, _ih in inners:
            if math.hypot(ox - ix, oy - iy) > SINK_NEST_CENTER_MM:
                continue
            if not any(
                math.hypot(dx - ox, dy - oy) <= SINK_DRAIN_NEAR_MM
                for dx, dy in drains
            ):
                continue
            cx = (ox + ix) * 0.5
            cy = (oy + iy) * 0.5
            sinks.append(
                (cx, cy, ox - ow * 0.5, oy - oh * 0.5, ox + ow * 0.5, oy + oh * 0.5)
            )
            break

    for index, (x, y, radius) in enumerate(circles):
        if not (SINK_OVAL_R_MM[0] <= radius <= SINK_OVAL_R_MM[1]):
            continue
        mate = False
        for other_x, other_y, other_r in circles[index + 1 :]:
            if math.hypot(other_x - x, other_y - y) > SINK_OVAL_MATE_CENTER_MM:
                continue
            delta = abs(other_r - radius)
            if (
                SINK_OVAL_MATE_DR_MM[0] <= delta <= SINK_OVAL_MATE_DR_MM[1]
                and SINK_OVAL_R_MM[0] <= other_r <= SINK_OVAL_R_MM[1]
            ):
                mate = True
                break
        if not mate:
            continue
        oval_arcs = sum(
            1
            for ax, ay, ar in arcs
            if math.hypot(ax - x, ay - y) <= SINK_OVAL_ARC_NEAR_MM
            and SINK_OVAL_ARC_R_MM[0] <= ar <= SINK_OVAL_ARC_R_MM[1]
        )
        if oval_arcs >= SINK_OVAL_ARC_MIN:
            sinks.append((x, y, x - radius, y - radius, x + radius, y + radius))

    uniq: list[tuple[float, float, float, float, float, float]] = []
    for hit in sinks:
        if any(math.hypot(hit[0] - u[0], hit[1] - u[1]) <= SINK_DEDUPE_MM for u in uniq):
            continue
        uniq.append(hit)
    return uniq


def _poly_has_sink(
    poly: Polygon,
    sinks: list[tuple[float, float] | tuple[float, float, float, float, float, float]],
) -> bool:
    """세면대 중심이 칸 안에 있거나, 세면기 외곽이 칸과 겹치·근접하면 True.

    WALL 로 올라간 세면기 외곽은 fill 래스터에서 빠져 중심이 칸 밖(노치)에
    남을 수 있어, 외곽 bbox 교집합·``SINK_NEAR_POLY_MM`` 이내 거리로도
    화장실로 본다(경주 부티크 호텔 201호 무명_013).
    """
    if not sinks or poly.is_empty:
        return False
    for hit in sinks:
        cx, cy = float(hit[0]), float(hit[1])
        center = Point(cx, cy)
        if poly.covers(center) or poly.distance(center) <= SINK_NEAR_POLY_MM:
            return True
        if len(hit) >= 6:
            vanity = box(float(hit[2]), float(hit[3]), float(hit[4]), float(hit[5]))
            if vanity.is_empty:
                continue
            if poly.intersects(vanity) or poly.distance(vanity) <= SINK_NEAR_POLY_MM:
                return True
    return False


def _poly_has_bathroom_text(msp, poly: Polygon) -> bool:
    """칸 안 TEXT/MTEXT 에 세면·화장실·WC·변기가 있으면 True."""
    tokens = tuple(room.norm_name(token) for token in FILL_BATHROOM_TEXT)
    for entity in msp:
        kind = entity.dxftype()
        if kind not in ("TEXT", "MTEXT"):
            continue
        try:
            if kind == "TEXT":
                text = str(entity.dxf.text or "")
                x = float(entity.dxf.insert.x)
                y = float(entity.dxf.insert.y)
            else:
                text = str(entity.text or "")
                x = float(entity.dxf.insert.x)
                y = float(entity.dxf.insert.y)
        except Exception:
            continue
        folded = room.norm_name(text.replace("\\P", " "))
        if not folded or not any(token in folded for token in tokens):
            continue
        if poly.covers(Point(x, y)):
            return True
    return False


def _diag_candidates(msp) -> list[tuple[float, float, float, float, float]]:
    """대각 후보 LINE (x0,y0,x1,y1,length)."""
    diags: list[tuple[float, float, float, float, float]] = []
    for entity in msp:
        if entity.dxftype() != "LINE":
            continue
        try:
            start = entity.dxf.start
            end = entity.dxf.end
            x0, y0 = float(start.x), float(start.y)
            x1, y1 = float(end.x), float(end.y)
        except Exception:  # noqa: BLE001
            continue
        dx, dy = abs(x1 - x0), abs(y1 - y0)
        length = math.hypot(x1 - x0, y1 - y0)
        if length < X_DIAG_LEN_MIN_MM or max(dx, dy) <= 0.0:
            continue
        if min(dx, dy) / max(dx, dy) < X_DIAG_ASPECT_MIN:
            continue
        diags.append((x0, y0, x1, y1, length))
    return diags


def _is_door_x_span(thick: float, span: float) -> bool:
    if (
        X_DOOR_THICK_MIN_MM <= thick <= X_DOOR_THICK_MAX_MM
        and X_DOOR_SPAN_MIN_MM <= span <= X_DOOR_SPAN_MAX_MM
        and thick / span <= X_DOOR_ASPECT_MAX
    ):
        return True
    return False


def _uniq_x_bboxes(
    boxes: list[tuple[float, float, float, float]],
) -> list[tuple[float, float, float, float]]:
    uniq: list[tuple[float, float, float, float]] = []
    for item in boxes:
        cx = (item[0] + item[2]) * 0.5
        cy = (item[1] + item[3]) * 0.5
        if any(
            abs(cx - (u[0] + u[2]) * 0.5) < 500.0 and abs(cy - (u[1] + u[3]) * 0.5) < 500.0
            for u in uniq
        ):
            continue
        uniq.append(item)
    return uniq


def find_crossed_x_bboxes(
    msp, *, pad_mm: float = X_BBOX_PAD_MM
) -> list[tuple[float, float, float, float]]:
    """교차 대각선(X) 영역의 mm bbox.

    무명 라벨 제외·기명 실 X 침범 차감용이다. 긴 변·대각 길이 상한은
    두지 않는다(크기 무관). 문 두께 안 얇은 X 만 뺀다.
    네모+/ 표식은 find_slash_rect_bboxes.
    """
    diags = _diag_candidates(msp)
    crosses: list[tuple[float, float, float, float]] = []
    pad = max(0.0, float(pad_mm))
    for index, left in enumerate(diags):
        for right in diags[index + 1 :]:
            short, long = sorted((left[4], right[4]))
            if short < long * X_CROSS_SHORT_FRAC:
                continue
            if not _segment_crosses(left[:4], right[:4]):
                continue
            xs = (left[0], left[2], right[0], right[2])
            ys = (left[1], left[3], right[1], right[3])
            x0, x1 = min(xs), max(xs)
            y0, y1 = min(ys), max(ys)
            width, height = x1 - x0, y1 - y0
            thick, span = min(width, height), max(width, height)
            if thick < X_SPAN_MIN_MM:
                continue
            if _is_door_x_span(thick, span):
                continue
            crosses.append((x0 - pad, y0 - pad, x1 + pad, y1 + pad))
    return _uniq_x_bboxes(crosses)


def _slash_pts_match(
    ax: float,
    ay: float,
    bx: float,
    by: float,
    cx: float,
    cy: float,
    dx: float,
    dy: float,
    *,
    tol: float,
) -> bool:
    return (
        math.hypot(ax - cx, ay - cy) <= tol and math.hypot(bx - dx, by - dy) <= tol
    ) or (math.hypot(ax - dx, ay - dy) <= tol and math.hypot(bx - cx, by - cy) <= tol)


def _axis_edge_covered(
    side: tuple[tuple[float, float], tuple[float, float]],
    lines: list[tuple[float, float, float, float]],
    *,
    tol_mm: float = 45.0,
    cover_ratio: float = 0.55,
) -> bool:
    a, b = side
    ex, ey = b[0] - a[0], b[1] - a[1]
    elen = math.hypot(ex, ey)
    if elen < 80.0:
        return False
    eux, euy = ex / elen, ey / elen
    covered = 0.0
    for x0, y0, x1, y1 in lines:
        mx, my = (x0 + x1) * 0.5, (y0 + y1) * 0.5
        wx, wy = mx - a[0], my - a[1]
        perp = abs(wx * (-euy) + wy * eux)
        if perp > tol_mm:
            continue
        if math.hypot(x1 - x0, y1 - y0) < 80.0:
            continue
        a0 = (x0 - a[0]) * eux + (y0 - a[1]) * euy
        a1 = (x1 - a[0]) * eux + (y1 - a[1]) * euy
        lo, hi = sorted((a0, a1))
        covered += max(0.0, min(hi, elen) - max(lo, 0.0))
    return covered >= cover_ratio * elen


def find_slash_rect_bboxes(
    msp, *, pad_mm: float = X_BBOX_PAD_MM
) -> list[tuple[float, float, float, float]]:
    """네모 + 대각선 하나(/) 표식 bbox. 무명 제외·기명 실 차감용, 긴 변 상한 없음.

    맞은편 모서리를 잇는 대각만 있고(교차 X 아님), 네 변 중 3개 이상이
    도면에 있을 때만. 문 두께 얇은 / 는 뺀다.
    """
    diags = _diag_candidates(msp)
    edge_lines: list[tuple[float, float, float, float]] = []
    pad = max(0.0, float(pad_mm))
    for entity in msp:
        if entity.dxftype() != "LINE":
            continue
        try:
            start = entity.dxf.start
            end = entity.dxf.end
            edge_lines.append((float(start.x), float(start.y), float(end.x), float(end.y)))
        except Exception:  # noqa: BLE001
            continue
    boxes: list[tuple[float, float, float, float]] = []
    for x0, y0, x1, y1, _length in diags:
        ax0, ax1 = sorted((x0, x1))
        ay0, ay1 = sorted((y0, y1))
        width, height = ax1 - ax0, ay1 - ay0
        thick, span = min(width, height), max(width, height)
        if thick < X_SPAN_MIN_MM:
            continue
        if _is_door_x_span(thick, span):
            continue
        if span <= 800.0 and thick / max(span, 1.0) >= 0.75:
            continue
        tol = max(80.0, min(250.0, 0.02 * span))
        if not (
            _slash_pts_match(x0, y0, x1, y1, ax0, ay0, ax1, ay1, tol=tol)
            or _slash_pts_match(x0, y0, x1, y1, ax1, ay0, ax0, ay1, tol=tol)
        ):
            continue
        if _slash_pts_match(x0, y0, x1, y1, ax0, ay0, ax1, ay1, tol=tol):
            other_a, other_b = (ax1, ay0), (ax0, ay1)
        else:
            other_a, other_b = (ax0, ay0), (ax1, ay1)
        if any(
            _slash_pts_match(ox0, oy0, ox1, oy1, other_a[0], other_a[1], other_b[0], other_b[1], tol=tol)
            for ox0, oy0, ox1, oy1, _olen in diags
        ):
            continue
        side_hits = 0
        for side in (
            ((ax0, ay0), (ax1, ay0)),
            ((ax1, ay0), (ax1, ay1)),
            ((ax1, ay1), (ax0, ay1)),
            ((ax0, ay1), (ax0, ay0)),
        ):
            if _axis_edge_covered(side, edge_lines):
                side_hits += 1
        if side_hits < 3:
            continue
        boxes.append((ax0 - pad, ay0 - pad, ax1 + pad, ay1 + pad))
    return _uniq_x_bboxes(boxes)


def _raster_cell_mm(bounds: tuple[float, float, float, float], cell_mm: float) -> float:
    """격자가 FILL_MAX_GRID 를 넘지 않게 셀을 키운다."""
    minx, miny, maxx, maxy = bounds
    width = max(maxx - minx, 1.0)
    height = max(maxy - miny, 1.0)
    cell = max(float(cell_mm), 1.0)
    cols = int(math.ceil(width / cell)) + 1
    rows = int(math.ceil(height / cell)) + 1
    if max(cols, rows) <= FILL_MAX_GRID:
        return cell
    scale = max(cols, rows) / float(FILL_MAX_GRID)
    return cell * scale


def corner_bridge_segments(
    segs,
    *,
    gap_min_mm: float = FILL_CORNER_GAP_MIN_MM,
    gap_max_mm: float = FILL_CORNER_GAP_MAX_MM,
    join_mm: float = FILL_CORNER_JOIN_MM,
    axis_mm: float = FILL_CORNER_AXIS_MM,
) -> list[tuple[float, float, float, float]]:
    """끊긴 직교 WALL 끝점을 L 자로 잇는 가상 선분(fill 래스터 전용).

    가로 자유 끝·세로 자유 끝이 각각 200–1100 mm 떨어지면 코너 (vx, hy) 로
    두 변을 보강한다. 문 개구(동일 선상 갭)는 건드리지 않는다.
    """
    from collections import defaultdict

    axis_segs: list[tuple[float, float, float, float, str]] = []
    for seg in segs:
        x0, y0, x1, y1 = float(seg[0]), float(seg[1]), float(seg[2]), float(seg[3])
        if abs(y1 - y0) <= axis_mm and abs(x1 - x0) >= 80.0:
            axis_segs.append((x0, y0, x1, y1, "h"))
        elif abs(x1 - x0) <= axis_mm and abs(y1 - y0) >= 80.0:
            axis_segs.append((x0, y0, x1, y1, "v"))

    buckets: dict[tuple[int, int], int] = defaultdict(int)
    for x0, y0, x1, y1, _orient in axis_segs:
        for x, y in ((x0, y0), (x1, y1)):
            buckets[(round(x / join_mm), round(y / join_mm))] += 1

    def _is_free(x: float, y: float) -> bool:
        qx, qy = round(x / join_mm), round(y / join_mm)
        count = 0
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                count += buckets.get((qx + dx, qy + dy), 0)
        return count <= 1

    h_ends: list[tuple[float, float]] = []
    v_ends: list[tuple[float, float]] = []
    for x0, y0, x1, y1, orient in axis_segs:
        for x, y in ((x0, y0), (x1, y1)):
            if not _is_free(x, y):
                continue
            if orient == "h":
                h_ends.append((x, y))
            else:
                v_ends.append((x, y))

    bridges: list[tuple[float, float, float, float]] = []
    seen: set[tuple[int, int, int, int]] = set()
    for hx, hy in h_ends:
        for vx, vy in v_ends:
            dx = abs(hx - vx)
            dy = abs(hy - vy)
            if not (gap_min_mm <= dx <= gap_max_mm and gap_min_mm <= dy <= gap_max_mm):
                continue
            key = (round(hx / 50.0), round(hy / 50.0), round(vx / 50.0), round(vy / 50.0))
            if key in seen:
                continue
            seen.add(key)
            # L: 가로 끝 → 코너 → 세로 끝
            bridges.append((hx, hy, vx, hy))
            bridges.append((vx, hy, vx, vy))
    return bridges


def rasterize_wall_mask(
    segs,
    bbox: dict,
    cell_mm: float = DEFAULT_SAMPLE_MM,
) -> tuple[object, float, tuple[float, float, float, float]] | tuple[None, float, None]:
    """벽 선을 이진 마스크로 그린다. (mask, cell_mm, bounds).

    직교 자유 끝 L 모서리 갭은 가상 선으로 보강한 뒤 그린다.
    """
    import numpy as np
    import cv2

    bounds = _bbox_dict(bbox)
    if bounds is None:
        return None, cell_mm, None
    minx, miny, maxx, maxy = bounds
    if maxx <= minx or maxy <= miny:
        return None, cell_mm, None
    cell = _raster_cell_mm(bounds, cell_mm)
    cols = int(math.ceil((maxx - minx) / cell)) + 1
    rows = int(math.ceil((maxy - miny) / cell)) + 1
    walls = np.zeros((rows, cols), dtype=np.uint8)

    def to_px(x: float, y: float) -> tuple[int, int]:
        return (
            int(round((x - minx) / cell)),
            int(round((maxy - y) / cell)),
        )

    draw_segs = list(segs) + corner_bridge_segments(segs)
    for x0, y0, x1, y1, *_rest in draw_segs:
        p0 = to_px(x0, y0)
        p1 = to_px(x1, y1)
        cv2.line(walls, p0, p1, 255, thickness=1, lineType=cv2.LINE_8)
    return walls, cell, bounds


def _flood_exterior(free: object) -> object:
    """테두리와 이어진 빈 칸을 외곽으로 표시한 뒤, 내부 빈 칸만 남긴다."""
    import numpy as np
    import cv2

    ff = (free.astype(np.uint8) * 255).copy()
    height, width = ff.shape
    mask = np.zeros((height + 2, width + 2), dtype=np.uint8)
    for col in range(width):
        if ff[0, col] == 255:
            cv2.floodFill(ff, mask, (col, 0), 128)
        if ff[height - 1, col] == 255:
            cv2.floodFill(ff, mask, (col, height - 1), 128)
    for row in range(height):
        if ff[row, 0] == 255:
            cv2.floodFill(ff, mask, (0, row), 128)
        if ff[row, width - 1] == 255:
            cv2.floodFill(ff, mask, (width - 1, row), 128)
    return ff == 255


def segs_with_door_close_bridges(
    segs: list[tuple[float, float, float, float]],
    bbox: dict,
    *,
    door: str = "close",
    boxes=None,
) -> list[tuple[float, float, float, float]]:
    """fill-unlabeled 용 경계. door=close 이면 문 틈(≤2.4 m)을 층 전체에서 잇는다.

    실명 detect 는 씨앗마다 bridged_runs 로 문 개구를 막지만, fill 래스터는
    생 wall_segments 만 쓰면 복도 벽 문 틈으로 실·외부가 한 칸이 된다.
    같은 close 규칙으로 축 런을 보강해 복도처럼 라벨 없는 닫힌 통로를 남긴다.
    """
    if door != "close" or not segs or not bbox:
        return list(segs)
    try:
        xmin = float(bbox["xmin"])
        ymin = float(bbox["ymin"])
        xmax = float(bbox["xmax"])
        ymax = float(bbox["ymax"])
    except (KeyError, TypeError, ValueError):
        return list(segs)
    ox = (xmin + xmax) * 0.5
    oy = (ymin + ymax) * 0.5
    pad = max(xmax - xmin, ymax - ymin) * 0.5 + 1000.0
    try:
        h_final, v_final, _dsegs, _xa, _ya = room.bridged_runs(
            segs,
            (ox, oy),
            pad,
            door="close",
            boxes=boxes or [],
            open_doors=None,
        )
    except Exception:  # noqa: BLE001
        return list(segs)
    out = list(segs)
    if isinstance(h_final, dict):
        for y, intervals in h_final.items():
            for a, b in intervals:
                if abs(b - a) > 1.0:
                    out.append((float(a), float(y), float(b), float(y)))
    if isinstance(v_final, dict):
        for x, intervals in v_final.items():
            for a, b in intervals:
                if abs(b - a) > 1.0:
                    out.append((float(x), float(a), float(x), float(b)))
    return out


def segment_unlabeled_components(
    segs,
    bbox: dict,
    cell_mm: float = DEFAULT_SAMPLE_MM,
    min_area_m2: float = MIN_LABEL_AREA_M2,
) -> list[dict]:
    """벽 래스터 + connected components 로 닫힌 빈 칸을 찾는다.

    각 항목: pts(mm), x, y(centroid), area_m2, width_m, height_m.
    """
    import numpy as np
    import cv2

    walls, cell, bounds = rasterize_wall_mask(segs, bbox, cell_mm=cell_mm)
    if walls is None or bounds is None:
        return []
    minx, miny, maxx, maxy = bounds
    kernel = cv2.getStructuringElement(
        cv2.MORPH_RECT, (FILL_CLOSE_CELLS, FILL_CLOSE_CELLS)
    )
    closed = cv2.morphologyEx(walls, cv2.MORPH_CLOSE, kernel)
    free = closed == 0
    interior = _flood_exterior(free)
    if not np.any(interior):
        return []
    count, labels, stats, centroids = cv2.connectedComponentsWithStats(
        interior.astype(np.uint8), connectivity=4
    )
    m2_per_px = (cell / 1000.0) ** 2
    sheet_m2 = ((maxx - minx) / 1000.0) * ((maxy - miny) / 1000.0)
    max_area = EXTERIOR_AREA_FRAC * sheet_m2 if sheet_m2 > 0 else float("inf")
    rooms: list[dict] = []
    for index in range(1, count):
        area_px = float(stats[index, cv2.CC_STAT_AREA])
        area_m2 = area_px * m2_per_px
        if area_m2 <= min_area_m2 or area_m2 > max_area:
            continue
        component = (labels == index).astype(np.uint8)
        contours, _hier = cv2.findContours(
            component, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
        )
        if not contours:
            continue
        contour = max(contours, key=cv2.contourArea)
        approx = cv2.approxPolyDP(contour, FILL_CONTOUR_EPS_PX, True)
        pts: list[tuple[float, float]] = []
        for point in approx.reshape(-1, 2):
            px, py = float(point[0]), float(point[1])
            pts.append((minx + px * cell, maxy - py * cell))
        if len(pts) < 3:
            continue
        if pts[0] == pts[-1]:
            pts = pts[:-1]
        if len(pts) < 3:
            continue
        area = abs(room.shoelace_m2(pts))
        if area <= min_area_m2:
            continue
        cx, cy = float(centroids[index][0]), float(centroids[index][1])
        seed_x = minx + cx * cell
        seed_y = maxy - cy * cell
        if not room.point_in_poly(seed_x, seed_y, pts):
            poly = Polygon(pts)
            if poly.is_valid and not poly.is_empty:
                rep = poly.representative_point()
                seed_x, seed_y = float(rep.x), float(rep.y)
        left = float(stats[index, cv2.CC_STAT_LEFT])
        top = float(stats[index, cv2.CC_STAT_TOP])
        width_px = float(stats[index, cv2.CC_STAT_WIDTH])
        height_px = float(stats[index, cv2.CC_STAT_HEIGHT])
        rooms.append(
            {
                "pts": pts,
                "holes": [],
                "area_m2": area,
                "column_protrusion_m2": 0.0,
                "width_m": width_px * cell / 1000.0,
                "height_m": height_px * cell / 1000.0,
                "x": seed_x,
                "y": seed_y,
                "bbox_px": (left, top, width_px, height_px),
            }
        )
    rooms.sort(key=lambda item: (-item["area_m2"], item["x"], item["y"]))
    return rooms


def _refine_fill_to_wall_face(
    msp,
    boxes,
    segs,
    door: str,
    seed_x: float,
    seed_y: float,
    raster_poly: Polygon,
    raster_area_m2: float,
) -> dict | None:
    """래스터 CC 씨앗에서 실명과 같은 벽 안쪽 면을 다시 잡는다(nudge 없음).

    격자로 벽에 안 붙는 흰 띠를 없앤다. CC 와 많이 안 겹치거나 면이 지나치게
    커지면(문으로 새는 경우) 거절하고 래스터 contour 를 쓴다.

    segs 는 래스터에 쓴 것과 같아야 한다(fill_segs: 문/개구 close bridge 포함).
    원본 wall_segments 만 넘기면 개구로 옆 칸과 합쳐져 면적 상한에 걸리기 쉽다.
    """
    found, _reason = detect_one(msp, boxes, segs, seed_x, seed_y, door=door)
    if found is None:
        return None
    try:
        vec = Polygon(found["pts"])
    except (TypeError, ValueError):
        return None
    if not vec.is_valid:
        vec = vec.buffer(0)
    if vec.is_empty or vec.geom_type != "Polygon":
        return None
    seed = Point(seed_x, seed_y)
    if not (vec.covers(seed) or vec.distance(seed) <= 1.0):
        return None
    raster_area = float(raster_poly.area)
    if raster_area <= 0:
        return None
    if float(vec.intersection(raster_poly).area) / raster_area < FILL_REFINE_MIN_OVERLAP:
        return None
    max_area = max(
        raster_area_m2 * FILL_REFINE_MAX_AREA_RATIO,
        raster_area_m2 + FILL_REFINE_MAX_AREA_EXTRA_M2,
    )
    if float(found["area_m2"]) > max_area:
        return None
    return found


def fill_unlabeled_rooms(
    msp,
    boxes,
    segs,
    detected: list[dict],
    skipped: list[dict],
    door: str,
    bbox: dict,
    sample_mm: float = DEFAULT_SAMPLE_MM,
    *,
    review_path: Path | None = None,
) -> tuple[list[dict], list[dict]]:
    """실명 없는 닫힌 칸을 래스터 CC 로 찾아 무명_N 을 붙인다.

    CC 로 칸을 찾은 뒤 씨앗에서 벽 안쪽 면(detect_one)을 다시 잡아 격자로
    생기는 틈을 메운다. llm_review/review.json 의 계단·화장실·승강기·외벽·침대·X bbox
    (X·샤프트는 크기 무관, 문 개구 X 제외), 기하 교차 대각선(X, 긴 변 상한 없음),
    세면대 심볼·세면/화장실 TEXT 와 겹치면 라벨을 붙이지 않는다.
    """
    occupied = _occupied_polygons(detected)
    named_bboxes = _named_label_bboxes(detected)
    skip_area = load_skip_bboxes_from_review(
        review_path, labels=FILL_SKIP_REVIEW_AREA_LABELS
    )
    # Vision X·샤프트는 크기와 관계없이 무명 라벨에서 뺀다(문 개구·문짝 X 크기만 제외).
    skip_vision_x = load_skip_bboxes_from_review(
        review_path, labels=FILL_SKIP_REVIEW_X_LABELS
    )
    skip_geom_x = find_crossed_x_bboxes(msp) + find_slash_rect_bboxes(msp)
    skip_x = list(skip_vision_x) + list(skip_geom_x)
    sinks = find_sink_centers(msp)
    fill_segs = segs_with_door_close_bridges(segs, bbox, door=door, boxes=boxes)
    components = segment_unlabeled_components(
        fill_segs, bbox, cell_mm=sample_mm, min_area_m2=FILL_MIN_AREA_M2
    )
    print(
        f"fill-unlabeled: cc_components={len(components)} cell_mm={sample_mm:.0f}"
        f" min_area_m2={FILL_MIN_AREA_M2:.0f}"
        f" door_bridge={'on' if door == 'close' else 'off'}"
        f" skip_area_bbox={len(skip_area)}"
        f" skip_vision_x_bbox={len(skip_vision_x)}"
        f" skip_geom_x_bbox={len(skip_geom_x)}"
        f" sinks={len(sinks)}",
        flush=True,
    )
    synth_n = 0
    skipped_area = 0
    skipped_x = 0
    skipped_bath = 0
    skipped_inside_named = 0
    for index, comp in enumerate(components, start=1):
        poly = Polygon(comp["pts"])
        if not poly.is_valid:
            poly = poly.buffer(0)
        if poly.is_empty or poly.geom_type != "Polygon":
            continue
        if _overlaps_occupied(poly, occupied):
            continue
        if any(poly.covers(Point(inst["x"], inst["y"])) for inst in detected):
            continue
        label_x, label_y = comp["x"], comp["y"]
        rep = poly.representative_point()
        if room.point_in_poly(float(rep.x), float(rep.y), comp["pts"]):
            label_x, label_y = float(rep.x), float(rep.y)
        if _mostly_inside_named_bbox(poly, named_bboxes):
            skipped_inside_named += 1
            print(
                f"[fill {index}/{len(components)}] skip inside named "
                f"{comp['area_m2']:.2f} m2 @ ({label_x:.0f},{label_y:.0f})",
                flush=True,
            )
            continue
        if _hits_skip_bbox(poly, label_x, label_y, skip_area):
            skipped_area += 1
            print(
                f"[fill {index}/{len(components)}] skip area review "
                f"{comp['area_m2']:.2f} m2 @ ({label_x:.0f},{label_y:.0f})",
                flush=True,
            )
            continue
        # Vision/기하 X 칸만 제외. 큰 실이 작은 X 에 조금 겹치는 경우는 남긴다.
        if _hits_skip_bbox(
            poly,
            label_x,
            label_y,
            skip_x,
            overlap_ratio=0.50,
            max_poly_to_box=4.0,
        ):
            skipped_x += 1
            print(
                f"[fill {index}/{len(components)}] skip X "
                f"{comp['area_m2']:.2f} m2 @ ({label_x:.0f},{label_y:.0f})",
                flush=True,
            )
            continue
        if _poly_has_sink(poly, sinks) or _poly_has_bathroom_text(msp, poly):
            skipped_bath += 1
            print(
                f"[fill {index}/{len(components)}] skip bathroom "
                f"{comp['area_m2']:.2f} m2 @ ({label_x:.0f},{label_y:.0f})",
                flush=True,
            )
            continue
        # 래스터와 같은 fill_segs 로 refine. 원본 segs 면 개구로 새어 거절된다.
        refined = _refine_fill_to_wall_face(
            msp,
            boxes,
            fill_segs,
            door,
            label_x,
            label_y,
            poly,
            float(comp["area_m2"]),
        )
        if refined is not None:
            try:
                refined_poly = Polygon(refined["pts"])
            except (TypeError, ValueError):
                refined_poly = None
            if refined_poly is not None and not refined_poly.is_valid:
                refined_poly = refined_poly.buffer(0)
            if (
                refined_poly is None
                or refined_poly.is_empty
                or refined_poly.geom_type != "Polygon"
            ):
                refined = None
            elif _mostly_inside_named_bbox(refined_poly, named_bboxes):
                skipped_inside_named += 1
                print(
                    f"[fill {index}/{len(components)}] skip inside named "
                    f"{float(refined['area_m2']):.2f} m2 @ ({label_x:.0f},{label_y:.0f})",
                    flush=True,
                )
                continue
            elif (
                _overlaps_occupied(refined_poly, occupied)
                or _poly_has_sink(refined_poly, sinks)
                or _poly_has_bathroom_text(msp, refined_poly)
            ):
                refined = None
            elif _hits_skip_bbox(refined_poly, label_x, label_y, skip_area):
                # CC 는 EPS 만이다가 refine 이 분리벽을 넘어 계단실까지
                # 키운 경우. 확장된 면에 대해 계단·화장실 제외를 다시 본다.
                skipped_area += 1
                print(
                    f"[fill {index}/{len(components)}] skip area review "
                    f"(after refine) {float(refined['area_m2']):.2f} m2 "
                    f"@ ({label_x:.0f},{label_y:.0f})",
                    flush=True,
                )
                continue
            elif _hits_skip_bbox(
                refined_poly,
                label_x,
                label_y,
                skip_x,
                overlap_ratio=0.50,
                max_poly_to_box=4.0,
            ):
                skipped_x += 1
                print(
                    f"[fill {index}/{len(components)}] skip X "
                    f"(after refine) {float(refined['area_m2']):.2f} m2 "
                    f"@ ({label_x:.0f},{label_y:.0f})",
                    flush=True,
                )
                continue
            else:
                poly = refined_poly
        synth_n += 1
        text = f"{SYNTHETIC_PREFIX}{synth_n:03d}"
        if refined is not None:
            found = {
                **refined,
                "text": text,
                "x": label_x,
                "y": label_y,
                "name": room.norm_name(text),
                "synthetic": True,
                "source": SYNTHETIC_SOURCE_UNLABELED,
            }
        else:
            found = {
                "pts": comp["pts"],
                "holes": [],
                "area_m2": comp["area_m2"],
                "column_protrusion_m2": 0.0,
                "width_m": comp["width_m"],
                "height_m": comp["height_m"],
                "drawing_area_m2": room.drawing_area_m2(msp, comp["pts"]),
                "boundary_doors": [],
                "text": text,
                "x": label_x,
                "y": label_y,
                "name": room.norm_name(text),
                "synthetic": True,
                "source": SYNTHETIC_SOURCE_UNLABELED,
            }
        detected.append(found)
        occupied.append(poly)
        print(
            f"[fill {index}/{len(components)}] {text}  {found['area_m2']:.2f} m2 "
            f"@ ({label_x:.0f},{label_y:.0f})",
            flush=True,
        )
    print(
        f"fill-unlabeled: added={synth_n} "
        f"skipped_area={skipped_area} skipped_x={skipped_x} "
        f"skipped_bathroom={skipped_bath} skipped_inside_named={skipped_inside_named}",
        flush=True,
    )
    return detected, skipped


def _ink_width_mm(text: str, cap_mm: float, mm_per_inch: float) -> float:
    """PNG에 찍히는 글줄의 가로 길이. 한글은 네모, 숫자는 그보다 좁다."""
    fs = max(cap_mm, 1.0) / max(mm_per_inch, 1e-6) * 72.0
    try:
        import matplotlib

        matplotlib.use("Agg")
        from matplotlib.font_manager import FontProperties, fontManager
        from matplotlib.textpath import TextPath

        font_name = None
        for cand in (
            "AppleGothic",
            "Apple SD Gothic Neo",
            "NanumGothic",
            "Malgun Gothic",
            "Noto Sans CJK KR",
            "Noto Sans KR",
        ):
            matches = [item for item in fontManager.ttflist if cand in item.name]
            if matches:
                font_name = matches[0].name
                break
        prop = FontProperties(family=font_name, size=fs) if font_name else FontProperties(size=fs)
        width_pt = float(TextPath((0, 0), text, size=fs, prop=prop).get_extents().width)
        if width_pt > 0:
            return width_pt / 72.0 * mm_per_inch
    except Exception:  # noqa: BLE001
        pass
    width = 0.0
    for ch in text:
        code = ord(ch)
        if ch == " ":
            width += 0.33
        elif code < 128:
            width += 0.55
        else:
            width += 1.0
    return max(width, 1.0) * cap_mm


def _add_label_tag(msp, layer: str, rgb: tuple[int, int, int], inst: dict, mm_per_inch: float) -> None:
    """실명과 면적을 흰 판 위에 올린다.

    판은 PNG가 글자를 그리는 높이에 맞춘다. 실 크기로 판을 키우면 렌더가
    글자를 고정 포인트로 자른 뒤에도 흰 판만 남는다.
    """
    name = inst["text"]
    area = f"{inst['area_m2']:.2f} ㎡"
    cap = png_text_cap_mm(1000.0, mm_per_inch)
    name_w = _ink_width_mm(name, cap, mm_per_inch)
    area_w = _ink_width_mm(area, cap, mm_per_inch)
    pad_x = cap * 0.4
    pad_y = cap * 0.28
    gap = cap * 0.22
    text_w = max(name_w, area_w)
    # 원본 실명은 이 점에서 오른쪽으로 그려진다. 판의 왼쪽을 그 점에 둔다.
    x_text = float(inst["x"])
    name_baseline = float(inst["y"])
    x0 = x_text - pad_x
    y0 = name_baseline - gap - cap - pad_y
    box_w = text_w + pad_x * 2
    box_h = pad_y * 2 + cap + gap + cap
    corners = [
        (x0, y0),
        (x0 + box_w, y0),
        (x0 + box_w, y0 + box_h),
        (x0, y0 + box_h),
    ]
    wipe = msp.add_wipeout(corners)
    wipe.dxf.layer = layer
    frame = msp.add_lwpolyline(corners, close=True, dxfattribs={"layer": layer})
    frame.rgb = rgb
    text = msp.add_text(
        name,
        height=cap,
        dxfattribs={"layer": layer, "insert": (x_text, name_baseline)},
    )
    text.rgb = (20, 20, 20)
    area_text = msp.add_text(
        area,
        height=cap,
        dxfattribs={"layer": layer, "insert": (x_text, y0 + pad_y)},
    )
    area_text.rgb = (20, 20, 20)


def write_dxf(doc, path: Path, labels: list[dict], mm_per_inch: float) -> None:
    """검증 도면 사본에 라벨 레이어를 더해 저장한다. 원본 경로는 쓰지 않는다."""
    msp = doc.modelspace()
    for item in labels:
        name = item["layer"]
        red, green, blue = item["color"]
        layer = doc.layers.add(name)
        layer.rgb = (red, green, blue)
        for inst in item["instances"]:
            hatch = msp.add_hatch(dxfattribs={"layer": name, "color": 256})
            hatch.transparency = float2transparency(0.55)
            hatch.set_solid_fill(color=256, style=0)
            hatch.paths.add_polyline_path(inst["pts"], is_closed=True, flags=1)
            for hole in inst["holes"]:
                hatch.paths.add_polyline_path(hole, is_closed=True, flags=0)
            hatch.seeds.append((inst["x"], inst["y"]))
            _add_label_tag(msp, name, (red, green, blue), inst, mm_per_inch)
    path.parent.mkdir(parents=True, exist_ok=True)
    apply_korean_text(doc)
    doc.saveas(str(path))


def _px_width(meta: dict) -> int:
    """검증 PNG 와 같은 도면 폭. 저장 크기는 렌더 뒤 오른쪽 여백이 더해진 값이다."""
    stored = meta.get("png_size")
    width = 14200
    if isinstance(stored, (list, tuple)) and stored and meta.get("bbox_mm"):
        width = int(stored[0]) - room.PNG_PAD_RIGHT
    return max(width, 100)


# structure span → floor_*_meta 와 같은 여백(mm).
_SPAN_PAD_L = 4500.0
_SPAN_PAD_B = 4500.0
_SPAN_PAD_R = 6000.0
_SPAN_PAD_T = 4500.0


def _msp_xy_extents(msp) -> tuple[list[float], list[float]]:
    """렌더 bbox 검증용으로 LINE/LWPOLYLINE 좌표만 모은다."""
    xs: list[float] = []
    ys: list[float] = []
    for e in msp:
        try:
            t = e.dxftype()
            if t == "LINE":
                xs.extend([float(e.dxf.start.x), float(e.dxf.end.x)])
                ys.extend([float(e.dxf.start.y), float(e.dxf.end.y)])
            elif t == "LWPOLYLINE":
                for x, y, *_ in e.get_points("xy"):
                    xs.append(float(x))
                    ys.append(float(y))
        except Exception:  # noqa: BLE001
            continue
        if len(xs) > 80_000:
            break
    return xs, ys


def _bbox_from_xy(xs: list[float], ys: list[float]) -> dict[str, float]:
    return {
        "xmin": min(xs) - _SPAN_PAD_L,
        "ymin": min(ys) - _SPAN_PAD_B,
        "xmax": max(xs) + _SPAN_PAD_R,
        "ymax": max(ys) + _SPAN_PAD_T,
    }


def resolve_label_meta_bbox(meta: dict, msp) -> dict:
    """meta bbox 가 DXF 와 안 겹치면 기하 기준으로 고친 메타를 돌려준다."""
    bbox = meta.get("bbox_mm") if isinstance(meta.get("bbox_mm"), dict) else None
    xs, ys = _msp_xy_extents(msp)
    if not xs or not ys:
        return meta
    if bbox and bbox_overlaps_xy(bbox, xs, ys):
        return meta
    fixed = dict(meta)
    new_bb = _bbox_from_xy(xs, ys)
    fixed["bbox_mm"] = new_bb
    fixed["size_m"] = {
        "width": (new_bb["xmax"] - new_bb["xmin"]) / 1000.0,
        "height": (new_bb["ymax"] - new_bb["ymin"]) / 1000.0,
    }
    old_y = (
        f"{float(bbox['ymin']):.1f}..{float(bbox['ymax']):.1f}"
        if bbox
        else "missing"
    )
    print(
        f"  [warn] meta bbox misses DXF (y={old_y}); "
        f"using geom y={min(ys):.1f}..{max(ys):.1f}",
        flush=True,
    )
    return fixed


def render_label_png(dxf_path: Path, meta: dict, png_path: Path) -> None:
    """floor_label_detected.dxf 의 HATCH·문자·흰 판을 PNG 로 그린다."""
    render_wall_dxf_png(
        dxf_path,
        png_path,
        bbox_mm=meta.get("bbox_mm"),
        px_width=_px_width(meta),
    )


def _public_instance(inst: dict, shared_with: list[str]) -> dict:
    row = {
        "text": inst["text"],
        "x": round(inst["x"], 1),
        "y": round(inst["y"], 1),
        "area_m2": round(inst["area_m2"], 4),
        "column_protrusion_m2": round(inst["column_protrusion_m2"], 4),
        "width_m": round(inst["width_m"], 4),
        "height_m": round(inst["height_m"], 4),
        "drawing_area_m2": inst["drawing_area_m2"],
        "boundary_doors": inst.get("boundary_doors") or [],
        "polygon_mm": [[round(x, 1), round(y, 1)] for x, y in inst["pts"]],
    }
    if shared_with:
        row["shared_with"] = shared_with
    if inst.get("synthetic"):
        row["synthetic"] = True
        row["source"] = inst.get("source") or SYNTHETIC_SOURCE_UNLABELED
    return row


def detect(
    dxf_path: Path,
    meta_path: Path,
    door: str = "close",
    *,
    fill_unlabeled: bool = False,
    sample_mm: float = DEFAULT_SAMPLE_MM,
) -> dict:
    started = time.time()
    doc = ezdxf.readfile(str(dxf_path))
    msp = doc.modelspace()
    seeds = collect_labels(msp)
    print(
        f"labels: {len(seeds)} door: {door}"
        + (f" fill_unlabeled sample_mm={sample_mm:.0f}" if fill_unlabeled else ""),
        flush=True,
    )
    boxes = room.column_boxes(msp)
    segs = room.wall_segments(msp, boxes)
    detected: list[dict] = []
    skipped: list[dict] = []
    open_misses: list[tuple[float, float, str]] = []
    for index, (x, y, text) in enumerate(seeds, start=1):
        try:
            found, reason, seed_x, seed_y = detect_one_with_nudge(
                msp, boxes, segs, x, y, door=door
            )
        except Exception as exc:  # noqa: BLE001
            found, reason, seed_x, seed_y = None, str(exc) or exc.__class__.__name__, x, y
        if found is None:
            if reason == "벽이 실을 닫지 않습니다" and "테라스" not in text:
                open_misses.append((x, y, text))
            skipped.append({"text": text, "x": round(x, 1), "y": round(y, 1), "reason": reason})
            print(f"[{index}/{len(seeds)}] skip {text}: {reason}", flush=True)
            continue
        found.update({"text": text, "x": seed_x, "y": seed_y, "name": room.norm_name(text)})
        detected.append(found)
        print(f"[{index}/{len(seeds)}] {text}  {found['area_m2']:.2f} m2", flush=True)
    detected, skipped = _detect_open_clusters(msp, boxes, segs, open_misses, detected, skipped, door)
    detected = _divide_shared_faces(msp, boxes, segs, detected, door)
    detected = _divide_on_dividers(msp, boxes, segs, detected, door)
    detected = _subtract_anchor_faces(msp, segs, detected)
    detected, skipped = _subtract_crossed_x_regions(msp, detected, skipped)
    detected, skipped = _drop_tiny_faces(detected, skipped)

    meta = resolve_label_meta_bbox(
        json.loads(meta_path.read_text(encoding="utf-8")),
        msp,
    )
    bbox = meta.get("bbox_mm") if isinstance(meta.get("bbox_mm"), dict) else {}
    if fill_unlabeled:
        if not bbox:
            xs, ys = _msp_xy_extents(msp)
            bbox = _bbox_from_xy(xs, ys) if xs and ys else {}
            meta = dict(meta)
            meta["bbox_mm"] = bbox
        detected, skipped = fill_unlabeled_rooms(
            msp,
            boxes,
            segs,
            detected,
            skipped,
            door,
            bbox,
            sample_mm=sample_mm,
            review_path=review_json_path(dxf_path),
        )

    disambiguate_duplicate_names(detected)

    by_face: dict[tuple, list[str]] = {}
    for inst in detected:
        by_face.setdefault(_face_key(inst["pts"]), []).append(inst["text"])

    grouped: dict[str, dict] = {}
    used_layers = {layer.dxf.name.lower() for layer in doc.layers}
    order: list[str] = []
    for inst in detected:
        name = inst["name"]
        if name not in grouped:
            grouped[name] = {
                "label": inst["text"],
                "layer": layer_name(inst["text"], used_layers),
                "color": list(rgb_for(len(order))),
                "synthetic": bool(inst.get("synthetic")),
                "instances": [],
            }
            order.append(name)
        others = [text for text in by_face[_face_key(inst["pts"])] if room.norm_name(text) != name]
        grouped[name]["instances"].append((inst, others))

    labels = [grouped[name] for name in order]
    out_dir = dxf_path.parent
    dxf_out = out_dir / f"{OUT_STEM}.dxf"
    png_out = out_dir / f"{OUT_STEM}.png"
    json_out = out_dir / f"{OUT_STEM}.json"
    mm_per_inch = floor_sheet_mm_per_inch(bbox, _px_width(meta))
    write_dxf(
        doc,
        dxf_out,
        [{**item, "instances": [pair[0] for pair in item["instances"]]} for item in labels],
        mm_per_inch,
    )
    print("render png from dxf", flush=True)
    render_label_png(dxf_out, meta, png_out)

    public_labels = []
    unique_area = 0.0
    seen_faces: set[tuple] = set()
    for item in labels:
        instances = []
        for inst, shared in item["instances"]:
            instances.append(_public_instance(inst, shared))
            key = _face_key(inst["pts"])
            if key not in seen_faces:
                seen_faces.add(key)
                unique_area += inst["area_m2"]
        row = {
            "label": item["label"],
            "layer": item["layer"],
            "color": item["color"],
            "count": len(instances),
            "area_m2": round(sum(r["area_m2"] for r in instances), 4),
            "instances": instances,
        }
        if item.get("synthetic"):
            row["synthetic"] = True
            row["source"] = SYNTHETIC_SOURCE_UNLABELED
        public_labels.append(row)

    info = {
        "source_dxf": str(dxf_path),
        "dxf": str(dxf_out),
        "png": str(png_out),
        "label_count": len(public_labels),
        "instance_count": len(detected),
        "skipped_count": len(skipped),
        "unique_face_area_m2": round(unique_area, 4),
        "labels": public_labels,
        "skipped": skipped,
        "rules": {
            "labels": "Korean room names, Latin names with 2+ letters and a digit, or space-separated Latin multi-word names (COFFEE SHOP); stacked lines within 1.8 text heights are joined top to bottom; fixture callouts such as 미니바, 옷장, 신발장, 수납장, 하부장, 상부장, 옷봉설치, 장비, 데스크, 서랍, 온수기, 살균기, 소독기, 도어, 테이블, 정수기, 휴대폰장, 게시판, 렉장, 랙장, 냉장고, 제빙기, 머신, 커피, MACHINE, WARMER, 전자레인지, 텀블러, 세척기, EV, 화분, 화장대, 월풀욕조, 욕조, (장애인) and non-area names 방풍실, 보관실, 퇴식대, 샤워실, 고압, 저압, 계단실, 승강기, EPS, OPEN, 오픈, 홀-, 홈-, 입구, 출구, 양치공간, 청소도구함, 청소도구실, 실외기실, 조경, 배경, 평면도, 비품고을, 항습기, 화물, 휠체어, 린넨실, 오물처리실, (장애인, 구급자, 응급환자, 의료가스실, 기계실, (여), (남), 문서고, 기울기, 자동방화, 인), 인승, OTIS, Wlqtnwjd, DA#, DA(, 올려옴, 내려감, DOOR, 패드, 기저귀, 침례탕약, 설치, 드라이기, 유사용, 상하부, 하부, (고객), 서비스동선, Drop-off, 상부, 캐노피선, 고장이슈, 대), 자쿠지, IN-S, 분전함, 세척실, 탕비실, 물품, 스트레쳐, E.V, ELEV, ELEV. HALL, ELEV. 홀, 유도등, 단자함, 소화전, 다용도실, 밸브, (근생), 정화조, 피난기, 보관소, 속도, 주거, 배수, 비상용, 탑승대기, ST(, 탱크, 실외기, 마감, 콘크리트, 행거, 클립바, 수조, 발전기실, 집수정, ASD, W.C, 세면대, ARM실, 옷장, 용수대, 자동문, 거울, 세탁기, 기기배치, 폐쇄, 장애인겸용, 데크, 비품고, 전기실, 충전소, 급속, 폐기품, 폐기물, 콤프레셔, 경계선, 대지안, 벽부형, 산책로, 올라옴, 수거함, 배기, FSD, DORDI, 승강식, OH4700, 유모차보관, C1A, C2A, C3A, TC1, PW1000, AG1100, 노출되는, 자전거, 피난구, DA-, 재활용, 급기, 주문제작, BC-, 세탁실, 승강장, 테라스, 청결물실, 수납, 보일러, 옷방, 파우더, 선큰가든, 발코니, 정원, 배선실, 병동, 수족관, 피난용, 설비, 셔틀, 커텐, 공조실, 공조기, (관통), GS건설, MDF, 펌프실, 축척, 사물함, 석], 공용욕실, S.L, F.L, FL., D.A, 장애인통로, 야외전망대, 안방욕실, LT-, 220V, 시공, 리무진, 세차, 정비, 카트, 용수, 하역장, 방재실, 출입통제, 정수실, 내려옴, 올라감, 회차공간, 하역, (승용/장애인용), (청사), DW3, 투석액, 불가, 엑스박스, C-AR, 승용, 장애인, 오피스텔, 사용중, 소방, UPS, 가스실, MMR, 집유정, BUS, MF-, 도면, PG11, AHU-, CTHC-, SSD, 확장형, 일반형, 계단, 알파, 드레스룸, WD, AW, ABS, AOC, 욕실 are excluded",
            "font": "PNG text is the TEXT stored on each label layer, drawn by the floor sheet renderer",
            "png": "rendered from floor_label_detected.dxf",
            "door": door,
            "fill_unlabeled": fill_unlabeled,
            "sample_mm": sample_mm if fill_unlabeled else None,
            "boundary": (
                "inner face of WALL, WINDOW, COLUMN, and DOOR lines; "
                "WALL/WINDOW ARC and CIRCLE are chord segments; "
                "COLUMN CIRCLE (radius 150-500 mm) is a round-column boundary; "
                "same-ring ARC gaps up to 2400 mm are bridged; "
                "open affects only doors on that room's closed boundary"
            ),
            "layer": (
                "validated drawing plus one layer per label; "
                "duplicate room names get #1/#2 postfixes so each place is its own layer; "
                "HATCH area is that label; name and area text sit on the layer"
            ),
            "shared_face": "each label keeps the whole face; do not sum shared faces",
            "unlabeled_fill": (
                "optional --fill-unlabeled rasters WALL/DOOR segments, "
                "L-bridges orthogonal free-end corner gaps (200-1100 mm), "
                "morph-closes small raster gaps, runs connected components on interior free space, "
                "then refits each seed to the vector wall inner face (detect_one, no nudge) "
                "so hatches meet walls like named labels; assigns 무명_N; skips llm_review/review.json "
                "stair/bathroom/elevator/outer-wall/bed bboxes, "
                "geometric crossed-diagonal X regions "
                "(review X only when it overlaps a geometric X), "
                "pockets mostly inside a named label's axis-aligned bbox "
                "(furniture WALL notches left out of the named face), "
                "and bathroom wall cells (nested vanity BASE/0/WALL "
                "LWPOLYLINE+drain CIRCLE — vanity within 100 mm of the face "
                "counts even when a WALL notch leaves a gap — "
                "concentric oval CIRCLE sinks, "
                "or 세면/화장실/WC/변기 TEXT); "
                "--sample-mm is raster cell size; validated DXF is not modified"
            ),
        },
    }
    json_out.write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")
    info["json"] = str(json_out)
    info["elapsed_s"] = round(time.time() - started, 1)
    return info


def main() -> None:
    parser = argparse.ArgumentParser(description="실명 라벨별 벽 안쪽 면적")
    parser.add_argument("--dxf", type=Path, required=True)
    parser.add_argument("--meta", type=Path, required=True)
    parser.add_argument(
        "--png",
        type=Path,
        default=None,
        help="쓰지 않는다. PNG는 floor_label_detected.dxf 를 렌더해서 만든다.",
    )
    parser.add_argument(
        "--door",
        choices=("close", "open"),
        default="close",
        help="각 실마다, 닫힌 벽 테두리에 닿는 문만 연다. close(기본)는 그 문도 막는다. 테두리 밖 DOOR는 어느 쪽이든 닫힌 경계로 둔다.",
    )
    parser.add_argument(
        "--fill-unlabeled",
        action="store_true",
        help="실명 없는 닫힌 칸을 벽 래스터·connected components 로 찾아 무명_N 을 더한다.",
    )
    parser.add_argument(
        "--sample-mm",
        type=float,
        default=DEFAULT_SAMPLE_MM,
        help=f"--fill-unlabeled 래스터 셀 크기(mm). 기본 {DEFAULT_SAMPLE_MM:.0f}",
    )
    args = parser.parse_args()
    for path in (args.dxf, args.meta):
        if not path.is_file():
            raise SystemExit(f"파일이 없습니다: {path}")
    if args.sample_mm <= 0:
        raise SystemExit("--sample-mm 은 양수여야 합니다")
    info = detect(
        args.dxf,
        args.meta,
        door=args.door,
        fill_unlabeled=args.fill_unlabeled,
        sample_mm=args.sample_mm,
    )
    print(f"door: {info['rules']['door']}")
    print(f"label_count: {info['label_count']}")
    print(f"instance_count: {info['instance_count']}")
    print(f"skipped_count: {info['skipped_count']}")
    print(f"unique_face_area_m2: {info['unique_face_area_m2']:.2f}")
    for item in info["labels"]:
        print(f"layer {item['layer']}: {item['area_m2']:.2f} m2 x{item['count']}")
    if info["skipped"]:
        shown = ", ".join(item["text"] for item in info["skipped"])
        print(f"skipped: {shown}")
    print(f"dxf: {info['dxf']}")
    print(f"png: {info['png']}")
    print(f"json: {info['json']}")
    print(f"elapsed_s: {info['elapsed_s']}")


if __name__ == "__main__":
    main()
