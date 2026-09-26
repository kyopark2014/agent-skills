#!/usr/bin/env python3
"""단일 타일 DXF에서 벽을 검출해 빨간 WALL 레이어 DXF/PNG로 저장.

Usage:
  python detect_walls_tile.py \\
    --dxf $ARTIFACTS_DIR/<id>/floors/12F/parts/R0C0.dxf \\
    --out-dir $ARTIFACTS_DIR/<id>/floors/12F/walls \\
    --tile-id R0C0 --floor 12F
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


def main() -> int:
    p = argparse.ArgumentParser(description="타일 벽 검출 → 빨간 DXF/PNG")
    p.add_argument("--dxf", type=Path, required=True, help="타일 DXF (parts/R0C0.dxf)")
    p.add_argument("--out-dir", type=Path, required=True, help="walls/ 출력 디렉터리")
    p.add_argument("--tile-id", default=None, help="예: R0C0 (미지정 시 dxf stem)")
    p.add_argument("--floor", default="")
    p.add_argument("--min-len-mm", type=float, default=500.0)
    p.add_argument("--thick-min-mm", type=float, default=50.0)
    p.add_argument("--thick-max-mm", type=float, default=420.0)
    p.add_argument("--no-png", action="store_true")
    p.add_argument("--dpi", type=int, default=200)
    p.add_argument("--px-width", type=int, default=2400)
    args = p.parse_args()

    tile_id = args.tile_id or args.dxf.stem
    out_dir = args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"loading {args.dxf} ...", flush=True)
    _, entities = load_tile_entities(args.dxf)
    print(f"  entities={len(entities)}")

    clf = classify_entities(
        entities,
        min_len_mm=args.min_len_mm,
        thick_min_mm=args.thick_min_mm,
        thick_max_mm=args.thick_max_mm,
    )
    print(
        f"  walls: entities={clf['n_wall_entities']} segs={clf['n_wall_segs']} "
        f"/ segs_total={clf['n_segs']} columns_skip={len(clf['skip_column_idxs'])}"
    )

    dxf_out = out_dir / f"{tile_id}_walls.dxf"
    counts = write_walls_dxf(entities, clf, dxf_out)
    print(f"  → {dxf_out}  base={counts['base']} wall={counts['wall']} seg_lines={counts['wall_seg_lines']}")

    png_out = None
    size = None
    if not args.no_png:
        png_out = out_dir / f"{tile_id}_walls.png"
        title = f"{args.floor} {tile_id} WALLS".strip()
        size = render_walls_png(
            entities,
            clf,
            png_out,
            dpi=args.dpi,
            px_width=args.px_width,
            title=title,
        )
        print(f"  → {png_out}  {size[0]}x{size[1]}")

    meta = {
        "floor": args.floor,
        "tile_id": tile_id,
        "source_dxf": str(args.dxf.resolve()),
        "params": {
            "min_len_mm": args.min_len_mm,
            "thick_min_mm": args.thick_min_mm,
            "thick_max_mm": args.thick_max_mm,
        },
        "stats": {
            "n_entities": clf["n_entities"],
            "n_wall_entities": clf["n_wall_entities"],
            "n_wall_segs": clf["n_wall_segs"],
            "n_segs": clf["n_segs"],
            "n_columns_skipped": len(clf["skip_column_idxs"]),
        },
        "files": {
            "dxf": str(dxf_out.resolve()),
            "png": str(png_out.resolve()) if png_out else None,
            "meta": str((out_dir / f"{tile_id}_walls_meta.json").resolve()),
        },
        "png_size": list(size) if size else None,
        "dxf_counts": counts,
    }
    meta_path = out_dir / f"{tile_id}_walls_meta.json"
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  → {meta_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
