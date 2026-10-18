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

    창고·차고·화장실·방풍실·보관실·퇴식대·샤워실·고압·저압·계단실·복도·승강기·EPS·OPEN·오픈·홀-·홈-·입구·출구·양치공간·청소도구함·청소도구실·실외기실·조경·배경·평면도·비품고을·항습기·화물·휠체어·린넨실·오물처리실·구급자·응급환자·의료가스실·기계실·(여)·(남)·문서고·기울기·자동방화·인)·인승·OTIS·Wlqtnwjd·DA#·DA(·올려옴·내려감·DOOR·패드·기저귀·침례탕약·설치·드라이기·유사용·상하부·하부·(고객)·서비스동선·Drop-off·상부·캐노피선·고장이슈·대)·자쿠지·IN-S·분전함·세척실·탕비실·물품·스트레쳐·E.V·ELEV·ELEV. HALL·ELEV. 홀·WC·DA·유도등·단자함·소화전·다용도실·밸브·(근생)·정화조·피난기·보관소·속도·주거·배수·비상용·탑승대기·ST( 쪽은 실로 남기지 않는다.
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
    return row


def detect(dxf_path: Path, meta_path: Path, door: str = "close") -> dict:
    started = time.time()
    doc = ezdxf.readfile(str(dxf_path))
    msp = doc.modelspace()
    seeds = collect_labels(msp)
    print(f"labels: {len(seeds)} door: {door}", flush=True)
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
    detected, skipped = _drop_tiny_faces(detected, skipped)

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
    meta = resolve_label_meta_bbox(
        json.loads(meta_path.read_text(encoding="utf-8")),
        msp,
    )
    bbox = meta.get("bbox_mm") if isinstance(meta.get("bbox_mm"), dict) else {}
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
        public_labels.append(
            {
                "label": item["label"],
                "layer": item["layer"],
                "color": item["color"],
                "count": len(instances),
                "area_m2": round(sum(row["area_m2"] for row in instances), 4),
                "instances": instances,
            }
        )

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
            "labels": "Korean room names, or Latin names with 2+ letters and a digit; stacked lines within 1.8 text heights are joined top to bottom; fixture callouts such as 미니바, 옷장, 신발장, 수납장, 하부장, 상부장, 옷봉설치, 장비, 데스크, 서랍, 온수기, 살균기, 소독기, 도어, 테이블, 정수기, 휴대폰장, 게시판, 렉장, 랙장, 냉장고, 제빙기, 머신, 커피, 전자레인지, 텀블러, 세척기, EV, 화분, 화장대, 월풀욕조, 욕조, (장애인) and non-area names 방풍실, 보관실, 퇴식대, 샤워실, 고압, 저압, 계단실, 복도, 승강기, EPS, OPEN, 오픈, 홀-, 홈-, 입구, 출구, 양치공간, 청소도구함, 청소도구실, 실외기실, 조경, 배경, 평면도, 비품고을, 항습기, 화물, 휠체어, 린넨실, 오물처리실, (장애인, 구급자, 응급환자, 의료가스실, 기계실, (여), (남), 문서고, 기울기, 자동방화, 인), 인승, OTIS, Wlqtnwjd, DA#, DA(, 올려옴, 내려감, DOOR, 패드, 기저귀, 침례탕약, 설치, 드라이기, 유사용, 상하부, 하부, (고객), 서비스동선, Drop-off, 상부, 캐노피선, 고장이슈, 대), 자쿠지, IN-S, 분전함, 세척실, 탕비실, 물품, 스트레쳐, E.V, ELEV, ELEV. HALL, ELEV. 홀, 유도등, 단자함, 소화전, 다용도실, 밸브, (근생), 정화조, 피난기, 보관소, 속도, 주거, 배수, 비상용, 탑승대기, ST(, 탱크, 실외기, 마감, 콘크리트, 행거, 클립바, 수조, 발전기실, 집수정, ASD, W.C, 세면대, ARM실, 옷장, 용수대, 자동문, 거울, 세탁기, 기기배치, 폐쇄, 장애인겸용, 데크, 비품고, 전기실, 충전소, 급속, 폐기품, 폐기물, 콤프레셔, 경계선, 대지안, 벽부형, 산책로, 올라옴, 수거함, 배기, FSD, DORDI, 승강식, OH4700, 유모차보관, C1A, C2A, C3A, TC1, PW1000, AG1100, 노출되는, 자전거, 피난구, DA-, 재활용, 급기, 주문제작, BC-, 부속실, 세탁실, 승강장, 테라스, 청결물실, 수납, 보일러, 옷방, 파우더, 선큰가든, 발코니, 정원, 배선실, 병동, 수족관, 피난용, 설비, 셔틀, 커텐, 공조실, 공조기, (관통), GS건설, MDF, 펌프실, 축척, 사물함, 석], 공용욕실, S.L, F.L, D.A, 장애인통로, 야외전망대, 안방욕실, LT-, 220V, 시공, 리무진, 세차, 정비, 카트, 용수, 하역장, 방재실, 출입통제, 정수실, 내려옴, 올라감, 회차공간, 하역, (승용/장애인용), (청사), DW3, 투석액 are excluded",
            "font": "PNG text is the TEXT stored on each label layer, drawn by the floor sheet renderer",
            "png": "rendered from floor_label_detected.dxf",
            "door": door,
            "boundary": "inner face of WALL, WINDOW, COLUMN, and DOOR lines; open affects only doors on that room's closed boundary",
            "layer": "validated drawing plus one layer per label; HATCH area is that label; name and area text sit on the layer",
            "shared_face": "each label keeps the whole face; do not sum shared faces",
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
    args = parser.parse_args()
    for path in (args.dxf, args.meta):
        if not path.is_file():
            raise SystemExit(f"파일이 없습니다: {path}")
    info = detect(args.dxf, args.meta, door=args.door)
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
