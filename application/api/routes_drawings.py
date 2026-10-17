"""Project drawings listed in ``{user}/artifacts/drawing_list.json``."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import unicodedata
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel

from application.api.routes_auth import require_user_id
from application import utils
from application.unicode_paths import resolve_existing_path

router = APIRouter(prefix="/api/drawings", tags=["drawings"])

# Hangul syllables (NFC) plus the previous ASCII set. Path separators and dots stay out.
_ID_RE = re.compile(r"^[A-Za-z0-9가-힣][A-Za-z0-9가-힣_-]{0,80}$")
_IMAGE_KINDS = {
    "original": "floor_original.png",
    "wall": "floor_wall_original.png",
    "validated": "floor_wall_validated.png",
    "label": "floor_label_detected.png",
}
_LABEL_JSON = "floor_label_detected.json"
_ORIGINAL_META = "floor_original_meta.json"


def _floor_label_count(floor_dir: Path) -> int:
    """How many named labels ``floor_label_detected.json`` still lists."""
    path = floor_dir / _LABEL_JSON
    if not path.is_file():
        return 0
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return 0
    if not isinstance(loaded, dict):
        return 0
    count = 0
    for item in loaded.get("labels") or []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("label") or item.get("layer") or "").strip()
        if name:
            count += 1
    return count
# Must match render_wall_dxf_png canvas padding (top, right, bottom).
_PNG_PAD_TOP = 160
_PNG_PAD_RIGHT = 200
_PNG_PAD_BOTTOM = 120


# Local drawings were produced under this session user. A login id such as
# ``lge`` has no catalog of its own, so Drawing still opens this file.
_LOCAL_DRAWING_OWNER = "ksdyb"


def _artifacts_dir(user_id: str) -> Path:
    """``{user}/artifacts``, or the local ksdyb catalog when this user has none."""
    root = Path(utils.get_user_artifacts_dir(user_id)).resolve()
    if _catalog_path(root).is_file():
        return root
    storage = Path(utils.SESSION_STORAGE_DIR).resolve()
    fallback = (storage / _LOCAL_DRAWING_OWNER / "artifacts").resolve()
    if fallback != root and _catalog_path(fallback).is_file() and _contained(fallback, storage):
        return fallback
    return root


def _contained(path: Path, root: Path) -> bool:
    try:
        return os.path.commonpath([str(path), str(root)]) == str(root)
    except ValueError:
        return False


def _safe_id(value: object) -> str | None:
    text = unicodedata.normalize("NFC", str(value or "").strip())
    if not text or not _ID_RE.fullmatch(text):
        return None
    return text


def _catalog_path(artifacts: Path) -> Path:
    return artifacts / "drawing_list.json"


def load_drawing_catalog(artifacts: Path) -> dict:
    path = _catalog_path(artifacts)
    if not path.is_file():
        return {"drawings": []}
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise HTTPException(
            status_code=500,
            detail="drawing_list.json을 읽지 못했습니다.",
        ) from exc
    if not isinstance(loaded, dict) or not isinstance(loaded.get("drawings"), list):
        raise HTTPException(status_code=500, detail="drawing_list.json 형식이 올바르지 않습니다.")
    loaded["drawings"] = [item for item in loaded["drawings"] if isinstance(item, dict)]
    return loaded


def _write_catalog(artifacts: Path, catalog: dict) -> None:
    path = _catalog_path(artifacts)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps(catalog, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


_DRAWING_EXTS = {".dwg", ".dxf", ".dwf"}


class DrawingTaskLink(BaseModel):
    file_name: str
    task_id: str
    drawing_id: str | None = None


class DrawingRatingBody(BaseModel):
    rating: str


_RATING_VALUES = frozenset({"pending", "good", "neutral", "bad", "review", "confirmed", "retest"})
_DEFAULT_RATING = "pending"


def _normalize_rating(value: object) -> str:
    text = str(value or "").strip().lower()
    return text if text in _RATING_VALUES else _DEFAULT_RATING


def _task_map_path(artifacts: Path) -> Path:
    return artifacts / "drawing_task_map.json"


def _file_key(file_name: str) -> str:
    name = unicodedata.normalize("NFC", os.path.basename(str(file_name or "").replace("\\", "/").strip()))
    return name.casefold()


def _clean_upload_name(file_name: str) -> str:
    name = unicodedata.normalize("NFC", os.path.basename(str(file_name or "").replace("\\", "/").strip()))
    if (
        not name
        or name in {".", ".."}
        or "/" in name
        or "\\" in name
        or name != os.path.basename(name)
    ):
        raise HTTPException(status_code=400, detail="파일 이름이 올바르지 않습니다.")
    if Path(name).suffix.lower() not in _DRAWING_EXTS:
        raise HTTPException(status_code=400, detail="dwg, dxf, dwf 파일만 다룰 수 있습니다.")
    return name


def _load_task_map(artifacts: Path) -> dict:
    path = _task_map_path(artifacts)
    if not path.is_file():
        return {"files": {}}
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"files": {}}
    files = loaded.get("files") if isinstance(loaded, dict) else None
    if not isinstance(files, dict):
        return {"files": {}}
    return {"files": files}


def _write_task_map(artifacts: Path, payload: dict) -> None:
    path = _task_map_path(artifacts)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def remember_drawing_task(
    artifacts: Path,
    *,
    file_name: str,
    task_id: str,
    drawing_id: str | None = None,
    started_at: str | None = None,
) -> dict:
    """Remember the newest task started from an uploaded drawing file."""
    name = _clean_upload_name(file_name)
    stamp = (started_at or datetime.now().astimezone().isoformat(timespec="seconds")).strip()
    safe_drawing = _safe_id(drawing_id) if drawing_id else None
    payload = _load_task_map(artifacts)
    files = payload["files"]
    key = _file_key(name)
    previous = files.get(key) if isinstance(files.get(key), dict) else None
    if previous and str(previous.get("started_at") or "") > stamp:
        return previous
    record = {
        "file_name": name,
        "drawing_id": safe_drawing or (previous or {}).get("drawing_id") or None,
        "task_id": task_id,
        "started_at": stamp,
    }
    files[key] = record
    _write_task_map(artifacts, payload)
    return record


def _latest_task_id(files: dict, *, drawing_id: str, source_filename: str) -> str | None:
    file_key = _file_key(source_filename) if source_filename else ""
    best_at = ""
    best_id: str | None = None
    for item in files.values():
        if not isinstance(item, dict):
            continue
        task_id = str(item.get("task_id") or "").strip()
        started_at = str(item.get("started_at") or "")
        if not task_id:
            continue
        item_drawing = _safe_id(item.get("drawing_id"))
        item_key = _file_key(str(item.get("file_name") or ""))
        matched = bool(drawing_id and item_drawing == drawing_id) or bool(file_key and item_key == file_key)
        if not matched:
            continue
        if best_id is None or started_at >= best_at:
            best_at = started_at
            best_id = task_id
    return best_id


def _floor_refs(entry: dict) -> list[dict]:
    refs: list[dict] = []
    seen: set[str] = set()
    for item in entry.get("floors") or []:
        if not isinstance(item, dict):
            continue
        floor_id = _safe_id(item.get("floor"))
        if not floor_id or floor_id in seen:
            continue
        seen.add(floor_id)
        refs.append(
            {
                "id": floor_id,
                "title": str(item.get("title") or "").strip(),
            }
        )
    return refs


def _source_size_bytes(entry: dict) -> int | None:
    raw = entry.get("source_size_bytes", entry.get("size"))
    try:
        size = int(raw)
    except (TypeError, ValueError):
        return None
    return size if size >= 0 else None


def _all_floors_labeled(artifacts: Path, folder: str, floors: list[dict]) -> bool:
    """True when every catalog floor has ``floor_label_detected.png`` on disk."""
    if not floors:
        return False
    try:
        floors_dir = _folder_dir(artifacts, folder) / "floors"
    except HTTPException:
        return False
    label_name = _IMAGE_KINDS["label"]
    for ref in floors:
        floor_id = str(ref.get("id") or "").strip()
        if not floor_id:
            return False
        label_path = floors_dir / floor_id / label_name
        if label_path.is_file():
            continue
        if Path(resolve_existing_path(str(label_path))).is_file():
            continue
        return False
    return True


def _summary(entry: dict, artifacts: Path | None = None) -> dict | None:
    drawing_id = _safe_id(entry.get("drawing_id"))
    if not drawing_id:
        return None
    folder = _safe_id(entry.get("folder")) or drawing_id
    floors = _floor_refs(entry)
    status = str(entry.get("status") or "pending").strip() or "pending"
    if artifacts is not None and _all_floors_labeled(artifacts, folder, floors):
        status = "done"
    return {
        "drawing_id": drawing_id,
        "folder": folder,
        "source_filename": str(entry.get("source_filename") or "").strip(),
        "created_at": str(entry.get("created_at") or "").strip(),
        "updated_at": str(entry.get("updated_at") or "").strip(),
        "status": status,
        "size": _source_size_bytes(entry),
        "rating": _normalize_rating(entry.get("rating")),
        "floors": floors,
    }


def _list_stamp(item: dict) -> str:
    return str(item.get("updated_at") or item.get("created_at") or "")


def list_drawings(artifacts: Path) -> dict:
    catalog = load_drawing_catalog(artifacts)
    files = _load_task_map(artifacts)["files"]
    drawings = []
    for entry in catalog["drawings"]:
        item = _summary(entry, artifacts)
        if item:
            item["task_id"] = _latest_task_id(
                files,
                drawing_id=item["drawing_id"],
                source_filename=item["source_filename"],
            )
            drawings.append(item)
    drawings.sort(key=_list_stamp, reverse=True)
    return {"drawings": drawings}


def _find_entry(catalog: dict, drawing_id: str) -> dict | None:
    for entry in catalog["drawings"]:
        if _safe_id(entry.get("drawing_id")) == drawing_id:
            return entry
    return None


def _folder_dir(artifacts: Path, folder: str) -> Path:
    root = artifacts.resolve()
    spelled = Path(resolve_existing_path(str(root / folder)))
    try:
        target = spelled.resolve()
    except OSError:
        target = spelled
    if target == root or not _contained(target, root):
        raise HTTPException(status_code=400, detail="Invalid drawing folder")
    return target


def list_floor_catalog(artifacts: Path, drawing_id: str) -> dict:
    drawing_id = _safe_id(drawing_id) or ""
    if not drawing_id:
        raise HTTPException(status_code=400, detail="Invalid drawing id")
    catalog = load_drawing_catalog(artifacts)
    entry = _find_entry(catalog, drawing_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="Drawing not found")
    summary = _summary(entry, artifacts)
    if summary is None:
        raise HTTPException(status_code=400, detail="Invalid drawing id")
    floors_dir = _folder_dir(artifacts, summary["folder"]) / "floors"
    floors = []
    seen: set[str] = set()
    for item in entry.get("floors") or []:
        if not isinstance(item, dict):
            continue
        floor_id = _safe_id(item.get("floor"))
        if not floor_id or floor_id in seen:
            continue
        seen.add(floor_id)
        floor_dir = floors_dir / floor_id
        floors.append(
            {
                "id": floor_id,
                "title": item.get("title"),
                "status": item.get("status") or "pending",
                "original": (floor_dir / _IMAGE_KINDS["original"]).is_file(),
                "wall": (floor_dir / _IMAGE_KINDS["wall"]).is_file(),
                "validated": (floor_dir / _IMAGE_KINDS["validated"]).is_file(),
                "label": (floor_dir / _IMAGE_KINDS["label"]).is_file(),
                "label_count": _floor_label_count(floor_dir),
            }
        )
    return {
        "drawing_id": summary["drawing_id"],
        "folder": summary["folder"],
        "source_filename": summary["source_filename"],
        "status": summary["status"],
        "rating": summary["rating"],
        "floors": floors,
    }


def _floor_dir(artifacts: Path, drawing_id: str, floor: str) -> tuple[dict, Path]:
    floor_id = _safe_id(floor)
    if not floor_id:
        raise HTTPException(status_code=400, detail="Invalid floor")
    catalog = list_floor_catalog(artifacts, drawing_id)
    if floor_id not in {item["id"] for item in catalog["floors"]}:
        raise HTTPException(status_code=404, detail="Floor not found")
    floors_dir = (_folder_dir(artifacts, catalog["folder"]) / "floors").resolve()
    floor_dir = (floors_dir / floor_id).resolve()
    if not _contained(floor_dir, floors_dir):
        raise HTTPException(status_code=400, detail="Invalid floor path")
    return catalog, floor_dir


def floor_image_path(artifacts: Path, drawing_id: str, floor: str, kind: str) -> Path:
    if kind not in _IMAGE_KINDS:
        raise HTTPException(status_code=400, detail="Unknown image kind")
    _catalog, floor_dir = _floor_dir(artifacts, drawing_id, floor)
    path = (floor_dir / _IMAGE_KINDS[kind]).resolve()
    if not _contained(path, floor_dir):
        raise HTTPException(status_code=400, detail="Invalid image path")
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Image not found")
    return path


def _rgb_color(value: object) -> list[int] | None:
    if not isinstance(value, (list, tuple)) or len(value) < 3:
        return None
    try:
        red, green, blue = (int(value[0]), int(value[1]), int(value[2]))
    except (TypeError, ValueError):
        return None
    return [
        max(0, min(255, red)),
        max(0, min(255, green)),
        max(0, min(255, blue)),
    ]


def _face_id(polygon_mm: object) -> str | None:
    """Stable id for a shared room face (same rule as detect_labels ``_face_key``)."""
    if not isinstance(polygon_mm, list) or not polygon_mm:
        return None
    parts: list[str] = []
    for point in polygon_mm:
        if not isinstance(point, (list, tuple)) or len(point) < 2:
            return None
        try:
            parts.append(f"{round(float(point[0]))},{round(float(point[1]))}")
        except (TypeError, ValueError):
            return None
    digest = hashlib.sha1(";".join(parts).encode("utf-8")).hexdigest()
    return digest[:16]


def _label_faces(item: dict) -> list[dict]:
    faces: list[dict] = []
    for inst in item.get("instances") or []:
        if not isinstance(inst, dict):
            continue
        face_id = _face_id(inst.get("polygon_mm"))
        try:
            area = float(inst.get("area_m2"))
        except (TypeError, ValueError):
            area = None
        if face_id is None or area is None:
            continue
        faces.append({"id": face_id, "area_m2": round(area, 4)})
    if faces:
        return faces
    try:
        area = float(item.get("area_m2"))
    except (TypeError, ValueError):
        return []
    # Fallback when instances are missing: treat the label row as one face.
    name = str(item.get("label") or item.get("layer") or "").strip()
    digest = hashlib.sha1(f"label:{name}:{area}".encode("utf-8")).hexdigest()[:16]
    return [{"id": digest, "area_m2": round(area, 4)}]


def _floor_plot_bounds(bbox_mm: dict) -> tuple[float, float, float, float]:
    """Same plot margin as ``floor_plot_bounds`` in lib_llm_correct."""
    cx0 = float(bbox_mm["xmin"])
    cy0 = float(bbox_mm["ymin"])
    cx1 = float(bbox_mm["xmax"])
    cy1 = float(bbox_mm["ymax"])
    span_y0 = max(cy1 - cy0, 1.0)
    tick = max(span_y0 * 0.025, 1000.0)
    xmin = cx0 - max(tick * 0.8, 1500.0)
    ymin = cy0 - max(tick * 2.5, 4000.0)
    xmax = cx1 + max(tick * 2.0, 3000.0)
    ymax = cy1 + max(tick * 6.5, 9000.0)
    return xmin, ymin, xmax, ymax


def _png_size(meta: dict) -> tuple[int, int] | None:
    stored = meta.get("png_size")
    if isinstance(stored, dict):
        try:
            width = int(stored.get("width") or 0)
            height = int(stored.get("height") or 0)
        except (TypeError, ValueError):
            return None
        if width > 0 and height > 0:
            return width, height
    if isinstance(stored, (list, tuple)) and len(stored) >= 2:
        try:
            width = int(stored[0])
            height = int(stored[1])
        except (TypeError, ValueError):
            return None
        if width > 0 and height > 0:
            return width, height
    return None


def _load_original_overlay(floor_dir: Path) -> dict | None:
    """Pixel transform for drawing label polygons on ``floor_original.png``."""
    path = (floor_dir / _ORIGINAL_META).resolve()
    if not _contained(path, floor_dir) or not path.is_file():
        return None
    try:
        meta = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(meta, dict):
        return None
    bbox = meta.get("bbox_mm")
    if not isinstance(bbox, dict):
        return None
    size = _png_size(meta)
    if size is None:
        return None
    width, height = size
    try:
        xmin, ymin, xmax, ymax = _floor_plot_bounds(bbox)
    except (KeyError, TypeError, ValueError):
        return None
    content_w = width - _PNG_PAD_RIGHT
    content_h = height - _PNG_PAD_TOP - _PNG_PAD_BOTTOM
    if content_w <= 0 or content_h <= 0:
        return None
    span_x = max(xmax - xmin, 1.0)
    span_y = max(ymax - ymin, 1.0)
    return {
        "width": width,
        "height": height,
        "xmin": xmin,
        "ymin": ymin,
        "xmax": xmax,
        "ymax": ymax,
        "span_x": span_x,
        "span_y": span_y,
        "content_w": content_w,
        "content_h": content_h,
    }


def _mm_to_px(x: float, y: float, overlay: dict) -> list[float]:
    px = (x - overlay["xmin"]) / overlay["span_x"] * overlay["content_w"]
    py = _PNG_PAD_TOP + (overlay["ymax"] - y) / overlay["span_y"] * overlay["content_h"]
    return [round(px, 2), round(py, 2)]


def _polygon_px_points(raw: object, overlay: dict) -> list[list[float]] | None:
    if not isinstance(raw, list) or len(raw) < 3:
        return None
    points: list[list[float]] = []
    for point in raw:
        if not isinstance(point, (list, tuple)) or len(point) < 2:
            return None
        try:
            points.append(_mm_to_px(float(point[0]), float(point[1]), overlay))
        except (TypeError, ValueError):
            return None
    return points if len(points) >= 3 else None


def _polygons_px(item: dict, overlay: dict | None) -> list[list[list[float]]]:
    if overlay is None:
        return []
    polygons: list[list[list[float]]] = []
    for inst in item.get("instances") or []:
        if not isinstance(inst, dict):
            continue
        points = _polygon_px_points(inst.get("polygon_mm"), overlay)
        if points:
            polygons.append(points)
    return polygons


def _tag_anchor_px(inst: dict, overlay: dict) -> list[float] | None:
    """Prefer instance seed (x, y); fall back to polygon centroid."""
    try:
        return _mm_to_px(float(inst["x"]), float(inst["y"]), overlay)
    except (KeyError, TypeError, ValueError):
        pass
    points = _polygon_px_points(inst.get("polygon_mm"), overlay)
    if not points:
        return None
    xs = [pt[0] for pt in points]
    ys = [pt[1] for pt in points]
    return [round(sum(xs) / len(xs), 2), round(sum(ys) / len(ys), 2)]


def _instance_tags_px(item: dict, overlay: dict | None, fallback_name: str) -> list[dict]:
    """Name + area badges for the summary overlay (same content as label PNG tags)."""
    if overlay is None:
        return []
    tags: list[dict] = []
    for inst in item.get("instances") or []:
        if not isinstance(inst, dict):
            continue
        anchor = _tag_anchor_px(inst, overlay)
        if anchor is None:
            continue
        try:
            area = float(inst.get("area_m2"))
        except (TypeError, ValueError):
            continue
        text = str(inst.get("text") or fallback_name).strip() or fallback_name
        tags.append(
            {
                "x": anchor[0],
                "y": anchor[1],
                "text": text,
                "area_m2": round(area, 4),
            }
        )
    return tags


def list_floor_labels(artifacts: Path, drawing_id: str, floor: str) -> dict:
    """Slim label legend from ``floor_label_detected.json`` (color · name · area)."""
    catalog, floor_dir = _floor_dir(artifacts, drawing_id, floor)
    path = (floor_dir / _LABEL_JSON).resolve()
    if not _contained(path, floor_dir):
        raise HTTPException(status_code=400, detail="Invalid label path")
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Label data not found")
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise HTTPException(
            status_code=500,
            detail="floor_label_detected.json을 읽지 못했습니다.",
        ) from exc
    if not isinstance(loaded, dict):
        raise HTTPException(status_code=500, detail="floor_label_detected.json 형식이 올바르지 않습니다.")

    overlay_tf = _load_original_overlay(floor_dir)
    labels = []
    for item in loaded.get("labels") or []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("label") or item.get("layer") or "").strip()
        if not name:
            continue
        color = _rgb_color(item.get("color"))
        if color is None:
            continue
        faces = _label_faces(item)
        try:
            area = float(item.get("area_m2"))
        except (TypeError, ValueError):
            area = sum(face["area_m2"] for face in faces) if faces else None
        if area is None:
            continue
        labels.append(
            {
                "label": name,
                "color": color,
                "area_m2": round(area, 4),
                "faces": faces,
                "polygons_px": _polygons_px(item, overlay_tf),
                "tags": _instance_tags_px(item, overlay_tf, name),
            }
        )
    labels.sort(key=lambda row: (-float(row["area_m2"]), str(row["label"])))

    unique_faces: dict[str, float] = {}
    for row in labels:
        for face in row["faces"]:
            unique_faces.setdefault(face["id"], float(face["area_m2"]))
    unique_area = round(sum(unique_faces.values()), 4)
    try:
        reported = float(loaded.get("unique_face_area_m2"))
    except (TypeError, ValueError):
        reported = unique_area

    payload: dict = {
        "drawing_id": catalog["drawing_id"],
        "floor": _safe_id(floor),
        "label_count": len(labels),
        "unique_face_area_m2": reported if reported > 0 else unique_area,
        "labels": labels,
    }
    if overlay_tf is not None:
        payload["overlay"] = {
            "width": overlay_tf["width"],
            "height": overlay_tf["height"],
        }
    return payload


def _forget_drawing_tasks(artifacts: Path, drawing_id: str) -> None:
    """Drop task-map rows tied to this drawing. Upload originals stay untouched."""
    payload = _load_task_map(artifacts)
    files = payload["files"]
    kept = {
        key: item
        for key, item in files.items()
        if not (isinstance(item, dict) and _safe_id(item.get("drawing_id")) == drawing_id)
    }
    if len(kept) != len(files):
        _write_task_map(artifacts, {"files": kept})


def delete_drawing(artifacts: Path, drawing_id: str) -> dict:
    """Remove artifact folder + catalog/task-map rows. Keep upload originals."""
    drawing_id = _safe_id(drawing_id) or ""
    if not drawing_id:
        raise HTTPException(status_code=400, detail="Invalid drawing id")
    catalog = load_drawing_catalog(artifacts)
    entry = _find_entry(catalog, drawing_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="Drawing not found")
    summary = _summary(entry, artifacts)
    if summary is None:
        raise HTTPException(status_code=400, detail="Invalid drawing id")
    folder_dir = _folder_dir(artifacts, summary["folder"])
    if folder_dir.is_dir():
        shutil.rmtree(folder_dir)
    elif folder_dir.exists():
        raise HTTPException(status_code=400, detail="Drawing folder is not a directory")
    catalog["drawings"] = [
        item
        for item in catalog["drawings"]
        if _safe_id(item.get("drawing_id")) != drawing_id
    ]
    _write_catalog(artifacts, catalog)
    _forget_drawing_tasks(artifacts, drawing_id)
    return {
        "ok": True,
        "drawing_id": drawing_id,
        "folder": summary["folder"],
        "source_deleted": False,
    }


def set_drawing_rating(artifacts: Path, drawing_id: str, rating: str) -> dict:
    """Persist pending / good / neutral / bad / review / confirmed / retest on the drawing catalog row."""
    drawing_id = _safe_id(drawing_id) or ""
    if not drawing_id:
        raise HTTPException(status_code=400, detail="Invalid drawing id")
    normalized = _normalize_rating(rating)
    if str(rating or "").strip().lower() not in _RATING_VALUES:
        raise HTTPException(status_code=400, detail="평가는 대기, 좋음, 중립, 나쁨, 검토, 확인, 다시 중 하나여야 합니다.")
    catalog = load_drawing_catalog(artifacts)
    entry = _find_entry(catalog, drawing_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="Drawing not found")
    entry["rating"] = normalized
    # Rating is marking-only; do not bump updated_at or list order.
    _write_catalog(artifacts, catalog)
    summary = _summary(entry, artifacts)
    assert summary is not None
    return {"ok": True, "drawing_id": drawing_id, "rating": summary["rating"]}


@router.post("/task-link")
def link_drawing_task(body: DrawingTaskLink, request: Request) -> dict:
    """Map the task created by 시작 to the uploaded drawing. A later start wins."""
    user_id = require_user_id(request)
    task_id = body.task_id.strip()
    if not re.fullmatch(r"[0-9a-fA-F-]{16,80}", task_id):
        raise HTTPException(status_code=400, detail="작업 번호가 올바르지 않습니다.")
    from application.task_store import get_task

    if get_task(task_id, user_id) is None:
        raise HTTPException(status_code=404, detail="작업을 찾지 못했습니다.")
    record = remember_drawing_task(
        _artifacts_dir(user_id),
        file_name=body.file_name,
        task_id=task_id,
        drawing_id=body.drawing_id,
    )
    return {"ok": True, **record}


@router.get("")
@router.get("/")
def get_drawings(request: Request) -> dict:
    user_id = require_user_id(request)
    return list_drawings(_artifacts_dir(user_id))


@router.get("/{drawing_id}/floors")
def get_drawing_floors(drawing_id: str, request: Request) -> dict:
    user_id = require_user_id(request)
    return list_floor_catalog(_artifacts_dir(user_id), drawing_id)


@router.get("/{drawing_id}/floors/{floor}/labels")
def get_drawing_floor_labels(drawing_id: str, floor: str, request: Request) -> dict:
    user_id = require_user_id(request)
    return list_floor_labels(_artifacts_dir(user_id), drawing_id, floor)


@router.get("/{drawing_id}/floors/{floor}/{kind}")
def get_drawing_floor_image(
    drawing_id: str,
    floor: str,
    kind: str,
    request: Request,
) -> FileResponse:
    user_id = require_user_id(request)
    path = floor_image_path(_artifacts_dir(user_id), drawing_id, floor, kind)
    filename = f"{floor}_{path.name}"
    return FileResponse(
        path,
        media_type="image/png",
        filename=filename,
        content_disposition_type="inline",
        headers={"Cache-Control": "private, max-age=60"},
    )


@router.patch("/{drawing_id}/rating")
def patch_drawing_rating(drawing_id: str, body: DrawingRatingBody, request: Request) -> dict:
    user_id = require_user_id(request)
    return set_drawing_rating(_artifacts_dir(user_id), drawing_id, body.rating)


@router.delete("/{drawing_id}")
def delete_drawing_route(drawing_id: str, request: Request) -> dict:
    user_id = require_user_id(request)
    return delete_drawing(_artifacts_dir(user_id), drawing_id)
