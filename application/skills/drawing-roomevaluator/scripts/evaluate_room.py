#!/usr/bin/env python3
"""실명 라벨이 들어 있는 공간을 빨간 WALL 안쪽 면으로 면적 계산."""

from __future__ import annotations

import argparse
import json
import math
import re
from collections import defaultdict, deque
from pathlib import Path

import ezdxf
import shapely
from PIL import Image, ImageDraw, ImageFont
from shapely.errors import GEOSException
from shapely.geometry import GeometryCollection, LineString, MultiPoint, Point, Polygon, box
from shapely.ops import polygonize, unary_union
from shapely.strtree import STRtree

Image.MAX_IMAGE_PIXELS = None

WALL_LAYER = "WALL"
WINDOW_LAYER = "WINDOW"
COLUMN_LAYER = "COLUMN"
DOOR_LAYER = "DOOR"
BASE_LAYER = "BASE"
BOUNDARY_LAYERS = (WALL_LAYER, WINDOW_LAYER, COLUMN_LAYER, DOOR_LAYER)
AXIS_TOL_MM = 20.0
CLUSTER_TOL_MM = 15.0
JOIN_MM = 80.0
DOOR_GAP_MM = 2400.0
DOOR_TOUCH_MM = 450.0
WALL_CAP_MM = 300.0
# WALL/WINDOW ARC·CIRCLE을 직선 조각으로 나눌 때 목표 현 길이.
ARC_CHORD_MM = 250.0
# WALL/WINDOW 에서 이보다 작은 CIRCLE은 심볼로 보고 실 경계에서 뺀다.
MIN_WALL_CIRCLE_R_MM = 400.0
# COLUMN 원형 기둥 CIRCLE 반지름. 벽 끝과 맞아 실을 닫는 경계로 쓴다.
MIN_COLUMN_CIRCLE_R_MM = 150.0
MAX_COLUMN_CIRCLE_R_MM = 500.0
# 같은 원호 묶음(중심·반지름)을 판별할 때 반올림 단위.
ARC_CLUSTER_MM = 50.0
# 끝점마다 잇는 이웃 수. 이중선 끝막음은 가까운 몇 선이면 된다.
CAP_PER_END = 3
# 이보다 많은 선은 실 하나가 아니라 층 전체다. node/polygonize 를 하지 않는다.
MAX_FACE_LINES = 12000
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


# 면적 제외·집기·설비 토큰 목록은 label_info.json.
_LABEL_INFO_PATH = Path(__file__).resolve().parent / "label_info.json"
_FIXTURE_TAIL = re.compile(r"^(?:[#＃]?\d+|[（(]\d+[）)])?$")


def _require_string_list(data: dict, key: str, path: Path) -> list[str]:
    tokens = data.get(key)
    if not isinstance(tokens, list) or not all(isinstance(t, str) and t for t in tokens):
        raise ValueError(f"label_info.json: {key} must be a non-empty string list ({path})")
    return tokens


def _load_label_info(path: Path = _LABEL_INFO_PATH) -> dict[str, tuple[str, ...]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    # 부분 문자열 매칭은 공백 제거·영문 대문자. fixture 이름은 표기 그대로 norm 만.
    return {
        "label_only_area_tokens": tuple(
            norm_name(t).upper() for t in _require_string_list(data, "label_only_area_tokens", path)
        ),
        "region_area_tokens": tuple(
            norm_name(t).upper() for t in _require_string_list(data, "region_area_tokens", path)
        ),
        "equipment_tokens": tuple(
            norm_name(t).upper().replace(".", "")
            for t in _require_string_list(data, "equipment_tokens", path)
        ),
        "fixture_names": tuple(norm_name(t) for t in _require_string_list(data, "fixture_names", path)),
        "non_room_tokens": tuple(
            norm_name(t).upper() for t in _require_string_list(data, "non_room_tokens", path)
        ),
    }


_LABEL_INFO = _load_label_info()
LABEL_ONLY_AREA_TOKENS = _LABEL_INFO["label_only_area_tokens"]
REGION_AREA_TOKENS = _LABEL_INFO["region_area_tokens"]
EQUIPMENT_TOKENS = _LABEL_INFO["equipment_tokens"]
FIXTURE_NAMES = _LABEL_INFO["fixture_names"]
NON_ROOM_TOKENS = _LABEL_INFO["non_room_tokens"]


def is_excluded_area_label(text: str, *, include_callouts: bool = True) -> bool:
    """면적과 실 라벨에서 빼는 이름.

    `label_info.json`의 `region_area_tokens`는 항상 제외하고,
    `label_only_area_tokens`는 `include_callouts=True`일 때만 제외한다.
    E.V / ELEV / EPS / PS 형태 매칭은 코드에 둔다. 공백을 없애고 영문은 대문자로 본다.
    """
    name = norm_name(text)
    folded = name.upper()
    if include_callouts and any(token in folded for token in LABEL_ONLY_AREA_TOKENS):
        return True
    if any(token in folded for token in REGION_AREA_TOKENS):
        return True
    nodot = folded.replace(".", "")
    if re.fullmatch(r"EV(?:[#＃-]?\d+)?", nodot) is not None:
        return True
    if "ELEV" in nodot:
        return True
    return re.fullmatch(r"(?:EPS|PS)(?:[#＃-]?\d+)?", nodot) is not None


def is_equipment_label(text: str) -> bool:
    """면적을 잡지 않는 집기·설비 표기. 목록은 label_info.json의 equipment_tokens.
    E.V는 여기가 아니라 면적 제외 이름이다."""
    name = norm_name(text)
    folded = name.upper().replace(".", "")
    return any(token in folded for token in EQUIPMENT_TOKENS)


def is_fixture_label(text: str) -> bool:
    """객실 안 집기 이름, 또는 그 뒤에 번호만 붙은 표기. fixture_names + equipment_tokens."""
    if is_equipment_label(text):
        return True
    name = norm_name(text)
    return any(
        name == fixture or (name.startswith(fixture) and _FIXTURE_TAIL.fullmatch(name[len(fixture) :]))
        for fixture in FIXTURE_NAMES
    )


WALL_PERIMETER_MIN = 0.90
WALL_COVER_MM = 250.0
SEED_REACH_MM = 25000.0
SEED_BUFFER_STEPS_MM = (80.0, 160.0, 320.0, 640.0, 1000.0, 1400.0, 1800.0, 2400.0)
CLUSTER_LINK_MM = 20000.0
CLUSTER_REACH_MM = 45000.0
CLUSTER_CELL_MM = 100.0
CLUSTER_GROW_MIN_MM = 2000.0
CLUSTER_GROW_MAX_MM = 30000.0
CLUSTER_GROW_STEP_MM = 500.0
CLUSTER_WALL_EDGE_MIN = 0.55
CLUSTER_WALL_BLOCK_MM = 150.0
CLUSTER_HULL_BUFFER_MM = 5000.0
CLUSTER_HULL_PAD_MM = 8000.0
CLUSTER_HULL_WALL_DENSITY_MIN = 0.40
CLUSTER_MIN_SEEDS = 2


def wall_perimeter_ratio(segs, pts) -> float:
    """면 둘레 중 벽·창·기둥·문 선에서 250 mm 안인 길이의 비율.

    방안 2에서만 쓴다. 90% 미만이면 그 면을 두지 않고 방안 3으로 넘긴다.
    근생-8 옆 테라스처럼 한 변이 열린 경우다.
    """
    if len(pts) < 2:
        return 0.0
    ring = [(float(x), float(y)) for x, y in pts]
    if ring[0] != ring[-1]:
        ring.append(ring[0])
    edges: list[LineString] = []
    total = 0.0
    for start, end in zip(ring, ring[1:]):
        length = math.hypot(end[0] - start[0], end[1] - start[1])
        if length < 1.0:
            continue
        edges.append(LineString([start, end]))
        total += length
    if total <= 0.0 or not segs:
        return 0.0
    minx = min(point[0] for point in ring) - 400.0
    maxx = max(point[0] for point in ring) + 400.0
    miny = min(point[1] for point in ring) - 400.0
    maxy = max(point[1] for point in ring) + 400.0
    near: list[LineString] = []
    for item in segs:
        if hasattr(item, "coords"):
            line = item
        else:
            x0, y0, x1, y1 = item[:4]
            if math.hypot(x1 - x0, y1 - y0) < 40.0:
                continue
            line = LineString([(x0, y0), (x1, y1)])
        bounds = line.bounds
        if bounds[2] < minx or bounds[0] > maxx or bounds[3] < miny or bounds[1] > maxy:
            continue
        near.append(line)
    if not near:
        return 0.0
    cover = unary_union(near).buffer(WALL_COVER_MM)
    covered = 0.0
    for edge in edges:
        hit = edge.intersection(cover)
        if not hit.is_empty:
            covered += min(hit.length, edge.length)
    return covered / total


def _latin_multiword_room(text: str) -> bool:
    """공백으로 나뉜 영문 단어 2개 이상(각 글자 2자+)이면 실명 후보. COFFEE SHOP 등."""
    parts = [p for p in re.split(r"\s+", (text or "").strip()) if p]
    if len(parts) < 2:
        return False
    if any(token in text for token in ("면적", "천장", ":", "：", "=", "/")):
        return False
    for part in parts:
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9#()·.+-]*", part):
            return False
        if len(re.findall(r"[A-Za-z]", part)) < 2:
            return False
    name = norm_name(text)
    return bool(name) and len(name) <= 24


def is_room_label(text: str) -> bool:
    """한글 실명, 영문 2자 이상+숫자, 또는 공백 구분 영문 다단어. 축선·치수·안내는 제외."""
    name = norm_name(text)
    # 마감·비치·주차 안내, 창고, 차고, 화장실, 방풍실, 보관실, 퇴식대, 샤워실, 고압, 저압, 계단실, 복도, EPS, WC, 입구, 출구, 상부 OPEN, DA는 면적 실이 아니다.
    folded = name.upper()
    if is_excluded_area_label(text) or is_fixture_label(text):
        return False
    if any(token in folded for token in NON_ROOM_TOKENS):
        return False
    if folded in {"DA", "D.A", "D.A.", "WC", "W.C", "W.C.", "상부"}:
        return False
    if folded.startswith("WC(") or folded.startswith("W.C("):
        return False
    if _roomish(text):
        return True
    if _latin_multiword_room(text):
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


def is_split_anchor(text: str) -> bool:
    """면적 실명은 아니지만, 다른 실과 한 면이면 벽 끝으로 가를 대상이다."""
    if is_room_label(text):
        return False
    name = norm_name(text)
    folded = name.upper()
    if "화장실" in folded or "계단실" in folded:
        return True
    if folded in {"WC", "W.C", "W.C."}:
        return True
    return folded.startswith("WC(") or folded.startswith("W.C(")


def is_region_label(text: str) -> bool:
    """한 면을 가를 때 다른 영역으로 보는 글자. 집기는 호출자가 뺀다."""
    if is_room_label(text) or is_split_anchor(text):
        return True
    if is_equipment_label(text) or is_fixture_label(text):
        return is_excluded_area_label(text, include_callouts=False)
    # 패드1, 패드2처럼 번호만 붙은 표기는 면을 가를 때 참조하지 않는다.
    if is_excluded_area_label(text, include_callouts=False):
        return True
    name = norm_name(text)
    folded = name.upper()
    return folded in {"DA", "D.A", "D.A."}


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


def _door_leaf_rectangle(entity) -> bool:
    """문짝으로 그린 얇은 닫힌 사각형.

    끝의 짧은 변이 이웃 문짝과 이어지면 실 한가운데에 벽이 생긴다.
    개구를 막는 문선은 직선이라 여기 해당하지 않는다.
    """
    if entity.dxftype() != "LWPOLYLINE" or not entity.closed:
        return False
    if entity.dxf.layer != DOOR_LAYER:
        return False
    pts = [(float(a), float(b)) for a, b in entity.get_points("xy")]
    if pts and pts[0] == pts[-1]:
        pts = pts[:-1]
    if len(pts) != 4:
        return False
    lengths = [
        math.hypot(pts[(i + 1) % 4][0] - pts[i][0], pts[(i + 1) % 4][1] - pts[i][1])
        for i in range(4)
    ]
    short, long = min(lengths), max(lengths)
    return short <= 80.0 and 500.0 <= long <= 1700.0


def _door_swing_hinges(msp) -> list[tuple[float, float, float]]:
    """DOOR ARC 힌지 (cx, cy, radius)."""
    hinges: list[tuple[float, float, float]] = []
    for entity in msp:
        if entity.dxftype() != "ARC" or getattr(entity.dxf, "layer", None) != DOOR_LAYER:
            continue
        try:
            radius = float(entity.dxf.radius)
            sweep = (float(entity.dxf.end_angle) - float(entity.dxf.start_angle)) % 360.0
            cx = float(entity.dxf.center.x)
            cy = float(entity.dxf.center.y)
        except Exception:  # noqa: BLE001
            continue
        if 600.0 <= radius <= 1600.0 and 20.0 <= sweep <= 200.0:
            hinges.append((cx, cy, radius))
    return hinges


def _is_swing_leaf_segment(
    x0: float,
    y0: float,
    x1: float,
    y1: float,
    hinges: list[tuple[float, float, float]],
) -> bool:
    """힌지에서 문 반지름만큼 나간 문짝 직선.

    close 모드에서는 개구를 벽으로 잇고 문짝은 경계가 아니다.
    힌지와 힌지를 잇는 개구 현(닫힌 문짝이 벽선에 누운 경우)은 남긴다.
    WALL로 잘못 올라온 문짝도 같은 형태로 뺀다.
    """
    if not hinges:
        return False
    length = math.hypot(x1 - x0, y1 - y0)
    if not (600.0 <= length <= 1700.0):
        return False
    for cx, cy, radius in hinges:
        if abs(length - radius) > 90.0:
            continue
        d0 = math.hypot(x0 - cx, y0 - cy)
        d1 = math.hypot(x1 - cx, y1 - cy)
        at0, at1 = d0 <= 55.0, d1 <= 55.0
        if at0 == at1:
            continue
        far_x, far_y = (x1, y1) if at0 else (x0, y0)
        # 다른 힌지에 닿으면 양개문 사이 벽선/닫힌 문짝 — 경계로 유지.
        if any(
            math.hypot(far_x - ox, far_y - oy) <= 55.0
            for ox, oy, _other_r in hinges
            if abs(ox - cx) > 100.0 or abs(oy - cy) > 100.0
        ):
            continue
        return True
    return False


def _arc_point(cx: float, cy: float, r: float, angle_deg: float) -> tuple[float, float]:
    rad = math.radians(angle_deg % 360.0)
    return cx + r * math.cos(rad), cy + r * math.sin(rad)


def _curve_parts(entity) -> list[tuple[float, float, float, float]]:
    """ARC/CIRCLE을 현 길이 약 ARC_CHORD_MM 인 직선 조각으로 나눈다.

    WALL/WINDOW 의 작은 CIRCLE(심볼)은 비운다. COLUMN 원형 기둥(반지름
    MIN..MAX_COLUMN_CIRCLE_R_MM)은 벽 끝과 이어 실 경계로 쓴다.
    DOOR 스윙 호는 호출측에서 제외한다.
    """
    kind = entity.dxftype()
    if kind not in ("ARC", "CIRCLE"):
        return []
    try:
        cx = float(entity.dxf.center.x)
        cy = float(entity.dxf.center.y)
        r = float(entity.dxf.radius)
    except Exception:  # noqa: BLE001
        return []
    if r < 1.0:
        return []
    if kind == "CIRCLE":
        layer = str(getattr(entity.dxf, "layer", "") or "").upper()
        if layer == COLUMN_LAYER:
            if r < MIN_COLUMN_CIRCLE_R_MM or r > MAX_COLUMN_CIRCLE_R_MM:
                return []
        elif r < MIN_WALL_CIRCLE_R_MM:
            return []
        start_deg = 0.0
        span_deg = 360.0
        closed = True
    else:
        start_deg = float(entity.dxf.start_angle) % 360.0
        end_deg = float(entity.dxf.end_angle) % 360.0
        span_deg = (end_deg - start_deg) % 360.0
        if span_deg <= 1e-9:
            span_deg = 360.0
        closed = False
    step_deg = max(0.5, math.degrees(ARC_CHORD_MM / r))
    steps = max(2, int(math.ceil(span_deg / step_deg)))
    pts: list[tuple[float, float]] = []
    for i in range(steps + (0 if closed else 1)):
        pts.append(_arc_point(cx, cy, r, start_deg + span_deg * i / steps))
    if closed:
        pts.append(pts[0])
    return [
        (pts[i][0], pts[i][1], pts[i + 1][0], pts[i + 1][1])
        for i in range(len(pts) - 1)
        if math.hypot(pts[i + 1][0] - pts[i][0], pts[i + 1][1] - pts[i][1]) > 0.5
    ]


def _arc_coverage_pieces(
    spans: list[tuple[float, float]], join_deg: float
) -> list[tuple[float, float]]:
    """ARC start/end(도) 목록을 [0,360) 위 겹치지 않는 덮개 구간으로 합친다."""
    if not spans:
        return []
    units: list[tuple[float, float]] = []
    for sa, ea in spans:
        start = sa % 360.0
        span = (ea - sa) % 360.0
        if span <= 1e-9:
            span = 360.0
        if span >= 360.0 - 1e-9:
            return [(0.0, 360.0)]
        end = start + span
        if end <= 360.0:
            units.append((start, end))
        else:
            units.append((start, 360.0))
            units.append((0.0, end - 360.0))
    units.sort()
    merged: list[list[float]] = [[units[0][0], units[0][1]]]
    for a, b in units[1:]:
        if a <= merged[-1][1] + join_deg:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])
    if (
        len(merged) >= 2
        and merged[0][0] <= join_deg
        and merged[-1][1] >= 360.0 - join_deg
    ):
        wrap = [merged[-1][0], merged[0][1] + 360.0]
        mid = merged[1:-1]
        merged = [wrap, *mid]
    return [(a, b) for a, b in merged]


def _bridged_arc_gap_chords(
    arc_spans: list[tuple[float, float, float, float, float]],
) -> list[tuple[float, float, float, float]]:
    """같은 중심·반지름 WALL/WINDOW ARC에서 원호 길이 DOOR_GAP_MM 이하 틈을 현으로 잇는다."""
    if not arc_spans:
        return []
    groups: dict[tuple[float, float, float], list[tuple[float, float]]] = defaultdict(list)
    centers: dict[tuple[float, float, float], list[tuple[float, float, float]]] = defaultdict(
        list
    )
    for cx, cy, r, sa, ea in arc_spans:
        key = (
            round(cx / ARC_CLUSTER_MM) * ARC_CLUSTER_MM,
            round(cy / ARC_CLUSTER_MM) * ARC_CLUSTER_MM,
            round(r / ARC_CLUSTER_MM) * ARC_CLUSTER_MM,
        )
        if key[2] < 1.0:
            continue
        groups[key].append((sa, ea))
        centers[key].append((cx, cy, r))
    out: list[tuple[float, float, float, float]] = []
    for key, spans in groups.items():
        samples = centers[key]
        cx = sum(c[0] for c in samples) / len(samples)
        cy = sum(c[1] for c in samples) / len(samples)
        r = sum(c[2] for c in samples) / len(samples)
        join_deg = max(0.2, math.degrees(JOIN_MM / r))
        covered = _arc_coverage_pieces(spans, join_deg)
        if not covered:
            continue
        if len(covered) == 1 and covered[0][1] - covered[0][0] >= 360.0 - 1e-6:
            continue
        # covered 항목은 [a,b] (b는 360 초과 가능 = 0°를 넘는 구간)
        ends: list[tuple[float, float]] = []
        for a, b in covered:
            if b > 360.0:
                ends.append((a, 360.0))
                ends.append((0.0, b - 360.0))
            else:
                ends.append((a, b))
        ends = [(a, b) for a, b in ends if b - a > 1e-6]
        ends.sort()
        if not ends:
            continue
        pieces: list[list[float]] = [[ends[0][0], ends[0][1]]]
        for a, b in ends[1:]:
            if a <= pieces[-1][1] + join_deg:
                pieces[-1][1] = max(pieces[-1][1], b)
            else:
                pieces.append([a, b])
        gaps = [(pieces[i][1], pieces[i + 1][0]) for i in range(len(pieces) - 1)]
        wrap = (pieces[0][0] + 360.0) - pieces[-1][1]
        if wrap > join_deg + 1e-9:
            gaps.append((pieces[-1][1], pieces[0][0] + 360.0))
        for ga, gb in gaps:
            span_deg = gb - ga
            if span_deg <= 1e-6:
                continue
            if r * math.radians(span_deg) > DOOR_GAP_MM:
                continue
            x0, y0 = _arc_point(cx, cy, r, ga)
            x1, y1 = _arc_point(cx, cy, r, gb)
            if math.hypot(x1 - x0, y1 - y0) > 0.5:
                out.append((x0, y0, x1, y1))
    return out


def wall_segments(msp, boxes, *, include_door: bool = True, open_doors=None) -> list[tuple[float, float, float, float]]:
    segs: list[tuple[float, float, float, float]] = []
    layers = BOUNDARY_LAYERS if include_door else (WALL_LAYER, WINDOW_LAYER, COLUMN_LAYER)
    arc_spans: list[tuple[float, float, float, float, float]] = []
    # close 모드: 문짝 LINE은 경계에서 빼고, 개구는 벽 현·문 틈 bridge 로 막는다.
    hinges = _door_swing_hinges(msp)
    for e in msp:
        # DOOR 선은 경계다. 문짝 사각형·스윙 문짝 LINE과 open_doors 의 문짝은 뺀다.
        # DOOR ARC(여닫이 스윙)는 경계가 아니다.
        if e.dxf.layer not in layers:
            continue
        if e.dxf.layer == DOOR_LAYER and e.dxftype() == "ARC":
            continue
        if e.dxf.layer == DOOR_LAYER and _door_leaf_rectangle(e):
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
        elif t in ("ARC", "CIRCLE"):
            parts = _curve_parts(e)
            if (
                t == "ARC"
                and e.dxf.layer in (WALL_LAYER, WINDOW_LAYER)
                and parts
            ):
                try:
                    arc_spans.append(
                        (
                            float(e.dxf.center.x),
                            float(e.dxf.center.y),
                            float(e.dxf.radius),
                            float(e.dxf.start_angle) % 360.0,
                            float(e.dxf.end_angle) % 360.0,
                        )
                    )
                except Exception:  # noqa: BLE001
                    pass
        for x0, y0, x1, y1 in parts:
            if _inside_box((x0 + x1) * 0.5, (y0 + y1) * 0.5, boxes):
                continue
            if e.dxf.layer == DOOR_LAYER and _segment_on_open_door(x0, y0, x1, y1, open_doors):
                continue
            # 실 안으로 튀어나온 문짝(DOOR·WALL 오승격)은 경계가 아니다.
            if _is_swing_leaf_segment(x0, y0, x1, y1, hinges):
                continue
            segs.append((x0, y0, x1, y1))
    for x0, y0, x1, y1 in _bridged_arc_gap_chords(arc_spans):
        if _inside_box((x0 + x1) * 0.5, (y0 + y1) * 0.5, boxes):
            continue
        segs.append((x0, y0, x1, y1))
    return segs


def entity_segments(msp, boxes, *, open_doors=None) -> list[tuple[float, float, float, float]]:
    """모든 LINE/LWPOLYLINE 엔티티를 경계로 쓴다 (WALL 레이어만이 아님).

    수동 다각형 추가용. 문짝 사각형·스윙 문짝·기둥 안 선은 wall_segments 와 같이 뺀다.
    """
    segs: list[tuple[float, float, float, float]] = []
    hinges = _door_swing_hinges(msp)
    for e in msp:
        if e.dxftype() not in ("LINE", "LWPOLYLINE"):
            continue
        if e.dxf.layer == DOOR_LAYER and _door_leaf_rectangle(e):
            continue
        for x0, y0, x1, y1 in _straight_parts(e):
            if _inside_box((x0 + x1) * 0.5, (y0 + y1) * 0.5, boxes):
                continue
            if e.dxf.layer == DOOR_LAYER and _segment_on_open_door(x0, y0, x1, y1, open_doors):
                continue
            if _is_swing_leaf_segment(x0, y0, x1, y1, hinges):
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


def _gap_crosses_partition(
    horizontal: bool,
    axis: float,
    gap0: float,
    gap1: float,
    cross_int: dict[float, list[tuple[float, float]]],
) -> bool:
    """문 틈을 가로지르는 직교 벽이 있으면 실간 격벽이다.

    이웃 실 욕실 하벽처럼 2.4 m 안 틈을 이을 때, 그 사이에 세로(또는 가로)
    실간 벽이 있으면 입구·복도까지 막아 버리므로 잇지 않는다.
    """
    lo, hi = (gap0, gap1) if gap0 <= gap1 else (gap1, gap0)
    if hi - lo < 40.0:
        return False
    for cross_axis, intervals in cross_int.items():
        if not (lo + 40.0 < cross_axis < hi - 40.0):
            continue
        for c0, c1 in _merge_intervals(list(intervals), JOIN_MM):
            if c1 - c0 < 400.0:
                continue
            if c0 - 80.0 <= axis <= c1 + 80.0:
                return True
    return False


def _gap_is_through_corridor(
    horizontal: bool,
    axis: float,
    gap0: float,
    gap1: float,
    cross_int: dict[float, list[tuple[float, float]]],
) -> bool:
    """틈 끝의 직교 벽이 축을 지나 양쪽으로 이어지면 복도 개구다.

    같은 세로선 위·아래 문틀만 있고 그 사이가 복도일 때, 위·아래 가로 벽이
    그 세로축을 관통해 반대편까지 간다. 이런 틈을 이으면 로비·복도 칸이
    가짜 세로벽에 막힌다. 한쪽만 닿는 일반 실 벽·문 옆은 해당하지 않는다.
    """
    del horizontal  # 축 방향은 cross_int 선택으로 이미 반영됨
    lo, hi = (gap0, gap1) if gap0 <= gap1 else (gap1, gap0)
    if hi - lo < 400.0:
        return False
    end_band = 150.0
    past = 400.0
    for end in (lo, hi):
        for cross_axis, intervals in cross_int.items():
            if abs(cross_axis - end) > end_band:
                continue
            for c0, c1 in _merge_intervals(list(intervals), JOIN_MM):
                if c1 - c0 < 800.0:
                    continue
                if c0 <= axis + 80.0 and c1 >= axis - 80.0:
                    if (axis - c0) >= past and (c1 - axis) >= past:
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
        cross = v_int if horizontal else h_int

        def _allow_close(a: float, b: float, horizontal=horizontal, axis=axis) -> bool:
            if _gap_crosses_partition(horizontal, axis, a, b, cross):
                return False
            if _gap_is_through_corridor(horizontal, axis, a, b, cross):
                return False
            return True

        if open_doors:
            return _bridge(
                merged,
                DOOR_GAP_MM,
                allow=lambda a, b, horizontal=horizontal, axis=axis: (
                    not _gap_is_open_door(horizontal, axis, a, b, open_doors)
                    and not _gap_crosses_partition(horizontal, axis, a, b, cross)
                    and not _gap_is_through_corridor(horizontal, axis, a, b, cross)
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
        return _bridge(merged, DOOR_GAP_MM, allow=_allow_close)

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
    """열린 끝점을 tol 안에서 가장 가까운 다른 벽선 몇 개에만 잇는다.

    이중선 벽의 끝막음이다. tol 안의 선을 전부 잇면 끝점마다 선이 생겨
    층 전체가 수만 선이 된다.
    """
    if not lines:
        return lines
    tree = STRtree(lines)
    extra: list[LineString] = []
    seen: set[tuple[tuple[float, float], tuple[float, float]]] = set()
    seen_ends: set[tuple[float, float]] = set()
    for ln in lines:
        for xy in (ln.coords[0], ln.coords[-1]):
            ax, ay = float(xy[0]), float(xy[1])
            end = (round(ax, 1), round(ay, 1))
            if end in seen_ends:
                continue
            seen_ends.add(end)
            pt = Point(ax, ay)
            hits: list[tuple[float, LineString]] = []
            for idx in tree.query(pt.buffer(tol)):
                other = lines[int(idx)]
                if other is ln:
                    continue
                dist = float(other.distance(pt))
                if dist <= 0.5 or dist > tol:
                    continue
                hits.append((dist, other))
            if not hits:
                continue
            hits.sort(key=lambda item: item[0])
            for _dist, other in hits[:CAP_PER_END]:
                nearest = other.interpolate(other.project(pt))
                b = (round(float(nearest.x), 1), round(float(nearest.y), 1))
                if end == b:
                    continue
                key = tuple(sorted((end, b)))
                if key in seen:
                    continue
                seen.add(key)
                extra.append(LineString([(ax, ay), (float(nearest.x), float(nearest.y))]))
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
    if len(lines) < 3 or len(lines) > MAX_FACE_LINES:
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


def _wallish_lines(msp, lines: list[LineString], origin: tuple[float, float], pad: float) -> list[LineString]:
    """벽 끝에서 이어진 BASE 평행 이중선.

    레이어는 벽이 아니어도 문틀이나 짧은 벽 꺾임처럼 보이면 그 선으로 실을 닫는다.
    해치처럼 간격이 여러 겹인 선은 넣지 않는다.
    """
    if not lines:
        return []
    ox, oy = origin
    base: list[tuple[float, float, float, float, float, float, float]] = []
    for entity in msp:
        if getattr(entity.dxf, "layer", None) != "BASE":
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
        for x0, y0, x1, y1 in parts:
            length = math.hypot(x1 - x0, y1 - y0)
            if not (500.0 <= length <= 4000.0):
                continue
            if abs((x0 + x1) * 0.5 - ox) > pad or abs((y0 + y1) * 0.5 - oy) > pad:
                continue
            base.append((x0, y0, x1, y1, length, (x1 - x0) / length, (y1 - y0) / length))
    if len(base) < 2:
        return []
    paired: set[int] = set()
    for i, (x0, y0, _x1, _y1, length, ux, uy) in enumerate(base):
        bins: set[int] = set()
        mates = 0
        for j, (a0, b0, a1, b1, _other, vx, vy) in enumerate(base):
            if i == j or abs(ux * vx + uy * vy) < 0.99:
                continue
            px, py = (a0 + a1) * 0.5 - x0, (b0 + b1) * 0.5 - y0
            perp = abs(-uy * px + ux * py)
            if perp > 220.0:
                continue
            along0 = (a0 - x0) * ux + (b0 - y0) * uy
            along1 = (a1 - x0) * ux + (b1 - y0) * uy
            lo, hi = min(along0, along1), max(along0, along1)
            overlap = min(length, hi) - max(0.0, lo)
            if overlap < 400.0:
                continue
            bins.add(round(perp / 30.0))
            if 15.0 <= perp <= 200.0:
                mates += 1
        if mates and len(bins) < 5:
            paired.add(i)
    if not paired:
        return []
    tree = STRtree(lines)
    kept: list[LineString] = []
    for index in sorted(paired):
        x0, y0, x1, y1, _length, _ux, _uy = base[index]
        near = False
        for x, y in ((x0, y0), (x1, y1)):
            pt = Point(x, y)
            for hit in tree.query(pt.buffer(400.0)):
                if float(lines[int(hit)].distance(pt)) <= 400.0:
                    near = True
                    break
            if near:
                break
        if near:
            kept.append(LineString([(x0, y0), (x1, y1)]))
    return kept


def _continuing_wall_lines(msp, lines: list[LineString], origin: tuple[float, float], pad: float) -> list[LineString]:
    """벽과 나란히 이어진 BASE 선과, 끊긴 벽 끝을 다음 벽까지 이은 선.

    실을 나눌 때 같은 방향으로 이어지는 벽을 경계로 쓴다.
    """
    if not lines:
        return []
    ox, oy = origin
    walls = [ln for ln in lines if ln.length >= 800.0]
    if not walls:
        return []
    refs: list[tuple[float, float, float, float]] = []
    for ln in walls:
        x0, y0 = float(ln.coords[0][0]), float(ln.coords[0][1])
        x1, y1 = float(ln.coords[-1][0]), float(ln.coords[-1][1])
        length = math.hypot(x1 - x0, y1 - y0)
        refs.append((x0, y0, (x1 - x0) / length, (y1 - y0) / length))
    raw: list[LineString] = []
    for entity in msp:
        if getattr(entity.dxf, "layer", None) != "BASE" or entity.dxftype() != "LINE":
            continue
        x0, y0 = float(entity.dxf.start.x), float(entity.dxf.start.y)
        x1, y1 = float(entity.dxf.end.x), float(entity.dxf.end.y)
        length = math.hypot(x1 - x0, y1 - y0)
        if not (800.0 <= length <= 8000.0):
            continue
        if abs((x0 + x1) * 0.5 - ox) > pad or abs((y0 + y1) * 0.5 - oy) > pad:
            continue
        vx, vy = (x1 - x0) / length, (y1 - y0) / length
        mx, my = (x0 + x1) * 0.5, (y0 + y1) * 0.5
        for wx, wy, ux, uy in refs:
            if abs(vx * ux + vy * uy) < 0.995:
                continue
            px, py = mx - wx, my - wy
            if abs(-uy * px + ux * py) <= 80.0:
                raw.append(LineString([(x0, y0), (x1, y1)]))
                break
    accepted: list[LineString] = []
    if raw:
        tree = STRtree(lines)

        def touches_wall(ln: LineString) -> bool:
            for xy in (ln.coords[0], ln.coords[-1]):
                pt = Point(float(xy[0]), float(xy[1]))
                for hit in tree.query(pt.buffer(400.0)):
                    if float(lines[int(hit)].distance(pt)) <= 400.0:
                        return True
            return False

        rest: list[LineString] = []
        for ln in raw:
            if touches_wall(ln):
                accepted.append(ln)
            else:
                rest.append(ln)
        for _ in range(8):
            if not accepted or not rest:
                break
            grown = STRtree(accepted)
            still: list[LineString] = []
            for ln in rest:
                joined = False
                for xy in (ln.coords[0], ln.coords[-1]):
                    pt = Point(float(xy[0]), float(xy[1]))
                    for hit in grown.query(pt.buffer(400.0)):
                        if float(accepted[int(hit)].distance(pt)) <= 400.0:
                            joined = True
                            break
                    if joined:
                        break
                if joined:
                    accepted.append(ln)
                else:
                    still.append(ln)
            if len(still) == len(rest):
                break
            rest = still
    extensions: list[LineString] = []
    seen: set[tuple] = set()
    wall_tree = STRtree(lines)
    for ln in walls:
        x0, y0 = float(ln.coords[0][0]), float(ln.coords[0][1])
        x1, y1 = float(ln.coords[-1][0]), float(ln.coords[-1][1])
        length = math.hypot(x1 - x0, y1 - y0)
        ux, uy = (x1 - x0) / length, (y1 - y0) / length
        for x, y, sign in ((x0, y0, -1.0), (x1, y1, 1.0)):
            if abs(x - ox) > pad or abs(y - oy) > pad:
                continue
            pt = Point(x, y)
            dx, dy = ux * sign, uy * sign
            blocked = False
            for hit in wall_tree.query(pt.buffer(80.0)):
                other = lines[int(hit)]
                if other.equals(ln) or float(other.distance(pt)) > 80.0:
                    continue
                nearest = other.interpolate(other.project(pt))
                along = (float(nearest.x) - x) * dx + (float(nearest.y) - y) * dy
                if along > 20.0:
                    blocked = True
                    break
            if blocked:
                continue
            ray = LineString([(x, y), (x + dx * 3600.0, y + dy * 3600.0)])
            best: tuple[float, float, float] | None = None
            for hit in wall_tree.query(ray.buffer(120.0)):
                other = lines[int(hit)]
                if other.equals(ln):
                    continue
                points: list[tuple[float, float]] = []
                inter = ray.intersection(other)
                if not inter.is_empty:
                    geoms = list(inter.geoms) if hasattr(inter, "geoms") else [inter]
                    for geom in geoms:
                        if geom.geom_type == "Point":
                            points.append((float(geom.x), float(geom.y)))
                for ex, ey in (other.coords[0], other.coords[-1]):
                    along = (float(ex) - x) * dx + (float(ey) - y) * dy
                    perp = abs((float(ex) - x) * -dy + (float(ey) - y) * dx)
                    if 200.0 <= along <= 3600.0 and perp <= 120.0:
                        points.append((float(ex), float(ey)))
                for px, py in points:
                    along = (px - x) * dx + (py - y) * dy
                    if along <= 200.0 or along > 3600.0:
                        continue
                    if best is None or along < best[0]:
                        best = (along, px, py)
            if best is None:
                continue
            key = tuple(sorted(((round(x, 1), round(y, 1)), (round(best[1], 1), round(best[2], 1)))))
            if key in seen:
                continue
            seen.add(key)
            extensions.append(LineString([(x, y), (best[1], best[2])]))
    return accepted + extensions


def _opening_entity_lines(msp, lines: list[LineString], origin: tuple[float, float], pad: float) -> list[LineString]:
    """벽이 2.4–18 m 끊긴 개구 안의 선을 경계로 쓴다.

    라벨이 있는 실이 벽만으로 닫히지 않으면, 그 개구를 지나는 선을 벽으로 본다.
    선들이 1.2 m 안쪽 틈으로 이어져 개구를 덮으면, 양끝 벽 사이를 한 줄로 닫는다.
    식생활관 오른쪽이 그 예다.
    """
    if not lines:
        return []
    ox, oy = origin
    walls: list[tuple[float, float, float, float, float, float, float]] = []
    for ln in lines:
        x0, y0 = float(ln.coords[0][0]), float(ln.coords[0][1])
        x1, y1 = float(ln.coords[-1][0]), float(ln.coords[-1][1])
        length = math.hypot(x1 - x0, y1 - y0)
        if length < 1500.0:
            continue
        if min(abs(x0 - ox), abs(x1 - ox)) > pad and min(abs(y0 - oy), abs(y1 - oy)) > pad:
            continue
        walls.append((x0, y0, x1, y1, length, (x1 - x0) / length, (y1 - y0) / length))
    openings: list[tuple[float, float, float, float, float, float]] = []
    seen_gap: set[tuple] = set()
    for i, left in enumerate(walls):
        lx0, ly0, lx1, ly1, llen, lux, luy = left
        for right in walls[i + 1 :]:
            rx0, ry0, rx1, ry1, _rlen, rux, ruy = right
            if abs(lux * rux + luy * ruy) < 0.98:
                continue
            nx, ny = -luy, lux
            off0 = abs((rx0 - lx0) * nx + (ry0 - ly0) * ny)
            off1 = abs((rx1 - lx0) * nx + (ry1 - ly0) * ny)
            if off0 > 80.0 or off1 > 80.0:
                continue
            along = (
                (lx0, ly0, 0.0),
                (lx1, ly1, llen),
                (rx0, ry0, (rx0 - lx0) * lux + (ry0 - ly0) * luy),
                (rx1, ry1, (rx1 - lx0) * lux + (ry1 - ly0) * luy),
            )
            lo1, hi1 = 0.0, llen
            lo2, hi2 = min(along[2][2], along[3][2]), max(along[2][2], along[3][2])
            if min(hi1, hi2) - max(lo1, lo2) > 200.0:
                continue
            gap = max(lo1, lo2) - min(hi1, hi2)
            if not (2400.0 <= gap <= 18000.0):
                continue
            inner = sorted(along, key=lambda item: item[2])
            near_x, near_y = inner[1][0], inner[1][1]
            far_x, far_y = inner[2][0], inner[2][1]
            dist = math.hypot(far_x - near_x, far_y - near_y) or gap
            mid_x = (near_x + far_x) * 0.5
            mid_y = (near_y + far_y) * 0.5
            if abs(mid_x - ox) > pad or abs(mid_y - oy) > pad:
                continue
            key = (
                round(near_x / 50.0),
                round(near_y / 50.0),
                round(far_x / 50.0),
                round(far_y / 50.0),
            )
            if key in seen_gap:
                continue
            seen_gap.add(key)
            openings.append((near_x, near_y, far_x, far_y, (far_x - near_x) / dist, (far_y - near_y) / dist))
    if not openings:
        return []
    raw: list[tuple[float, float, float, float]] = []
    for entity in msp:
        kind = entity.dxftype()
        parts: list[tuple[float, float, float, float]] = []
        if kind == "LINE":
            parts.append(
                (
                    float(entity.dxf.start.x),
                    float(entity.dxf.start.y),
                    float(entity.dxf.end.x),
                    float(entity.dxf.end.y),
                )
            )
        elif kind == "LWPOLYLINE":
            pts = [(float(a), float(b)) for a, b in entity.get_points("xy")]
            count = len(pts)
            for index in range(count - 1 + (1 if entity.closed else 0)):
                start, end = pts[index], pts[(index + 1) % count]
                parts.append((start[0], start[1], end[0], end[1]))
        for x0, y0, x1, y1 in parts:
            if math.hypot(x1 - x0, y1 - y0) < 1500.0:
                continue
            if abs((x0 + x1) * 0.5 - ox) > pad or abs((y0 + y1) * 0.5 - oy) > pad:
                continue
            raw.append((x0, y0, x1, y1))
    kept: list[LineString] = []
    for ax, ay, bx, by, ux, uy in openings:
        nx, ny = -uy, ux
        gap = math.hypot(bx - ax, by - ay)
        cover: list[tuple[float, float]] = []
        for x0, y0, x1, y1 in raw:
            elen = math.hypot(x1 - x0, y1 - y0)
            if abs(((x1 - x0) / elen) * ux + ((y1 - y0) / elen) * uy) < 0.98:
                continue
            off0 = (x0 - ax) * nx + (y0 - ay) * ny
            off1 = (x1 - ax) * nx + (y1 - ay) * ny
            if abs(off1 - off0) > 80.0 or abs(off0) > 500.0:
                continue
            t0 = (x0 - ax) * ux + (y0 - ay) * uy
            t1 = (x1 - ax) * ux + (y1 - ay) * uy
            lo, hi = min(t0, t1), max(t0, t1)
            if min(hi, gap) - max(lo, 0.0) < 2500.0:
                continue
            cover.append((lo, hi))
        if not cover:
            continue
        cover.sort()
        merged: list[list[float]] = [list(cover[0])]
        for lo, hi in cover[1:]:
            if lo - merged[-1][1] <= 1200.0:
                merged[-1][1] = max(merged[-1][1], hi)
            else:
                merged.append([lo, hi])
        span = None
        for lo, hi in merged:
            if lo <= 1200.0:
                lo = 0.0
            if hi >= gap - 1200.0:
                hi = gap
            if lo <= 0.0 and hi >= gap and (span is None or hi - lo > span[1] - span[0]):
                span = (lo, hi)
        if span is None:
            continue
        kept.append(LineString([(ax, ay), (bx, by)]))
    return kept


def _open_corner_lines(lines: list[LineString], origin: tuple[float, float]) -> list[LineString]:
    """가로·세로 벽 끝이 사선 외벽과 2–9 m 떨어지면 그 사이를 닫는다.

    라벨이 있는 실이 벽만으로 닫히지 않을 때만 쓴다.
    식생활관 왼쪽 위·아래처럼, 긴 벽이 사선 외벽에 닿지 않아 실이 새는 경우다.
    """
    if not lines:
        return []
    ox, oy = origin
    tree = STRtree(lines)
    kept: list[LineString] = []
    seen: set[tuple] = set()
    for ln in lines:
        if ln.length < 3500.0:
            continue
        x0, y0 = float(ln.coords[0][0]), float(ln.coords[0][1])
        x1, y1 = float(ln.coords[-1][0]), float(ln.coords[-1][1])
        if min(abs(x1 - x0), abs(y1 - y0)) > 400.0:
            continue
        length = math.hypot(x1 - x0, y1 - y0)
        ux, uy = (x1 - x0) / length, (y1 - y0) / length
        for x, y in ((x0, y0), (x1, y1)):
            if math.hypot(x - ox, y - oy) > 24000.0:
                continue
            pt = Point(x, y)
            best: tuple[float, float, float] | None = None
            for idx in tree.query(pt.buffer(9000.0)):
                other = lines[int(idx)]
                if other.length < 15000.0:
                    continue
                c0, c1 = other.coords[0], other.coords[-1]
                if abs(float(c0[0]) - float(c1[0])) < 1500.0 or abs(float(c0[1]) - float(c1[1])) < 1500.0:
                    continue
                dist = float(other.distance(pt))
                if not (2000.0 <= dist <= 9000.0):
                    continue
                vx = float(c1[0]) - float(c0[0])
                vy = float(c1[1]) - float(c0[1])
                vlen = math.hypot(vx, vy) or 1.0
                if abs(ux * vx / vlen + uy * vy / vlen) > 0.4:
                    continue
                nearest = other.interpolate(other.project(pt))
                if best is None or dist < best[0]:
                    best = (dist, float(nearest.x), float(nearest.y))
            if best is None:
                continue
            if math.hypot((x + best[1]) * 0.5 - ox, (y + best[2]) * 0.5 - oy) > 18000.0:
                continue
            key = (
                round(x / 200.0),
                round(y / 200.0),
                round(best[1] / 200.0),
                round(best[2] / 200.0),
            )
            if key in seen:
                continue
            seen.add(key)
            kept.append(LineString([(x, y), (best[1], best[2])]))
    return kept


def _gap_entity_lines(msp, lines: list[LineString], origin: tuple[float, float], pad: float) -> list[LineString]:
    """벽이 끊긴 구간 안의 선을 벽으로 본다.

    벽 끝이 다른 벽에 닿지 않고, 그 앞으로 2.4 m 안에 같은 방향의 선이 있으면
    그 선을 경계로 쓰고 벽 끝과 잇는다. 선이 없는 개구는 그대로 연다.
    일반교실 1-5 왼쪽 위처럼 문 위 인방이 BASE인 경우다.
    """
    if not lines:
        return []
    ox, oy = origin
    reach = min(pad, 20000.0)
    cands: list[tuple[float, float, float, float, float, float, float]] = []
    for entity in msp:
        kind = entity.dxftype()
        parts: list[tuple[float, float, float, float]] = []
        if kind == "LINE":
            parts.append(
                (
                    float(entity.dxf.start.x),
                    float(entity.dxf.start.y),
                    float(entity.dxf.end.x),
                    float(entity.dxf.end.y),
                )
            )
        elif kind == "LWPOLYLINE":
            pts = [(float(a), float(b)) for a, b in entity.get_points("xy")]
            count = len(pts)
            for index in range(count - 1 + (1 if entity.closed else 0)):
                start, end = pts[index], pts[(index + 1) % count]
                parts.append((start[0], start[1], end[0], end[1]))
        for x0, y0, x1, y1 in parts:
            length = math.hypot(x1 - x0, y1 - y0)
            if not (400.0 <= length <= 6000.0):
                continue
            if abs((x0 + x1) * 0.5 - ox) > reach or abs((y0 + y1) * 0.5 - oy) > reach:
                continue
            cands.append((x0, y0, x1, y1, length, (x1 - x0) / length, (y1 - y0) / length))
    if not cands:
        return []
    tree = STRtree(lines)
    kept: list[LineString] = []
    seen: set[tuple] = set()

    def add(ax: float, ay: float, bx: float, by: float) -> None:
        if math.hypot(bx - ax, by - ay) <= 0.5:
            return
        key = tuple(sorted(((round(ax, 1), round(ay, 1)), (round(bx, 1), round(by, 1)))))
        if key in seen:
            return
        seen.add(key)
        kept.append(LineString([(ax, ay), (bx, by)]))

    for ln in lines:
        if ln.length < 1500.0:
            continue
        x0, y0 = float(ln.coords[0][0]), float(ln.coords[0][1])
        x1, y1 = float(ln.coords[-1][0]), float(ln.coords[-1][1])
        length = math.hypot(x1 - x0, y1 - y0)
        ux, uy = (x1 - x0) / length, (y1 - y0) / length
        for x, y, dx, dy in ((x0, y0, -ux, -uy), (x1, y1, ux, uy)):
            if abs(x - ox) > reach or abs(y - oy) > reach:
                continue
            pt = Point(x, y)
            closed_end = False
            for hit in tree.query(pt.buffer(80.0)):
                other = lines[int(hit)]
                if other is ln or float(other.distance(pt)) > 80.0:
                    continue
                c0, c1 = other.coords[0], other.coords[-1]
                span = math.hypot(float(c1[0]) - float(c0[0]), float(c1[1]) - float(c0[1])) or 1.0
                oux = (float(c1[0]) - float(c0[0])) / span
                ouy = (float(c1[1]) - float(c0[1])) / span
                parallel = abs(dx * oux + dy * ouy) >= 0.98
                nearest = other.interpolate(other.project(pt))
                along = (float(nearest.x) - x) * dx + (float(nearest.y) - y) * dy
                if parallel and along > 20.0:
                    closed_end = True
                    break
                if not parallel and other.length > 400.0:
                    closed_end = True
                    break
            if closed_end:
                continue
            for ex0, ey0, ex1, ey1, _elen, eux, euy in cands:
                if abs(dx * eux + dy * euy) < 0.98:
                    continue
                off0 = abs((ex0 - x) * -dy + (ey0 - y) * dx)
                off1 = abs((ex1 - x) * -dy + (ey1 - y) * dx)
                if off0 > 80.0 or off1 > 80.0:
                    continue
                a0 = (ex0 - x) * dx + (ey0 - y) * dy
                a1 = (ex1 - x) * dx + (ey1 - y) * dy
                lo, hi = min(a0, a1), max(a0, a1)
                if hi < 200.0 or lo > DOOR_GAP_MM:
                    continue
                if min(hi, DOOR_GAP_MM) - max(lo, 0.0) < 400.0:
                    continue
                add(ex0, ey0, ex1, ey1)
                near_along = min(a0, a1)
                if 40.0 <= near_along <= DOOR_GAP_MM:
                    if a0 <= a1:
                        add(x, y, ex0, ey0)
                    else:
                        add(x, y, ex1, ey1)
    return kept


def _corner_entity_lines(msp, lines: list[LineString], origin: tuple[float, float], pad: float) -> list[LineString]:
    """벽 끝에 닿아 다른 벽까지 이어진 작은 엔터티.

    열린 실의 모서리에 놓인 사각형처럼, 끊긴 벽 끝과 다른 벽을 같이 닿으면
    그 변으로 실을 닫는다. 한 변이 2 m를 넘으면 넣지 않는다.
    """
    if not lines:
        return []
    ox, oy = origin
    tree = STRtree(lines)
    free: list[Point] = []
    for ln in lines:
        if ln.length < 400.0:
            continue
        x0, y0 = float(ln.coords[0][0]), float(ln.coords[0][1])
        x1, y1 = float(ln.coords[-1][0]), float(ln.coords[-1][1])
        length = math.hypot(x1 - x0, y1 - y0)
        ux, uy = (x1 - x0) / length, (y1 - y0) / length
        for x, y, dx, dy in ((x0, y0, -ux, -uy), (x1, y1, ux, uy)):
            if abs(x - ox) > pad or abs(y - oy) > pad:
                continue
            pt = Point(x, y)
            closed_end = False
            for hit in tree.query(pt.buffer(80.0)):
                other = lines[int(hit)]
                if other is ln or float(other.distance(pt)) > 80.0:
                    continue
                c0, c1 = other.coords[0], other.coords[-1]
                span = math.hypot(float(c1[0]) - float(c0[0]), float(c1[1]) - float(c0[1])) or 1.0
                oux = (float(c1[0]) - float(c0[0])) / span
                ouy = (float(c1[1]) - float(c0[1])) / span
                parallel = abs(dx * oux + dy * ouy) >= 0.98
                nearest = other.interpolate(other.project(pt))
                along = (float(nearest.x) - x) * dx + (float(nearest.y) - y) * dy
                if parallel and along > 20.0:
                    closed_end = True
                    break
                if not parallel and other.length > 400.0:
                    closed_end = True
                    break
            if not closed_end:
                free.append(pt)
    if not free:
        return []
    kept: list[LineString] = []
    seen: set[tuple] = set()
    for entity in msp:
        if entity.dxftype() != "LWPOLYLINE" or not entity.closed:
            continue
        if getattr(entity.dxf, "layer", None) != BASE_LAYER:
            continue
        pts = [(float(a), float(b)) for a, b in entity.get_points("xy")]
        if len(pts) >= 2 and pts[0] == pts[-1]:
            pts = pts[:-1]
        if len(pts) < 4:
            continue
        xs = [point[0] for point in pts]
        ys = [point[1] for point in pts]
        width, height = max(xs) - min(xs), max(ys) - min(ys)
        if min(width, height) < 200.0 or max(width, height) > 2000.0:
            continue
        cx, cy = (min(xs) + max(xs)) * 0.5, (min(ys) + max(ys)) * 0.5
        if abs(cx - ox) > pad or abs(cy - oy) > pad:
            continue
        ring = LineString(pts + [pts[0]])
        touched = [pt for pt in free if float(ring.distance(pt)) <= 120.0]
        if not touched:
            continue
        spans_gap = any(
            float(a.distance(b)) >= 200.0
            for index, a in enumerate(touched)
            for b in touched[index + 1 :]
        )
        other_wall = False
        if not spans_gap:
            for ln in lines:
                if float(ring.distance(ln)) > 120.0:
                    continue
                if any(float(ln.distance(pt)) <= 120.0 for pt in touched):
                    continue
                other_wall = True
                break
        if not spans_gap and not other_wall:
            continue
        count = len(pts)
        for index in range(count):
            start, end = pts[index], pts[(index + 1) % count]
            if math.hypot(end[0] - start[0], end[1] - start[1]) <= 0.5:
                continue
            key = tuple(sorted(((round(start[0], 1), round(start[1], 1)), (round(end[0], 1), round(end[1], 1)))))
            if key in seen:
                continue
            seen.add(key)
            kept.append(LineString([start, end]))
    return kept


def _entity_face(lines, added, origin, reach):
    """추가 선으로 라벨이 닫히면 그 선 묶음과 면을 돌려준다. 창에 닿으면 버린다."""
    if not added or len(lines) + len(added) > MAX_FACE_LINES:
        return None, None
    closed = _cap_wall_ends(list(lines) + list(added), WALL_CAP_MM)
    found = _face_at(closed, origin[0], origin[1])
    if found is None or _touches_window(found, origin, reach):
        return None, None
    return closed, found


def _larger_inside(candidate, current) -> bool:
    """같은 테두리 안에서만 더 넓은 면을 고른다. 옆 실로 번지면 버린다."""
    if current is None:
        return True
    if candidate.area <= current.area * 1.01:
        return False
    minx, miny, maxx, maxy = current.bounds
    pad = 400.0
    cminx, cminy, cmaxx, cmaxy = candidate.bounds
    return (
        cminx >= minx - pad
        and cminy >= miny - pad
        and cmaxx <= maxx + pad
        and cmaxy <= maxy + pad
    )


def _best_open_face(lines, added, origin, reach):
    """열린 실만. 짧은 엔터티가 안을 가르면 긴 엔터티로 닫힌 더 큰 면을 쓴다.

    벽만으로 이미 닫힌 실에는 호출하지 않는다.
    """
    long_added = [ln for ln in added if ln.length >= 1500.0]
    full_lines, full = _entity_face(lines, added, origin, reach)
    long_lines, long_face = _entity_face(lines, long_added, origin, reach)
    # 이미 한 실 크기인 면만 키운다. 여러 실이 한 면으로 새면 짧은 선을 빼지 않는다.
    if (
        full is not None
        and long_face is not None
        and max(full.bounds[2] - full.bounds[0], full.bounds[3] - full.bounds[1]) <= 12000.0
        and _larger_inside(long_face, full)
    ):
        return long_lines, long_face
    if full is not None:
        return full_lines, full
    return None, None


def _seed_edt(wall):
    """벽 칸까지 화소 거리. wall 이 True 인 칸은 0이다."""
    import numpy as np

    height, width = wall.shape
    huge = 1.0e20
    feature = np.where(wall, 0.0, huge)

    def along(values):
        count = values.shape[0]
        index = np.zeros(count, dtype=np.int32)
        bound = np.full(count + 1, np.inf)
        bound[0] = -np.inf
        k = 0
        for q in range(1, count):
            vk = int(index[k])
            span = 2.0 * q - 2.0 * vk
            if span == 0.0:
                k += 1
                index[k] = q
                bound[k] = -np.inf
                bound[k + 1] = np.inf
                continue
            cross = ((values[q] + q * q) - (values[vk] + vk * vk)) / span
            while cross <= bound[k]:
                k -= 1
                vk = int(index[k])
                span = 2.0 * q - 2.0 * vk
                if span == 0.0:
                    cross = -np.inf
                    break
                cross = ((values[q] + q * q) - (values[vk] + vk * vk)) / span
            k += 1
            index[k] = q
            bound[k] = cross
            bound[k + 1] = np.inf
        k = 0
        out = np.empty(count)
        for q in range(count):
            while bound[k + 1] < q:
                k += 1
            delta = q - int(index[k])
            out[q] = delta * delta + values[int(index[k])]
        return out

    temporary = np.empty_like(feature)
    for col in range(width):
        temporary[:, col] = along(feature[:, col])
    out = np.empty_like(feature)
    for row in range(height):
        out[row, :] = along(temporary[row, :])
    return np.sqrt(out)


def _seed_ring(mask, ox: float, oy: float, reach: float, cell: float, origin: tuple[float, float]):
    """칸 마스크의 바깥 고리를 도면 mm 다각형으로 만든다."""
    import numpy as np

    height, width = mask.shape
    nxt: dict[tuple[float, float], list[tuple[float, float]]] = defaultdict(list)
    rows, cols = np.nonzero(mask)
    for row, col in zip(rows.tolist(), cols.tolist()):
        x0 = ox - reach + col * cell
        y0 = oy - reach + row * cell
        x1 = x0 + cell
        y1 = y0 + cell
        if row == 0 or not mask[row - 1, col]:
            nxt[(x0, y0)].append((x1, y0))
        if row + 1 == height or not mask[row + 1, col]:
            nxt[(x1, y1)].append((x0, y1))
        if col == 0 or not mask[row, col - 1]:
            nxt[(x0, y1)].append((x0, y0))
        if col + 1 == width or not mask[row, col + 1]:
            nxt[(x1, y0)].append((x1, y1))
    unused = {(start, end) for start, ends in nxt.items() for end in ends}
    rings = []
    while unused:
        start, end = unused.pop()
        ring = [start, end]
        cursor = end
        while cursor != start:
            found = None
            for nxt_pt in nxt.get(cursor, ()):
                edge = (cursor, nxt_pt)
                if edge in unused:
                    found = nxt_pt
                    unused.discard(edge)
                    break
            if found is None:
                break
            ring.append(found)
            cursor = found
        if len(ring) >= 4 and ring[0] == ring[-1]:
            rings.append(ring[:-1])
    seed = Point(origin)
    chosen = None
    for ring in rings:
        face = Polygon(ring)
        if not face.is_valid:
            face = face.buffer(0)
        if face.is_empty or face.geom_type != "Polygon" or not face.covers(seed):
            continue
        if chosen is None or face.area > chosen.area:
            chosen = face
    return chosen



def cluster_open_origins(
    origins: list[tuple[float, float]],
    link_mm: float = CLUSTER_LINK_MM,
) -> list[list[tuple[float, float]]]:
    """오픈 라벨 좌표를 link_mm 안의 묶음으로 나눈다. 2개 미만 묶음은 뺀다."""
    if len(origins) < CLUSTER_MIN_SEEDS:
        return []
    parent = list(range(len(origins)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i, (x0, y0) in enumerate(origins):
        for j in range(i + 1, len(origins)):
            x1, y1 = origins[j]
            if math.hypot(x1 - x0, y1 - y0) <= link_mm:
                ri, rj = find(i), find(j)
                if ri != rj:
                    parent[rj] = ri
    groups: dict[int, list[tuple[float, float]]] = {}
    for i, origin in enumerate(origins):
        groups.setdefault(find(i), []).append(origin)
    return [group for group in groups.values() if len(group) >= CLUSTER_MIN_SEEDS]


def cluster_hull_wall_density(segs, origins: list[tuple[float, float]]) -> float:
    """씨앗 주변 상자의 벽 길이(m) / 면적(m²). 제목란처럼 벽이 드물면 낮다."""
    if len(origins) < CLUSTER_MIN_SEEDS:
        return 0.0
    xs = [x for x, _y in origins]
    ys = [y for _x, y in origins]
    span = math.hypot(max(xs) - min(xs), max(ys) - min(ys))
    pad = max(CLUSTER_HULL_BUFFER_MM, 0.5 * span + 8000.0)
    zone = box(min(xs) - pad, min(ys) - pad, max(xs) + pad, max(ys) + pad)
    if zone.is_empty or zone.area <= 0.0:
        return 0.0
    length = 0.0
    for x0, y0, x1, y1, *_rest in segs:
        line = LineString([(x0, y0), (x1, y1)])
        if line.length < 40.0 or not zone.intersects(line):
            continue
        length += line.intersection(zone).length
    return (length / 1000.0) / (zone.area / 1_000_000.0)


def _cluster_wall_edge_ratio(region, wall) -> float:
    """영역 테두리 중 벽에 닿는 변의 비율."""
    import numpy as np

    if not region.any():
        return 0.0
    inner = (
        region
        & np.roll(region, 1, axis=0)
        & np.roll(region, -1, axis=0)
        & np.roll(region, 1, axis=1)
        & np.roll(region, -1, axis=1)
    )
    rows, cols = np.nonzero(region & ~inner)
    covered = 0
    total = 0
    height, width = region.shape
    for row, col in zip(rows.tolist(), cols.tolist()):
        for n_row, n_col in (
            (row - 1, col),
            (row + 1, col),
            (row, col - 1),
            (row, col + 1),
        ):
            if not (0 <= n_row < height and 0 <= n_col < width) or not region[n_row, n_col]:
                total += 1
                if 0 <= n_row < height and 0 <= n_col < width and wall[n_row, n_col]:
                    covered += 1
    return covered / total if total else 0.0


def _seed_cluster_face(
    lines: list[LineString],
    origins: list[tuple[float, float]],
    blocked_faces=None,
):
    """방안 3 묶음. 겹치는 오픈 라벨을 씨앗으로 같이 넓혀 실을 잡는다.

    벽선은 150 mm 두께로 막아 격자 틈으로 벽을 넘지 않는다. 이미 잡힌 실 면도
    막아서 그쪽으로 새지 않는다. 씨앗 볼록껍질 밖 8 m까지만 퍼져 개구로 멀리
    삐져나가지 않는다. 창 밖으로 새지 않는 범위에서 벽 테두리 비율이 가장 높은
    거리로 확정한다.
    """
    try:
        import numpy as np
    except ImportError:
        return None
    if len(origins) < CLUSTER_MIN_SEEDS:
        return None
    ox = sum(x for x, _y in origins) / len(origins)
    oy = sum(y for _x, y in origins) / len(origins)
    span = max(max(abs(x - ox), abs(y - oy)) for x, y in origins) + 15000.0
    reach = max(CLUSTER_REACH_MM, span)
    cell = CLUSTER_CELL_MM
    count = int(reach * 2.0 / cell)
    window = box(ox - reach, oy - reach, ox + reach, oy + reach)
    usable = [ln for ln in lines if ln.length > 40.0 and window.intersects(ln)]
    if len(usable) < 3:
        return None
    wall = np.zeros((count, count), dtype=bool)

    def paint(x0: float, y0: float, x1: float, y1: float, mask) -> None:
        col = int((x0 - (ox - reach)) / cell)
        row = int((y0 - (oy - reach)) / cell)
        col_b = int((x1 - (ox - reach)) / cell)
        row_b = int((y1 - (oy - reach)) / cell)
        step_c = abs(col_b - col)
        step_r = abs(row_b - row)
        sign_c = 1 if col < col_b else -1
        sign_r = 1 if row < row_b else -1
        err = step_c - step_r
        while True:
            if 0 <= row < count and 0 <= col < count:
                mask[row, col] = True
            if col == col_b and row == row_b:
                break
            doubled = 2 * err
            if doubled > -step_r:
                err -= step_r
                col += sign_c
            if doubled < step_c:
                err += step_c
                row += sign_r

    def paint_poly(poly, mask) -> None:
        if poly is None or poly.is_empty:
            return
        parts = [poly] if poly.geom_type == "Polygon" else list(getattr(poly, "geoms", ()))
        for part in parts:
            if part.is_empty or part.geom_type != "Polygon":
                continue
            minx, miny, maxx, maxy = part.bounds
            col0 = max(0, int((minx - (ox - reach)) / cell) - 1)
            row0 = max(0, int((miny - (oy - reach)) / cell) - 1)
            col1 = min(count - 1, int((maxx - (ox - reach)) / cell) + 1)
            row1 = min(count - 1, int((maxy - (oy - reach)) / cell) + 1)
            for row in range(row0, row1 + 1):
                y = oy - reach + (row + 0.5) * cell
                for col in range(col0, col1 + 1):
                    x = ox - reach + (col + 0.5) * cell
                    if part.covers(Point(x, y)):
                        mask[row, col] = True

    for line in usable:
        coords = list(line.coords)
        for start, end in zip(coords, coords[1:]):
            paint(float(start[0]), float(start[1]), float(end[0]), float(end[1]), wall)
    if not wall.any():
        return None
    # 벽 중심선만 있으면 격자 틈으로 넘어간다. 150 mm까지 막아 벽을 넘지 않게 한다.
    block_px = CLUSTER_WALL_BLOCK_MM / cell
    barrier = _seed_edt(wall) <= block_px
    if blocked_faces:
        for face in blocked_faces:
            paint_poly(face, barrier)
    free = ~barrier
    seeds: list[tuple[int, int]] = []
    for sx, sy in origins:
        col = int((sx - (ox - reach)) / cell)
        row = int((sy - (oy - reach)) / cell)
        if not (0 <= row < count and 0 <= col < count):
            continue
        if not free[row, col]:
            found = None
            best = None
            for d_row in range(-12, 13):
                for d_col in range(-12, 13):
                    n_row = row + d_row
                    n_col = col + d_col
                    if 0 <= n_row < count and 0 <= n_col < count and free[n_row, n_col]:
                        dist2 = d_row * d_row + d_col * d_col
                        if best is None or dist2 < best:
                            best = dist2
                            found = (n_row, n_col)
            if found is None:
                continue
            seeds.append(found)
        else:
            seeds.append((row, col))
    if len(seeds) < CLUSTER_MIN_SEEDS:
        return None
    # 씨앗 볼록껍질 밖 8 m까지만 허용. 개구로 멀리 둥글게 새는 삐죽 면을 막는다.
    hull = MultiPoint([Point(x, y) for x, y in origins]).convex_hull.buffer(CLUSTER_HULL_PAD_MM)
    allow = np.zeros((count, count), dtype=bool)
    minx, miny, maxx, maxy = hull.bounds
    col0 = max(0, int((minx - (ox - reach)) / cell) - 1)
    row0 = max(0, int((miny - (oy - reach)) / cell) - 1)
    col1 = min(count - 1, int((maxx - (ox - reach)) / cell) + 1)
    row1 = min(count - 1, int((maxy - (oy - reach)) / cell) + 1)
    for row in range(row0, row1 + 1):
        y = oy - reach + (row + 0.5) * cell
        for col in range(col0, col1 + 1):
            x = ox - reach + (col + 0.5) * cell
            if hull.covers(Point(x, y)):
                allow[row, col] = True
    free = free & allow
    if any(not free[row, col] for row, col in seeds):
        return None
    dist = np.full((count, count), -1, dtype=np.int32)
    queue = deque(seeds)
    for row, col in seeds:
        dist[row, col] = 0
    while queue:
        row, col = queue.popleft()
        step = dist[row, col] + 1
        for n_row, n_col in (
            (row - 1, col),
            (row + 1, col),
            (row, col - 1),
            (row, col + 1),
        ):
            if 0 <= n_row < count and 0 <= n_col < count and free[n_row, n_col] and dist[n_row, n_col] < 0:
                dist[n_row, n_col] = step
                queue.append((n_row, n_col))
    if any(dist[row, col] < 0 for row, col in seeds):
        return None
    best = None
    best_region = None
    max_cells = int(CLUSTER_GROW_MAX_MM / cell)
    min_cells = int(CLUSTER_GROW_MIN_MM / cell)
    step_cells = max(1, int(CLUSTER_GROW_STEP_MM / cell))
    for radius_cells in range(min_cells, max_cells + 1, step_cells):
        region = (dist >= 0) & (dist <= radius_cells)
        if (
            region[0].any()
            or region[-1].any()
            or region[:, 0].any()
            or region[:, -1].any()
        ):
            break
        # 면은 barrier(벽 두께) 안쪽에서 멈추므로 테두리도 barrier에 닿는지 본다.
        edge = _cluster_wall_edge_ratio(region, barrier)
        if edge < CLUSTER_WALL_EDGE_MIN:
            continue
        area = float(region.sum()) * cell * cell
        score = (edge, -area, radius_cells)
        if best is None or score[:2] > best[:2]:
            best = score
            best_region = region.copy()
    if best_region is None:
        return None
    chosen = None
    for sx, sy in origins:
        face = _seed_ring(best_region, ox, oy, reach, cell, (sx, sy))
        if face is None:
            continue
        if chosen is None or face.area > chosen.area:
            chosen = face
    if chosen is None or chosen.area < 200_000.0:
        return None
    if blocked_faces:
        obstacle = unary_union([face for face in blocked_faces if face is not None and not face.is_empty])
        if not obstacle.is_empty:
            clipped = chosen.difference(obstacle.buffer(80.0))
            if clipped.is_empty:
                return None
            if clipped.geom_type == "MultiPolygon":
                holding = [
                    geom
                    for geom in clipped.geoms
                    if any(geom.covers(Point(sx, sy)) or geom.distance(Point(sx, sy)) <= 500.0 for sx, sy in origins)
                ]
                if not holding:
                    return None
                clipped = max(holding, key=lambda geom: geom.area)
            if clipped.geom_type != "Polygon":
                return None
            chosen = clipped
    inside = sum(
        1
        for sx, sy in origins
        if chosen.covers(Point(sx, sy)) or chosen.distance(Point(sx, sy)) <= 500.0
    )
    if inside < CLUSTER_MIN_SEEDS:
        return None
    return chosen


def _seed_face(lines: list[LineString], origin: tuple[float, float]):
    """방안 3. 라벨 반경 25 m를 100 mm 격자로 두고 벽까지 거리를 잰다.

    벽 두께는 80 mm부터 2.4 m까지 키운다. 씨앗 공간이 창 테두리에 닿지 않는
    가장 얇은 두께에서 실로 둔다. 벽이 씨앗을 덮거나 2.4 m에서도 새면 실이 아니다.
    """
    try:
        import numpy as np
    except ImportError:
        return None
    ox, oy = origin
    reach = SEED_REACH_MM
    cell = 100.0
    count = int(reach * 2.0 / cell)
    window = box(ox - reach, oy - reach, ox + reach, oy + reach)
    usable = [ln for ln in lines if ln.length > 40.0 and window.intersects(ln)]
    if len(usable) < 3:
        return None
    wall = np.zeros((count, count), dtype=bool)

    def paint(x0: float, y0: float, x1: float, y1: float) -> None:
        col = int((x0 - (ox - reach)) / cell)
        row = int((y0 - (oy - reach)) / cell)
        col_b = int((x1 - (ox - reach)) / cell)
        row_b = int((y1 - (oy - reach)) / cell)
        step_c = abs(col_b - col)
        step_r = abs(row_b - row)
        sign_c = 1 if col < col_b else -1
        sign_r = 1 if row < row_b else -1
        err = step_c - step_r
        while True:
            if 0 <= row < count and 0 <= col < count:
                wall[row, col] = True
            if col == col_b and row == row_b:
                break
            doubled = 2 * err
            if doubled > -step_r:
                err -= step_r
                col += sign_c
            if doubled < step_c:
                err += step_c
                row += sign_r

    for line in usable:
        coords = list(line.coords)
        for start, end in zip(coords, coords[1:]):
            paint(float(start[0]), float(start[1]), float(end[0]), float(end[1]))
    if not wall.any():
        return None
    dist = _seed_edt(wall)
    seed_row = int(reach / cell)
    seed_col = int(reach / cell)
    if not (0 <= seed_row < count and 0 <= seed_col < count):
        return None
    if dist[seed_row, seed_col] <= 0.0:
        found = None
        for d_row in range(-4, 5):
            for d_col in range(-4, 5):
                row = seed_row + d_row
                col = seed_col + d_col
                if 0 <= row < count and 0 <= col < count and dist[row, col] > 0.0:
                    if found is None or dist[row, col] > dist[found]:
                        found = (row, col)
        if found is None:
            return None
        seed_row, seed_col = found
    for radius in SEED_BUFFER_STEPS_MM:
        radius_px = radius / cell
        if dist[seed_row, seed_col] <= radius_px:
            return None
        free = dist > radius_px
        seen = np.zeros((count, count), dtype=bool)
        stack = [(seed_row, seed_col)]
        seen[seed_row, seed_col] = True
        leaks = False
        while stack:
            row, col = stack.pop()
            if row == 0 or col == 0 or row == count - 1 or col == count - 1:
                leaks = True
                break
            for n_row, n_col in ((row - 1, col), (row + 1, col), (row, col - 1), (row, col + 1)):
                if free[n_row, n_col] and not seen[n_row, n_col]:
                    seen[n_row, n_col] = True
                    stack.append((n_row, n_col))
        if leaks:
            continue
        room_dist = _seed_edt(~seen)
        restored = room_dist <= radius_px
        if restored[0].any() or restored[-1].any() or restored[:, 0].any() or restored[:, -1].any():
            continue
        face = _seed_ring(restored, ox, oy, reach, cell, origin)
        if face is None or face.area < 200_000.0:
            continue
        return face
    return None


def _visibility_face(lines: list[LineString], origin: tuple[float, float], reach: float = 22000.0):
    """방안 2. 벽이 닫히지 않은 라벨에서 본 면.

    라벨에서 2°마다 처음 닿는 벽을 모은다. 끝점이 300 mm 안에서 이어진 선은 한 벽이다.
    이웃보다 1.8 m 넘게 먼 점은 틈 너머라 뺀다. 24°보다 짧고 주위 벽보다 1.2 m 이상
    가까운 선은 실 안 장애물이라 그 뒤의 벽을 쓴다. 빠진 방향은 양옆 점을 이어 막는다.
    둘레의 90% 이상이 벽일 때만 이 면을 쓴다. 그 판단은 호출하는 쪽에서 한다.
    """
    step = 2
    count = 360 // step
    usable = [ln for ln in lines if ln.length > 0.5]
    if len(usable) < 2:
        return None
    tree = STRtree(usable)
    parent = list(range(len(usable)))

    def find(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    def union(left: int, right: int) -> None:
        root_left, root_right = find(left), find(right)
        if root_left != root_right:
            parent[root_right] = root_left

    for index, line in enumerate(usable):
        for xy in (line.coords[0], line.coords[-1]):
            point = Point(float(xy[0]), float(xy[1]))
            for hit in tree.query(point.buffer(300.0)):
                other = int(hit)
                if other != index and float(usable[other].distance(point)) <= 300.0:
                    union(index, other)
    component = [find(index) for index in range(len(usable))]
    ox, oy = origin
    rays: list[list[tuple[float, int, float, float]]] = []
    for index in range(count):
        angle = math.radians(index * step)
        ray = LineString([(ox, oy), (ox + math.cos(angle) * reach, oy + math.sin(angle) * reach)])
        raw: list[tuple[float, int, float, float]] = []
        for hit in tree.query(ray):
            other = int(hit)
            inter = ray.intersection(usable[other])
            if inter.is_empty:
                continue
            points: list[Point] = []
            kind = inter.geom_type
            if kind == "Point":
                points = [inter]
            elif kind == "MultiPoint":
                points = list(inter.geoms)
            elif kind == "LineString":
                points = [Point(item) for item in (inter.coords[0], inter.coords[-1])]
            elif kind == "GeometryCollection":
                for geom in inter.geoms:
                    if geom.geom_type == "Point":
                        points.append(geom)
            for point in points:
                distance = math.hypot(point.x - ox, point.y - oy)
                if 80.0 < distance <= reach:
                    raw.append((distance, component[other], float(point.x), float(point.y)))
        raw.sort()
        kept: list[tuple[float, int, float, float]] = []
        for item in raw:
            if kept and item[0] - kept[-1][0] <= 280.0 and item[1] == kept[-1][1]:
                continue
            kept.append(item)
            if len(kept) >= 4:
                break
        rays.append(kept)
    chosen: list[tuple[float, int, float, float] | None] = [hits[0] if hits else None for hits in rays]
    for _repeat in range(3):
        for index, item in enumerate(chosen):
            if item is None:
                continue
            window = [
                chosen[(index + offset) % count][0]
                for offset in range(-6, 7)
                if offset and chosen[(index + offset) % count] is not None
            ]
            if len(window) < 4:
                continue
            window.sort()
            if item[0] > window[len(window) // 2] + 1800.0:
                chosen[index] = None
    span: dict[int, int] = defaultdict(int)
    for item in chosen:
        if item is not None:
            span[item[1]] += 1
    for index, hits in enumerate(rays):
        item = chosen[index]
        if not hits or item is None or span[item[1]] >= 12:
            continue
        window = [
            chosen[(index + offset) % count][0]
            for offset in range(-10, 11)
            if offset
            and chosen[(index + offset) % count] is not None
            and span[chosen[(index + offset) % count][1]] >= 12
        ]
        if len(window) < 3:
            continue
        window.sort()
        median = window[len(window) // 2]
        if item[0] >= median - 1200.0:
            continue
        replacement = None
        for hit in hits:
            if hit[0] <= item[0] + 400.0:
                continue
            if replacement is None or abs(hit[0] - median) < abs(replacement[0] - median):
                replacement = hit
        chosen[index] = replacement
    points = [(item[2], item[3]) for item in chosen if item is not None]
    if len(points) < 3:
        return None
    face = Polygon(points)
    if not face.is_valid:
        face = face.buffer(0)
    if face.is_empty:
        return None
    if face.geom_type == "MultiPolygon":
        holding = [geom for geom in face.geoms if geom.contains(Point(ox, oy))]
        face = holding[0] if holding else max(face.geoms, key=lambda geom: geom.area)
    if face.geom_type != "Polygon" or not face.contains(Point(ox, oy)):
        return None
    return face


def _closed_lines(msp, segs, origin, boxes, open_doors):
    """라벨이 들어 있는 닫힌 면과, 그 면을 만든 벽선.

    벽만으로 닫히면 그 면을 그대로 쓴다.
    열릴 때만 엔터티로 닫고, 그 안을 짧은 선이 가르면 더 큰 면을 고른다.
    """
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
            return lines, found, x_axes, y_axes, swings
        pad += 8000
    opening = _opening_entity_lines(msp, lines, origin, pad + 2000)
    corners = _open_corner_lines(lines, origin)
    if opening or corners:
        # 라벨 근처만 400 mm로 잇는다. 층 전체를 같은 너비로 막으면 선이 너무 많아진다.
        near = box(lx - 20000.0, ly - 20000.0, lx + 15000.0, ly + 18000.0)
        pool = lines + opening + corners
        local = [ln for ln in pool if near.intersects(ln)]
        far = [ln for ln in lines if not near.intersects(ln)]
        closed = _cap_wall_ends(local, max(WALL_CAP_MM, 400.0)) + far
        if len(closed) <= MAX_FACE_LINES:
            found = _face_at(closed, lx, ly)
            if found is not None and not _touches_window(found, origin, pad + 2000):
                return closed, found, x_axes, y_axes, swings
    reach = pad + 2000
    extra = _wallish_lines(msp, lines, origin, reach)
    closed, found = _best_open_face(lines, extra, origin, reach)
    if found is None:
        extra = list(extra)
        extra.extend(_opening_entity_lines(msp, lines, origin, reach))
        extra.extend(_continuing_wall_lines(msp, lines, origin, reach))
        closed, found = _best_open_face(lines, extra, origin, reach)
    if found is None:
        # 앞에서 넣은 보조선은 실을 닫지 못했다. 끊긴 구간 안의 선만 더한다.
        gap = _gap_entity_lines(msp, lines, origin, reach)
        closed, found = _best_open_face(lines, gap, origin, reach)
    if found is None:
        # 벽 끝에 닿아 다른 벽으로 이어진 작은 엔터티로 모서리를 닫는다.
        # 라벨 근처만 본다. 멀리 있는 엔터티까지 넣으면 여러 실이 한 면이 된다.
        corner_entity = _corner_entity_lines(msp, lines, origin, min(reach, 8000.0))
        closed, found = _best_open_face(lines, corner_entity, origin, reach)
        if found is not None and max(found.bounds[2] - found.bounds[0], found.bounds[3] - found.bounds[1]) > 8000.0:
            found = None
    if found is not None:
        return closed, found, x_axes, y_axes, swings
    return lines, None, x_axes, y_axes, swings


def _room_lines(msp, segs, origin, boxes, open_doors):
    """라벨의 면. 앞에서 놓친 라벨만 다음 방안으로 넘긴다.

    방안 1은 벽이 닫힌 면이다. 방안 2는 이어진 벽으로 만든 면 중 둘레의 90% 이상이
    벽인 경우다.     방안 3은 한 라벨이면 25 m 창·벽 두께 2.4 m 씨앗이고, 여러 오픈 라벨 묶음은
    detect 쪽에서 같이 넓혀 잡는다.
    """
    lines, found, x_axes, y_axes, swings = _closed_lines(msp, segs, origin, boxes, open_doors)
    if found is not None:
        return found, x_axes, y_axes, swings
    if not lines:
        return None, [], [], []
    seen = _visibility_face(lines, origin)
    if (
        seen is not None
        and wall_perimeter_ratio(segs, list(seen.exterior.coords)) >= WALL_PERIMETER_MIN
    ):
        return seen, x_axes, y_axes, swings
    seeded = _seed_face(lines, origin)
    if seeded is not None:
        return seeded, x_axes, y_axes, swings
    return None, [], [], []


def split_face_at_wall_end(msp, segs, boxes, origin, others: list[tuple[float, float]]):
    """한 면 안에서 끊긴 벽 끝을 바깥 경계까지 이어 라벨을 다른 면으로 나눈다.

    벽 끝에서 200 mm 안의 꺾임은 건너뛴다. 1600 mm 안에서 가장 짧은 연장만 쓴다.
    나누지 못하면 None.
    """
    lines, face, x_axes, y_axes, swings = _closed_lines(msp, segs, origin, boxes, [])
    if not lines or face is None:
        return None
    ring = face.exterior
    best = None
    for ln in lines:
        coords = list(ln.coords)
        x0, y0 = float(coords[0][0]), float(coords[0][1])
        x1, y1 = float(coords[-1][0]), float(coords[-1][1])
        length = math.hypot(x1 - x0, y1 - y0)
        if length < 800.0:
            continue
        ux, uy = (x1 - x0) / length, (y1 - y0) / length
        for x, y, sign in ((x0, y0, -1.0), (x1, y1, 1.0)):
            if not face.buffer(40.0).covers(Point(x, y)):
                continue
            ray = LineString([(x, y), (x + ux * sign * 1600.0, y + uy * sign * 1600.0)])
            inter = ray.intersection(ring)
            pts = []
            if inter.is_empty:
                continue
            if inter.geom_type == "Point":
                pts = [inter]
            elif inter.geom_type == "MultiPoint":
                pts = list(inter.geoms)
            else:
                continue
            hit = None
            for ip in pts:
                dist = math.hypot(ip.x - x, ip.y - y)
                if dist <= 200.0:
                    continue
                if hit is None or dist < hit[0]:
                    hit = (dist, float(ip.x), float(ip.y))
            if hit is None:
                continue
            dist, x2, y2 = hit
            chord = LineString([(x, y), (x2, y2)])
            if not face.buffer(80.0).covers(chord.interpolate(0.5, normalized=True)):
                continue
            extra = lines + [chord]
            face_a = _face_at(extra, origin[0], origin[1])
            if face_a is None:
                continue
            faces_b = []
            ok = True
            for ox, oy in others:
                face_b = _face_at(extra, ox, oy)
                if face_b is None:
                    ok = False
                    break
                shared = face_a.intersection(face_b).area
                if shared > min(face_a.area, face_b.area) * 0.5:
                    ok = False
                    break
                faces_b.append(face_b)
            if not ok or not faces_b:
                continue
            if min(face_a.area, *(item.area for item in faces_b)) < 500_000.0:
                continue
            if face_a.area + sum(item.area for item in faces_b) > face.area * 1.2:
                continue
            if best is None or dist < best[0]:
                best = (dist, face_a, faces_b, x_axes, y_axes, swings)
    if best is None:
        return None
    return best[1], best[2], best[3], best[4], best[5]


def _straight_parts(entity) -> list[tuple[float, float, float, float]]:
    kind = entity.dxftype()
    if kind == "LINE":
        return [
            (
                float(entity.dxf.start.x),
                float(entity.dxf.start.y),
                float(entity.dxf.end.x),
                float(entity.dxf.end.y),
            )
        ]
    if kind != "LWPOLYLINE":
        return []
    pts = [(float(a), float(b)) for a, b in entity.get_points("xy")]
    n = len(pts)
    parts = []
    for i in range(n - 1 + (1 if entity.closed else 0)):
        a, b = pts[i], pts[(i + 1) % n]
        parts.append((a[0], a[1], b[0], b[1]))
    return parts


def _hit_ring(x: float, y: float, ux: float, uy: float, sign: float, ring, limit: float):
    """점에서 방향으로 limit 안에서 면 테두리와 만나는 가장 가까운 점."""
    if ring.distance(Point(x, y)) <= 15.0:
        return x, y, 0.0
    ray = LineString([(x, y), (x + ux * sign * limit, y + uy * sign * limit)])
    inter = ray.intersection(ring)
    if inter.is_empty:
        return None
    if inter.geom_type == "Point":
        pts = [inter]
    elif inter.geom_type == "MultiPoint":
        pts = list(inter.geoms)
    else:
        return None
    hit = None
    for ip in pts:
        dist = math.hypot(ip.x - x, ip.y - y)
        if dist <= 20.0:
            continue
        if hit is None or dist < hit[0]:
            hit = (dist, float(ip.x), float(ip.y))
    if hit is None:
        return None
    return hit[1], hit[2], hit[0]


def split_face_on_divider(msp, segs, boxes, origin, others: list[tuple[float, float]], current_pts=None):
    """한 면의 라벨 사이를 지나는 벽이 아닌 선을 면 경계까지 이어 면을 나눈다.

    선 끝과 경계의 틈은 800 mm 까지만 잇는다. 이중선이면 실 라벨에 가까운 쪽을 쓴다.
    나누지 못하면 None.
    """
    lines, face, x_axes, y_axes, swings = _closed_lines(msp, segs, origin, boxes, [])
    if not lines or face is None or not others:
        return None
    ring = face.exterior
    current = Polygon(current_pts) if current_pts and len(current_pts) >= 3 else None
    spans = [LineString([origin, (ox, oy)]) for ox, oy in others]
    minx, miny, maxx, maxy = face.bounds
    candidates: list[tuple[float, float, LineString]] = []
    seen: set[tuple] = set()
    for entity in msp:
        try:
            if entity.dxf.layer != BASE_LAYER:
                continue
        except Exception:
            continue
        for x0, y0, x1, y1 in _straight_parts(entity):
            length = math.hypot(x1 - x0, y1 - y0)
            if length < 1500.0:
                continue
            if max(x0, x1) < minx - 800.0 or min(x0, x1) > maxx + 800.0:
                continue
            if max(y0, y1) < miny - 800.0 or min(y0, y1) > maxy + 800.0:
                continue
            segment = LineString([(x0, y0), (x1, y1)])
            if not any(segment.intersects(span) for span in spans):
                continue
            mid = segment.interpolate(0.5, normalized=True)
            if not face.buffer(30.0).covers(mid):
                continue
            key = (round(x0, 0), round(y0, 0), round(x1, 0), round(y1, 0), round(length, 0))
            if key in seen:
                continue
            seen.add(key)
            ux, uy = (x1 - x0) / length, (y1 - y0) / length
            start = _hit_ring(x0, y0, ux, uy, -1.0, ring, 800.0)
            end = _hit_ring(x1, y1, ux, uy, 1.0, ring, 800.0)
            if start is None or end is None:
                continue
            chord = LineString([(start[0], start[1]), (end[0], end[1])])
            if chord.length < 800.0:
                continue
            if ring.distance(Point(start[0], start[1])) > 20.0 or ring.distance(Point(end[0], end[1])) > 20.0:
                continue
            if any(
                not face.buffer(60.0).covers(chord.interpolate(t, normalized=True))
                for t in (0.25, 0.5, 0.75)
            ):
                continue
            perp = abs((origin[0] - x0) * uy - (origin[1] - y0) * ux)
            candidates.append((perp, chord.length - length, chord))
    candidates.sort(key=lambda item: (item[0], item[1]))
    best = None
    for perp, extra, chord in candidates[:8]:
        trial = lines + [chord]
        face_a = _face_at(trial, origin[0], origin[1])
        if face_a is None or not face_a.contains(Point(origin)):
            continue
        if current is not None and face_a.difference(current.buffer(80.0)).area > face_a.area * 0.05:
            continue
        separated = []
        for index, (ox, oy) in enumerate(others):
            face_b = _face_at(trial, ox, oy)
            if face_b is None:
                continue
            shared = face_a.intersection(face_b).area
            if shared > min(face_a.area, face_b.area) * 0.5:
                continue
            separated.append((index, face_b))
        if not separated:
            continue
        areas = [face_a.area] + [item.area for _index, item in separated]
        if min(areas) < 500_000.0:
            continue
        if sum(areas) > face.area * 1.2:
            continue
        best = (face_a, separated, x_axes, y_axes, swings)
        break
    return best


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
            "boundary": (
                "inner face of WALL, WINDOW, COLUMN, and DOOR lines; "
                "WALL/WINDOW ARC and CIRCLE are chord segments; "
                f"same-ring ARC gaps up to {DOOR_GAP_MM:.0f} mm are bridged"
            ),
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
