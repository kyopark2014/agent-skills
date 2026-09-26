#!/usr/bin/env python3
"""한 층을 60×60 m 이하 타일로 자르고 치수를 넣어 저장.

parts 기본 입력: floors/<F>/floor_original.dxf (원본 층 스냅샷)
층 미리보기는 floor_original.* 만 사용 (floor_overview.* 생성·사용 안 함).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import ezdxf

_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from lib_render import (  # noqa: E402
    build_dim_plan,
    render_hires_png,
    write_clean_dxf,
)
from lib_split import (  # noqa: E402
    assert_tile_limit,
    expand_bbox_include_outer_walls,
    filter_bbox,
    find_primary_floor_bbox,
    find_primary_line_bbox,
    write_json,
)


def resolve_source_dxf(
    artifacts: Path,
    floor: str,
    floor_plan: dict,
    *,
    source: str,
    clean_arg: Path | None,
) -> Path:
    """parts용 입력 DXF 경로."""
    if clean_arg is not None:
        return clean_arg

    if source == "original":
        candidates = [
            artifacts / "floors" / floor / "floor_original.dxf",
            artifacts.parent / f"floor_{floor}_original.dxf",
            Path(floor_plan.get("source_original") or ""),
        ]
        for p in candidates:
            if p and p.is_file():
                return p
        raise SystemExit(
            f"floor_original.dxf 없음: {candidates[0]}\n"
            f"  먼저 extract_2d.py --variant original --drawing-id … --floor {floor}"
        )

    # legacy clean (비권장 — 기본 워크플로에서 제거됨)
    clean = Path(floor_plan.get("source_clean") or "")
    if clean.is_file():
        return clean
    alt = artifacts.parent / f"floor_{floor}_clean.dxf"
    if alt.is_file():
        print(f"  [warn] legacy clean 사용: {alt}", file=sys.stderr)
        return alt
    raise SystemExit(
        f"입력 DXF 없음 (source={source}). "
        f"floor_original.dxf 를 먼저 추출하세요."
    )


def _remove_legacy_overview(floor_dir: Path) -> None:
    for name in (
        "floor_overview.dxf",
        "floor_overview.png",
        "floor_overview_meta.json",
    ):
        p = floor_dir / name
        if p.is_file():
            p.unlink()
            print(f"  removed legacy {name}")


def main() -> int:
    p = argparse.ArgumentParser(description="층 타일 분할 (+치수)")
    p.add_argument("--artifacts", type=Path, required=True)
    p.add_argument("--floor", required=True, help="예: 12F")
    p.add_argument(
        "--source",
        choices=("original", "clean"),
        default="original",
        help="parts 입력 (기본 original = floor_original.dxf)",
    )
    p.add_argument(
        "--clean",
        type=Path,
        default=None,
        help="입력 DXF 경로 직접 지정 (source 무시)",
    )
    p.add_argument("--max-tile-m", type=float, default=60.0)
    p.add_argument("--overlap-m", type=float, default=1.0)
    p.add_argument("--dpi", type=int, default=300)
    p.add_argument("--px-width", type=int, default=4000)
    p.add_argument("--linewidth", type=float, default=0.35)
    p.add_argument("--no-dims", action="store_true")
    p.add_argument("--no-png", action="store_true")
    p.add_argument("--no-dxf", action="store_true")
    p.add_argument("--no-json", action="store_true")
    args = p.parse_args()

    plan_path = args.artifacts / "split_plan.json"
    if not plan_path.is_file():
        raise SystemExit(f"split_plan.json 없음: {plan_path} (plan_split.py 먼저)")
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    drawing_id = plan["drawing_id"]
    floor = args.floor
    if floor not in plan["floors"]:
        raise SystemExit(f"계획에 층 없음: {floor}. keys={list(plan['floors'])}")

    floor_plan = plan["floors"][floor]
    source_dxf = resolve_source_dxf(
        args.artifacts,
        floor,
        floor_plan,
        source=args.source,
        clean_arg=args.clean,
    )

    tiles = floor_plan["tiles"]
    assert_tile_limit(tiles, max_tile_m=args.max_tile_m)

    print(f"loading {source_dxf} (source={args.source}) ...", flush=True)
    doc = ezdxf.readfile(str(source_dxf))
    entities = list(doc.modelspace())
    print(f"  entities={len(entities)}")

    # dual layout / 이상치: original은 plan span을 핵으로 확장, clean은 LINE bbox
    if args.source == "original":
        bb0 = floor_plan["span"]["bbox_mm"]
        core = (bb0["xmin"], bb0["ymin"], bb0["xmax"], bb0["ymax"])
        primary = find_primary_floor_bbox(entities, core_bbox=core)
    else:
        primary = find_primary_line_bbox(entities)
    if primary:
        before = len(entities)
        entities = filter_bbox(entities, *primary)
        print(f"  primary bbox filter: {before} → {len(entities)}")

    floor_dir = args.artifacts / "floors" / floor
    parts_dir = floor_dir / "parts"
    parts_dir.mkdir(parents=True, exist_ok=True)

    # 레거시 floor_overview.* 제거 (더 이상 생성·사용하지 않음)
    _remove_legacy_overview(floor_dir)

    # 상·하단 긴 외벽이 span 밖이면 확장 (타일 잘림 방지)
    span = floor_plan["span"]
    bb = span["bbox_mm"]
    if primary:
        ox0, oy0, ox1, oy1 = primary
    else:
        ox0, oy0, ox1, oy1 = bb["xmin"], bb["ymin"], bb["xmax"], bb["ymax"]
    before_bb = (ox0, oy0, ox1, oy1)
    ox0, oy0, ox1, oy1 = expand_bbox_include_outer_walls(
        entities, (ox0, oy0, ox1, oy1)
    )
    if (ox0, oy0, ox1, oy1) != before_bb:
        entities = filter_bbox(list(doc.modelspace()), ox0, oy0, ox1, oy1)
        print(
            f"  outer-wall expand: "
            f"{(before_bb[2]-before_bb[0])/1000:.1f}×{(before_bb[3]-before_bb[1])/1000:.1f}"
            f" → {(ox1-ox0)/1000:.1f}×{(oy1-oy0)/1000:.1f} m  ents={len(entities)}"
        )

    with_dims = not args.no_dims
    part_records = []

    for tile in tiles:
        tid = tile["id"]
        print(f"\n== {floor} {tid} ==")
        part_ents = filter_bbox(
            entities, tile["xmin"], tile["ymin"], tile["xmax"], tile["ymax"]
        )
        print(
            f"  entities={len(part_ents)}  "
            f"{tile['width_m']:.2f}×{tile['height_m']:.2f} m"
        )
        if not part_ents:
            print("  [warn] empty skip")
            continue
        if (
            tile["width_m"] > args.max_tile_m + 0.05
            or tile["height_m"] > args.max_tile_m + 0.05
        ):
            raise RuntimeError(f"{tid} exceeds max_tile_m")

        origin = (tile["xmin"], tile["ymin"])
        dim_plan = build_dim_plan(part_ents, origin, detail=True) if with_dims else None
        title = f"{floor} {tid}  {tile['width_m']:.2f}×{tile['height_m']:.2f} m"
        stem = parts_dir / tid
        files: dict = {}

        if not args.no_dxf:
            dxf_path = Path(f"{stem}.dxf")
            write_clean_dxf(
                part_ents,
                dxf_path,
                include_text=True,
                origin_shift=origin,
                with_dims=with_dims,
                dim_plan=dim_plan,
            )
            files["dxf"] = str(dxf_path)
            print(f"  DXF → {dxf_path}")

        if not args.no_png:
            png_path = Path(f"{stem}.png")
            px = render_hires_png(
                part_ents,
                png_path,
                xmin=tile["xmin"],
                ymin=tile["ymin"],
                xmax=tile["xmax"],
                ymax=tile["ymax"],
                dpi=args.dpi,
                px_width=args.px_width,
                linewidth=args.linewidth,
                origin=origin,
                dim_plan=dim_plan,
                dim_title=title,
            )
            files["png"] = str(png_path)
            print(f"  PNG → {png_path}  {px[0]}x{px[1]}")

        meta = {
            "drawing_id": drawing_id,
            "floor": floor,
            "id": tid,
            "row": tile["row"],
            "col": tile["col"],
            "bbox_mm": {
                "xmin": tile["xmin"],
                "ymin": tile["ymin"],
                "xmax": tile["xmax"],
                "ymax": tile["ymax"],
            },
            "size_m": {"width": tile["width_m"], "height": tile["height_m"]},
            "dimensions": dim_plan,
            "files": files,
            "source": args.source,
        }
        meta_path = Path(f"{stem}_meta.json")
        write_json(meta_path, meta)
        files["meta"] = str(meta_path)

        if not args.no_json and dim_plan:
            geom_path = Path(f"{stem}_geom.json")
            write_json(
                geom_path,
                {
                    "id": tid,
                    "floor": floor,
                    "size_m": meta["size_m"],
                    "dimensions_overall": (dim_plan or {}).get("overall"),
                    "n_entities": len(part_ents),
                    "source": args.source,
                },
            )
            files["geom"] = str(geom_path)

        part_records.append(
            {
                "id": tid,
                "row": tile["row"],
                "col": tile["col"],
                "bbox_mm": meta["bbox_mm"],
                "size_m": meta["size_m"],
                "files": files,
                "dimensions_overall": (dim_plan or {}).get("overall"),
            }
        )

    index = {
        "floor": floor,
        "drawing_id": drawing_id,
        "max_tile_m": args.max_tile_m,
        "overlap_m": args.overlap_m,
        "grid": floor_plan["grid"],
        "source": {
            "kind": args.source,
            "dxf": str(source_dxf),
            "original_dxf": str(
                args.artifacts / "floors" / floor / "floor_original.dxf"
            ),
        },
        "bbox_mm": floor_plan["span"]["bbox_mm"],
        "parts": part_records,
        "status": "completed",
        "approved_for_remaining_floors": False,
        "n_parts": len(part_records),
    }
    index_path = floor_dir / "floor_parts_index.json"
    write_json(index_path, index)
    write_json(
        floor_dir / "floor_meta.json",
        {
            "floor": floor,
            "drawing_id": drawing_id,
            "grid": floor_plan["grid"],
            "span": floor_plan["span"],
            "source": index["source"],
            "index": str(index_path),
        },
    )
    print(f"\n→ {index_path}  parts={len(part_records)}")
    print("STOP: 사용자 허락 후 나머지 층 진행 (SKILL Gate).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
