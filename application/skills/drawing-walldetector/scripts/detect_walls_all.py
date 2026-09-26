#!/usr/bin/env python3
"""artifacts 아래 층들에 대해 벽 검출 (사용자 일괄 명시 시에만).

Usage:
  python detect_walls_all.py --artifacts $ARTIFACTS_DIR/<drawing_id>
  python detect_walls_all.py --artifacts $ARTIFACTS_DIR/<drawing_id> --skip 12F
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent


def list_floors(artifacts: Path) -> list[str]:
    floors_dir = artifacts / "floors"
    if not floors_dir.is_dir():
        return []
    out: list[str] = []
    for p in floors_dir.iterdir():
        if p.is_dir() and (p / "floor_parts_index.json").is_file():
            out.append(p.name)
    def key(f: str) -> int:
        n = f[:-1] if f.endswith("F") and f[:-1].isdigit() else "0"
        return int(n) if n.isdigit() else 0
    return sorted(out, key=key)


def main() -> int:
    p = argparse.ArgumentParser(description="다층 벽 검출")
    p.add_argument(
        "--artifacts",
        type=Path,
        required=True,
        help="$ARTIFACTS_DIR/<drawing_id>",
    )
    p.add_argument(
        "--floors",
        default=None,
        help="쉼표 구분 층 (미지정 시 floors/ 전부)",
    )
    p.add_argument("--skip", default=None, help="건너뛸 층 (예: 12F)")
    p.add_argument("--min-len-mm", type=float, default=500.0)
    p.add_argument("--thick-min-mm", type=float, default=50.0)
    p.add_argument("--thick-max-mm", type=float, default=420.0)
    p.add_argument("--no-png", action="store_true")
    p.add_argument("--overview-only", action="store_true", help="층 전체 wall만 (타일 생략)")
    p.add_argument("--dpi", type=int, default=200)
    p.add_argument("--px-width", type=int, default=2400)
    args = p.parse_args()

    if args.floors:
        floors = [x.strip() for x in args.floors.split(",") if x.strip()]
    else:
        floors = list_floors(args.artifacts)
    skip = {x.strip() for x in (args.skip or "").split(",") if x.strip()}
    floors = [f for f in floors if f not in skip]
    if not floors:
        raise SystemExit("처리할 층 없음")

    floor_script = _SCRIPTS / "detect_walls_floor.py"
    summary: dict = {"drawing_id": None, "floors": {}, "status": "completed"}
    for fl in floors:
        print(f"\n######## WALL FLOOR {fl} ########", flush=True)
        cmd = [
            sys.executable,
            str(floor_script),
            "--artifacts",
            str(args.artifacts),
            "--floor",
            fl,
            "--min-len-mm",
            str(args.min_len_mm),
            "--thick-min-mm",
            str(args.thick_min_mm),
            "--thick-max-mm",
            str(args.thick_max_mm),
            "--dpi",
            str(args.dpi),
            "--px-width",
            str(args.px_width),
        ]
        if args.no_png:
            cmd.append("--no-png")
        if args.overview_only:
            cmd.append("--overview-only")
        r = subprocess.run(cmd, check=False)
        if r.returncode != 0:
            raise SystemExit(f"실패: {fl} exit={r.returncode}")
        idx = args.artifacts / "floors" / fl / "walls" / "walls_index.json"
        if idx.is_file():
            data = json.loads(idx.read_text(encoding="utf-8"))
            summary["drawing_id"] = data.get("drawing_id") or summary["drawing_id"]
            fov = data.get("floor_wall_original") or data.get("floor_walls_overview") or {}
            summary["floors"][fl] = {
                "n_tiles": data.get("n_tiles"),
                "walls_index": str(idx),
                "floor_wall_original_dxf": fov.get("dxf"),
                "floor_wall_original_png": fov.get("png"),
            }

    out = args.artifacts / "walls_all_index.json"
    out.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n→ {out}")
    print("floors:", ", ".join(f"{k}({v['n_tiles']})" for k, v in summary["floors"].items()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
