"""floor_original PNG 잔여 축 Vision 검사 → 레이어 drop 후보.

extract_2d 가 PNG 렌더 직후 호출한다. Vision 실패해도 추출은 계속한다.
이번 추출 entity 에서만 레이어를 빼고, is_axis_layer 소스는 고치지 않는다.
"""

from __future__ import annotations

import base64
import json
import math
import re
import sys
from io import BytesIO
from pathlib import Path
from typing import Any, Callable

from ezdxf.document import Drawing

from lib_structure import (
    AXIS_RESIDUAL_MIN_LEN_MM,
    drop_named_layers,
    has_residual_axis_candidates,
    layer_leaf_name,
    layers_near_bboxes,
    select_droppable_axis_layers,
    _entities_span,
    _entity_ortho_segs,
)

AXIS_SAMPLES_DIR = "axis_samples"
AXIS_VISION_FILE = "axis_vision.json"

AXIS_VISION_PROMPT = """이 이미지는 건축 평면도에서 잘라 낸 샘플입니다.
기둥·벽을 가로지르거나 건물 밖으로 길게 뻗은 **직교 격자 축선**(그리드 중심선)만 찾으세요.
치수선, 외벽 윤곽, 조경, 가구, 배관, 문자, 해칭은 축이 아닙니다.
축선마다 bbox 하나를 주세요. 좌표는 이 샘플 기준입니다. 왼쪽 위가 (0, 0), 오른쪽 아래가 (1, 1)입니다.
bbox는 [x0, y0, x1, y1]이고 값은 0 이상 1 이하입니다.
축선이 없으면 axes는 빈 배열이고 present는 false 입니다.
설명 없이 아래 JSON만 <result> 안에 넣으세요.
<result>
{"present":true,"axes":[{"bbox":[0.40,0.05,0.42,0.95]}]}
</result>
"""

CROP_SIDE_MIN_MM = 15000.0
CROP_SIDE_MAX_MM = 25000.0
CROP_PX_WIDTH = 1600


def _content_bounds(entities: list) -> tuple[float, float, float, float] | None:
    span = _entities_span(entities)
    if span is None:
        return None
    min_x = min_y = math.inf
    max_x = max_y = -math.inf
    found = False
    for entity in entities:
        for x0, y0, x1, y1, _length, _h, _v in _entity_ortho_segs(entity):
            found = True
            min_x = min(min_x, x0, x1)
            max_x = max(max_x, x0, x1)
            min_y = min(min_y, y0, y1)
            max_y = max(max_y, y0, y1)
    if not found or not math.isfinite(min_x):
        return None
    return (min_x, min_y, max_x, max_y)


def pick_center_crops(
    bounds: tuple[float, float, float, float],
    *,
    count: int = 2,
) -> list[dict]:
    """도면 가운데 15–25 m 창 1~2개."""
    xmin, ymin, xmax, ymax = bounds
    width = max(xmax - xmin, 1.0)
    height = max(ymax - ymin, 1.0)
    side = min(CROP_SIDE_MAX_MM, max(CROP_SIDE_MIN_MM, min(width, height) * 0.35))
    side = min(side, width * 0.95, height * 0.95)
    cx = (xmin + xmax) * 0.5
    cy = (ymin + ymax) * 0.5
    centers = [(cx, cy)]
    if count >= 2 and width >= side * 1.4:
        offset = side * 0.45
        centers = [(cx - offset * 0.5, cy), (cx + offset * 0.5, cy)]
    windows: list[dict] = []
    for index, (x, y) in enumerate(centers[:count], start=1):
        half = side * 0.5
        x0 = max(xmin, min(x - half, xmax - side))
        y0 = max(ymin, min(y - half, ymax - side))
        x1 = x0 + side
        y1 = y0 + side
        windows.append(
            {
                "id": f"axis_{index:02d}",
                "bbox_mm": {"xmin": x0, "ymin": y0, "xmax": x1, "ymax": y1},
            }
        )
    return windows


def _seg_hits_window(
    x0: float, y0: float, x1: float, y1: float, window: tuple[float, float, float, float]
) -> bool:
    xmin, ymin, xmax, ymax = window
    return not (max(x0, x1) < xmin or min(x0, x1) > xmax or max(y0, y1) < ymin or min(y0, y1) > ymax)


def render_crop_png(
    entities: list,
    window: tuple[float, float, float, float],
    png_path: Path,
    *,
    px_width: int = CROP_PX_WIDTH,
) -> tuple[int, int]:
    """여백 없이 window 를 PNG 로 그린다. 왼쪽 위가 (xmin, ymax). window 밖 선은 생략."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.collections import LineCollection
    from PIL import Image

    xmin, ymin, xmax, ymax = window
    span_x = max(xmax - xmin, 1.0)
    span_y = max(ymax - ymin, 1.0)
    dpi = 100
    fig_w = px_width / dpi
    fig_h = fig_w * (span_y / span_x)
    fig, ax = plt.subplots(figsize=(fig_w, fig_h), dpi=dpi)
    segs: list[list[tuple[float, float]]] = []
    for entity in entities:
        kind = entity.dxftype()
        try:
            if kind == "LINE":
                x0 = float(entity.dxf.start.x)
                y0 = float(entity.dxf.start.y)
                x1 = float(entity.dxf.end.x)
                y1 = float(entity.dxf.end.y)
                if _seg_hits_window(x0, y0, x1, y1, window):
                    segs.append([(x0, y0), (x1, y1)])
            elif kind == "LWPOLYLINE":
                pts = [(float(p[0]), float(p[1])) for p in entity.get_points("xy")]
                if len(pts) < 2:
                    continue
                if entity.closed and pts[0] != pts[-1]:
                    pts = pts + [pts[0]]
                # 창과 겹치는 세그먼트만
                for i in range(len(pts) - 1):
                    x0, y0 = pts[i]
                    x1, y1 = pts[i + 1]
                    if _seg_hits_window(x0, y0, x1, y1, window):
                        segs.append([(x0, y0), (x1, y1)])
        except Exception:  # noqa: BLE001
            continue
    if segs:
        ax.add_collection(LineCollection(segs, colors="#222222", linewidths=0.7, clip_on=True))
    ax.autoscale(False)
    ax.set_xlim(xmin, xmax)
    ax.set_ylim(ymin, ymax)
    ax.margins(0)
    ax.axis("off")
    fig.subplots_adjust(left=0, right=1, bottom=0, top=1)
    png_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(png_path, dpi=dpi, pad_inches=0)
    plt.close(fig)
    with Image.open(png_path) as image:
        return image.size


def norm_bbox_to_mm(
    bbox: list[float] | tuple[float, float, float, float],
    window: tuple[float, float, float, float],
) -> tuple[float, float, float, float] | None:
    """이미지 정규화 bbox(왼위 0,0) → mm (xmin,ymin,xmax,ymax)."""
    if not bbox or len(bbox) < 4:
        return None
    try:
        nx0, ny0, nx1, ny1 = (float(bbox[0]), float(bbox[1]), float(bbox[2]), float(bbox[3]))
    except (TypeError, ValueError):
        return None
    xmin, ymin, xmax, ymax = window
    w = max(xmax - xmin, 1.0)
    h = max(ymax - ymin, 1.0)
    x0 = xmin + min(nx0, nx1) * w
    x1 = xmin + max(nx0, nx1) * w
    # 이미지 y↓ → CAD y↑
    y_top = ymax - min(ny0, ny1) * h
    y_bot = ymax - max(ny0, ny1) * h
    return (x0, y_bot, x1, y_top)


def parse_axis_observation(payload: Any) -> tuple[bool, list[list[float]]]:
    """Vision JSON → (present, axes bboxes 정규화)."""
    if not isinstance(payload, dict):
        return False, []
    axes_raw = payload.get("axes")
    boxes: list[list[float]] = []
    if isinstance(axes_raw, list):
        for item in axes_raw:
            if isinstance(item, dict) and "bbox" in item:
                box = item["bbox"]
            else:
                box = item
            if isinstance(box, (list, tuple)) and len(box) >= 4:
                try:
                    boxes.append([float(box[0]), float(box[1]), float(box[2]), float(box[3])])
                except (TypeError, ValueError):
                    continue
    present = payload.get("present")
    if present is None:
        present = bool(boxes)
    return bool(present) and bool(boxes), boxes


def invoke_axis_vision(png_path: Path) -> dict:
    """chat.get_chat() 로 샘플 PNG 를 물어 JSON dict 를 받는다."""
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
            {"type": "text", "text": AXIS_VISION_PROMPT},
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
    data = json.loads(inner[start : end + 1])
    if not isinstance(data, dict):
        raise RuntimeError(f"Vision JSON 이 객체가 아닙니다: {png_path.name}")
    return data


def prepare_axis_samples(
    entities: list,
    floor_dir: Path,
    *,
    count: int = 2,
) -> list[dict]:
    bounds = _content_bounds(entities)
    if bounds is None:
        return []
    windows = pick_center_crops(bounds, count=count)
    out_dir = floor_dir / AXIS_SAMPLES_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    samples: list[dict] = []
    for window in windows:
        box = window["bbox_mm"]
        png_path = out_dir / f"{window['id']}.png"
        width, height = render_crop_png(
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
    return samples


WriteCallback = Callable[..., Any]


def run_axis_vision_pass(
    *,
    entities: list,
    doc: Drawing | None,
    floor_dir: Path,
    floor: str,
    origin: tuple[float, float],
    include_text: bool,
    write_dxf: WriteCallback,
    render_png: WriteCallback | None,
    plan_bbox: dict | None = None,
    invoke_vision: Callable[[Path], dict] | None = None,
    n_samples: int = 1,
) -> tuple[list, dict]:
    """잔여 축 Vision → 안전 레이어 drop → DXF/PNG 재기록.

    CENTER 축 후보가 없을 때는 LLM 을 건너뛴다. 샘플은 기본 1장.

    Returns:
        (entities_after, report_dict)
    """
    report: dict[str, Any] = {
        "floor": floor,
        "present": False,
        "sample_pngs": [],
        "observations": [],
        "candidate_layers": {},
        "dropped_layers": [],
        "leaf_name_candidates": [],
        "skipped_unsafe": [],
        "error": None,
        "skipped_no_candidates": False,
    }
    if not has_residual_axis_candidates(doc, entities):
        report["skipped_no_candidates"] = True
        _write_report(floor_dir, report)
        print("  axis vision: skipped (no CENTER residual candidates)")
        return entities, report

    invoke = invoke_vision or invoke_axis_vision
    try:
        samples = prepare_axis_samples(entities, floor_dir, count=n_samples)
    except Exception as exc:  # noqa: BLE001
        report["error"] = f"sample_prepare: {exc}"
        print(f"  [warn] axis vision sample failed: {exc}", file=sys.stderr)
        _write_report(floor_dir, report)
        return entities, report

    if not samples:
        report["error"] = "no_samples"
        _write_report(floor_dir, report)
        return entities, report

    report["sample_pngs"] = [s["png"] for s in samples]
    mm_boxes: list[tuple[float, float, float, float]] = []
    any_present = False
    for sample in samples:
        png_path = Path(sample["png"])
        box = sample["bbox_mm"]
        window = (box["xmin"], box["ymin"], box["xmax"], box["ymax"])
        try:
            payload = invoke(png_path)
            present, axes = parse_axis_observation(payload)
            report["observations"].append(
                {"id": sample["id"], "present": present, "axes": axes, "raw": payload}
            )
            if not present:
                continue
            any_present = True
            for nb in axes:
                mm = norm_bbox_to_mm(nb, window)
                if mm is not None:
                    mm_boxes.append(mm)
        except Exception as exc:  # noqa: BLE001
            report["observations"].append(
                {"id": sample["id"], "present": False, "axes": [], "error": str(exc)}
            )
            print(f"  [warn] axis vision {sample['id']}: {exc}", file=sys.stderr)

    report["present"] = any_present and bool(mm_boxes)
    if not report["present"]:
        _write_report(floor_dir, report)
        print("  axis vision: present=False")
        return entities, report

    candidates = layers_near_bboxes(
        entities, mm_boxes, min_len_mm=AXIS_RESIDUAL_MIN_LEN_MM
    )
    report["candidate_layers"] = candidates
    dropped, skipped = select_droppable_axis_layers(doc, entities, candidates)
    report["dropped_layers"] = dropped
    report["skipped_unsafe"] = skipped
    leaves = sorted({layer_leaf_name(name) for name in dropped if layer_leaf_name(name)})
    report["leaf_name_candidates"] = leaves

    if not dropped:
        _write_report(floor_dir, report)
        print(
            f"  axis vision: present=True layers_dropped=[] "
            f"unsafe={len(skipped)} candidates={list(candidates)}"
        )
        return entities, report

    entities2, n_skip = drop_named_layers(entities, dropped)
    dxf_path = floor_dir / "floor_original.dxf"
    try:
        write_dxf(
            entities2,
            dxf_path,
            include_text=include_text,
            origin_shift=origin,
        )
        if render_png is not None:
            render_png(dxf_path, floor=floor, plan_bbox=plan_bbox)
    except Exception as exc:  # noqa: BLE001
        report["error"] = f"rewrite: {exc}"
        print(f"  [warn] axis vision rewrite failed: {exc}", file=sys.stderr)
        _write_report(floor_dir, report)
        return entities, report

    _write_report(floor_dir, report)
    print(
        f"  axis vision: present=True layers_dropped={dropped} "
        f"entities_removed={n_skip} leaves={leaves}"
    )
    return entities2, report


def _write_report(floor_dir: Path, report: dict) -> Path:
    path = floor_dir / AXIS_VISION_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return path
