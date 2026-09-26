#!/usr/bin/env python3
"""Vision/기하 보정: floor_wall_original → floor_wall_validated.

Usage:
  python correct_walls_floor.py --artifacts $ART --floor 5F
  python correct_walls_floor.py --artifacts $ART --floor 5F --review llm_review/review.json
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import ezdxf  # noqa: E402

from lib_llm_correct import (  # noqa: E402
    apply_corrections,
    load_review,
    render_wall_dxf_png,
)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--artifacts", required=True)
    ap.add_argument("--floor", required=True)
    ap.add_argument(
        "--review",
        default=None,
        help="Vision review JSON (demote_bboxes / promote_bboxes)",
    )
    ap.add_argument("--no-gap-promote", action="store_true")
    ap.add_argument(
        "--short-demote",
        action="store_true",
        help="긴 벽 런에 안 붙은 짧은 WALL 강등 (복도 조각 손상 가능 — 기본 OFF, 복도 이중선은 보호)",
    )
    ap.add_argument("--no-pack-demote", action="store_true")
    ap.add_argument(
        "--no-corridor-promote",
        action="store_true",
        help="긴 이중선 BASE(복도 양측 등) 자동 승격 끄기",
    )
    ap.add_argument("--no-png", action="store_true")
    args = ap.parse_args()

    floor_dir = Path(args.artifacts) / "floors" / args.floor
    src_dxf = floor_dir / "floor_wall_original.dxf"
    src_meta = floor_dir / "floor_wall_original_meta.json"
    out_dxf = floor_dir / "floor_wall_validated.dxf"
    out_png = floor_dir / "floor_wall_validated.png"
    out_meta = floor_dir / "floor_wall_validated_meta.json"
    if not src_dxf.is_file():
        raise SystemExit(f"missing {src_dxf}")

    review_path = Path(args.review) if args.review else floor_dir / "llm_review" / "review.json"
    review = load_review(review_path if review_path.is_file() else None)

    print(f"load {src_dxf}")
    doc = ezdxf.readfile(str(src_dxf))
    stats = apply_corrections(
        doc,
        review=review,
        do_gap_promote=not args.no_gap_promote,
        do_short_demote=bool(args.short_demote),
        do_pack_demote=not args.no_pack_demote,
        do_corridor_promote=not args.no_corridor_promote,
    )
    print(
        f"  demoted={stats['n_demoted']} promoted={stats['n_promoted']} "
        f"corridor_protected={stats.get('n_corridor_protected', 0)} "
        f"wall_after={stats['n_wall_after']}"
    )
    doc.saveas(str(out_dxf))
    print(f"saved {out_dxf}")

    meta: dict = {}
    if src_meta.is_file():
        meta = json.loads(src_meta.read_text(encoding="utf-8"))

    png_size = None
    if not args.no_png:
        bbox = meta.get("bbox_mm")
        title = f"{args.floor} FLOOR WALL VALIDATED"
        if bbox and meta.get("size_m"):
            title += f" {meta['size_m']['width']:.2f} × {meta['size_m']['height']:.2f} m"
        print("render png…")
        png_size = render_wall_dxf_png(
            out_dxf,
            out_png,
            bbox_mm=bbox,
            title=title,
            px_width=int((meta.get("png_size") or [14200, 6712])[0]),
        )
        print(f"  → {out_png.name} {png_size}")

    out = {
        "floor": args.floor,
        "source_dxf": str(src_dxf),
        "tile_id": "floor_wall_validated",
        "bbox_mm": meta.get("bbox_mm"),
        "size_m": meta.get("size_m"),
        "files": {
            "dxf": str(out_dxf),
            "png": str(out_png) if not args.no_png else None,
        },
        "llm_validate": {
            **stats,
            "review_path": str(review_path) if review_path.is_file() else None,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        },
    }
    if png_size:
        out["png_size"] = list(png_size)
    out_meta.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")

    review_dir = floor_dir / "llm_review"
    review_dir.mkdir(parents=True, exist_ok=True)
    corr_path = review_dir / "corrections.json"
    corr_path.write_text(
        json.dumps(
            {
                "floor": args.floor,
                **stats,
                "source_dxf": str(src_dxf),
                "dxf": str(out_dxf),
                "png": str(out_png),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"→ {corr_path}")


if __name__ == "__main__":
    main()
