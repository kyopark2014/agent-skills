#!/usr/bin/env python3
"""floor_wall_validated.dxf 의 실명 라벨마다 벽 안쪽 면을 잡아
floor_label_detected.dxf / .png / .json 으로 쓴다.

면적 계산은 drawing-roomevaluator 와 같다.
DXF 는 검증 도면을 복사한 뒤 라벨마다 레이어를 더한다.
그 레이어의 HATCH 면적이 그 라벨의 면적이고, 이름과 면적 문자가 같이 있다.
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
from PIL import Image, ImageDraw

_SKILLS_DIR = Path(__file__).resolve().parents[2]
if str(_SKILLS_DIR) not in sys.path:
    sys.path.insert(0, str(_SKILLS_DIR))

from lib_korean_dxf import apply_korean_text  # noqa: E402

ROOM_SCRIPTS = Path(__file__).resolve().parents[2] / "drawing-roomevaluator" / "scripts"
sys.path.insert(0, str(ROOM_SCRIPTS))
import evaluate_room as room  # noqa: E402

Image.MAX_IMAGE_PIXELS = None

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


def detect_one(msp, boxes, segs, x: float, y: float) -> tuple[dict | None, str | None]:
    """라벨 좌표를 둘러싼 벽 안쪽 면. 실패하면 (None, 이유)."""
    pad = 12000.0
    face = None
    x_axes: list[float] = []
    y_axes: list[float] = []
    while pad <= 50000:
        h_final, v_final, dsegs, x_axes, y_axes = room.bridged_runs(segs, (x, y), pad + 2000)
        lines = room._cap_wall_ends(
            room._snap_endpoints(room._lines_from_runs(h_final, v_final, dsegs), room.JOIN_MM),
            room.WALL_CAP_MM,
        )
        lines = lines + room._door_thresholds(msp, lines, (x, y), pad + 2000)
        found = room._face_at(lines, x, y)
        if found is not None and not room._touches_window(found, (x, y), pad + 2000):
            face = found
            break
        pad += 8000
    else:
        return None, "벽이 실을 닫지 않습니다"
    if face is None:
        return None, "벽이 실을 닫지 않습니다"
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


def _map_px(pts, to_px, left: float, top: float) -> list[tuple[float, float]]:
    out = []
    for x, y in pts:
        c, r = to_px(x, y)
        out.append((c - left, r - top))
    return out


def _paint_face(base: Image.Image, to_px, pts, holes, rgb: tuple[int, int, int], alpha: int = 110) -> None:
    xs: list[float] = []
    ys: list[float] = []
    for x, y in list(pts) + [p for hole in holes for p in hole]:
        c, r = to_px(x, y)
        xs.append(c)
        ys.append(r)
    if not xs:
        return
    left = max(0, int(math.floor(min(xs))) - 2)
    top = max(0, int(math.floor(min(ys))) - 2)
    right = min(base.width, int(math.ceil(max(xs))) + 3)
    bottom = min(base.height, int(math.ceil(max(ys))) + 3)
    if right - left < 2 or bottom - top < 2:
        return
    mask = Image.new("L", (right - left, bottom - top), 0)
    mask_draw = ImageDraw.Draw(mask)
    outer = _map_px(pts, to_px, left, top)
    if len(outer) >= 3:
        mask_draw.polygon(outer, fill=alpha)
    for hole in holes:
        mapped = _map_px(hole, to_px, left, top)
        if len(mapped) >= 3:
            mask_draw.polygon(mapped, fill=0)
    chip = Image.new("RGBA", mask.size, (*rgb, 0))
    chip.putalpha(mask)
    base.paste(chip, (left, top), chip)


def label_font_px(image_height: int, px_per_m: float, shorts_m: list[float]) -> int:
    """실이 여러 곳이면 가운데 실의 짧은 변에 맞춘다.

    그렇게 맞춘 크기가 성북동·원광대 도면에서 읽기 좋은 크기였다.
    짧은 변이 1.5 m 이상인 실이 없으면, 시트 전체에서 읽히도록 이미지 높이에 맞춘다.
    """
    substantial = sorted(side for side in shorts_m if side >= 1.5)
    if substantial:
        median = substantial[len(substantial) // 2]
        size = median * px_per_m * 0.115
        size = min(max(size, px_per_m * 0.32), px_per_m * 0.55)
    else:
        size = max(px_per_m * 0.42, image_height * 0.011)
        size = min(size, max(px_per_m * 1.1, 22.0))
    return max(22, int(size))


def _tag(draw: ImageDraw.ImageDraw, x: float, y: float, lines: list[str], font, rgb) -> None:
    widths = []
    heights = []
    for line in lines:
        box = draw.textbbox((0, 0), line, font=font)
        widths.append(box[2] - box[0])
        heights.append(box[3] - box[1])
    box_w = max(widths) + 16
    box_h = sum(heights) + 8 * len(lines) + 10
    left = x
    top = y - box_h - 6
    draw.rounded_rectangle(
        (left, top, left + box_w, top + box_h),
        radius=6,
        fill=(255, 255, 255, 220),
        outline=(*rgb, 255),
        width=2,
    )
    cursor = top + 6
    for i, line in enumerate(lines):
        draw.text((left + 8, cursor), line, font=font, fill=(20, 20, 20, 255))
        cursor += heights[i] + 6


def write_png(png_path: Path, meta: dict, labels: list[dict], out_path: Path) -> None:
    base = Image.open(png_path).convert("RGBA")
    to_px = room.png_transform(meta, base.size)
    for item in labels:
        for inst in item["instances"]:
            _paint_face(base, to_px, inst["pts"], inst["holes"], tuple(item["color"]))
    draw = ImageDraw.Draw(base)
    for item in labels:
        rgb = tuple(item["color"])
        for inst in item["instances"]:
            outer = []
            for x, y in inst["pts"]:
                outer.append(to_px(x, y))
            if len(outer) >= 2:
                draw.line(outer + [outer[0]], fill=(*rgb, 255), width=3)
            for hole in inst["holes"]:
                mapped = [to_px(x, y) for x, y in hole]
                if len(mapped) >= 2:
                    draw.line(mapped + [mapped[0]], fill=(*rgb, 255), width=2)
    sample = abs(to_px(1000.0, 0.0)[0] - to_px(0.0, 0.0)[0])
    shorts = [
        min(inst["width_m"], inst["height_m"])
        for item in labels
        for inst in item["instances"]
    ]
    font_px = label_font_px(base.height, sample, shorts)
    font = room._font(font_px)
    for item in labels:
        for inst in item["instances"]:
            c, r = to_px(inst["x"], inst["y"])
            _tag(
                draw,
                c,
                r,
                [inst["text"], f"{inst['area_m2']:.2f} ㎡"],
                font,
                tuple(item["color"]),
            )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    base.convert("RGB").save(out_path)


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
        "polygon_mm": [[round(x, 1), round(y, 1)] for x, y in inst["pts"]],
    }
    if shared_with:
        row["shared_with"] = shared_with
    return row


def detect(dxf_path: Path, meta_path: Path, png_path: Path) -> dict:
    started = time.time()
    doc = ezdxf.readfile(str(dxf_path))
    msp = doc.modelspace()
    seeds = collect_labels(msp)
    print(f"labels: {len(seeds)}", flush=True)
    boxes = room.column_boxes(msp)
    segs = room.wall_segments(msp, boxes)
    detected: list[dict] = []
    skipped: list[dict] = []
    for index, (x, y, text) in enumerate(seeds, start=1):
        try:
            found, reason = detect_one(msp, boxes, segs, x, y)
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
    write_png(png_path, meta, [{**item, "instances": [pair[0] for pair in item["instances"]]} for item in labels], png_out)

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
            "font": "tag text tracks the median room short side; with no room at least 1.5 m across, it tracks 1.1% of the sheet height",
            "boundary": "inner face of WALL, WINDOW, COLUMN, and DOOR lines",
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
    parser.add_argument("--png", type=Path, required=True)
    args = parser.parse_args()
    for path in (args.dxf, args.meta, args.png):
        if not path.is_file():
            raise SystemExit(f"파일이 없습니다: {path}")
    info = detect(args.dxf, args.meta, args.png)
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
