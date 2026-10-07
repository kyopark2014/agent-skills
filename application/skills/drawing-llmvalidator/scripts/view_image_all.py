#!/usr/bin/env python3
"""tiles.json 이 있는 층을 Vision으로 판정하고 review.json 을 쓴다.

진행 중인 Vision 호출 수는 물리 CPU 코어 수다. 한 층이 그 수를 모두 쓰고,
층이 끝나면 다음 층으로 넘어간다. 이 스크립트는 전 층이 끝날 때까지 포그라운드로 기다린다.

Usage:
  python view_image_all.py --artifacts $ARTIFACTS_DIR/<drawing_id>
  python view_image_all.py --artifacts $ARTIFACTS_DIR/<drawing_id> --floors 1F,2F
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import threading
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent
_PRINT = threading.Lock()


def physical_cpu_count() -> int:
    """동시에 진행할 Vision 호출 수. 물리 CPU 코어 수."""
    if sys.platform == "darwin":
        try:
            out = subprocess.check_output(
                ["sysctl", "-n", "hw.physicalcpu"],
                text=True,
                timeout=2,
            ).strip()
            n = int(out)
            if n >= 1:
                return n
        except (OSError, ValueError, subprocess.SubprocessError):
            pass
    return max(1, os.cpu_count() or 1)


def _floor_key(floor: str) -> tuple:
    if floor.endswith("F") and floor[:-1].isdigit():
        return (1, int(floor[:-1]), floor)
    return (2, 0, floor)


def list_floors(artifacts: Path) -> list[str]:
    floors_dir = artifacts / "floors"
    if not floors_dir.is_dir():
        return []
    out: list[str] = []
    for path in floors_dir.iterdir():
        if path.is_dir() and (path / "llm_review" / "tiles.json").is_file():
            out.append(path.name)
    return sorted(out, key=_floor_key)


def _run_floor(artifacts: Path, floor: str, tile_workers: int) -> tuple[str, int]:
    cmd = [
        sys.executable,
        str(_SCRIPTS / "view_image.py"),
        "--artifacts",
        str(artifacts),
        "--floor",
        floor,
        "--workers",
        str(tile_workers),
    ]
    with _PRINT:
        print(f"\n######## VIEW {floor} workers={tile_workers} ########", flush=True)
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    assert proc.stdout is not None
    for line in proc.stdout:
        with _PRINT:
            print(f"[{floor}] {line}", end="", flush=True)
    return floor, proc.wait()


def main() -> int:
    parser = argparse.ArgumentParser(description="여러 층 Vision 판정. 동시 호출은 물리 코어 수")
    parser.add_argument("--artifacts", type=Path, required=True, help="$ARTIFACTS_DIR/<drawing_id>")
    parser.add_argument("--floors", default=None, help="쉼표 구분 층. 생략하면 tiles.json 이 있는 층")
    parser.add_argument("--skip", default=None, help="건너뛸 층")
    parser.add_argument(
        "--workers",
        type=int,
        default=None,
        help="진행 중인 Vision 호출 상한. 생략하면 물리 CPU 코어 수. 한 층이 이 수를 쓴다",
    )
    args = parser.parse_args()
    if args.workers is not None and args.workers < 1:
        raise SystemExit("--workers 는 1 이상이어야 합니다")

    if args.floors:
        floors = [item.strip() for item in args.floors.split(",") if item.strip()]
    else:
        floors = list_floors(args.artifacts)
    skip = {item.strip() for item in (args.skip or "").split(",") if item.strip()}
    floors = [floor for floor in floors if floor not in skip]
    if not floors:
        raise SystemExit("처리할 층 없음. llm_review/tiles.json 이 있는 층만 판정한다")

    budget = physical_cpu_count() if args.workers is None else args.workers
    print(f"floors={len(floors)} vision_slots=1 tile_workers={budget}", flush=True)

    codes: dict[str, int] = {}
    for floor in floors:
        floor_name, code = _run_floor(args.artifacts, floor, budget)
        codes[floor_name] = code

    failed = [floor for floor in floors if codes.get(floor, 1) != 0]
    summary = {
        "floors": floors,
        "status": "failed" if failed else "completed",
        "workers": budget,
        "vision_slots": 1,
        "failed": failed,
    }
    out = args.artifacts / "view_image_index.json"
    out.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n→ {out}")
    if failed:
        raise SystemExit("실패: " + ", ".join(f"{floor} exit={codes[floor]}" for floor in failed))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
