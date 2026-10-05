#!/usr/bin/env python3
"""floor_wall_validated.dxf 의 실명 라벨마다 벽 안쪽 면을 잡아
floor_label_detected.dxf / .png / .json 으로 쓴다.

면적 계산은 drawing-roomevaluator 와 같다.
DXF 는 검증 도면을 복사한 뒤 라벨마다 레이어를 더한다.
그 레이어의 HATCH 면적이 그 라벨의 면적이고, 이름과 면적 문자가 같이 있다.
PNG 는 그 DXF 를 검증 도면과 같은 렌더러로 그린 것이다.
"""

from __future__ import annotations

import argparse
import colorsys
import json
import re
import sys
import time
from pathlib import Path

import ezdxf
from ezdxf.colors import float2transparency

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
from lib_llm_correct import render_wall_dxf_png  # noqa: E402

_LAYER_BAD = set('<>/\\":;?*=`')
OUT_STEM = "floor_label_detected"
# 객실 안 집기 표기. 실이 아니므로 면적을 잡지 않는다.
_FIXTURE_NAMES = (
    "미니바",
    "미비바",
    "옷장",
    "신발장",
    "화분",
    "화장대",
    "월풀욕조",
    "욕조",
    "(장애인)",
)
_FIXTURE_TAIL = re.compile(r"^(?:[#＃]?\d+|[（(]\d+[）)])?$")


def is_fixture_label(text: str) -> bool:
    """집기 이름, 또는 그 뒤에 번호만 붙은 표기."""
    name = room.norm_name(text)
    return any(
        name == fixture or (name.startswith(fixture) and _FIXTURE_TAIL.fullmatch(name[len(fixture) :]))
        for fixture in _FIXTURE_NAMES
    )


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


def _face_key(pts: list[tuple[float, float]]) -> tuple[tuple[int, int], ...]:
    return tuple((round(x), round(y)) for x, y in pts)


def _add_label_tag(msp, layer: str, rgb: tuple[int, int, int], inst: dict) -> None:
    """실명과 면적을 흰 판 위에 올린다. PNG 꼬리표와 같은 두 줄이다."""
    name = inst["text"]
    area = f"{inst['area_m2']:.2f} ㎡"
    chars = max(len(name), len(area), 1)
    room_w = max(inst["width_m"], 0.4) * 1000.0
    room_h = max(inst["height_m"], 0.4) * 1000.0
    height = min(420.0, room_w * 0.82 / (chars * 1.02 + 1.2), room_h * 0.16)
    height = max(140.0, height)
    pad_x = height * 0.55
    pad_y = height * 0.42
    gap = height * 0.30
    line2 = height * 0.82
    box_w = chars * height * 1.02 + pad_x * 2
    box_h = pad_y * 2 + height + gap + line2
    x0 = inst["x"] - box_w / 2.0
    y0 = inst["y"] - box_h / 2.0
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
        height=height,
        dxfattribs={"layer": layer, "insert": (x0 + pad_x, y0 + pad_y + line2 + gap)},
    )
    text.rgb = (20, 20, 20)
    area_text = msp.add_text(
        area,
        height=line2,
        dxfattribs={"layer": layer, "insert": (x0 + pad_x, y0 + pad_y)},
    )
    area_text.rgb = (20, 20, 20)


def write_dxf(doc, path: Path, labels: list[dict]) -> None:
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
            _add_label_tag(msp, name, (red, green, blue), inst)
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
    for index, (x, y, text) in enumerate(seeds, start=1):
        try:
            found, reason = detect_one(msp, boxes, segs, x, y, door=door)
        except Exception as exc:  # noqa: BLE001
            found, reason = None, str(exc) or exc.__class__.__name__
        if found is None:
            skipped.append({"text": text, "x": round(x, 1), "y": round(y, 1), "reason": reason})
            print(f"[{index}/{len(seeds)}] skip {text}: {reason}", flush=True)
            continue
        found.update({"text": text, "x": x, "y": y, "name": room.norm_name(text)})
        detected.append(found)
        print(f"[{index}/{len(seeds)}] {text}  {found['area_m2']:.2f} m2", flush=True)

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
    write_dxf(doc, dxf_out, [{**item, "instances": [pair[0] for pair in item["instances"]]} for item in labels])
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
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
            "labels": "Korean room names, or Latin names with 2+ letters and a digit; stacked lines within 1.8 text heights are joined top to bottom; fixture callouts such as 미니바, 옷장, 신발장, 화분, 화장대, 월풀욕조, 욕조, (장애인) are excluded",
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
