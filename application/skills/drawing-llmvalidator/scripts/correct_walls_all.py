#!/usr/bin/env python3
"""review.json 이 있는 층을 동시에 보정하고 diff 를 그린다.

한 층 안에서는 correct_walls_floor.py 다음에 render_wall_diff.py 를 돌린다.
동시에 도는 층 수는 물리 CPU 코어 수다. --workers 로 바꿀 수 있다.

Usage:
  python correct_walls_all.py --artifacts $ARTIFACTS_DIR/<drawing_id>
  python correct_walls_all.py --artifacts $ARTIFACTS_DIR/<drawing_id> --floors 1F,2F
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent
_PRINT = threading.Lock()


def physical_cpu_count() -> int:
    """병렬 워커 기본값. 물리 CPU 코어 수."""
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


def list_floors(artifacts: Path) -> list[str]:
    floors_dir = artifacts / "floors"
    if not floors_dir.is_dir():
        return []
    out: list[str] = []
    for path in floors_dir.iterdir():
        if not path.is_dir():
            continue
        if (path / "floor_wall_original.dxf").is_file() and (
            path / "llm_review" / "review.json"
        ).is_file():
            out.append(path.name)

    def key(floor: str) -> tuple:
        if floor.endswith("F") and floor[:-1].isdigit():
            return (1, int(floor[:-1]), floor)
        return (2, 0, floor)

    return sorted(out, key=key)


def _run_step(floor: str, cmd: list[str]) -> int:
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
    return proc.wait()


def _run_floor(artifacts: Path, floor: str) -> tuple[str, int]:
    with _PRINT:
        print(f"\n######## CORRECT {floor} ########", flush=True)
    correct = [
        sys.executable,
        str(_SCRIPTS / "correct_walls_floor.py"),
        "--artifacts",
        str(artifacts),
        "--floor",
        floor,
    ]
    code = _run_step(floor, correct)
    if code != 0:
        return floor, code
    diff = [
        sys.executable,
        str(_SCRIPTS / "render_wall_diff.py"),
        "--artifacts",
        str(artifacts),
        "--floor",
        floor,
    ]
    return floor, _run_step(floor, diff)


def main() -> int:
    parser = argparse.ArgumentParser(description="여러 층 벽 보정과 diff 를 동시에 실행")
    parser.add_argument("--artifacts", type=Path, required=True, help="$ARTIFACTS_DIR/<drawing_id>")
    parser.add_argument(
        "--floors",
        default=None,
        help="쉼표 구분 층. 생략하면 review.json 이 있는 층",
    )
    parser.add_argument("--skip", default=None, help="건너뛸 층")
    parser.add_argument(
        "--workers",
        type=int,
        default=None,
        help="동시에 보정할 층 수. 생략하면 물리 CPU 코어 수",
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
        raise SystemExit("처리할 층 없음. review.json 이 있는 층만 보정한다")

    workers = physical_cpu_count() if args.workers is None else args.workers
    worker_n = max(1, min(workers, len(floors)))
    print(f"floors={len(floors)} workers={worker_n}", flush=True)

    codes: dict[str, int] = {}
    with ThreadPoolExecutor(max_workers=worker_n) as pool:
        futures = [pool.submit(_run_floor, args.artifacts, floor) for floor in floors]
        for future in as_completed(futures):
            floor, code = future.result()
            codes[floor] = code

    failed = [floor for floor in floors if codes.get(floor, 1) != 0]
    summary = {
        "floors": floors,
        "status": "failed" if failed else "completed",
        "workers": worker_n,
        "failed": failed,
    }
    out = args.artifacts / "walls_correct_index.json"
    out.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n→ {out}")
    if failed:
        raise SystemExit("실패: " + ", ".join(f"{floor} exit={codes[floor]}" for floor in failed))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
