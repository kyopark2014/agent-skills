#!/usr/bin/env python3
"""층 단위: floor_original → floor_wall_original 벽 검출.

기본: floors/<F>/floor_wall_original.{dxf,png,_meta.json} 만 생성.
타일(parts) 분할은 기본 워크플로에서 제외. 레거시 parts가 있고
--with-tiles 를 주면 floors/<F>/walls/R*C*_walls.* 도 생성.

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


def _resolve_original_dxf(floor_dir: Path, index: dict | None) -> Path | None:
    """층 전체 벽 입력: floor_original.dxf."""
    if index:
        src = (index.get("source") or {}).get("original_dxf") or (
            index.get("source") or {}
        ).get("dxf")
        if src:
            p = Path(src)
            if p.is_file() and "original" in p.name:
                return p
    cand = floor_dir / "floor_original.dxf"
    return cand if cand.is_file() else None


def _load_parts_index(floor_dir: Path) -> dict | None:
    index_path = floor_dir / "floor_parts_index.json"
    if not index_path.is_file():
        return None
    return json.loads(index_path.read_text(encoding="utf-8"))


def main() -> int:
    p = argparse.ArgumentParser(
        description="층 floor_original → floor_wall_original 벽 검출 (기본)"
    )
    p.add_argument(
        "--artifacts",
        type=Path,
        required=True,
        help="$ARTIFACTS_DIR/<drawing_id> (예: …/artifacts/sk_yongin_jiwon)",
    )
    p.add_argument("--floor", required=True, help="예: 12F")
    p.add_argument(
        "--with-tiles",
        action="store_true",
        help="레거시 parts 타일도 검출 (floor_parts_index.json 필요)",
    )
    p.add_argument("--only", default=None, help="특정 타일만 (--with-tiles 시, 예: R0C0,R1C0)")
    p.add_argument(
        "--tiles-only",
        action="store_true",
        help="레거시: 타일만 (층 전체 생략) — floor_parts_index 필요",
    )
    p.add_argument(
        "--overview-only",
        action="store_true",
        help="(레거시) 층 전체만 — 기본 동작과 동일",
    )
    p.add_argument("--original-only", action="store_true", help="층 전체만 (= 기본)")
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

    # 기본: 층만. --with-tiles → 층+타일. --tiles-only → 타일만.
    # --overview-only / --original-only → 층만 (기본과 동일, 타일 끔).
    if args.tiles_only:
        do_tiles, run_floor = True, False
    elif args.overview_only or args.original_only:
        do_tiles, run_floor = False, True
    elif args.with_tiles:
        do_tiles, run_floor = True, True
    else:
        do_tiles, run_floor = False, True

    floor_px = (
        args.overview_px_width if args.overview_px_width is not None else args.floor_px_width
    )

    floor_dir = args.artifacts / "floors" / args.floor
    if not floor_dir.is_dir():
        raise SystemExit(f"층 폴더 없음: {floor_dir}")

    index = _load_parts_index(floor_dir)
    original_dxf = _resolve_original_dxf(floor_dir, index)
    if run_floor and (not original_dxf or not original_dxf.is_file()):
        raise SystemExit(
            f"floor_original.dxf 없음: {floor_dir / 'floor_original.dxf'}\n"
            f"  먼저 drawing-devider extract_2d.py --floor {args.floor}"
        )

    original_png = floor_dir / "floor_original.png"
    original_meta_path = floor_dir / "floor_original_meta.json"
    original_meta = None
    if original_meta_path.is_file():
        try:
            original_meta = json.loads(original_meta_path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            original_meta = None

    drawing_id = (index or {}).get("drawing_id") or args.artifacts.name

    print(f"floor={args.floor}")
    print(f"floor_original_dxf={original_dxf}")
    print(f"floor_original_png={original_png if original_png.is_file() else None}")
    mode = "floor+tiles" if (run_floor and do_tiles) else ("tiles" if do_tiles else "floor")
    print(f"mode={mode}")

    only = None
    if args.only:
        only = {x.strip() for x in args.only.split(",") if x.strip()}

    walls_dir = floor_dir / "walls"
    results: list = []

    if do_tiles:
        if not index or not (index.get("parts") or []):
            raise SystemExit(
                f"타일 검출 요청(--with-tiles/--tiles-only)이지만 "
                f"floor_parts_index.json / parts 없음: {floor_dir}"
            )
        walls_dir.mkdir(parents=True, exist_ok=True)
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

    floor_original_walls_meta = None
    if run_floor:
        assert original_dxf is not None
        bbox_mm = (original_meta or {}).get("bbox_mm") or (index or {}).get("bbox_mm")
        size_m = (original_meta or {}).get("size_m") or {
            "width": ((index or {}).get("grid") or {}).get("width_m"),
            "height": ((index or {}).get("grid") or {}).get("height_m"),
        }
        if args.overview_px_width is None and args.floor_px_width == 4000:
            ps = (original_meta or {}).get("png_size") or {}
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
            walls_dir=floor_dir,
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
        if walls_dir.is_dir():
            for name in (
                "floor_walls_overview.dxf",
                "floor_walls_overview.png",
                "floor_walls_overview_meta.json",
                "floor_wall_original.dxf",
                "floor_wall_original.png",
                "floor_wall_original_meta.json",
            ):
                lp = walls_dir / name
                if lp.is_file():
                    lp.unlink()
                    print(f"  removed legacy {lp}")
        for name in (
            "floor_walls_overview.dxf",
            "floor_walls_overview.png",
            "floor_walls_overview_meta.json",
        ):
            lp = floor_dir / name
            if lp.is_file():
                lp.unlink()
                print(f"  removed legacy {lp}")

    summary = {
        "drawing_id": drawing_id,
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
    if results:
        walls_dir.mkdir(parents=True, exist_ok=True)
        sum_path = walls_dir / "walls_index.json"
    else:
        sum_path = floor_dir / "floor_wall_index.json"
    sum_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        f"\n→ {sum_path}  tiles={len(results)}  "
        f"floor_wall_original={'yes' if floor_original_walls_meta else 'no'}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
