#!/usr/bin/env python3
"""도면 구조 실측 → structure.md / structure.json."""

from __future__ import annotations

import argparse
import re
import sys
from collections import Counter
from pathlib import Path

import ezdxf

_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from lib_split import (  # noqa: E402
    count_types,
    find_primary_line_bbox,
    write_json,
    write_text,
)


def discover_floors(doc) -> list[str]:
    found: set[str] = set()
    for e in doc.modelspace().query("INSERT"):
        m = re.search(r"XA-S-(\d+)F\s*평면$", e.dxf.name)
        if m:
            found.add(f"{m.group(1)}F")
    return sorted(found, key=lambda x: int(x[:-1]))


def analyze_clean_floor(path: Path) -> dict:
    doc = ezdxf.readfile(str(path))
    msp = doc.modelspace()
    ents = list(msp)
    types = count_types(ents)
    layers = dict(Counter(e.dxf.layer for e in ents))
    bbox = find_primary_line_bbox(ents)
    span = None
    if bbox:
        w = (bbox[2] - bbox[0]) / 1000.0
        h = (bbox[3] - bbox[1]) / 1000.0
        span = {
            "bbox_mm": {
                "xmin": bbox[0],
                "ymin": bbox[1],
                "xmax": bbox[2],
                "ymax": bbox[3],
            },
            "width_m": w,
            "height_m": h,
        }
    return {
        "path": str(path),
        "entity_counts": types,
        "layers": layers,
        "n_entities": len(ents),
        "span": span,
    }


def analyze_raw(path: Path) -> dict:
    doc = ezdxf.readfile(str(path))
    msp = doc.modelspace()
    floors = discover_floors(doc)
    layers = [layer.dxf.name for layer in doc.layers]
    types = count_types(msp)
    inserts = Counter(e.dxf.name for e in msp.query("INSERT"))
    floor_inserts = {
        fl: [
            n
            for n in inserts
            if re.search(rf"XA-S-{fl[:-1]}F\s*(평면|코어)$", n)
            or re.search(rf"XS-S-{fl[:-1]}F\s*기둥$", n)
        ]
        for fl in floors
    }
    return {
        "path": str(path),
        "dxfversion": doc.dxfversion,
        "n_blocks": len(doc.blocks),
        "n_layers": len(layers),
        "layers": layers,
        "modelspace_types": types,
        "n_modelspace": sum(types.values()),
        "floors": floors,
        "top_inserts": inserts.most_common(20),
        "floor_inserts": floor_inserts,
        "units_hint": "mm (check $INSUNITS)",
    }


def render_structure_md(drawing_id: str, raw: dict | None, cleans: dict[str, dict]) -> str:
    lines = [
        f"# 도면 구조 — {drawing_id}",
        "",
        "## 파일 메타",
        "",
        "| 항목 | 내용 |",
        "|------|------|",
    ]
    if raw:
        lines += [
            f"| 원본 경로 | `{raw['path']}` |",
            f"| CAD 버전 | {raw.get('dxfversion')} |",
            f"| 블록 수 | {raw.get('n_blocks')} |",
            f"| 레이어 수 | {raw.get('n_layers')} |",
            f"| 단위 | {raw.get('units_hint')} |",
        ]
    lines += [
        "",
        "## 파일 실측 요약",
        "",
        "| 항목 | 값 |",
        "|------|-----|",
    ]
    if raw:
        lines += [
            f"| modelspace 엔티티 | {raw.get('n_modelspace')} |",
            f"| 블록 정의 | {raw.get('n_blocks')} |",
            f"| 층 목록 | {', '.join(raw.get('floors') or [])} |",
            f"| modelspace 타입 | {raw.get('modelspace_types')} |",
        ]
    lines += [
        "",
        "## 층별 span (클린 DXF 기준)",
        "",
        "| 층 | 폭(m) | 깊이(m) | 엔티티 | 경로 |",
        "|----|-------|---------|--------|------|",
    ]
    for fl, info in sorted(cleans.items(), key=lambda x: int(x[0][:-1]) if x[0][:-1].isdigit() else 0):
        sp = info.get("span") or {}
        lines.append(
            f"| {fl} | {sp.get('width_m', '-')} | {sp.get('height_m', '-')} | "
            f"{info.get('n_entities')} | `{info.get('path')}` |"
        )
    lines += [
        "",
        "## 리스크 / 전처리",
        "",
        "- modelspace만 보면 도면이 거의 없을 수 있음 → 층 INSERT / `extract_2d.py` 필요",
        "- 기하가 한 레이어(`0arch`)에 몰리면 벽·가구 레이어 분리 불가 → 블록명 필터",
        "- 좌·우 이중 클러스터 가능 → primary(LINE 다수) bbox만 사용",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    p = argparse.ArgumentParser(description="도면 구조 실측")
    p.add_argument("--drawing-id", required=True)
    p.add_argument("--out", type=Path, required=True, help="artifacts/<id> 디렉터리")
    p.add_argument("--raw-dxf", type=Path, default=None)
    p.add_argument(
        "--floor-glob",
        type=str,
        default=None,
        help="예: …/floors/*/floor_original.dxf",
    )
    p.add_argument(
        "--floor-dir",
        type=Path,
        default=None,
        help="floors/ 디렉터리 (floor_original.dxf 검색)",
    )
    # legacy aliases
    p.add_argument("--clean-glob", type=str, default=None, help=argparse.SUPPRESS)
    p.add_argument("--clean-dir", type=Path, default=None, help=argparse.SUPPRESS)
    args = p.parse_args()

    out: Path = args.out
    out.mkdir(parents=True, exist_ok=True)

    raw_info = None
    if args.raw_dxf and args.raw_dxf.is_file():
        print(f"analyzing raw {args.raw_dxf} ...", flush=True)
        raw_info = analyze_raw(args.raw_dxf)

    floors_data: dict[str, dict] = {}
    floor_files: list[Path] = []
    from glob import glob as _glob

    if args.floor_glob or args.clean_glob:
        floor_files = [Path(x) for x in sorted(_glob(args.floor_glob or args.clean_glob))]
    elif args.floor_dir:
        floor_files = sorted(args.floor_dir.glob("*/floor_original.dxf"))
    elif args.clean_dir:
        floor_files = sorted(args.clean_dir.glob("floor_*_clean.dxf"))
    else:
        # default: out/floors/*/floor_original.dxf
        floor_files = sorted((out / "floors").glob("*/floor_original.dxf"))

    for cf in floor_files:
        m = re.search(r"floor_(\d+F)_clean\.dxf$", cf.name)
        if m:
            fl = m.group(1)
        elif cf.name == "floor_original.dxf":
            fl = cf.parent.name  # floors/5F/floor_original.dxf
            if not re.fullmatch(r"\d+F", fl):
                continue
        else:
            continue
        print(f"analyzing {cf} ...", flush=True)
        floors_data[fl] = analyze_clean_floor(cf)

    payload = {
        "drawing_id": args.drawing_id,
        "raw": raw_info,
        "floors": floors_data,
    }
    write_json(out / "structure.json", payload)
    md = render_structure_md(args.drawing_id, raw_info, floors_data)
    write_text(out / "structure.md", md)
    print(f"→ {out / 'structure.md'}")
    print(f"→ {out / 'structure.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
