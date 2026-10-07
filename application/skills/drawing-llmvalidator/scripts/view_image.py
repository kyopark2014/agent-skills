#!/usr/bin/env python3
"""llm_review 타일을 Vision으로 보고 review.json을 쓴다.

에이전트 도구가 아니다. prepare_review.py 다음에 이 스크립트를 한 번 실행한다.
5000px 이하면 타일이 한 장이고, 한 변이 5000px를 넘을 때만 tiles.json 의 파일마다
Vision을 호출한다. 답은 bbox JSON만 받아 층 전체 mm로 바꿔 review.json에 모은다.

    python3.13 view_image.py --artifacts "$ARTIFACTS_DIR/<id>" --floor 3F
"""

from __future__ import annotations

import argparse
import base64
import inspect
import json
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
    """bash의 python3는 종종 Xcode 3.9이다. chat import는 python3.13이 필요하다."""
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

from PIL import Image  # noqa: E402

Image.MAX_IMAGE_PIXELS = None

_MAX_SIDE = 8000
_PATCH = 32
_MAX_PATCHES = 30_000
_MAX_PNG_BYTES = int(3.75 * 1024 * 1024)
# chat 를 가져오기 전에 둔다. import 가 UI_MODEL_NAME 을 모듈 기본값으로 덮는다.
_UI_MODEL = os.environ.get("UI_MODEL_NAME", "").strip()
_UI_GATEWAY = os.environ.get("UI_LLM_GATEWAY")
_UI_GUARDRAIL = os.environ.get("UI_GUARDRAIL")
_CHAT_LOCK = threading.Lock()
_CHAT = None
_CLIENT = None
_PRINT = threading.Lock()


def _vision_criteria() -> str:
    skill = Path(__file__).resolve().parents[1] / "SKILL.md"
    text = skill.read_text(encoding="utf-8")
    start = text.find("## Vision 판정 기준")
    end = text.find("\n## review.json", start)
    if start < 0 or end < 0:
        raise SystemExit(f"Vision 판정 기준을 찾지 못했습니다: {skill}")
    return text[start:end].strip()


def _prompt(criteria: str, tile_name: str) -> str:
    return (
        f"{criteria}\n\n"
        "위 기준으로 이 이미지 한 장만 판정한다. "
        "설명, 제목, 공간 분석, 마크다운은 쓰지 않는다. JSON 객체 하나만 답한다.\n"
        "좌표는 이 이미지 기준 0~1이다. 왼쪽 위가 (0,0), 오른쪽 아래가 (1,1)이고 y는 아래로 증가한다.\n"
        "빨강인데 벽이 아닌 곳만 demote_bboxes, 회색인데 벽인 곳만 promote_bboxes에 넣는다. "
        "해당 없으면 빈 배열이다. 한 상자는 가구·개구·짧은 벽 한 덩어리만 감싼다.\n"
        f'타일 파일명: {tile_name}\n'
        '{"demote_bboxes":[{"label":"가구","bbox":[0.10,0.20,0.30,0.40]}],'
        '"promote_bboxes":[]}'
    )


def _encode_png(path: Path) -> str:
    img = Image.open(path)
    img.load()
    if img.mode not in ("RGB", "RGBA"):
        img = img.convert("RGBA" if "A" in img.getbands() else "RGB")

    def _over(width: int, height: int) -> bool:
        patches = math.ceil(width / _PATCH) * math.ceil(height / _PATCH)
        return width > _MAX_SIDE or height > _MAX_SIDE or patches > _MAX_PATCHES

    width, height = img.size
    while width > 1 and height > 1 and _over(width, height):
        width = max(1, int(width * 0.8))
        height = max(1, int(height * 0.8))
        img = img.resize((width, height), Image.Resampling.LANCZOS)
    for _ in range(6):
        buffer = BytesIO()
        img.save(buffer, format="PNG", optimize=True)
        png = buffer.getvalue()
        if len(png) <= _MAX_PNG_BYTES and not _over(width, height):
            return base64.b64encode(png).decode("utf-8")
        width = max(1, int(width * 0.8))
        height = max(1, int(height * 0.8))
        img = img.resize((width, height), Image.Resampling.LANCZOS)
    raise RuntimeError(f"이미지가 3.75MB 이하로 줄지 않습니다: {path}")


def _parse_json(text: str) -> dict:
    inner = text or ""
    start_tag = inner.find("<result>")
    end_tag = inner.find("</result>")
    if start_tag != -1 and end_tag > start_tag:
        inner = inner[start_tag + len("<result>") : end_tag]
    inner = re.sub(r"^```(?:json)?\s*", "", inner.strip(), flags=re.IGNORECASE)
    inner = re.sub(r"\s*```$", "", inner)
    start = inner.find("{")
    end = inner.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("JSON 객체가 없습니다")
    data = json.loads(inner[start : end + 1])
    if not isinstance(data, dict):
        raise ValueError("JSON 객체가 아닙니다")
    return data


def _four(raw: object) -> tuple[float, float, float, float] | None:
    if isinstance(raw, (list, tuple)) and len(raw) >= 4:
        try:
            return float(raw[0]), float(raw[1]), float(raw[2]), float(raw[3])
        except (TypeError, ValueError):
            return None
    if isinstance(raw, dict):
        nested = raw.get("bbox") or raw.get("bbox_norm") or raw.get("rect")
        if nested is not None and nested is not raw:
            got = _four(nested)
            if got:
                return got
        keys = (
            ("xmin", "ymin", "xmax", "ymax"),
            ("x0", "y0", "x1", "y1"),
            ("left", "top", "right", "bottom"),
        )
        for names in keys:
            vals = []
            ok = True
            for name in names:
                if name not in raw or raw[name] is None:
                    ok = False
                    break
                try:
                    vals.append(float(raw[name]))
                except (TypeError, ValueError):
                    ok = False
                    break
            if ok and len(vals) == 4:
                return vals[0], vals[1], vals[2], vals[3]
    return None


def _to_mm(raw: object, tile_mm: dict[str, float]) -> dict[str, float] | None:
    """0~1(왼쪽 위 원점, y 아래)이면 타일 mm로 바꾼다. 이미 mm면 그대로 둔다."""
    box = _four(raw)
    if box is None:
        return None
    x0, y0, x1, y1 = box
    xmin, ymin = float(tile_mm["xmin"]), float(tile_mm["ymin"])
    xmax, ymax = float(tile_mm["xmax"]), float(tile_mm["ymax"])
    if max(abs(x0), abs(y0), abs(x1), abs(y1)) <= 1.05:
        def _nx(value: float) -> float:
            return xmin + min(1.0, max(0.0, value)) * (xmax - xmin)

        def _ny(value: float) -> float:
            return ymax - min(1.0, max(0.0, value)) * (ymax - ymin)

        xs = (_nx(x0), _nx(x1))
        ys = (_ny(y0), _ny(y1))
        return {"xmin": min(xs), "ymin": min(ys), "xmax": max(xs), "ymax": max(ys)}
    return {
        "xmin": min(x0, x1),
        "ymin": min(y0, y1),
        "xmax": max(x0, x1),
        "ymax": max(y0, y1),
    }


def _items_mm(items: object, tile: dict) -> list[dict]:
    if isinstance(items, dict):
        items = [items]
    if not isinstance(items, list):
        return []
    tile_mm = tile["bbox_mm"]
    name = tile["file"]
    out: list[dict] = []
    for item in items:
        label = ""
        if isinstance(item, dict):
            label = str(item.get("label") or "")
        got = _to_mm(item, tile_mm)
        if got is None:
            continue
        if label:
            got["label"] = label
        got["tile"] = name
        out.append(got)
    return out


def _content_to_text(content: object) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict) and item.get("text"):
                parts.append(str(item["text"]))
            else:
                text = getattr(item, "text", None)
                if text:
                    parts.append(str(text))
        return "\n".join(parts).strip()
    return str(content).strip()


def _import_chat():
    root = str(Path(__file__).resolve().parents[3])
    if root not in sys.path:
        sys.path.insert(0, root)
    import chat

    if not hasattr(chat, "HumanMessage"):
        from langchain_core.messages import HumanMessage

        chat.HumanMessage = HumanMessage
    if not hasattr(chat, "_content_to_text"):
        chat._content_to_text = _content_to_text
    return chat


def _configure_chat():
    """UI에서 고른 모델로 클라이언트를 한 번만 만든다.

    워커가 동시에 chat.update 와 get_chat 을 호출하면 일부는 모듈 기본 모델로
    남는다. 스레드 풀을 열기 전에 이 함수를 호출한다.
    """
    global _CHAT, _CLIENT
    with _CHAT_LOCK:
        if _CLIENT is not None:
            return _CHAT, _CLIENT
        chat = _import_chat()
        name = _UI_MODEL or getattr(chat, "model_name", "") or "Claude 5.0 Sonnet"
        params = inspect.signature(chat.update).parameters
        kwargs: dict = {"modelName": name}
        if _UI_GUARDRAIL is not None and "guardrailEnabled" in params:
            kwargs["guardrailEnabled"] = _UI_GUARDRAIL == "1"
        if _UI_GATEWAY is not None and "llmGatewayEnabled" in params:
            kwargs["llmGatewayEnabled"] = _UI_GATEWAY == "1"
        chat.update(**kwargs)
        if getattr(chat, "model_name", None) != name:
            raise SystemExit(f"알 수 없는 모델입니다: {name}")
        get_params = inspect.signature(chat.get_chat).parameters
        if "extended_thinking" in get_params:
            client = chat.get_chat("Disable")
        else:
            client = chat.get_chat()
        print(f"vision model={chat.model_name} id={getattr(chat, 'model_id', '')}", flush=True)
        _CHAT = chat
        _CLIENT = client
        return chat, client


def _chat():
    return _configure_chat()


def _invoke_tile(tile: dict, review_dir: Path, prompt: str) -> tuple[str, list[dict], list[dict]]:
    name = tile["file"]
    png = review_dir / name
    if not png.is_file():
        raise FileNotFoundError(png)
    encoded = _encode_png(png)
    chat, client = _chat()
    last_error = "응답 없음"
    for attempt in range(3):
        try:
            result = client.invoke(
                [
                    chat.HumanMessage(
                        content=[
                            {
                                "type": "image_url",
                                "image_url": {"url": f"data:image/png;base64,{encoded}"},
                            },
                            {"type": "text", "text": prompt},
                        ]
                    )
                ]
            )
            data = _parse_json(chat._content_to_text(result.content))
            demote = _items_mm(data.get("demote_bboxes") or [], tile)
            promote = _items_mm(data.get("promote_bboxes") or [], tile)
            with _PRINT:
                print(f"  {name} demote={len(demote)} promote={len(promote)}", flush=True)
            return name, demote, promote
        except Exception as exc:
            last_error = str(exc)
            with _PRINT:
                print(f"  {name} attempt {attempt + 1} failed: {exc}", file=sys.stderr, flush=True)
    raise RuntimeError(f"{name}: {last_error}")


def main() -> int:
    parser = argparse.ArgumentParser(description="llm_review 타일을 Vision으로 보고 review.json을 쓴다")
    parser.add_argument("--artifacts", type=Path, required=True)
    parser.add_argument("--floor", required=True)
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args()
    if args.workers < 1:
        raise SystemExit("--workers 는 1 이상이어야 합니다")

    review_dir = args.artifacts / "floors" / args.floor / "llm_review"
    tiles_path = review_dir / "tiles.json"
    if not tiles_path.is_file():
        raise SystemExit(f"tiles.json 없음: {tiles_path}  먼저 prepare_review.py")
    manifest = json.loads(tiles_path.read_text(encoding="utf-8"))
    tiles = manifest.get("tiles") or []
    if not tiles:
        raise SystemExit(f"타일이 없습니다: {tiles_path}")

    criteria = _vision_criteria()
    jobs = [(tile, _prompt(criteria, tile["file"])) for tile in tiles]
    demote: list[dict] = []
    promote: list[dict] = []
    failed: list[str] = []
    _configure_chat()
    print(f"tiles={len(tiles)} workers={args.workers}", flush=True)
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {
            pool.submit(_invoke_tile, tile, review_dir, prompt): tile["file"]
            for tile, prompt in jobs
        }
        for future in as_completed(futures):
            name = futures[future]
            try:
                _name, tile_demote, tile_promote = future.result()
                demote.extend(tile_demote)
                promote.extend(tile_promote)
            except Exception:
                failed.append(name)
                traceback.print_exc()

    review = {"demote_bboxes": demote, "promote_bboxes": promote}
    out = review_dir / "review.json"
    out.write_text(json.dumps(review, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        f"demote={len(demote)} promote={len(promote)} failed={len(failed)} → {out}",
        flush=True,
    )
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
