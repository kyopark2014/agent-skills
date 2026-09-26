#!/usr/bin/env python3
"""(레거시·선택) structure.json → 60×60 m 분할 계획 (split_plan.md / .json).

기본 워크플로는 층 단위 floor_original 만 사용한다.
사용자가 타일 분할을 명시한 경우에만 이 스크립트를 실행한다.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from lib_split import assert_tile_limit, grid_for_span, iter_tile_boxes, write_json, write_text  # noqa: E402


def main() -> int:
    p = argparse.ArgumentParser(description="도면 분할 계획")
    p.add_argument("--artifacts", type=Path, required=True)
    p.add_argument("--max-tile-m", type=float, default=60.0)
    p.add_argument("--overlap-m", type=float, default=1.0)
    p.add_argument(
        "--pilot-floor",
        default=None,
        help="먼저 진행할 층 (미지정 시 목록 첫 층)",
    )
    args = p.parse_args()

    structure_path = args.artifacts / "structure.json"
    if not structure_path.is_file():
        raise SystemExit(f"structure.json 없음: {structure_path}")
    structure = json.loads(structure_path.read_text(encoding="utf-8"))
    drawing_id = structure["drawing_id"]
    floors = structure.get("floors") or {}
    if not floors:
        raise SystemExit("structure.json 에 floors 없음 — analyze_drawing 먼저 실행")

    overlap_mm = args.overlap_m * 1000.0
    plan_floors: dict = {}
    lines = [
        f"# 분할 계획 — {drawing_id}",
        "",
        f"- max_tile_m: **{args.max_tile_m}**",
        f"- overlap_m: **{args.overlap_m}**",
        "- 모드: **2D 그리드** (한 변이 60 m를 넘는 strip 금지)",
        "",
        "| 층 | 폭(m) | 깊이(m) | nx | ny | N | tile(m) |",
        "|----|-------|---------|----|----|---|---------|",
    ]

    for fl, info in sorted(
        floors.items(),
        key=lambda x: int(x[0][:-1]) if x[0][:-1].isdigit() else 0,
    ):
        span = info.get("span")
        if not span:
            lines.append(f"| {fl} | - | - | - | - | - | span 없음 |")
            continue
        w, h = float(span["width_m"]), float(span["height_m"])
        grid = grid_for_span(w, h, max_tile_m=args.max_tile_m, overlap_m=args.overlap_m)
        bb = span["bbox_mm"]
        tiles = iter_tile_boxes(
            bb["xmin"],
            bb["ymin"],
            bb["xmax"],
            bb["ymax"],
            nx=int(grid["nx"]),
            ny=int(grid["ny"]),
            overlap_mm=overlap_mm,
        )
        assert_tile_limit(tiles, max_tile_m=args.max_tile_m)
        plan_floors[fl] = {
            "source_original": info.get("path")
            or str(args.artifacts / "floors" / fl / "floor_original.dxf"),
            "span": span,
            "grid": grid,
            "overlap_m": args.overlap_m,
            "tiles": tiles,
        }
        lines.append(
            f"| {fl} | {w:.2f} | {h:.2f} | {grid['nx']} | {grid['ny']} | "
            f"{grid['n_tiles']} | {grid['tile_w_m']:.2f}×{grid['tile_h_m']:.2f} "
            f"(≤{grid['worst_w_m']:.2f}×{grid['worst_h_m']:.2f} w/ov) |"
        )

    floor_list = list(plan_floors.keys())
    pilot = args.pilot_floor or (floor_list[0] if floor_list else None)
    lines += [
        "",
        "## 실행 게이트",
        "",
        f"1. **파일럿 층 `{pilot}`** 만 `split_floor.py` 실행",
        "2. 사용자 허락 후 나머지 층 진행",
        "",
        "## 타일 ID",
        "",
        "- `R{row}C{col}` (row=0…ny-1, col=0…nx-1)",
        "- 산출: `floors/<FLOOR>/parts/R0C0.png` 등",
        "",
    ]

    payload = {
        "drawing_id": drawing_id,
        "max_tile_m": args.max_tile_m,
        "overlap_m": args.overlap_m,
        "pilot_floor": pilot,
        "floors": plan_floors,
    }
    write_json(args.artifacts / "split_plan.json", payload)
    write_text(args.artifacts / "split_plan.md", "\n".join(lines))
    print(f"pilot_floor={pilot}")
    print(f"→ {args.artifacts / 'split_plan.md'}")
    print(f"→ {args.artifacts / 'split_plan.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
