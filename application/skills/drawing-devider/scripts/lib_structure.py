"""floor_original용 구조 필터.

건축 벽·창·실명과 문 스윙만 남긴다. 창선은 벽과 같이 남긴다. 결과는 항상
`floors/<F>/floor_original.dxf` / `floor_original.png` 로 저장한다.
`floor_structure.*` 는 만들지 않는다.

건축 레이어(이름에 ARCH, BG·CEN 제외)가 도곽 안에 있을 때만 적용한다.
없으면 호출측이 도곽 안 전체를 그대로 둔다.
"""

from __future__ import annotations

import math

from ezdxf import bbox as ezbbox
from ezdxf.document import Drawing
from ezdxf.math import Matrix44
from ezdxf.upright import upright

from lib_split import entity_centroid, filter_bbox

_GEOM = frozenset({"LINE", "ARC", "CIRCLE", "LWPOLYLINE", "TEXT", "MTEXT"})


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
