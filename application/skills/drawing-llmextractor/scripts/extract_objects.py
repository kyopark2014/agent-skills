#!/usr/bin/env python3
"""주제(topic)에 맞는 객체를 도면 이미지에서 찾아 원본 위에 빨간색으로 표시한다.

Vision 호출은 application/chat.py extract_text 와 같다.
base64 PNG + 프롬프트를 UI에서 선택한 모델(chat.get_chat)에 보낸다.

    python3 extract_objects.py --image /path/to/floor.png --topic "기둥"
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import os
import re
import shutil
import sys
import threading
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from io import BytesIO
from pathlib import Path


def _reexec_supported_python() -> None:
    """bash의 python3는 종종 Xcode 3.9이다. 앱 패키지는 python3.13에 있다.

    chat.py → utils.py 가 import 시점에 langchain_community 를 요구하므로,
    그 모듈이 없는 인터프리터면 Homebrew python3.13으로 다시 실행한다.
    """
    if __name__ != "__main__":
        return
    try:
        import langchain_aws  # noqa: F401
        from PIL import Image  # noqa: F401
        return
    except ModuleNotFoundError as exc:
        missing = exc.name or "dependency"
    for cand in ("python3.13", "python3.12", "python3.11"):
        path = shutil.which(cand)
        if not path or os.path.realpath(path) == os.path.realpath(sys.executable):
            continue
        os.execv(path, [path, *sys.argv])
    raise SystemExit(
        f"{sys.executable} 에 '{missing}' 가 없습니다. python3.13 으로 실행하세요."
    )


_reexec_supported_python()

from PIL import Image, ImageDraw, ImageFont

from prepare_image import encode_vision_png

Image.MAX_IMAGE_PIXELS = None

# Snapshot before importing chat. A child process inherits UI_MODEL_NAME from
# the agent; importing chat must not replace that choice with the module default.
_UI_MODEL = os.environ.get("UI_MODEL_NAME", "").strip()
_UI_GATEWAY = os.environ.get("UI_LLM_GATEWAY")
_UI_GUARDRAIL = os.environ.get("UI_GUARDRAIL")

logger = logging.getLogger("drawing-llmextractor")

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tif", ".tiff"}
MAX_TILE_SIDE = 5000
OVERLAP = 0.12
RED = (255, 0, 0, 255)
RED_FILL = (255, 0, 0, 72)
# 연두. 빨간 벽 도면 위에서 문 표시가 구분되도록 쓴다.
LIGHT_GREEN = (118, 186, 37, 255)
LIGHT_GREEN_FILL = (166, 226, 70, 96)
NAMED_COLORS = {
    "red": (RED, RED_FILL),
    "빨강": (RED, RED_FILL),
    "연두": (LIGHT_GREEN, LIGHT_GREEN_FILL),
    "lightgreen": (LIGHT_GREEN, LIGHT_GREEN_FILL),
}


def _app_root() -> Path:
    # scripts/ -> drawing-llmextractor/ -> skills/ -> application/
    return Path(__file__).resolve().parents[3]


def _import_chat():
    root = str(_app_root())
    if root not in sys.path:
        sys.path.insert(0, root)
    import chat

    return chat


def configure_llm(explicit_model: str | None) -> str:
    """Apply the UI-selected model (or --model) and return the display name."""
    chat = _import_chat()
    name = (explicit_model or _UI_MODEL or chat.model_name or "").strip()
    if not name:
        name = "Claude 5.0 Sonnet"
    kwargs: dict = {"modelName": name}
    if _UI_GATEWAY is not None:
        kwargs["llmGatewayEnabled"] = _UI_GATEWAY == "1"
    if _UI_GUARDRAIL is not None:
        kwargs["guardrailEnabled"] = _UI_GUARDRAIL == "1"
    try:
        chat.update(**kwargs)
    except Exception as exc:
        raise SystemExit(f"모델 설정 실패 ({name}): {exc}") from exc
    if chat.model_name != name:
        raise SystemExit(f"알 수 없는 모델입니다: {name}")
    logger.info(
        "vision model=%s id=%s gateway=%s",
        chat.model_name,
        chat.model_id,
        chat.llm_gateway_enabled,
    )
    return chat.model_name


def build_prompt(topic: str) -> str:
    subject = topic.strip()
    return (
        f'이 이미지는 큰 도면의 한 조각입니다. 주제 "{subject}"에 해당하는 객체를 모두 찾으세요. '
        "원문의 언어를 그대로 유지하고 번역하지 마세요. "
        "각 객체는 그 객체만 꽉 감싸는 작은 bbox로 표시하세요. "
        "도면 전체, 이 조각 전체, 방 여러 개를 한 박스로 묶지 마세요. "
        "벽이면 벽 한 줄(가늘고 긴 구간)씩, 기둥·문이면 그 기호 하나씩입니다. "
        "좌표는 이 조각 기준입니다. 왼쪽 위 (0, 0), 오른쪽 아래 (1, 1). "
        "bbox는 [x0, y0, x1, y1]이고 값은 0 이상 1 이하 소수입니다. "
        "픽셀 좌표는 쓰지 마세요. "
        "같은 객체를 중복해서 넣지 마세요. "
        "해당하는 객체가 없으면 objects는 빈 배열입니다. "
        "설명 문장 없이 아래 JSON만 <result> tag 안에 넣으세요.\n"
        "<result>\n"
        '{"objects":[{"label":"짧은 이름","bbox":[0.1,0.2,0.3,0.4]}]}\n'
        "</result>"
    )


def _strip_fence(text: str) -> str:
    body = text.strip()
    body = re.sub(r"^```(?:json)?\s*", "", body, flags=re.IGNORECASE)
    body = re.sub(r"\s*```$", "", body)
    return body.strip()


def _as_norm_bbox(raw, tile_w: int, tile_h: int) -> list[float] | None:
    if not isinstance(raw, (list, tuple)) or len(raw) != 4:
        return None
    try:
        vals = [float(v) for v in raw]
    except (TypeError, ValueError):
        return None
    if any(math.isnan(v) or math.isinf(v) for v in vals):
        return None
    peak = max(abs(v) for v in vals)
    fits_tile = (
        tile_w > 0
        and tile_h > 0
        and max(vals[0], vals[2]) <= tile_w * 1.05
        and max(vals[1], vals[3]) <= tile_h * 1.05
    )
    if peak <= 1.5:
        norm = vals
    elif fits_tile and peak > 1.5:
        norm = [vals[0] / tile_w, vals[1] / tile_h, vals[2] / tile_w, vals[3] / tile_h]
    elif peak <= 1000:
        norm = [v / 1000.0 for v in vals]
    else:
        return None
    x0, y0, x1, y1 = norm
    if x1 < x0:
        x0, x1 = x1, x0
    if y1 < y0:
        y0, y1 = y1, y0
    x0 = min(1.0, max(0.0, x0))
    y0 = min(1.0, max(0.0, y0))
    x1 = min(1.0, max(0.0, x1))
    y1 = min(1.0, max(0.0, y1))
    if (x1 - x0) < 0.002 or (y1 - y0) < 0.002:
        return None
    return [x0, y0, x1, y1]


def parse_objects(text: str, tile_w: int, tile_h: int) -> list[dict]:
    """Parse <result> JSON from a vision reply into normalized boxes."""
    chat = _import_chat()
    inner = _strip_fence(chat._parse_result_tag(text or ""))
    start = inner.find("{")
    end = inner.rfind("}")
    if start == -1 or end <= start:
        start = inner.find("[")
        end = inner.rfind("]")
        if start == -1 or end <= start:
            raise ValueError("JSON 객체를 찾지 못했습니다")
        data = json.loads(inner[start : end + 1])
    else:
        data = json.loads(inner[start : end + 1])

    if isinstance(data, list):
        items = data
    elif isinstance(data, dict):
        items = data.get("objects")
        if items is None:
            items = data.get("items") or data.get("detections") or []
    else:
        raise ValueError("JSON 형식이 아닙니다")
    if not isinstance(items, list):
        raise ValueError("objects가 배열이 아닙니다")

    found: list[dict] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        label = str(item.get("label") or item.get("name") or item.get("topic") or "").strip()
        raw = item.get("bbox") or item.get("box") or item.get("bbox_norm")
        if raw is None and all(k in item for k in ("x0", "y0", "x1", "y1")):
            raw = [item["x0"], item["y0"], item["x1"], item["y1"]]
        norm = _as_norm_bbox(raw, tile_w, tile_h)
        if norm is None:
            continue
        found.append({"label": label, "bbox_norm": norm})
    return found


def _tile_boxes(
    width: int,
    height: int,
    cols: int,
    rows: int,
    overlap: float,
) -> list[tuple[int, int, int, int]]:
    step_w = math.ceil(width / cols)
    step_h = math.ceil(height / rows)
    pad_w = int(step_w * overlap)
    pad_h = int(step_h * overlap)
    tiles: list[tuple[int, int, int, int]] = []
    for r in range(rows):
        for c in range(cols):
            left = max(0, c * step_w - (pad_w if c else 0))
            top = max(0, r * step_h - (pad_h if r else 0))
            right = min(width, (c + 1) * step_w + (pad_w if c + 1 < cols else 0))
            bottom = min(height, (r + 1) * step_h + (pad_h if r + 1 < rows else 0))
            if right - left >= 8 and bottom - top >= 8:
                tiles.append((left, top, right, bottom))
    return tiles


def plan_tiles(
    width: int,
    height: int,
    *,
    max_tiles: int = 0,
    overlap: float = OVERLAP,
    max_side: int = MAX_TILE_SIDE,
) -> list[tuple[int, int, int, int]]:
    """Split until every crop, including overlap, is at most ``max_side`` on both axes.

    A side longer than 5000px gets another cut. 5000×5000 stays one image.
    Overlap keeps an object on a cut line visible in two crops, so the grid
    counts that padding. ``max_tiles`` does not stop this.
    """
    del max_tiles
    if width <= 0 or height <= 0:
        return []

    cols, rows = 1, 1
    while True:
        boxes = _tile_boxes(width, height, cols, rows, overlap)
        widths = [right - left for left, _top, right, _bottom in boxes]
        heights = [bottom - top for _left, top, _right, bottom in boxes]
        too_w = any(side > max_side for side in widths)
        too_h = any(side > max_side for side in heights)
        if not too_w and not too_h:
            break
        if too_w and (not too_h or max(widths) >= max(heights)):
            cols += 1
        else:
            rows += 1
        if cols > width and rows > height:
            break

    return _tile_boxes(width, height, cols, rows, overlap) or [(0, 0, width, height)]


def _tile_has_ink(crop: Image.Image, min_ratio: float = 0.002) -> bool:
    """Skip blank margin tiles. Thin drawing lines still count as ink."""
    small = crop.convert("L")
    small.thumbnail((96, 96))
    pixels = list(small.getdata())
    if not pixels:
        return False
    ink = sum(1 for value in pixels if value < 245)
    return ink / len(pixels) >= min_ratio


def _keep_detection(box: list[int], tile: tuple[int, int, int, int], image_w: int, image_h: int) -> bool:
    """Drop boxes that cover a whole tile or a large share of the sheet."""
    x0, y0, x1, y1 = box
    bw = max(1, x1 - x0)
    bh = max(1, y1 - y0)
    left, top, right, bottom = tile
    tw = max(1, right - left)
    th = max(1, bottom - top)
    if bw / tw > 0.85 and bh / th > 0.85:
        return False
    area_ratio = (bw * bh) / max(1, image_w * image_h)
    thin = min(bw, bh) / max(bw, bh) < 0.12
    if thin:
        return area_ratio <= 0.2
    return area_ratio <= 0.04


def _iou(a: list[int], b: list[int]) -> float:
    ax0, ay0, ax1, ay1 = a
    bx0, by0, bx1, by1 = b
    ix0, iy0 = max(ax0, bx0), max(ay0, by0)
    ix1, iy1 = min(ax1, bx1), min(ay1, by1)
    inter = max(0, ix1 - ix0) * max(0, iy1 - iy0)
    if inter <= 0:
        return 0.0
    area_a = max(0, ax1 - ax0) * max(0, ay1 - ay0)
    area_b = max(0, bx1 - bx0) * max(0, by1 - by0)
    union = area_a + area_b - inter
    return inter / union if union else 0.0


def _containment(outer: list[int], inner: list[int]) -> float:
    """Share of ``inner`` that lies inside ``outer``."""
    ax0, ay0, ax1, ay1 = outer
    bx0, by0, bx1, by1 = inner
    ix0, iy0 = max(ax0, bx0), max(ay0, by0)
    ix1, iy1 = min(ax1, bx1), min(ay1, by1)
    inter = max(0, ix1 - ix0) * max(0, iy1 - iy0)
    area_b = max(1, (bx1 - bx0) * (by1 - by0))
    return inter / area_b


def merge_objects(objects: list[dict], iou_thresh: float = 0.45) -> list[dict]:
    """Keep the tighter box when overlap tiles report the same object."""
    ranked = sorted(
        objects,
        key=lambda o: (o["bbox_px"][2] - o["bbox_px"][0]) * (o["bbox_px"][3] - o["bbox_px"][1]),
    )
    kept: list[dict] = []
    for obj in ranked:
        box = obj["bbox_px"]
        label = (obj.get("label") or "").casefold()
        if any(
            (not label or label == (prev.get("label") or "").casefold() or not prev.get("label"))
            and (
                _iou(box, prev["bbox_px"]) >= iou_thresh
                or _containment(box, prev["bbox_px"]) >= 0.7
                or _containment(prev["bbox_px"], box) >= 0.7
            )
            for prev in kept
        ):
            continue
        kept.append(obj)
    kept.sort(key=lambda o: (o["bbox_px"][1], o["bbox_px"][0]))
    return kept


def _font(size: int) -> ImageFont.ImageFont:
    candidates = [
        "/System/Library/Fonts/Supplemental/AppleGothic.ttf",
        "/System/Library/Fonts/AppleSDGothicNeo.ttc",
        "/usr/share/fonts/truetype/nanum/NanumGothic.ttf",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    ]
    for path in candidates:
        if os.path.isfile(path):
            try:
                return ImageFont.truetype(path, size=size)
            except OSError:
                continue
    return ImageFont.load_default()


def parse_mark_color(value: str | None) -> tuple[tuple[int, int, int, int], tuple[int, int, int, int]]:
    """표시 색. 생략하면 빨강. `#RRGGBB` 또는 연두·빨강 이름을 받는다."""
    text = (value or "").strip()
    if not text:
        return RED, RED_FILL
    named = NAMED_COLORS.get(text.lower()) or NAMED_COLORS.get(text)
    if named is not None:
        return named
    hex_text = text[1:] if text.startswith("#") else text
    if re.fullmatch(r"[0-9A-Fa-f]{6}", hex_text):
        red = int(hex_text[0:2], 16)
        green = int(hex_text[2:4], 16)
        blue = int(hex_text[4:6], 16)
        return (red, green, blue, 255), (red, green, blue, 96)
    raise SystemExit(f"알 수 없는 표시 색입니다: {value}")


def mark_objects(
    image: Image.Image,
    objects: list[dict],
    color: tuple[tuple[int, int, int, int], tuple[int, int, int, int]] | None = None,
) -> Image.Image:
    """Draw boxes on a copy of the original image. Default color is red."""
    outline, fill = color or (RED, RED_FILL)
    base = image.convert("RGBA")
    overlay = Image.new("RGBA", base.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    width, height = base.size
    stroke = max(3, min(width, height) // 500)
    font = _font(max(16, min(42, min(width, height) // 180)))
    for obj in objects:
        x0, y0, x1, y1 = obj["bbox_px"]
        draw.rectangle([x0, y0, x1, y1], fill=fill, outline=outline, width=stroke)
        label = (obj.get("label") or "").strip()
        if not label:
            continue
        text_y = y0 - stroke - 4
        if text_y < 4:
            text_y = y0 + stroke + 2
        draw.text((x0 + stroke, text_y), label, fill=outline, font=font)
    return Image.alpha_composite(base, overlay)


def _tile_to_original(norm: list[float], tile: tuple[int, int, int, int]) -> list[int]:
    left, top, right, bottom = tile
    tw = max(1, right - left)
    th = max(1, bottom - top)
    x0 = int(round(left + norm[0] * tw))
    y0 = int(round(top + norm[1] * th))
    x1 = int(round(left + norm[2] * tw))
    y1 = int(round(top + norm[3] * th))
    return [x0, y0, x1, y1]


def _thread_llm(chat):
    """One chat client per worker thread. invoke() waits on the network."""
    client = getattr(_TLS, "llm", None)
    if client is None:
        client = chat.get_chat()
        _TLS.llm = client
    return client


_TLS = threading.local()


def _invoke_tile(chat, tile_png: bytes, prompt: str) -> str:
    """One multimodal call, same message shape as chat.extract_text."""
    img_base64 = encode_vision_png(Image.open(BytesIO(tile_png)))
    messages = [
        chat.HumanMessage(
            content=[
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/png;base64,{img_base64}"},
                },
                {"type": "text", "text": prompt},
            ]
        )
    ]
    result = _thread_llm(chat).invoke(messages)
    return chat._content_to_text(result.content)


def _boxes_from_tile(chat, prompt: str, job: tuple, image_w: int, image_h: int) -> tuple[bool, list[dict]]:
    index, total, tile, tile_png = job
    left, top, right, bottom = tile
    logger.info("tile %s/%s px=(%s,%s)-(%s,%s)", index, total, left, top, right, bottom)
    objects: list[dict] | None = None
    for attempt in range(5):
        logger.info("tile %s attempt: %s", index, attempt)
        try:
            raw = _invoke_tile(chat, tile_png, prompt)
            logger.info("tile %s Extracted_text: %s", index, raw[:500])
            objects = parse_objects(raw, right - left, bottom - top)
            break
        except Exception:
            logger.info("tile %s error message: %s", index, traceback.format_exc())
            objects = None
    if objects is None:
        return False, []
    found: list[dict] = []
    for obj in objects:
        box = _tile_to_original(obj["bbox_norm"], tile)
        box[0] = min(image_w - 1, max(0, box[0]))
        box[1] = min(image_h - 1, max(0, box[1]))
        box[2] = min(image_w, max(box[0] + 1, box[2]))
        box[3] = min(image_h, max(box[1] + 1, box[3]))
        if not _keep_detection(box, tile, image_w, image_h):
            logger.info("drop oversized box %s", box)
            continue
        found.append(
            {
                "label": obj["label"],
                "bbox_px": box,
                "bbox_norm": [
                    round(box[0] / image_w, 6),
                    round(box[1] / image_h, 6),
                    round(box[2] / image_w, 6),
                    round(box[3] / image_h, 6),
                ],
            }
        )
    return True, found


def extract_objects(
    image: Image.Image,
    topic: str,
    *,
    max_tiles: int = 0,
    workers: int = 4,
) -> tuple[list[dict], dict]:
    chat = _import_chat()
    prompt = build_prompt(topic)
    width, height = image.size
    tiles = plan_tiles(width, height, max_tiles=max_tiles)
    logger.info("tiles: %s for %sx%s", len(tiles), width, height)
    jobs: list[tuple] = []
    skipped_blank = 0
    for index, tile in enumerate(tiles, start=1):
        crop = image.crop(tile)
        if not _tile_has_ink(crop):
            skipped_blank += 1
            logger.info("tile %s/%s blank, skip", index, len(tiles))
            continue
        buffer = BytesIO()
        crop.save(buffer, format="PNG")
        jobs.append((index, len(tiles), tile, buffer.getvalue()))

    worker_n = max(1, min(workers, len(jobs) or 1))
    logger.info("parallel workers: %s jobs: %s", worker_n, len(jobs))
    found: list[dict] = []
    failed_tiles = 0
    if jobs:
        with ThreadPoolExecutor(max_workers=worker_n) as pool:
            futures = [
                pool.submit(_boxes_from_tile, chat, prompt, job, width, height)
                for job in jobs
            ]
            for future in as_completed(futures):
                ok, boxes = future.result()
                if not ok:
                    failed_tiles += 1
                    continue
                for box in boxes:
                    if not box["label"]:
                        box["label"] = topic.strip()
                    found.append(box)
    analyzed = len(jobs)
    if analyzed and failed_tiles == analyzed:
        raise RuntimeError("모든 타일에서 객체 추출에 실패했습니다.")
    stats = {
        "tiles": len(tiles),
        "tiles_analyzed": analyzed,
        "tiles_blank": skipped_blank,
        "tiles_failed": failed_tiles,
        "workers": worker_n,
    }
    logger.info("tile stats: %s objects_raw=%s", stats, len(found))
    return merge_objects(found), stats


def _slug(topic: str) -> str:
    slug = re.sub(r"[^\w가-힣]+", "_", topic.strip(), flags=re.UNICODE)
    slug = slug.strip("_")[:40]
    return slug or "topic"


def default_output_paths(image_path: Path, topic: str) -> tuple[Path, Path]:
    slug = _slug(topic)
    png = image_path.with_name(f"{image_path.stem}.{slug}.png")
    meta = image_path.with_name(f"{image_path.stem}.{slug}.json")
    return png, meta


def run(
    image_path: Path,
    topic: str,
    output: Path | None,
    model: str | None,
    max_tiles: int,
    workers: int = 4,
    mark_color: str | None = None,
) -> dict:
    if not topic.strip():
        raise SystemExit("주제가 비어 있습니다.")
    if not image_path.is_file():
        raise SystemExit(f"이미지 파일이 없습니다: {image_path}")
    if image_path.suffix.lower() not in IMAGE_EXTENSIONS:
        raise SystemExit(f"지원하지 않는 이미지 형식입니다: {image_path.suffix}")

    model_name = configure_llm(model)
    chat = _import_chat()
    image = Image.open(image_path)
    image.load()
    objects, tile_stats = extract_objects(image, topic, max_tiles=max_tiles, workers=workers)
    color = parse_mark_color(mark_color)
    marked = mark_objects(image, objects, color)
    png_path, json_path = default_output_paths(image_path, topic)
    if output is not None:
        png_path = output
        json_path = output.with_suffix(".json")
    png_path.parent.mkdir(parents=True, exist_ok=True)
    if png_path.resolve() == image_path.resolve():
        raise SystemExit("출력 경로가 입력 이미지와 같습니다. 원본은 덮어쓰지 않습니다.")
    marked.convert("RGB").save(png_path, format="PNG")
    payload = {
        "image": str(image_path),
        "topic": topic.strip(),
        "model": model_name,
        "model_id": chat.model_id,
        "width": image.size[0],
        "height": image.size[1],
        "tiles": tile_stats,
        "objects": objects,
        "mark_color": mark_color or "red",
        "marked_image": str(png_path),
    }
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    payload["json"] = str(json_path)
    return payload


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(filename)s:%(lineno)d | %(message)s",
        stream=sys.stderr,
    )
    parser = argparse.ArgumentParser(description="도면 이미지에서 주제 객체를 추출하고 빨간색으로 표시")
    parser.add_argument("--image", required=True, type=Path, help="입력 이미지 경로")
    parser.add_argument("--topic", required=True, help="추출할 주제 (예: 기둥, 문, 엘리베이터)")
    parser.add_argument("--output", type=Path, default=None, help="표시된 PNG 경로 (기본: 원본 옆)")
    parser.add_argument(
        "--model",
        default=None,
        help="UI 표시 모델 이름. 생략하면 UI_MODEL_NAME(UI에서 선택한 모델)을 사용",
    )
    parser.add_argument(
        "--max-tiles",
        type=int,
        default=0,
        help="호환용. 조각 수는 바꾸지 않는다. 가로 또는 세로가 5000px를 넘으면 그 방향으로 더 나눈다",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=4,
        help="동시에 분석할 조각 수 (기본 4). Vision 호출은 네트워크 대기라 병렬로 줄인다",
    )
    parser.add_argument(
        "--color",
        default=None,
        help="표시 색. 기본 빨강. 연두, lightgreen, #RRGGBB",
    )
    args = parser.parse_args()

    payload = run(
        args.image.expanduser().resolve(),
        args.topic,
        args.output.expanduser().resolve() if args.output else None,
        args.model,
        max(0, args.max_tiles),
        max(1, args.workers),
        args.color,
    )
    print(f"model: {payload['model']}")
    print(f"model_id: {payload['model_id']}")
    print(f"topic: {payload['topic']}")
    print(f"tiles: {payload['tiles']}")
    print(f"objects: {len(payload['objects'])}")
    print(f"marked: {payload['marked_image']}")
    print(f"json: {payload['json']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
