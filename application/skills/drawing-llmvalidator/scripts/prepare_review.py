#!/usr/bin/env python3
"""floor_wall_original.png 에서 Vision 검수용 이미지를 준비한다.

한 변이 5000px를 넘는 이미지는 겹침 12% 격자로 나눈다. 5000×5000 이하는
그대로 둔다. 층 전체를 한 장으로 복사하지 않는다.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

from PIL import Image

Image.MAX_IMAGE_PIXELS = None

MAX_TILE_SIDE = 5000
OVERLAP = 0.12


def _render_window(bbox: dict[str, float]) -> tuple[float, float, float, float]:
    cx0, cy0 = float(bbox["xmin"]), float(bbox["ymin"])
    cx1, cy1 = float(bbox["xmax"]), float(bbox["ymax"])
    span_y0 = max(cy1 - cy0, 1.0)
    tick = max(span_y0 * 0.025, 1000.0)
    xmin = cx0 - max(tick * 0.8, 1500)
    ymin = cy0 - max(tick * 2.5, 4000)
    xmax = cx1 + max(tick * 2.0, 3000)
    ymax = cy1 + max(tick * 6.5, 9000)
    return xmin, ymin, xmax, ymax


def _tile_boxes(
    width: int,
    height: int,
    cols: int,
    rows: int,
    overlap: float,
) -> list[tuple[int, int, int, int, int, int]]:
    step_w = math.ceil(width / cols)
    step_h = math.ceil(height / rows)
    pad_w = int(step_w * overlap)
    pad_h = int(step_h * overlap)
    tiles: list[tuple[int, int, int, int, int, int]] = []
    for r in range(rows):
        for c in range(cols):
            left = max(0, c * step_w - (pad_w if c else 0))
            top = max(0, r * step_h - (pad_h if r else 0))
            right = min(width, (c + 1) * step_w + (pad_w if c + 1 < cols else 0))
            bottom = min(height, (r + 1) * step_h + (pad_h if r + 1 < rows else 0))
            if right - left >= 8 and bottom - top >= 8:
                tiles.append((r, c, left, top, right, bottom))
    return tiles


def plan_tiles(
    width: int,
    height: int,
    *,
    max_side: int = MAX_TILE_SIDE,
    overlap: float = OVERLAP,
) -> list[tuple[int, int, int, int, int, int]]:
    """Return (row, col, left, top, right, bottom) crops of at most max_side."""
    if width <= 0 or height <= 0:
        return []
    if width <= max_side and height <= max_side:
        return [(0, 0, 0, 0, width, height)]

    cols, rows = 1, 1
    while True:
        boxes = _tile_boxes(width, height, cols, rows, overlap)
        widths = [right - left for _r, _c, left, _top, right, _bottom in boxes]
        heights = [bottom - top for _r, _c, _left, top, _right, bottom in boxes]
        too_w = any(side > max_side for side in widths)
        too_h = any(side > max_side for side in heights)
        if not too_w and not too_h:
            break
        if too_w and (not too_h or max(widths) >= max(heights)):
            cols += 1
        else:
            rows += 1
        if cols > width and rows > height:
            break
    return _tile_boxes(width, height, cols, rows, overlap) or [(0, 0, 0, 0, width, height)]


def _px_to_mm(
    px: float,
    py: float,
    image_w: int,
    image_h: int,
    window: tuple[float, float, float, float],
) -> tuple[float, float]:
    xmin, ymin, xmax, ymax = window
    x = xmin + (px / image_w) * (xmax - xmin)
    y = ymax - (py / image_h) * (ymax - ymin)
    return x, y


def _bbox_mm(
    left: int,
    top: int,
    right: int,
    bottom: int,
    image_w: int,
    image_h: int,
    window: tuple[float, float, float, float],
) -> dict[str, float]:
    x0, y1 = _px_to_mm(left, top, image_w, image_h, window)
    x1, y0 = _px_to_mm(right, bottom, image_w, image_h, window)
    return {
        "xmin": round(min(x0, x1), 3),
        "ymin": round(min(y0, y1), 3),
        "xmax": round(max(x0, x1), 3),
        "ymax": round(max(y0, y1), 3),
    }


def _write_crop(
    img: Image.Image,
    out: Path,
    name: str,
    box: tuple[int, int, int, int],
    image_size: tuple[int, int],
    window: tuple[float, float, float, float],
    origin: tuple[int, int] = (0, 0),
) -> dict:
    left, top, right, bottom = box
    ox, oy = origin
    abs_box = (ox + left, oy + top, ox + right, oy + bottom)
    crop = img.crop(abs_box)
    path = out / name
    crop.save(path)
    width, height = image_size
    record = {
        "file": name,
        "width": crop.size[0],
        "height": crop.size[1],
        "bbox_px": list(abs_box),
        "bbox_mm": _bbox_mm(*abs_box, width, height, window),
    }
    print(f"  {name} {crop.size[0]}x{crop.size[1]}")
    return record


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--artifacts", required=True)
    ap.add_argument("--floor", required=True)
    args = ap.parse_args()

    floor_dir = Path(args.artifacts) / "floors" / args.floor
    png = floor_dir / "floor_wall_original.png"
    meta_path = floor_dir / "floor_wall_original_meta.json"
    if not png.is_file():
        raise SystemExit(f"없음: {png}")
    if not meta_path.is_file():
        raise SystemExit(f"없음: {meta_path}")

    meta = json.loads(meta_path.read_text())
    out = floor_dir / "llm_review"
    out.mkdir(parents=True, exist_ok=True)
    for stale in out.glob("*.png"):
        stale.unlink()
    old_review = out / "review.json"
    if old_review.is_file():
        old_review.unlink()

    window = _render_window(meta["bbox_mm"])
    img = Image.open(png)
    img.load()
    width, height = img.size

    parts_path = floor_dir / "floor_parts_index.json"
    parts = []
    if parts_path.is_file():
        parts = json.loads(parts_path.read_text()).get("parts") or []

    tiles: list[dict] = []
    if not parts:
        for row, col, left, top, right, bottom in plan_tiles(width, height):
            tiles.append(
                _write_crop(
                    img,
                    out,
                    f"R{row}C{col}.png",
                    (left, top, right, bottom),
                    (width, height),
                    window,
                )
            )
    else:
        xmin, ymin, xmax, ymax = window

        def mm_to_px(x: float, y: float) -> tuple[float, float]:
            px = (x - xmin) / (xmax - xmin) * width
            py = (ymax - y) / (ymax - ymin) * height
            return px, py

        for part in parts:
            tid = part["id"]
            bounds = part["bbox_mm"]
            x0, y0 = mm_to_px(bounds["xmin"], bounds["ymax"])
            x1, y1 = mm_to_px(bounds["xmax"], bounds["ymin"])
            left, right = int(max(0, min(x0, x1))), int(min(width, max(x0, x1)))
            top, bottom = int(max(0, min(y0, y1))), int(min(height, max(y0, y1)))
            crop_w, crop_h = right - left, bottom - top
            pieces = plan_tiles(crop_w, crop_h)
            for row, col, cl, ct, cr, cb in pieces:
                name = f"{tid}_wall_crop.png" if len(pieces) == 1 else f"{tid}_R{row}C{col}.png"
                tiles.append(
                    _write_crop(
                        img,
                        out,
                        name,
                        (cl, ct, cr, cb),
                        (width, height),
                        window,
                        origin=(left, top),
                    )
                )

    manifest = {
        "max_side": MAX_TILE_SIDE,
        "overlap": OVERLAP,
        "image": {"width": width, "height": height, "file": png.name},
        "tiles": tiles,
    }
    (out / "tiles.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"tiles: {len(tiles)} max_side: {MAX_TILE_SIDE}")
    print(f"→ {out}")


if __name__ == "__main__":
    main()
