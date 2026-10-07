#!/usr/bin/env python3
"""같은 실명을 floor_wall_validated 가 있는 여러 층에서 동시에 계산한다.

동시에 도는 층 수는 물리 CPU 코어 수다. --workers 로 바꿀 수 있다.
층마다 evaluate_room.py 를 띄우고, 결과는 그 층 room_eval/ 에 쓴다.

Usage:
  python3.13 evaluate_floors.py --artifacts $ARTIFACTS_DIR/<drawing_id> --room "회의실#1"
  python3.13 evaluate_floors.py --artifacts $ARTIFACTS_DIR/<drawing_id> --room "회의실#1" --floors 1F,2F
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
        if (path / "floor_wall_validated.dxf").is_file() and (
            path / "floor_wall_validated_meta.json"
        ).is_file():
            out.append(path.name)

    def key(floor: str) -> tuple:
        if floor.endswith("F") and floor[:-1].isdigit():
            return (1, int(floor[:-1]), floor)
        return (2, 0, floor)

    return sorted(out, key=key)


def _cmd(args: argparse.Namespace, floor: str) -> list[str]:
    floor_dir = args.artifacts / "floors" / floor
    cmd = [
        sys.executable,
        str(_SCRIPTS / "evaluate_room.py"),
        "--dxf",
        str(floor_dir / "floor_wall_validated.dxf"),
        "--meta",
        str(floor_dir / "floor_wall_validated_meta.json"),
        "--room",
        args.room,
        "--door",
        args.door,
    ]
    if args.x is not None:
        cmd.extend(["--x", str(args.x), "--y", str(args.y)])
    return cmd


def _read_summary(artifacts: Path, floor: str, room: str) -> dict | None:
    folder = artifacts / "floors" / floor / "room_eval"
    if not folder.is_dir():
        return None
    matches = sorted(folder.glob("*.json"))
    if not matches:
        return None
    chosen = matches[0]
    for path in matches:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if str(data.get("room", "")).replace(" ", "") == room.replace(" ", ""):
            chosen_data = data
            chosen = path
            break
    else:
        try:
            chosen_data = json.loads(chosen.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
    return {
        "json": str(chosen),
        "room": chosen_data.get("room"),
        "area_m2": chosen_data.get("area_m2"),
        "dxf": chosen_data.get("dxf"),
        "overlay_png": chosen_data.get("overlay_png"),
    }


def _run_floor(args: argparse.Namespace, floor: str) -> tuple[str, int]:
    with _PRINT:
        print(f"\n######## ROOM {floor} {args.room} ########", flush=True)
    proc = subprocess.Popen(
        _cmd(args, floor),
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
    parser = argparse.ArgumentParser(description="같은 실명을 여러 층에서 동시에 계산")
    parser.add_argument("--artifacts", type=Path, required=True, help="$ARTIFACTS_DIR/<drawing_id>")
    parser.add_argument("--room", required=True, help="실명")
    parser.add_argument("--floors", default=None, help="쉼표 구분 층. 생략하면 validated 가 있는 층")
    parser.add_argument("--skip", default=None, help="건너뛸 층")
    parser.add_argument("--door", choices=("close", "open"), default="close")
    parser.add_argument("--x", type=float, default=None)
    parser.add_argument("--y", type=float, default=None)
    parser.add_argument(
        "--workers",
        type=int,
        default=None,
        help="동시에 계산할 층 수. 생략하면 물리 CPU 코어 수",
    )
    args = parser.parse_args()
    if (args.x is None) != (args.y is None):
        raise SystemExit("--x 와 --y 는 함께 지정하세요.")
    if args.workers is not None and args.workers < 1:
        raise SystemExit("--workers 는 1 이상이어야 합니다")

    if args.floors:
        floors = [item.strip() for item in args.floors.split(",") if item.strip()]
    else:
        floors = list_floors(args.artifacts)
    skip = {item.strip() for item in (args.skip or "").split(",") if item.strip()}
    floors = [floor for floor in floors if floor not in skip]
    if not floors:
        raise SystemExit("처리할 층 없음")

    workers = physical_cpu_count() if args.workers is None else args.workers
    worker_n = max(1, min(workers, len(floors)))
    print(f"floors={len(floors)} workers={worker_n} room={args.room}", flush=True)

    codes: dict[str, int] = {}
    with ThreadPoolExecutor(max_workers=worker_n) as pool:
        futures = [pool.submit(_run_floor, args, floor) for floor in floors]
        for future in as_completed(futures):
            floor, code = future.result()
            codes[floor] = code

    failed = [floor for floor in floors if codes.get(floor, 1) != 0]
    summary: dict = {
        "room": args.room,
        "door": args.door,
        "workers": worker_n,
        "status": "failed" if failed else "completed",
        "floors": {},
    }
    if failed:
        summary["failed"] = failed
    for floor in floors:
        info = _read_summary(args.artifacts, floor, args.room)
        if info:
            summary["floors"][floor] = info

    out = args.artifacts / "room_eval_index.json"
    out.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n→ {out}")
    if failed:
        raise SystemExit("실패: " + ", ".join(f"{floor} exit={codes[floor]}" for floor in failed))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
