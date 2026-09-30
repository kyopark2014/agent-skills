#!/usr/bin/env python3
"""
지원동 DXF → 층별 floor_original.dxf (+ .png) 전처리.

원본은 XREF/블록 중심(277MB)이라 modelspace 순회만으로는 도면이 거의 없다.
해당 층 INSERT만 골라 explode한 뒤 `floors/<F>/floor_original.dxf`를 만든다
(조경·가구 포함, plan/original 좌표계에 맞춤).
XA-S-{N}F 평면 블록이 없으면 도곽(축정렬 테두리)과 층 제목으로 영역을 자른다.
기본으로 같은 경로에 `floor_original.png` / `_meta.json`도 렌더한다 (`--no-png`로 생략).

레거시 `--variant clean`(가구 제외)은 비권장 — 기본 워크플로에서 제거됨.

Usage:
  python extract_2d.py --dxf $ARTIFACTS_DIR/input.dxf --floor 5F \\
    --out $ARTIFACTS_DIR --drawing-id sk_yongin_jiwon
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
from collections import Counter
from pathlib import Path

import ezdxf
from ezdxf.document import Drawing
from ezdxf.entities import DXFEntity, Insert
from ezdxf.enums import TextEntityAlignment

# skill scripts/ 기준 — 산출은 사용자 artifacts (ARTIFACTS_DIR / cwd)
SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from lib_render import render_floor_original_preview  # noqa: E402
from lib_sheet import discover_layout, normalize_floor_token  # noqa: E402
from lib_split import find_primary_floor_bbox, find_primary_line_bbox  # noqa: E402


def resolve_artifacts_dir() -> Path:
    env = os.environ.get("ARTIFACTS_DIR") or os.environ.get("ARTIFACT_DIR")
    return Path(env) if env else Path.cwd()

# 블록명에 포함되면 가구/비구조로 보고 제외 (벽체 오염 완화)
FURNITURE_KEYWORDS = (
    "가구",
    "chair",
    "피트니스",
    "화)",
    "DOOR",
    "DOR_",
    "도어",
    "자동문",
    "락커",
    "라커",
    "샤워",
    "신발",
    "러닝",
    "파우더",
    "큐비클",
    "대변기",
    "소변",
    "객석",
    "좌석",
    "모바일",
    "회의",
    "업다운",
    "절취",
    "RAIN",
    "rain",
    "입면",
    "슬라이딩",
    "접견",
)

GEOM_TYPES = frozenset(
    {"LINE", "LWPOLYLINE", "POLYLINE", "ARC", "CIRCLE", "ELLIPSE", "SPLINE", "HATCH"}
)
TEXT_TYPES = frozenset({"TEXT", "MTEXT"})


def is_furniture(name: str) -> bool:
    lower = name.lower()
    return any(k.lower() in lower for k in FURNITURE_KEYWORDS)


def floor_token(floor: str) -> str:
    """'5', '5F', 'b1', 'RF' → '5F' / 'B1F' / 'RF'."""
    s = floor.strip().upper().replace(" ", "")
    if s == "ALL":
        return "ALL"
    return normalize_floor_token(floor)


def discover_floors(doc: Drawing) -> list[str]:
    """modelspace의 XA-S-{{N}}F 평면 블록에서 층 목록 추출."""
    return discover_layout(doc, mode="block").floors


def find_floor_inserts(
    doc: Drawing,
    floor: str,
    *,
    include_extra: bool = False,
) -> list[Insert]:
    """해당 층의 평면/기둥/코어 INSERT를 반환.

    include_extra=True 이면 조경·천장(C)·P코어 등도 포함.
    """
    n = floor[:-1]  # '5F' → '5'
    core_patterns = [
        re.compile(rf"^XA-S-{n}F\s*평면$"),
        re.compile(rf"^XA-S-{n}F\s*코어$"),
        re.compile(rf"^XS-S-{n}F\s*기둥$"),
    ]
    extra_patterns = [
        re.compile(rf"^XA-P-{n}F\s*코어$"),
        re.compile(rf"^XA-C-{n}F\s*평면$"),
        re.compile(rf"^XA-G-조경\s*\({n}F\)$"),
    ]
    patterns = core_patterns + (extra_patterns if include_extra else [])
    hits: list[Insert] = []
    for e in doc.modelspace().query("INSERT"):
        name = e.dxf.name
        if any(p.search(name) for p in patterns):
            hits.append(e)
    return hits


def explode_insert(
    insert: Insert,
    *,
    skip_furniture: bool = True,
    max_depth: int = 12,
) -> list[DXFEntity]:
    """INSERT를 재귀 explode. 가구 블록은 건너뛴다."""
    out: list[DXFEntity] = []

    def walk(ins: Insert, depth: int) -> None:
        if depth > max_depth:
            return
        try:
            entities = list(ins.virtual_entities())
        except Exception as exc:  # noqa: BLE001 — 개별 블록 실패는 스킵
            print(f"  [warn] virtual_entities 실패 ({ins.dxf.name}): {exc}", file=sys.stderr)
            return
        for e in entities:
            if e.dxftype() == "INSERT":
                name = e.dxf.name
                if skip_furniture and is_furniture(name):
                    continue
                walk(e, depth + 1)
            else:
                out.append(e)

    walk(insert, 0)
    return out


def _xy(p) -> tuple[float, float]:
    return float(p[0]), float(p[1])


def entity_to_record(e: DXFEntity) -> dict | None:
    """수치 추출용 JSON 레코드."""
    t = e.dxftype()
    layer = e.dxf.layer if e.dxf.hasattr("layer") else "0"
    base = {"type": t, "layer": layer}

    if t == "LINE":
        s, end = _xy(e.dxf.start), _xy(e.dxf.end)
        length = math.hypot(end[0] - s[0], end[1] - s[1])
        return {**base, "start": s, "end": end, "length": length}

    if t == "CIRCLE":
        c = _xy(e.dxf.center)
        r = float(e.dxf.radius)
        return {**base, "center": c, "radius": r, "diameter": r * 2}

    if t == "ARC":
        c = _xy(e.dxf.center)
        return {
            **base,
            "center": c,
            "radius": float(e.dxf.radius),
            "start_angle": float(e.dxf.start_angle),
            "end_angle": float(e.dxf.end_angle),
        }

    if t == "LWPOLYLINE":
        pts = [(_xy(p)) for p in e.get_points("xy")]
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        return {
            **base,
            "points": pts,
            "closed": bool(e.closed),
            "width": (max(xs) - min(xs)) if xs else 0.0,
            "height": (max(ys) - min(ys)) if ys else 0.0,
        }

    if t == "TEXT":
        return {
            **base,
            "text": e.dxf.text,
            "insert": _xy(e.dxf.insert),
            "height": float(e.dxf.height),
        }

    if t == "MTEXT":
        return {
            **base,
            "text": e.text,
            "insert": _xy(e.dxf.insert),
            "height": float(getattr(e.dxf, "char_height", 0) or 0),
        }

    return None


def _entity_points(e: DXFEntity) -> list[tuple[float, float]]:
    pts: list[tuple[float, float]] = []
    t = e.dxftype()
    try:
        if t == "LINE":
            pts.append(_xy(e.dxf.start))
            pts.append(_xy(e.dxf.end))
        elif t in {"CIRCLE", "ARC"}:
            c, r = _xy(e.dxf.center), float(e.dxf.radius)
            pts.extend([(c[0] - r, c[1] - r), (c[0] + r, c[1] + r), c])
        elif t == "LWPOLYLINE":
            pts.extend(_xy(p) for p in e.get_points("xy"))
        elif t in TEXT_TYPES:
            pts.append(_xy(e.dxf.insert))
        elif t == "ELLIPSE":
            pts.append(_xy(e.dxf.center))
    except Exception:  # noqa: BLE001
        return []
    return [(x, y) for x, y in pts if math.isfinite(x) and math.isfinite(y)]


def extents_of(entities: list[DXFEntity]) -> tuple[float, float, float, float] | None:
    xs: list[float] = []
    ys: list[float] = []
    for e in entities:
        for x, y in _entity_points(e):
            xs.append(x)
            ys.append(y)
    if not xs:
        return None
    return min(xs), min(ys), max(xs), max(ys)


def robust_extents(
    entities: list[DXFEntity],
    *,
    lo: float = 5.0,
    hi: float = 95.0,
    pad_ratio: float = 0.03,
) -> tuple[float, float, float, float] | None:
    """백분위수 기반 bbox — 멀리 떨어진 이상치 좌표를 제거."""
    xs: list[float] = []
    ys: list[float] = []
    for e in entities:
        if e.dxftype() not in {"LINE", "LWPOLYLINE", "ARC", "CIRCLE"}:
            continue
        for x, y in _entity_points(e):
            xs.append(x)
            ys.append(y)
    if len(xs) < 20:
        return extents_of(entities)

    def pct(vals: list[float], p: float) -> float:
        s = sorted(vals)
        i = (len(s) - 1) * p / 100.0
        lo_i, hi_i = int(math.floor(i)), int(math.ceil(i))
        if lo_i == hi_i:
            return s[lo_i]
        return s[lo_i] * (hi_i - i) + s[hi_i] * (i - lo_i)

    min_x, max_x = pct(xs, lo), pct(xs, hi)
    min_y, max_y = pct(ys, lo), pct(ys, hi)
    # 너무 납작하면(한쪽만 밀집) 반대축은 더 넓게 유지
    w, h = max_x - min_x, max_y - min_y
    if w < 1 or h < 1:
        return extents_of(entities)
    pad_x = max(w * pad_ratio, 500.0)
    pad_y = max(h * pad_ratio, 500.0)
    return min_x - pad_x, min_y - pad_y, max_x + pad_x, max_y + pad_y


def _centroid(e: DXFEntity) -> tuple[float, float] | None:
    pts = _entity_points(e)
    if not pts:
        return None
    return sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts)


def filter_by_bbox(
    entities: list[DXFEntity],
    bbox: tuple[float, float, float, float],
) -> list[DXFEntity]:
    min_x, min_y, max_x, max_y = bbox
    kept: list[DXFEntity] = []
    for e in entities:
        c = _centroid(e)
        if c is None:
            continue
        if min_x <= c[0] <= max_x and min_y <= c[1] <= max_y:
            kept.append(e)
    return kept


def write_clean_dxf(
    entities: list[DXFEntity],
    out_path: Path,
    *,
    include_text: bool = True,
    origin_shift: tuple[float, float] | None = None,
) -> Counter:
    """주요 기하만 새 DXF로 복사. origin_shift=(ox,oy)이면 (x-ox, y-oy)로 이동."""
    doc = ezdxf.new("R2010")
    msp = doc.modelspace()
    counts: Counter = Counter()
    ox, oy = origin_shift or (0.0, 0.0)

    def sh(p) -> tuple[float, float, float]:
        x, y = float(p[0]), float(p[1])
        z = float(p[2]) if len(p) > 2 else 0.0
        return x - ox, y - oy, z

    for e in entities:
        t = e.dxftype()
        if t not in GEOM_TYPES and not (include_text and t in TEXT_TYPES):
            continue
        try:
            if t == "LINE":
                msp.add_line(sh(e.dxf.start), sh(e.dxf.end), dxfattribs={"layer": e.dxf.layer})
            elif t == "CIRCLE":
                msp.add_circle(
                    sh(e.dxf.center),
                    e.dxf.radius,
                    dxfattribs={"layer": e.dxf.layer},
                )
            elif t == "ARC":
                msp.add_arc(
                    sh(e.dxf.center),
                    e.dxf.radius,
                    e.dxf.start_angle,
                    e.dxf.end_angle,
                    dxfattribs={"layer": e.dxf.layer},
                )
            elif t == "LWPOLYLINE":
                pts = []
                for p in e.get_points("xyb"):
                    pts.append((p[0] - ox, p[1] - oy, p[2] if len(p) > 2 else 0))
                pl = msp.add_lwpolyline(pts, dxfattribs={"layer": e.dxf.layer})
                pl.closed = bool(e.closed)
            elif t == "TEXT":
                ins = sh(e.dxf.insert)
                msp.add_text(
                    e.dxf.text,
                    height=e.dxf.height,
                    dxfattribs={"layer": e.dxf.layer, "insert": ins},
                ).set_placement(ins, align=TextEntityAlignment.LEFT)
            elif t == "MTEXT":
                msp.add_mtext(
                    e.text,
                    dxfattribs={
                        "layer": e.dxf.layer,
                        "insert": sh(e.dxf.insert),
                        "char_height": getattr(e.dxf, "char_height", 2.5) or 2.5,
                    },
                )
            else:
                continue
            counts[t] += 1
        except Exception as exc:  # noqa: BLE001
            counts[f"skip:{t}"] += 1
            if counts[f"skip:{t}"] <= 3:
                print(f"  [warn] copy {t}: {exc}", file=sys.stderr)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    doc.saveas(out_path)
    return counts


def write_json(
    entities: list[DXFEntity],
    out_path: Path,
    *,
    floor: str,
    source_blocks: list[str],
) -> dict:
    records = []
    type_counts: Counter = Counter()
    for e in entities:
        type_counts[e.dxftype()] += 1
        rec = entity_to_record(e)
        if rec:
            records.append(rec)

    ext = extents_of(entities)
    payload = {
        "floor": floor,
        "source_blocks": source_blocks,
        "entity_counts": dict(type_counts),
        "extents": (
            {"min_x": ext[0], "min_y": ext[1], "max_x": ext[2], "max_y": ext[3]}
            if ext
            else None
        ),
        "entities": records,
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


def collect_modelspace_labels(
    doc: Drawing,
    bbox: tuple[float, float, float, float],
    *,
    pad_mm: float = 2000.0,
) -> list[DXFEntity]:
    """modelspace TEXT/MTEXT 중 bbox 안 실명·면적 라벨을 수집.

    지원동 도면은 실명(접견실#1, 면적, 천장고 등)이 층 INSERT가 아니라
    modelspace TEXT로 따로 있어, 층 explode만으로는 빠진다.
    """
    min_x, min_y, max_x, max_y = bbox
    min_x -= pad_mm
    min_y -= pad_mm
    max_x += pad_mm
    max_y += pad_mm
    out: list[DXFEntity] = []
    for e in doc.modelspace():
        t = e.dxftype()
        if t not in TEXT_TYPES:
            continue
        try:
            if t == "TEXT":
                x, y = float(e.dxf.insert.x), float(e.dxf.insert.y)
            else:  # MTEXT
                x, y = float(e.dxf.insert.x), float(e.dxf.insert.y)
        except Exception:  # noqa: BLE001
            continue
        if not (math.isfinite(x) and math.isfinite(y)):
            continue
        if min_x <= x <= max_x and min_y <= y <= max_y:
            out.append(e)
    return out


def resolve_original_path(
    out_dir: Path,
    floor: str,
    *,
    drawing_id: str | None,
) -> Path:
    """floor_original.dxf 경로.

    drawing_id가 있으면 `$out/<drawing_id>/floors/<F>/floor_original.dxf`
    없으면 `$out/floor_{F}_original.dxf`
    """
    if drawing_id:
        return out_dir / drawing_id / "floors" / floor / "floor_original.dxf"
    return out_dir / f"floor_{floor}_original.dxf"


def _load_plan_core_bbox(out_dir: Path, drawing_id: str | None, floor: str):
    """기존 split_plan / floor_original 과 같은 클러스터를 쓰기 위한 bbox."""
    candidates: list[Path] = []
    if drawing_id:
        art = out_dir / drawing_id
        candidates.append(art / "split_plan.json")
        candidates.append(art / "floors" / floor / "floor_original_meta.json")
    for path in candidates:
        if not path.is_file():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        if path.name == "split_plan.json":
            fl = (data.get("floors") or {}).get(floor) or {}
            bb = (fl.get("span") or {}).get("bbox_mm")
            if bb:
                return bb["xmin"], bb["ymin"], bb["xmax"], bb["ymax"]
        else:
            bb = data.get("bbox_mm")
            if bb:
                return bb["xmin"], bb["ymin"], bb["xmax"], bb["ymax"]
    return None


def extract_floor(
    doc: Drawing,
    floor: str,
    out_dir: Path,
    *,
    variant: str = "clean",
    drawing_id: str | None = None,
    skip_furniture: bool | None = None,
    include_extra: bool | None = None,
    do_dxf: bool = True,
    do_json: bool = False,
    do_png: bool = True,
    include_text: bool = True,
    origin_shift_mode: str = "none",
) -> dict:
    """variant: clean | original

    - clean: 평면/코어/기둥, 가구 제외, LINE primary
    - original: +조경 등 extra, 가구 유지.
      기존 plan/original이 있으면 그 좌표계로 맞춘다.
    """
    if variant not in {"clean", "original"}:
        raise ValueError(f"variant 오류: {variant!r}")

    if skip_furniture is None:
        skip_furniture = variant == "clean"
    if include_extra is None:
        include_extra = variant == "original"

    core_inserts = find_floor_inserts(doc, floor, include_extra=False)
    if not core_inserts:
        raise RuntimeError(f"{floor}: 매칭 코어 INSERT 없음")

    core_ents: list[DXFEntity] = []
    for ins in core_inserts:
        core_ents.extend(explode_insert(ins, skip_furniture=True))

    fresh_core = find_primary_line_bbox(core_ents) or robust_extents(core_ents)
    if fresh_core is None:
        raise RuntimeError(f"{floor}: 코어 bbox를 계산할 수 없음")

    plan_bb = _load_plan_core_bbox(out_dir, drawing_id, floor)

    print(f"\n== {floor} ({variant}) ==")
    print(
        f"  fresh core bbox: ({fresh_core[0]:.0f},{fresh_core[1]:.0f})"
        f"-({fresh_core[2]:.0f},{fresh_core[3]:.0f})"
    )
    if plan_bb:
        print(
            f"  plan/original bbox: ({plan_bb[0]:.0f},{plan_bb[1]:.0f})"
            f"-({plan_bb[2]:.0f},{plan_bb[3]:.0f})"
        )

    inserts = find_floor_inserts(doc, floor, include_extra=include_extra)
    block_names = [i.dxf.name for i in inserts]
    for n in block_names:
        print(f"  INSERT: {n}")

    entities: list[DXFEntity] = []
    for ins in inserts:
        ents = explode_insert(ins, skip_furniture=skip_furniture)
        print(f"  exploded {ins.dxf.name}: {len(ents)} entities")
        entities.extend(ents)

    raw_count = len(entities)
    # 필터는 항상 fresh 좌표의 코어 클러스터 기준 (조경 확장)
    if variant == "original":
        bbox = find_primary_floor_bbox(entities, core_bbox=fresh_core)
        label = "primary floor bbox"
    else:
        bbox = find_primary_line_bbox(entities) or fresh_core
        label = "primary line bbox"

    if bbox:
        entities = filter_by_bbox(entities, bbox)
        print(
            f"  {label} filter: {raw_count} → {len(entities)}  "
            f"bbox=({bbox[0]:.0f},{bbox[1]:.0f})-({bbox[2]:.0f},{bbox[3]:.0f})"
        )
    else:
        bbox = fresh_core
        entities = filter_by_bbox(entities, bbox)
        print(f"  fallback core filter: {raw_count} → {len(entities)}")

    # modelspace 실명·면적·천장고 TEXT (층 INSERT 밖)
    if include_text and bbox is not None:
        labels = collect_modelspace_labels(doc, bbox)
        if labels:
            entities.extend(labels)
            print(f"  modelspace labels: +{len(labels)} TEXT/MTEXT")

    # plan과 맞추기: x' = x - fresh_x0 + plan_x0
    if plan_bb is not None:
        origin = (fresh_core[0] - plan_bb[0], fresh_core[1] - plan_bb[1])
        align_note = "aligned to plan/original"
    elif origin_shift_mode == "bbox" and bbox:
        origin = (bbox[0], bbox[1])
        align_note = "origin=bbox min"
    else:
        origin = (0.0, 0.0)
        align_note = "absolute"

    type_counts = Counter(e.dxftype() for e in entities)
    print(f"  total: {len(entities)}  {dict(type_counts.most_common(8))}")
    print(f"  origin_shift={origin} ({align_note})")

    result: dict = {
        "floor": floor,
        "variant": variant,
        "blocks": block_names,
        "entity_counts": dict(type_counts),
        "bbox_fresh": (
            {"min_x": bbox[0], "min_y": bbox[1], "max_x": bbox[2], "max_y": bbox[3]}
            if bbox
            else None
        ),
        "plan_bbox": (
            {
                "min_x": plan_bb[0],
                "min_y": plan_bb[1],
                "max_x": plan_bb[2],
                "max_y": plan_bb[3],
            }
            if plan_bb
            else None
        ),
        "origin_shift": {"ox": origin[0], "oy": origin[1], "note": align_note},
        "files": {},
    }

    if do_dxf:
        if variant == "original":
            dxf_path = resolve_original_path(out_dir, floor, drawing_id=drawing_id)
        else:
            dxf_path = out_dir / f"floor_{floor}_clean.dxf"
        copied = write_clean_dxf(
            entities,
            dxf_path,
            include_text=include_text,
            origin_shift=origin,
        )
        print(f"  DXF → {dxf_path}  copied={dict(copied)}")
        result["files"]["dxf"] = str(dxf_path)

        # original만 미리보기 PNG 자동 생성
        if do_png and variant == "original":
            try:
                plan_bb_dict = None
                if plan_bb is not None:
                    plan_bb_dict = {
                        "xmin": plan_bb[0],
                        "ymin": plan_bb[1],
                        "xmax": plan_bb[2],
                        "ymax": plan_bb[3],
                    }
                meta = render_floor_original_preview(
                    dxf_path,
                    floor=floor,
                    plan_bbox=plan_bb_dict,
                )
                result["files"]["png"] = meta["files"]["png"]
                result["files"]["png_meta"] = str(
                    dxf_path.with_name("floor_original_meta.json")
                )
                result["preview"] = {
                    "size_m": meta["size_m"],
                    "n_labels": meta["n_labels"],
                }
            except Exception as exc:  # noqa: BLE001
                print(f"  [warn] floor_original.png 실패: {exc}", file=sys.stderr)
                result["preview_error"] = str(exc)

    if do_json:
        stem = out_dir / f"floor_{floor}_{variant}"
        json_path = Path(f"{stem}_geom.json")
        geom_only = [e for e in entities if e.dxftype() in GEOM_TYPES]
        write_json(geom_only, json_path, floor=floor, source_blocks=block_names)
        print(f"  JSON → {json_path}  geom_records≈{len(geom_only)}")
        result["files"]["json"] = str(json_path)

    return result


def extract_sheet_floor(
    doc: Drawing,
    floor: str,
    out_dir: Path,
    sheet,
    *,
    variant: str = "original",
    drawing_id: str | None = None,
    skip_furniture: bool = False,
    do_dxf: bool = True,
    do_json: bool = False,
    do_png: bool = True,
    include_text: bool = True,
    origin_shift_mode: str = "none",
) -> dict:
    """도곽 bbox 안의 modelspace 기하와, 그 안에 놓인 INSERT를 층으로 저장."""
    if variant not in {"clean", "original"}:
        raise ValueError(f"variant 오류: {variant!r}")

    x0, y0, x1, y1 = sheet.bbox
    pad = 50.0
    clip = (x0 - pad, y0 - pad, x1 + pad, y1 + pad)
    print(f"\n== {floor} (sheet:{sheet.title}) ==")
    print(f"  frame bbox: ({x0:.0f},{y0:.0f})-({x1:.0f},{y1:.0f})")

    entities: list[DXFEntity] = []
    used_inserts: set[int] = set()

    def absorb_insert(ins: Insert) -> None:
        if id(ins) in used_inserts:
            return
        name = ins.dxf.name
        if skip_furniture and is_furniture(name):
            return
        used_inserts.add(id(ins))
        ents = explode_insert(ins, skip_furniture=skip_furniture)
        kept = filter_by_bbox(ents, clip)
        print(f"  exploded {name}: {len(ents)} → {len(kept)} inside frame")
        entities.extend(kept)

    for entity in doc.modelspace():
        if entity.dxftype() == "INSERT":
            try:
                ix, iy = float(entity.dxf.insert.x), float(entity.dxf.insert.y)
            except Exception:  # noqa: BLE001
                continue
            if clip[0] <= ix <= clip[2] and clip[1] <= iy <= clip[3]:
                absorb_insert(entity)
            continue
        center = _centroid(entity)
        if center and clip[0] <= center[0] <= clip[2] and clip[1] <= center[1] <= clip[3]:
            entities.append(entity)

    line_count = sum(1 for e in entities if e.dxftype() == "LINE")
    if line_count < 30:
        try:
            from ezdxf import bbox as ezbbox
        except Exception:  # noqa: BLE001
            ezbbox = None
        if ezbbox is not None:
            cache = ezbbox.Cache()
            for entity in doc.modelspace().query("INSERT"):
                if id(entity) in used_inserts:
                    continue
                try:
                    ext = ezbbox.extents([entity], cache=cache)
                except Exception:  # noqa: BLE001
                    continue
                if not ext.has_data:
                    continue
                cx = (float(ext.extmin.x) + float(ext.extmax.x)) / 2
                cy = (float(ext.extmin.y) + float(ext.extmax.y)) / 2
                if clip[0] <= cx <= clip[2] and clip[1] <= cy <= clip[3]:
                    absorb_insert(entity)

    if not any(e.dxftype() in GEOM_TYPES for e in entities):
        raise RuntimeError(f"{floor}: 도곽 안에 기하가 없습니다 ({sheet.title})")

    bbox = clip
    plan_bb = _load_plan_core_bbox(out_dir, drawing_id, floor)
    if plan_bb is not None:
        origin = (bbox[0] - plan_bb[0], bbox[1] - plan_bb[1])
        align_note = "aligned to plan/original"
    elif origin_shift_mode == "bbox":
        origin = (bbox[0], bbox[1])
        align_note = "origin=bbox min"
    else:
        origin = (0.0, 0.0)
        align_note = "absolute"

    block_names = [f"sheet:{sheet.title}"]
    type_counts = Counter(e.dxftype() for e in entities)
    print(f"  total: {len(entities)}  {dict(type_counts.most_common(8))}")
    print(f"  origin_shift={origin} ({align_note})")

    result: dict = {
        "floor": floor,
        "variant": variant,
        "layout": "sheet",
        "title": sheet.title,
        "blocks": block_names,
        "entity_counts": dict(type_counts),
        "bbox_fresh": {
            "min_x": bbox[0],
            "min_y": bbox[1],
            "max_x": bbox[2],
            "max_y": bbox[3],
        },
        "origin_shift": {"ox": origin[0], "oy": origin[1], "note": align_note},
        "files": {},
    }

    if do_dxf:
        if variant == "original":
            dxf_path = resolve_original_path(out_dir, floor, drawing_id=drawing_id)
        else:
            dxf_path = out_dir / f"floor_{floor}_clean.dxf"
        copied = write_clean_dxf(
            entities,
            dxf_path,
            include_text=include_text,
            origin_shift=origin,
        )
        print(f"  DXF → {dxf_path}  copied={dict(copied)}")
        result["files"]["dxf"] = str(dxf_path)
        if do_png and variant == "original":
            try:
                plan_bb_dict = None
                if plan_bb is not None:
                    plan_bb_dict = {
                        "xmin": plan_bb[0],
                        "ymin": plan_bb[1],
                        "xmax": plan_bb[2],
                        "ymax": plan_bb[3],
                    }
                meta = render_floor_original_preview(
                    dxf_path,
                    floor=floor,
                    plan_bbox=plan_bb_dict,
                )
                result["files"]["png"] = meta["files"]["png"]
                result["files"]["png_meta"] = str(dxf_path.with_name("floor_original_meta.json"))
                result["preview"] = {
                    "size_m": meta["size_m"],
                    "n_labels": meta["n_labels"],
                }
            except Exception as exc:  # noqa: BLE001
                print(f"  [warn] floor_original.png 실패: {exc}", file=sys.stderr)
                result["preview_error"] = str(exc)

    if do_json:
        stem = out_dir / f"floor_{floor}_{variant}"
        json_path = Path(f"{stem}_geom.json")
        geom_only = [e for e in entities if e.dxftype() in GEOM_TYPES]
        write_json(geom_only, json_path, floor=floor, source_blocks=block_names)
        result["files"]["json"] = str(json_path)

    return result


def default_dxf_path() -> Path:
    """ARTIFACTS_DIR 또는 cwd에서 *.dxf 탐색. 없으면 --dxf 필수."""
    for base in (resolve_artifacts_dir(), Path.cwd()):
        matches = sorted(base.glob("*.dxf"))
        if matches:
            return matches[0]
        raw = base / "raw"
        if raw.is_dir():
            matches = sorted(raw.glob("*.dxf"))
            if matches:
                return matches[0]
    raise FileNotFoundError(
        "입력 DXF를 찾지 못했습니다. --dxf <path> 로 지정하세요."
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="원본 DXF → floors/<F>/floor_original.dxf (+ .png)"
    )
    parser.add_argument("--dxf", type=Path, default=None, help="입력 DXF (기본: ARTIFACTS_DIR/*.dxf)")
    parser.add_argument("--floor", default="5F", help="층 (예: 5F, B1F) 또는 all")
    parser.add_argument(
        "--layout",
        choices=("auto", "block", "sheet"),
        default="auto",
        help="auto: XA-S 블록이 없으면 도곽·층 제목. block: XA-S만. sheet: 도곽만.",
    )
    parser.add_argument(
        "--list-floors",
        action="store_true",
        help="층을 추출하지 않고 구분 결과(JSON)만 출력",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="출력 디렉터리 (기본: $ARTIFACTS_DIR 또는 cwd)",
    )
    parser.add_argument(
        "--drawing-id",
        required=True,
        help="산출 경로용 (floors/<F>/floor_original.dxf)",
    )
    parser.add_argument(
        "--variant",
        choices=("original", "clean"),
        default="original",
        help="기본 original. clean은 레거시(가구 제외, 비권장)",
    )
    parser.add_argument("--keep-furniture", action="store_true", help="(clean) 가구 유지")
    parser.add_argument(
        "--include-extra",
        action="store_true",
        help="(clean) 조경/천장 등 포함",
    )
    parser.add_argument(
        "--origin-shift",
        choices=("none", "bbox"),
        default="none",
        help="plan 없을 때 좌표 이동 (plan 있으면 자동 align)",
    )
    parser.add_argument("--no-dxf", action="store_true")
    parser.add_argument(
        "--no-png",
        action="store_true",
        help="floor_original.png 렌더 생략 (기본은 DXF와 함께 생성)",
    )
    parser.add_argument(
        "--geom-json",
        action="store_true",
        help="디버그용 floor_*_geom.json 생성 (기본 끔)",
    )
    parser.add_argument("--no-text", action="store_true", help="TEXT/MTEXT 제외")
    args = parser.parse_args()

    dxf_path = args.dxf or default_dxf_path()
    out_dir = args.out or resolve_artifacts_dir()

    floor_arg = floor_token(args.floor)
    print(f"loading {dxf_path} ...", flush=True)
    doc = ezdxf.readfile(str(dxf_path))
    print(f"loaded version={doc.dxfversion}", flush=True)

    layout = discover_layout(doc, mode=args.layout)
    floors = layout.floors
    sheets = {s.floor: s for s in layout.sheets}
    print(f"layout: {layout.method}")
    print(f"discovered floors: {floors}")
    for note in layout.warnings:
        print(f"[layout] {note}", file=sys.stderr)
    for sheet in layout.sheets:
        box = sheet.bbox
        print(
            f"  sheet {sheet.floor}: {sheet.title!r}  "
            f"({box[0]:.0f},{box[1]:.0f})-({box[2]:.0f},{box[3]:.0f})"
        )

    if args.list_floors:
        print(json.dumps(layout.as_dict(), ensure_ascii=False, indent=2))
        return 0 if floors else 2

    if not floors:
        print("[error] 층 블록과 도곽·층 제목을 모두 찾지 못했습니다.", file=sys.stderr)
        return 2
    if layout.method == "sheet" and floor_arg != "ALL" and floor_arg not in sheets:
        print(
            f"[error] {floor_arg} 도곽이 없습니다. 발견된 층: {floors}",
            file=sys.stderr,
        )
        return 2

    out_dir.mkdir(parents=True, exist_ok=True)
    targets = floors if floor_arg == "ALL" else [floor_arg]
    if layout.method == "block" and floor_arg != "ALL" and floor_arg not in floors:
        print(f"[warn] {floor_arg}가 평면 블록 목록에 없음. INSERT 패턴으로 재시도.", file=sys.stderr)

    if args.variant == "clean":
        print("[warn] --variant clean 은 레거시입니다. original 사용을 권장합니다.", file=sys.stderr)

    variants = [args.variant]
    clean_kwargs: dict = {}
    if args.keep_furniture:
        clean_kwargs["skip_furniture"] = False
    if args.include_extra:
        clean_kwargs["include_extra"] = True

    summary = []
    for fl in targets:
        for var in variants:
            try:
                kw = dict(clean_kwargs) if var == "clean" else {}
                if layout.method == "sheet":
                    summary.append(
                        extract_sheet_floor(
                            doc,
                            fl,
                            out_dir,
                            sheets[fl],
                            variant=var,
                            drawing_id=args.drawing_id,
                            skip_furniture=bool(kw.get("skip_furniture", var == "clean")),
                            do_dxf=not args.no_dxf,
                            do_json=args.geom_json,
                            do_png=not args.no_png,
                            include_text=not args.no_text,
                            origin_shift_mode=args.origin_shift,
                        )
                    )
                else:
                    summary.append(
                        extract_floor(
                            doc,
                            fl,
                            out_dir,
                            variant=var,
                            drawing_id=args.drawing_id,
                            do_dxf=not args.no_dxf,
                            do_json=args.geom_json,
                            do_png=not args.no_png,
                            include_text=not args.no_text,
                            origin_shift_mode=args.origin_shift,
                            **kw,
                        )
                    )
            except Exception as exc:  # noqa: BLE001
                print(f"[error] {fl}/{var}: {exc}", file=sys.stderr)
                summary.append({"floor": fl, "variant": var, "error": str(exc)})

    meta_path = out_dir / "extract_summary.json"
    meta_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nsummary → {meta_path}")
    return 1 if any("error" in item for item in summary) else 0


if __name__ == "__main__":
    raise SystemExit(main())
