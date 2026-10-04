#!/usr/bin/env python3
"""층 도면 가운데 샘플을 읽어, LLM이 벽으로 본 구간의 두께·길이를 조건으로 저장한다.

기본은 샘플 PNG 두 장과 samples.json 만 만든다. --vision 이면 이 스크립트가
Vision으로 observations.json 을 쓰고 wall_samples/wall_conditions.json 까지 만든다.
view_image 도구는 없다.

Usage:
  python sample_wall_conditions.py --artifacts $ARTIFACTS_DIR/<id> --floor 1F --prepare-only
  python sample_wall_conditions.py --artifacts $ARTIFACTS_DIR/<id> --floor 1F
"""

from __future__ import annotations

import argparse
import base64
import json
import re
import sys
from io import BytesIO
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from lib_wall_samples import (  # noqa: E402
    OBSERVATIONS_FILE,
    SAMPLED_DIR,
    SAMPLED_FILE,
    SAMPLES_FILE,
    WALL_SAMPLE_PROMPT,
    collect_conditions,
    content_bounds,
    load_floor_entities,
    parse_observations,
    pick_sample_windows,
    render_sample_png,
    segments_of,
    write_sample_document,
)
from lib_walls import detect_wall_keys, load_wall_conditions  # noqa: E402


def _load_meta_bbox(floor_dir: Path) -> dict | None:
    path = floor_dir / "floor_original_meta.json"
    if not path.is_file():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    box = data.get("bbox_mm")
    return box if isinstance(box, dict) else None


def _read_json_loose(path: Path) -> object:
    text = path.read_text(encoding="utf-8").strip()
    start = text.find("<result>")
    end = text.find("</result>")
    if start != -1 and end > start:
        text = text[start + len("<result>") : end].strip()
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s*```$", "", text)
    return json.loads(text)


def _prepare(floor_dir: Path, floor: str, drawing_id: str, count: int) -> list[dict]:
    dxf_path = floor_dir / "floor_original.dxf"
    if not dxf_path.is_file():
        raise SystemExit(f"floor_original.dxf 없음: {dxf_path}")
    entities = load_floor_entities(dxf_path)
    segs = segments_of(entities)
    bounds = content_bounds(segs, _load_meta_bbox(floor_dir))
    windows = pick_sample_windows(segs, bounds, count=count)
    if len(windows) < count:
        raise SystemExit(f"샘플 창을 {count}개 만들지 못했습니다")
    out_dir = floor_dir / SAMPLED_DIR
    for stale in (OBSERVATIONS_FILE, SAMPLED_FILE):
        old = out_dir / stale
        if old.is_file():
            old.unlink()
    samples: list[dict] = []
    for window in windows:
        box = window["bbox_mm"]
        png_path = out_dir / f"{window['id']}.png"
        width, height = render_sample_png(
            entities,
            (box["xmin"], box["ymin"], box["xmax"], box["ymax"]),
            png_path,
        )
        samples.append(
            {
                "id": window["id"],
                "png": str(png_path),
                "bbox_mm": box,
                "png_size": {"width": width, "height": height},
            }
        )
        print(f"  {window['id']} {png_path} {width}x{height}")
    write_sample_document(
        out_dir / SAMPLES_FILE,
        floor=floor,
        drawing_id=drawing_id,
        samples=samples,
    )
    print(f"  → {out_dir / SAMPLES_FILE}")
    return samples


def _load_samples(floor_dir: Path) -> tuple[dict, list[dict]]:
    path = floor_dir / SAMPLED_DIR / SAMPLES_FILE
    if not path.is_file():
        raise SystemExit(f"샘플 목록 없음: {path}  먼저 --prepare-only")
    doc = json.loads(path.read_text(encoding="utf-8"))
    samples = doc.get("samples") or []
    if not samples:
        raise SystemExit(f"샘플이 비어 있습니다: {path}")
    return doc, samples


def _invoke_vision(png_path: Path) -> object:
    root = str(Path(__file__).resolve().parents[3])
    if root not in sys.path:
        sys.path.insert(0, root)
    import chat
    from PIL import Image

    image = Image.open(png_path)
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    encoded = base64.b64encode(buffer.getvalue()).decode("utf-8")
    message = chat.HumanMessage(
        content=[
            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{encoded}"}},
            {"type": "text", "text": WALL_SAMPLE_PROMPT},
        ]
    )
    result = chat.get_chat().invoke([message])
    text = chat._content_to_text(result.content)
    inner = chat._parse_result_tag(text or "")
    inner = re.sub(r"^```(?:json)?\s*", "", inner.strip(), flags=re.IGNORECASE)
    inner = re.sub(r"\s*```$", "", inner)
    start = inner.find("{")
    end = inner.rfind("}")
    if start == -1 or end <= start:
        raise RuntimeError(f"Vision 응답에 JSON 이 없습니다: {png_path.name}")
    return json.loads(inner[start : end + 1])


def _observations_from_vision(samples: list[dict]) -> dict:
    groups = []
    for sample in samples:
        payload = _invoke_vision(Path(sample["png"]))
        walls = payload.get("walls") if isinstance(payload, dict) else payload
        groups.append({"id": sample["id"], "walls": walls or []})
        print(f"  vision {sample['id']} walls={len(walls or [])}")
    return {"samples": groups}


def _collect(floor_dir: Path, floor: str, drawing_id: str, samples: list[dict], observations: object) -> Path:
    boxes = parse_observations(observations, samples)
    dxf_path = floor_dir / "floor_original.dxf"
    entities = load_floor_entities(dxf_path)
    segs = segments_of(entities)
    wall_keys = detect_wall_keys(segs, conditions=load_wall_conditions(None))
    collected = collect_conditions(segs, samples, boxes, wall_keys=wall_keys)
    out = {
        "floor": floor,
        "drawing_id": drawing_id,
        "source": "llm_sample",
        "samples": [
            {"id": sample["id"], "png": sample.get("png"), "bbox_mm": sample.get("bbox_mm")}
            for sample in samples
        ],
        "measurements": collected["measurements"],
        "conditions": collected["conditions"],
    }
    path = floor_dir / SAMPLED_DIR / SAMPLED_FILE
    path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        f"  measurements={len(collected['measurements'])} "
        f"conditions={len(collected['conditions'])}"
    )
    print(f"  → {path}")
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description="샘플 2장에서 도면별 벽 두께·길이 조건을 모은다")
    parser.add_argument("--artifacts", type=Path, required=True, help="$ARTIFACTS_DIR/<drawing_id>")
    parser.add_argument("--floor", required=True, help="예: 1F, sheet_03")
    parser.add_argument("--n-samples", type=int, default=2, help="가운데에서 자를 샘플 수 (기본 2)")
    parser.add_argument(
        "--prepare-only",
        action="store_true",
        help="샘플 PNG 와 samples.json 만 만들고 끝낸다",
    )
    parser.add_argument(
        "--observations",
        type=Path,
        default=None,
        help="LLM이 쓴 observations.json. 생략 시 wall_samples/observations.json",
    )
    parser.add_argument(
        "--vision",
        action="store_true",
        help="샘플을 Vision 모델에 직접 물어 observations 를 만든다",
    )
    args = parser.parse_args()
    if args.n_samples < 1:
        raise SystemExit("--n-samples 는 1 이상이어야 합니다")

    floor_dir = args.artifacts / "floors" / args.floor
    if not floor_dir.is_dir():
        raise SystemExit(f"층 폴더 없음: {floor_dir}")
    drawing_id = args.artifacts.name
    print(f"floor={args.floor}")

    if args.prepare_only or not (floor_dir / SAMPLED_DIR / SAMPLES_FILE).is_file():
        _prepare(floor_dir, args.floor, drawing_id, args.n_samples)
    if args.prepare_only:
        print("prompt:")
        print(WALL_SAMPLE_PROMPT)
        return 0

    _doc, samples = _load_samples(floor_dir)
    obs_path = args.observations or (floor_dir / SAMPLED_DIR / OBSERVATIONS_FILE)
    if args.vision:
        observations = _observations_from_vision(samples)
        obs_path.write_text(json.dumps(observations, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"  → {obs_path}")
    elif obs_path.is_file():
        observations = _read_json_loose(obs_path)
    else:
        print(f"observations 없음: {obs_path}")
        print("observations 를 만들려면 같은 명령을 --vision 으로 다시 실행하세요.")
        print("prompt:")
        print(WALL_SAMPLE_PROMPT)
        return 0

    _collect(floor_dir, args.floor, drawing_id, samples, observations)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
