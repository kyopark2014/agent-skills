#!/usr/bin/env python3
"""floor_wall_original.png 에서 Vision 검수용 이미지 준비.

parts(floor_parts_index)가 있으면 타일 크롭을 만들고,
없으면 층 전체 PNG를 llm_review/ 에 복사한다.
"""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

from PIL import Image

Image.MAX_IMAGE_PIXELS = None


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

    parts_path = floor_dir / "floor_parts_index.json"
    parts = []
    if parts_path.is_file():
        parts = json.loads(parts_path.read_text()).get("parts") or []

    if not parts:
        dest = out / "floor_wall_full.png"
        shutil.copy2(png, dest)
        print(f"parts 없음 — 층 전체 복사 → {dest}")
        print(f"→ {out}")
        return

    bbox = meta["bbox_mm"]
    xmin, ymin, xmax, ymax = _render_window(bbox)
    img = Image.open(png)
    W, H = img.size

    def mm_to_px(x: float, y: float) -> tuple[float, float]:
        px = (x - xmin) / (xmax - xmin) * W
        py = (ymax - y) / (ymax - ymin) * H
        return px, py

    for p in parts:
        tid = p["id"]
        b = p["bbox_mm"]
        x0, y0 = mm_to_px(b["xmin"], b["ymax"])
        x1, y1 = mm_to_px(b["xmax"], b["ymin"])
        left, right = int(max(0, min(x0, x1))), int(min(W, max(x0, x1)))
        top, bottom = int(max(0, min(y0, y1))), int(min(H, max(y0, y1)))
        crop = img.crop((left, top, right, bottom))
        crop.save(out / f"{tid}_wall_crop.png")
        print(f"  {tid} {crop.size}")

    print(f"→ {out}")


if __name__ == "__main__":
    main()
