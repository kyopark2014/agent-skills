"""floor_original용 구조 필터.

건축 벽·창·실명과 문 스윙만 남긴다. 창선은 벽과 같이 남긴다. 결과는 항상
`floors/<F>/floor_original.dxf` / `floor_original.png` 로 저장한다.
`floor_structure.*` 는 만들지 않는다.

건축 레이어(이름에 ARCH, BG·CEN 제외)가 도곽 안에 있을 때만 적용한다.
없으면 호출측이 도곽 안 전체를 그대로 둔다.
"""

from __future__ import annotations

import math
import re

from ezdxf import bbox as ezbbox
from ezdxf.document import Drawing
from ezdxf.math import Matrix44
from ezdxf.upright import upright

from lib_split import entity_centroid, filter_bbox

_GEOM = frozenset({"LINE", "ARC", "CIRCLE", "LWPOLYLINE", "TEXT", "MTEXT"})


# 선종류 이름의 마지막 토큰. '1-plan$0$HIDDEN', 'HID2' 처럼 숨은선만 해당한다.
_HIDDEN_LINETYPE = re.compile(r"^(?:HIDDENX2|HIDDEN2|HIDDEN|HID2|HID|H1)$", re.IGNORECASE)


def is_hidden_linetype(name: str) -> bool:
    token = (name or "").split("$")[-1].strip()
    return _HIDDEN_LINETYPE.fullmatch(token) is not None


def _effective_linetype(doc: Drawing, entity) -> str:
    name = str(getattr(entity.dxf, "linetype", "") or "BYLAYER")
    if name.upper() != "BYLAYER":
        return name
    layer_name = str(getattr(entity.dxf, "layer", "") or "0")
    try:
        return str(doc.layers.get(layer_name).dxf.linetype or "CONTINUOUS")
    except Exception:  # noqa: BLE001 — 레이어 표가 없으면 실선으로 본다
        return "CONTINUOUS"


def hidden_on_off_layer(doc: Drawing, entity) -> bool:
    """꺼진 레이어 위의 숨은선. 켜진 레이어의 점선과 실선은 남긴다."""
    layer_name = str(getattr(entity.dxf, "layer", "") or "0")
    try:
        layer = doc.layers.get(layer_name)
    except Exception:  # noqa: BLE001
        return False
    if layer.is_on():
        return False
    return is_hidden_linetype(_effective_linetype(doc, entity))


def drop_off_layer_hidden(doc: Drawing, entities: list) -> tuple[list, int]:
    kept = []
    skipped = 0
    for entity in entities:
        if hidden_on_off_layer(doc, entity):
            skipped += 1
            continue
        kept.append(entity)
    return kept, skipped


# 구조 축선·버블·축치수. 'A-ST-AXIS-CEN', 'A-ST-AXIS-BUB', 'AXIS-DIM',
# 'AA-AXIS-LINE'(가산동 EHP 등), 단독 'CEN', 'A-CEN'/'A-CEN-*' 등.
# 단독 CEN·A-CEN은 CENTER* 격자 축선(예: 전남대 예술대, 어린이집 동력배치).
# G-CEN·ARCH_*_CEN 벽 중심선은 제외.
# N-CNTL / N-CNTL-CEN* / N-CNTL-AXIS / GRID: CEN linetype 구조 축(예: 중화5구역 부대시설).
_AXIS_LAYER = re.compile(
    r"(?:^|[^A-Za-z0-9])(?:A[_-]?)?ST[_-]?AXIS|"
    r"(?:^|[^A-Za-z0-9]|\$0\$)(?:AA[_-]?)?AXIS"
    r"(?:[_-](?:CEN|CENTER|BUB|BUBBLE|DIM|LINE|IDEN))?(?:$|[^A-Za-z0-9])|"
    r"축선|축버블|구조축|"
    r"^CEN$|"
    r"(?:^|\$0\$)A-CEN(?:$|[-_])|"
    r"(?:^|[^A-Za-z0-9])N-CNTL(?:-CEN\d*|-AXIS)?(?:$|[^A-Za-z0-9])|"
    r"(?:^|\$0\$)GRID$",
    re.IGNORECASE,
)


def is_axis_layer(name: str) -> bool:
    """구조 그리드 축선·버블·축치수 레이어. 벽·중심선(G-CEN·ARCH_*_CEN)과는 별개."""
    return bool(_AXIS_LAYER.search(name or ""))


# 이름 미매칭 축: CEN/CENTER* linetype + 긴 직교 격자(피치 1.5–12 m).
# Continuous 외벽 장축은 지우지 않는다. G-CEN·ARCH_*_CEN 벽 중심선은 제외.
_CENTER_LINETYPE = re.compile(
    r"(?:^|\$0\$|[^A-Za-z0-9])(?:CENTER(?:X?2|X?5)?|CENN|CEN)(?:$|[^A-Za-z0-9])",
    re.IGNORECASE,
)
_KEEP_CENTERLINE_LAYER = re.compile(
    r"(?:^|[^A-Za-z0-9])G-CEN(?:$|[^A-Za-z0-9])|ARCH[_-].*CEN",
    re.IGNORECASE,
)

AXIS_TRAIT_MIN_LEN_MM = 10000.0
AXIS_TRAIT_MIN_COUNT = 3
AXIS_TRAIT_PITCH_MIN_MM = 1500.0
AXIS_TRAIT_PITCH_MAX_MM = 12000.0
AXIS_TRAIT_OVERLAP_RATIO = 0.50
AXIS_TRAIT_SPAN_RATIO = 0.40
AXIS_TRAIT_ORTHO_TOL_MM = 1.0
# Vision 잔여 축·짧은 격자(예: 6–8 m A-CEN)용
AXIS_RESIDUAL_MIN_LEN_MM = 5000.0
WALL_PAIR_GAP_MIN_MM = 30.0
WALL_PAIR_GAP_MAX_MM = 480.0
WALL_PAIR_MIN_OVERLAP_MM = 400.0
WALL_PAIR_MIN_COUNT = 2


def is_center_linetype(name: str) -> bool:
    """CENTER / CENTER2 / CEN 등 중심선(축) 선종류."""
    token = (name or "").split("$")[-1].strip()
    if not token:
        return False
    return _CENTER_LINETYPE.search(token) is not None or token.upper() in {
        "CEN",
        "CENN",
        "CENTER",
        "CENTER2",
        "CENTERX2",
        "CENTER5",
        "CENTERX5",
    }


def is_keep_centerline_layer(name: str) -> bool:
    """벽 중심선 레이어. 특성 탐지로 지우지 않는다."""
    return bool(_KEEP_CENTERLINE_LAYER.search(name or ""))


def _entity_ortho_segs(entity) -> list[tuple[float, float, float, float, float, bool, bool]]:
    """(x0,y0,x1,y1,length,is_h,is_v). 직교 선분만."""
    out: list[tuple[float, float, float, float, float, bool, bool]] = []
    tol = AXIS_TRAIT_ORTHO_TOL_MM
    dxftype = entity.dxftype()
    if dxftype == "LINE":
        pairs = [(entity.dxf.start.x, entity.dxf.start.y, entity.dxf.end.x, entity.dxf.end.y)]
    elif dxftype == "LWPOLYLINE":
        pts = list(entity.get_points("xy"))
        pairs = []
        for i in range(len(pts) - 1):
            pairs.append((pts[i][0], pts[i][1], pts[i + 1][0], pts[i + 1][1]))
        if getattr(entity, "closed", False) and len(pts) >= 2:
            pairs.append((pts[-1][0], pts[-1][1], pts[0][0], pts[0][1]))
    else:
        return out
    for x0, y0, x1, y1 in pairs:
        dx, dy = x1 - x0, y1 - y0
        length = math.hypot(dx, dy)
        if length < 1.0:
            continue
        is_h = abs(dy) <= tol
        is_v = abs(dx) <= tol
        if is_h or is_v:
            out.append((float(x0), float(y0), float(x1), float(y1), float(length), is_h, is_v))
    return out


def _entities_span(entities: list) -> tuple[float, float] | None:
    """전체 기하 bbox → (width_mm, height_mm)."""
    min_x = min_y = math.inf
    max_x = max_y = -math.inf
    found = False
    for entity in entities:
        for x0, y0, x1, y1, _length, _h, _v in _entity_ortho_segs(entity):
            found = True
            min_x = min(min_x, x0, x1)
            max_x = max(max_x, x0, x1)
            min_y = min(min_y, y0, y1)
            max_y = max(max_y, y0, y1)
        if entity.dxftype() in {"CIRCLE", "ARC", "TEXT", "MTEXT", "LWPOLYLINE", "LINE"}:
            try:
                center = entity_centroid(entity)
            except Exception:  # noqa: BLE001
                center = None
            if center is not None:
                found = True
                min_x = min(min_x, center[0])
                max_x = max(max_x, center[0])
                min_y = min(min_y, center[1])
                max_y = max(max_y, center[1])
    if not found or not math.isfinite(min_x):
        return None
    return (max_x - min_x, max_y - min_y)


def _parallel_axis_grid(
    segs: list[tuple[float, float, float, float, float, bool, bool]],
    *,
    vertical: bool,
    min_len_mm: float,
) -> bool:
    """같은 방향 긴 선이 축 피치로 3줄 이상이면 True."""
    long = [s for s in segs if s[4] >= min_len_mm and (s[6] if vertical else s[5])]
    if len(long) < AXIS_TRAIT_MIN_COUNT:
        return False
    need = AXIS_TRAIT_MIN_COUNT - 1
    for s in long:
        ortho = (s[0] + s[2]) * 0.5 if vertical else (s[1] + s[3]) * 0.5
        a0 = min(s[1], s[3]) if vertical else min(s[0], s[2])
        a1 = max(s[1], s[3]) if vertical else max(s[0], s[2])
        peers = 0
        for other in long:
            if other is s:
                continue
            other_ortho = (other[0] + other[2]) * 0.5 if vertical else (other[1] + other[3]) * 0.5
            pitch = abs(other_ortho - ortho)
            if not (AXIS_TRAIT_PITCH_MIN_MM <= pitch <= AXIS_TRAIT_PITCH_MAX_MM):
                continue
            b0 = min(other[1], other[3]) if vertical else min(other[0], other[2])
            b1 = max(other[1], other[3]) if vertical else max(other[0], other[2])
            overlap = max(0.0, min(a1, b1) - max(a0, b0))
            if overlap >= AXIS_TRAIT_OVERLAP_RATIO * min(s[4], other[4]):
                peers += 1
                if peers >= need:
                    return True
    return False


def layer_has_axis_grid(
    doc: Drawing,
    entities: list,
    layer_name: str,
    *,
    span: tuple[float, float] | None = None,
) -> bool:
    """한 레이어가 CEN/CENTER 축 격자인지."""
    if is_axis_layer(layer_name) or is_keep_centerline_layer(layer_name):
        return False
    layer_ents = [
        e for e in entities if str(getattr(e.dxf, "layer", "") or "") == layer_name
    ]
    if not layer_ents:
        return False
    try:
        layer_lt = str(doc.layers.get(layer_name).dxf.linetype or "")
    except Exception:  # noqa: BLE001
        layer_lt = ""
    segs: list[tuple[float, float, float, float, float, bool, bool]] = []
    center_long = 0
    for entity in layer_ents:
        lt = _effective_linetype(doc, entity)
        if not is_center_linetype(lt) and not is_center_linetype(layer_lt):
            continue
        for seg in _entity_ortho_segs(entity):
            segs.append(seg)
            if seg[4] >= AXIS_TRAIT_MIN_LEN_MM:
                center_long += 1
    if center_long < AXIS_TRAIT_MIN_COUNT:
        return False
    if span is None:
        span = _entities_span(entities)
    min_len_v = AXIS_TRAIT_MIN_LEN_MM
    min_len_h = AXIS_TRAIT_MIN_LEN_MM
    if span is not None:
        width, height = span
        min_len_v = max(AXIS_TRAIT_MIN_LEN_MM, height * AXIS_TRAIT_SPAN_RATIO)
        min_len_h = max(AXIS_TRAIT_MIN_LEN_MM, width * AXIS_TRAIT_SPAN_RATIO)
    return _parallel_axis_grid(segs, vertical=True, min_len_mm=min_len_v) or _parallel_axis_grid(
        segs, vertical=False, min_len_mm=min_len_h
    )


def discover_axis_layers(doc: Drawing, entities: list) -> list[str]:
    """이름에 안 걸려도 CEN/CENTER 긴 직교 격자인 레이어를 찾는다.

    1) 층에 축 격자 기하가 있는지 보고
    2) 그 기하가 속한 레이어를 모아 반환한다.
    """
    if not entities:
        return []
    span = _entities_span(entities)
    candidates: set[str] = set()
    for entity in entities:
        layer = str(getattr(entity.dxf, "layer", "") or "")
        if not layer or is_axis_layer(layer) or is_keep_centerline_layer(layer):
            continue
        if layer in candidates:
            continue
        candidates.add(layer)
    found = [
        name
        for name in sorted(candidates)
        if layer_has_axis_grid(doc, entities, name, span=span)
    ]
    return found


def drop_axis_layers(
    entities: list,
    doc: Drawing | None = None,
) -> tuple[list, int, dict]:
    """이름 패턴 축 레이어를 빼고, 남아 있는 CEN/CENTER 축 격자 레이어도 뺀다.

    Returns:
        (kept, skipped_count, info)
        info["trait_layers"]: 특성으로 추가 제거한 레이어 이름
    """
    kept: list = []
    skipped = 0
    for entity in entities:
        layer = str(getattr(entity.dxf, "layer", "") or "")
        if is_axis_layer(layer):
            skipped += 1
            continue
        kept.append(entity)

    info: dict = {"trait_layers": []}
    if doc is None or not kept:
        return kept, skipped, info

    trait_layers = discover_axis_layers(doc, kept)
    if not trait_layers:
        return kept, skipped, info

    drop = set(trait_layers)
    kept2: list = []
    for entity in kept:
        layer = str(getattr(entity.dxf, "layer", "") or "")
        if layer in drop:
            skipped += 1
            continue
        kept2.append(entity)
    info["trait_layers"] = trait_layers
    return kept2, skipped, info


def layer_leaf_name(name: str) -> str:
    """XREF `…$0$A-CEN` → `A-CEN`."""
    text = name or ""
    if "$0$" in text:
        return text.rsplit("$0$", 1)[-1]
    return text


def drop_named_layers(entities: list, layer_names: set[str] | list[str]) -> tuple[list, int]:
    """지정 레이어 엔티티를 뺀다. (kept, skipped_count)."""
    drop = set(layer_names)
    if not drop:
        return list(entities), 0
    kept: list = []
    skipped = 0
    for entity in entities:
        layer = str(getattr(entity.dxf, "layer", "") or "")
        if layer in drop:
            skipped += 1
            continue
        kept.append(entity)
    return kept, skipped


def _layer_linetype(doc: Drawing | None, layer_name: str) -> str:
    if doc is None:
        return ""
    try:
        return str(doc.layers.get(layer_name).dxf.linetype or "")
    except Exception:  # noqa: BLE001
        return ""


def _layer_ortho_segs(entities: list, layer_name: str) -> list[tuple[float, float, float, float, float, bool, bool]]:
    segs: list[tuple[float, float, float, float, float, bool, bool]] = []
    for entity in entities:
        if str(getattr(entity.dxf, "layer", "") or "") != layer_name:
            continue
        segs.extend(_entity_ortho_segs(entity))
    return segs


def layer_has_wall_thickness_pairs(
    entities: list,
    layer_name: str,
    *,
    min_count: int = WALL_PAIR_MIN_COUNT,
) -> bool:
    """벽 두께(30–480 mm) 평행 이중선 짝이 있으면 True."""
    segs = _layer_ortho_segs(entities, layer_name)
    if len(segs) < 2:
        return False

    def _count_pairs(vertical: bool) -> int:
        group = [s for s in segs if (s[6] if vertical else s[5]) and s[4] >= WALL_PAIR_MIN_OVERLAP_MM]
        pairs = 0
        for i, a in enumerate(group):
            ao = (a[0] + a[2]) * 0.5 if vertical else (a[1] + a[3]) * 0.5
            a0 = min(a[1], a[3]) if vertical else min(a[0], a[2])
            a1 = max(a[1], a[3]) if vertical else max(a[0], a[2])
            for b in group[i + 1 :]:
                bo = (b[0] + b[2]) * 0.5 if vertical else (b[1] + b[3]) * 0.5
                gap = abs(bo - ao)
                if not (WALL_PAIR_GAP_MIN_MM <= gap <= WALL_PAIR_GAP_MAX_MM):
                    continue
                b0 = min(b[1], b[3]) if vertical else min(b[0], b[2])
                b1 = max(b[1], b[3]) if vertical else max(b[0], b[2])
                overlap = max(0.0, min(a1, b1) - max(a0, b0))
                if overlap >= WALL_PAIR_MIN_OVERLAP_MM:
                    pairs += 1
                    if pairs >= min_count:
                        return pairs
        return pairs

    return _count_pairs(True) >= min_count or _count_pairs(False) >= min_count


def layer_has_residual_axis_grid(
    entities: list,
    layer_name: str,
    *,
    min_len_mm: float = AXIS_RESIDUAL_MIN_LEN_MM,
    span: tuple[float, float] | None = None,
) -> bool:
    """이름·CENTER 없이도 피치 격자(완화 길이)면 True. Vision 잔여 귀속용."""
    segs = _layer_ortho_segs(entities, layer_name)
    long = [s for s in segs if s[4] >= min_len_mm]
    if len(long) < AXIS_TRAIT_MIN_COUNT:
        return False
    if span is None:
        span = _entities_span(entities)
    min_len_v = min_len_mm
    min_len_h = min_len_mm
    if span is not None:
        width, height = span
        # 잔여 패스는 층 span 비율을 강제하지 않고 하한만 둔다.
        min_len_v = max(min_len_mm, min(height * 0.15, AXIS_TRAIT_MIN_LEN_MM))
        min_len_h = max(min_len_mm, min(width * 0.15, AXIS_TRAIT_MIN_LEN_MM))
    return _parallel_axis_grid(segs, vertical=True, min_len_mm=min_len_v) or _parallel_axis_grid(
        segs, vertical=False, min_len_mm=min_len_h
    )


def layer_uses_center_linetype(doc: Drawing | None, entities: list, layer_name: str) -> bool:
    """레이어 기본 또는 엔티티 linetype 이 CENTER/CEN 계열이면 True."""
    layer_lt = _layer_linetype(doc, layer_name)
    if is_center_linetype(layer_lt):
        return True
    if doc is None:
        return False
    for entity in entities:
        if str(getattr(entity.dxf, "layer", "") or "") != layer_name:
            continue
        if is_center_linetype(_effective_linetype(doc, entity)):
            return True
    return False


_AXIS_UNSAFE_LEAF = re.compile(
    r"일람|범례|범레|NOTE|NOTES|LEGEND|TITLE|표제|장비|SCHEDULE|TEXT|DIMS?$|치수",
    re.IGNORECASE,
)


def layer_is_safe_to_drop_as_axis(
    doc: Drawing | None,
    entities: list,
    layer_name: str,
    *,
    span: tuple[float, float] | None = None,
) -> tuple[bool, str]:
    """Vision이 후보로 준 레이어를 이번 추출에서 지워도 되는지.

    Continuous 표·장비일람 격자는 제거하지 않는다. CENTER/CEN 선종류이거나
    leaf 이름이 축 패턴일 때만 허용한다.

    Returns:
        (ok, reason) reason 은 ok면 'center'|'axis_name', 아니면 거절 사유.
    """
    if not layer_name:
        return False, "empty"
    if is_keep_centerline_layer(layer_name):
        return False, "keep_centerline"
    leaf = layer_leaf_name(layer_name)
    if _AXIS_UNSAFE_LEAF.search(leaf):
        return False, "annotation_leaf"
    if layer_has_wall_thickness_pairs(entities, layer_name):
        return False, "wall_pairs"
    if layer_uses_center_linetype(doc, entities, layer_name):
        # CENTER 선종류면 격자 개수가 모자라도 Vision 귀속만으로 허용.
        return True, "center"
    # Continuous 피치 격자만으로는 장비일람·표 선을 축으로 오인하므로 거절.
    # 이름 패턴 축은 is_axis_layer 로 이미 1차 제거된다.
    if is_axis_layer(layer_name):
        return True, "axis_name"
    return False, "no_center_linetype"


def has_residual_axis_candidates(doc: Drawing | None, entities: list) -> bool:
    """Vision 호출 전: CENTER 축 후보가 남아 있으면 True.

    후보가 없으면 LLM 을 건너뛴다(속도). Continuous 표 격자는 후보가 아니다.
    """
    if not entities:
        return False
    seen: set[str] = set()
    for entity in entities:
        layer = str(getattr(entity.dxf, "layer", "") or "")
        if not layer or layer in seen:
            continue
        seen.add(layer)
        if is_keep_centerline_layer(layer) or is_axis_layer(layer):
            continue
        leaf = layer_leaf_name(layer)
        if _AXIS_UNSAFE_LEAF.search(leaf):
            continue
        if layer_uses_center_linetype(doc, entities, layer):
            return True
    return False


def layers_near_bboxes(
    entities: list,
    bboxes_mm: list[tuple[float, float, float, float]],
    *,
    min_len_mm: float = AXIS_RESIDUAL_MIN_LEN_MM,
    pad_mm: float = 200.0,
) -> dict[str, int]:
    """Vision bbox(mm)와 겹치는 긴 직교 선분의 레이어 집계."""
    from collections import Counter

    counts: Counter[str] = Counter()
    if not bboxes_mm:
        return {}
    for entity in entities:
        layer = str(getattr(entity.dxf, "layer", "") or "")
        if not layer:
            continue
        for x0, y0, x1, y1, length, _h, _v in _entity_ortho_segs(entity):
            if length < min_len_mm:
                continue
            sx0, sx1 = min(x0, x1), max(x0, x1)
            sy0, sy1 = min(y0, y1), max(y0, y1)
            for bx0, by0, bx1, by1 in bboxes_mm:
                px0, px1 = bx0 - pad_mm, bx1 + pad_mm
                py0, py1 = by0 - pad_mm, by1 + pad_mm
                if sx1 < px0 or sx0 > px1 or sy1 < py0 or sy0 > py1:
                    continue
                counts[layer] += 1
                break
    return dict(counts)


def select_droppable_axis_layers(
    doc: Drawing | None,
    entities: list,
    candidate_layers: list[str] | dict[str, int],
) -> tuple[list[str], list[dict]]:
    """후보 중 안전하게 drop 가능한 레이어와 거절 목록."""
    if isinstance(candidate_layers, dict):
        names = sorted(candidate_layers.keys(), key=lambda n: (-candidate_layers[n], n))
    else:
        names = list(dict.fromkeys(candidate_layers))
    span = _entities_span(entities)
    dropped: list[str] = []
    skipped: list[dict] = []
    for name in names:
        if is_axis_layer(name):
            # 이름 패턴이면 이미 1차 drop 대상. Vision 패스에서도 지우도록 허용.
            dropped.append(name)
            continue
        ok, reason = layer_is_safe_to_drop_as_axis(doc, entities, name, span=span)
        if ok:
            dropped.append(name)
        else:
            skipped.append({"layer": name, "reason": reason})
    return dropped, skipped


def is_arch_layer(name: str) -> bool:
    """벽·실명에 쓰는 건축 레이어. 등고(BG)·중심선(CEN)은 제외."""
    upper = name.upper()
    if "ARCH" not in upper:
        return False
    if "ARCH_BG" in upper or upper.endswith("_BG"):
        return False
    if "ARCH_CEN" in upper or upper.endswith("_CEN"):
        return False
    return True


def _in_clip(x: float, y: float, clip: tuple[float, float, float, float]) -> bool:
    return clip[0] <= x <= clip[2] and clip[1] <= y <= clip[3]


def _is_door_block(block) -> bool:
    """90도 스윙(호+문짝) 블록. 가구·설비 블록은 엔티티가 많거나 호가 아니다."""
    arcs: list[tuple[float, float]] = []
    n = 0
    for entity in block:
        n += 1
        if entity.dxftype() in {"SPLINE", "ELLIPSE", "HATCH", "TEXT", "MTEXT", "INSERT"}:
            return False
        if entity.dxftype() == "ARC":
            radius = float(entity.dxf.radius)
            sweep = (float(entity.dxf.end_angle) - float(entity.dxf.start_angle)) % 360.0
            arcs.append((radius, sweep))
    if n == 0 or n > 20:
        return False
    return any(400.0 <= radius <= 1400.0 and 70.0 <= sweep <= 110.0 for radius, sweep in arcs)


def _place_insert(doc: Drawing, insert):
    sx = float(insert.dxf.xscale or 1)
    sy = float(insert.dxf.yscale or 1)
    sz = float(insert.dxf.zscale or 1)
    rotation = math.radians(float(insert.dxf.rotation or 0))
    matrix = Matrix44.chain(
        Matrix44.scale(sx, sy, sz),
        Matrix44.z_rotate(rotation),
        Matrix44.translate(insert.dxf.insert.x, insert.dxf.insert.y, insert.dxf.insert.z),
    )
    placed = []
    block = doc.blocks.get(insert.dxf.name)
    for entity in block:
        if entity.dxftype() not in {"LINE", "ARC", "CIRCLE", "LWPOLYLINE"}:
            continue
        copied = entity.copy()
        copied.transform(matrix)
        try:
            upright(copied)
        except Exception:  # noqa: BLE001 — 반전 블록만 보정, 실패 시 원본 복사본 유지
            pass
        placed.append(copied)
    return placed


def collect_structural_sheet(doc: Drawing, clip: tuple[float, float, float, float]):
    """도곽 안의 벽·창·실명·문. 건축 레이어의 창선은 벽과 같이 남긴다. 없으면 None."""
    direct = []
    layers: set[str] = set()
    for entity in doc.modelspace():
        if entity.dxftype() == "INSERT" or entity.dxftype() not in _GEOM:
            continue
        if not is_arch_layer(entity.dxf.layer):
            continue
        center = entity_centroid(entity)
        if center is None or not _in_clip(center[0], center[1], clip):
            continue
        direct.append(entity)
        layers.add(entity.dxf.layer)
    if not direct:
        return None

    door_names = {
        block.name
        for block in doc.blocks
        if not block.name.startswith("*") and _is_door_block(block)
    }
    doors = []
    door_hits: dict[str, int] = {}
    for insert in doc.modelspace().query("INSERT"):
        if insert.dxf.name not in door_names:
            continue
        try:
            ext = ezbbox.extents([insert])
        except Exception:  # noqa: BLE001
            continue
        if not ext.has_data:
            continue
        x0, y0 = float(ext.extmin.x), float(ext.extmin.y)
        x1, y1 = float(ext.extmax.x), float(ext.extmax.y)
        if x1 < clip[0] or x0 > clip[2] or y1 < clip[1] or y0 > clip[3]:
            continue
        inside = filter_bbox(_place_insert(doc, insert), clip[0], clip[1], clip[2], clip[3])
        if not inside:
            continue
        doors.extend(inside)
        door_hits[insert.dxf.name] = door_hits.get(insert.dxf.name, 0) + 1

    info = {
        "layers": sorted(layers),
        "n_direct": len(direct),
        "n_door_entities": len(doors),
        "door_blocks": door_hits,
    }
    return direct + doors, info
