#!/usr/bin/env python3
"""층 단위: 타일별 + 층 전체(floor_original) 벽 검출.

층 전체 출력: floors/<F>/floor_wall_original.{dxf,png,meta.json}
타일 출력:     floors/<F>/walls/R*C*_walls.*
(구 floor_walls_overview / walls/floor_wall_original 은 더 이상 생성하지 않음)

Usage:
  python detect_walls_floor.py \\
    --artifacts $ARTIFACTS_DIR/<drawing_id> --floor 12F
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from lib_walls import (  # noqa: E402
    classify_entities,
    load_tile_entities,
    render_walls_png,
    write_walls_dxf,
)


def _detect_one(
    *,
    floor: str,
    tile_id: str,
    src: Path,
    walls_dir: Path,
    min_len_mm: float,
    thick_min_mm: float,
    thick_max_mm: float,
    no_png: bool,
    dpi: int,
    px_width: int,
    title: str,
    out_stem: str | None = None,
    row=None,
    col=None,
    bbox_mm=None,
    size_m=None,
) -> dict:
    stem = out_stem or f"{tile_id}_walls"
    print(f"\n== {floor} {tile_id} ==", flush=True)
    print(f"  source={src}")
    _, entities = load_tile_entities(src)
    clf = classify_entities(
        entities,
        min_len_mm=min_len_mm,
        thick_min_mm=thick_min_mm,
        thick_max_mm=thick_max_mm,
    )
    print(
        f"  walls: entities={clf['n_wall_entities']} segs={clf['n_wall_segs']} "
        f"furn_skip={clf.get('n_furniture_skipped', 0)} "
        f"hatch_skip={clf.get('n_hatch_skipped', 0)} "
        f"cols_skip={len(clf['skip_column_idxs'])}  ents={len(entities)}"
    )
    dxf_out = walls_dir / f"{stem}.dxf"
    counts = write_walls_dxf(entities, clf, dxf_out)
    png_out = None
    size = None
    if not no_png:
        png_out = walls_dir / f"{stem}.png"
        size = render_walls_png(
            entities,
            clf,
            png_out,
            dpi=dpi,
            px_width=px_width,
            title=title,
            bbox_mm=bbox_mm,
        )
        print(f"  → {png_out.name} {size[0]}x{size[1]}")
    meta = {
        "floor": floor,
        "tile_id": tile_id,
        "row": row,
        "col": col,
        "bbox_mm": bbox_mm,
        "size_m": size_m,
        "source_dxf": str(src),
        "stats": {
            "n_entities": clf["n_entities"],
            "n_wall_entities": clf["n_wall_entities"],
            "n_wall_segs": clf["n_wall_segs"],
            "n_columns_skipped": len(clf["skip_column_idxs"]),
            "n_furniture_skipped": clf.get("n_furniture_skipped"),
            "n_hatch_skipped": clf.get("n_hatch_skipped"),
            "entity_wall_ratio": clf.get("entity_wall_ratio"),
            "furniture_box_max_mm": clf.get("furniture_box_max_mm"),
        },
        "files": {
            "dxf": str(dxf_out),
            "png": str(png_out) if png_out else None,
        },
        "dxf_counts": counts,
        "png_size": list(size) if size else None,
    }
    meta_path = walls_dir / f"{stem}_meta.json"
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  → {dxf_out.name}")
    return meta


def _resolve_original_dxf(floor_dir: Path, index: dict) -> Path | None:
    """층 전체 벽 입력: floor_original.dxf (overview 아님)."""
    src = (index.get("source") or {}).get("original_dxf") or (
        index.get("source") or {}
    ).get("dxf")
    if src:
        p = Path(src)
        if p.is_file() and "original" in p.name:
            return p
    cand = floor_dir / "floor_original.dxf"
    return cand if cand.is_file() else None


def main() -> int:
    p = argparse.ArgumentParser(description="층 타일 + floor_original 층 전체 벽 검출")
    p.add_argument(
        "--artifacts",
        type=Path,
        required=True,
        help="$ARTIFACTS_DIR/<drawing_id> (예: …/artifacts/sk_yongin_jiwon)",
    )
    p.add_argument("--floor", required=True, help="예: 12F")
    p.add_argument("--only", default=None, help="특정 타일만 (예: R0C0,R1C0)")
    p.add_argument("--tiles-only", action="store_true", help="타일만 (층 전체 생략)")
    p.add_argument(
        "--overview-only",
        action="store_true",
        help="층 전체(floor_wall_original)만 — 타일 생략 (레거시 플래그명)",
    )
    p.add_argument("--original-only", action="store_true", help="층 전체만 (= --overview-only)")
    p.add_argument("--min-len-mm", type=float, default=500.0)
    p.add_argument("--thick-min-mm", type=float, default=50.0)
    p.add_argument("--thick-max-mm", type=float, default=420.0)
    p.add_argument("--no-png", action="store_true")
    p.add_argument("--dpi", type=int, default=200)
    p.add_argument("--px-width", type=int, default=2400)
    p.add_argument(
        "--overview-px-width",
        type=int,
        default=None,
        help="(레거시) = --floor-px-width",
    )
    p.add_argument(
        "--floor-px-width",
        type=int,
        default=4000,
        help="floor_wall_original.png 가로 픽셀",
    )
    args = p.parse_args()
    floor_only = args.overview_only or args.original_only
    floor_px = (
        args.overview_px_width if args.overview_px_width is not None else args.floor_px_width
    )

    floor_dir = args.artifacts / "floors" / args.floor
    index_path = floor_dir / "floor_parts_index.json"
    if not index_path.is_file():
        raise SystemExit(f"floor_parts_index.json 없음: {index_path}")
    index = json.loads(index_path.read_text(encoding="utf-8"))

    original_dxf = _resolve_original_dxf(floor_dir, index)
    original_png = floor_dir / "floor_original.png"
    original_meta_path = floor_dir / "floor_original_meta.json"
    original_meta = None
    if original_meta_path.is_file():
        try:
            original_meta = json.loads(original_meta_path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            original_meta = None

    print(f"floor={args.floor}")
    print(f"floor_original_dxf={original_dxf}")
    print(f"floor_original_png={original_png if original_png.is_file() else None}")
    print(f"parts={len(index.get('parts') or [])}")

    only = None
    if args.only:
        only = {x.strip() for x in args.only.split(",") if x.strip()}

    walls_dir = floor_dir / "walls"
    walls_dir.mkdir(parents=True, exist_ok=True)

    sum_path = walls_dir / "walls_index.json"
    prev_tiles = []
    if sum_path.is_file() and floor_only:
        try:
            prev_tiles = json.loads(sum_path.read_text(encoding="utf-8")).get("tiles") or []
        except Exception:  # noqa: BLE001
            prev_tiles = []

    results = []
    if not floor_only:
        for part in index.get("parts") or []:
            tid = part["id"]
            if only and tid not in only:
                continue
            src = Path(part["files"]["dxf"])
            if not src.is_file():
                print(f"  SKIP missing {src}", flush=True)
                continue
            results.append(
                _detect_one(
                    floor=args.floor,
                    tile_id=tid,
                    src=src,
                    walls_dir=walls_dir,
                    min_len_mm=args.min_len_mm,
                    thick_min_mm=args.thick_min_mm,
                    thick_max_mm=args.thick_max_mm,
                    no_png=args.no_png,
                    dpi=args.dpi,
                    px_width=args.px_width,
                    title=f"{args.floor} {tid} WALLS",
                    row=part.get("row"),
                    col=part.get("col"),
                    bbox_mm=part.get("bbox_mm"),
                    size_m=part.get("size_m"),
                )
            )
    else:
        results = prev_tiles

    floor_original_walls_meta = None
    if not args.tiles_only:
        if not original_dxf or not original_dxf.is_file():
            print("  WARN: floor_original.dxf 없음 — 층 전체 wall 생략", flush=True)
        else:
            bbox_mm = (original_meta or {}).get("bbox_mm") or index.get("bbox_mm")
            size_m = (original_meta or {}).get("size_m") or {
                "width": (index.get("grid") or {}).get("width_m"),
                "height": (index.get("grid") or {}).get("height_m"),
            }
            # floor_original.png 과 동일 창·해상도 (render_floor_original_preview: 14000@300dpi + pad)
            if args.overview_px_width is None and args.floor_px_width == 4000:
                ps = (original_meta or {}).get("png_size") or {}
                # meta png_size 는 패딩 포함 → 렌더 입력은 -200
                if ps.get("width"):
                    floor_px = max(int(ps["width"]) - 200, 1000)
                else:
                    floor_px = 14000
            floor_dpi = args.dpi if args.dpi != 200 else 300
            w_m = (size_m or {}).get("width")
            h_m = (size_m or {}).get("height")
            if w_m and h_m:
                title = f"{args.floor} FLOOR WALL ORIGINAL  {float(w_m):.2f}×{float(h_m):.2f} m"
            else:
                title = f"{args.floor} FLOOR WALL ORIGINAL"
            floor_original_walls_meta = _detect_one(
                floor=args.floor,
                tile_id="floor_original",
                src=original_dxf,
                walls_dir=floor_dir,  # floors/<F>/ (walls/ 아님)
                min_len_mm=args.min_len_mm,
                thick_min_mm=args.thick_min_mm,
                thick_max_mm=args.thick_max_mm,
                no_png=args.no_png,
                dpi=floor_dpi,
                px_width=floor_px,
                title=title,
                out_stem="floor_wall_original",
                bbox_mm=bbox_mm,
                size_m=size_m,
            )
            print("  → floors/<F>/floor_wall_original.dxf (층 전체 wall, source=floor_original)")
            for legacy_dir, names in (
                (
                    walls_dir,
                    (
                        "floor_walls_overview.dxf",
                        "floor_walls_overview.png",
                        "floor_walls_overview_meta.json",
                        "floor_wall_original.dxf",
                        "floor_wall_original.png",
                        "floor_wall_original_meta.json",
                    ),
                ),
                (
                    floor_dir,
                    (
                        "floor_walls_overview.dxf",
                        "floor_walls_overview.png",
                        "floor_walls_overview_meta.json",
                    ),
                ),
            ):
                for name in names:
                    lp = legacy_dir / name
                    if lp.is_file():
                        lp.unlink()
                        print(f"  removed legacy {lp.relative_to(floor_dir.parent.parent)}")

    summary = {
        "drawing_id": index.get("drawing_id"),
        "floor": args.floor,
        "floor_original_png": str(original_png) if original_png.is_file() else None,
        "floor_wall_original": {
            "dxf": str(floor_dir / "floor_wall_original.dxf"),
            "png": str(floor_dir / "floor_wall_original.png"),
            "meta": floor_original_walls_meta,
            "source": str(original_dxf) if original_dxf else None,
        }
        if floor_original_walls_meta
        else None,
        "n_tiles": len(results),
        "params": {
            "min_len_mm": args.min_len_mm,
            "thick_min_mm": args.thick_min_mm,
            "thick_max_mm": args.thick_max_mm,
        },
        "tiles": results,
        "status": "completed",
    }
    sum_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        f"\n→ {sum_path}  tiles={len(results)}  "
        f"floor_wall_original={'yes' if floor_original_walls_meta else 'no'}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
