"""Project drawings listed in ``{user}/artifacts/drawing_list.json``."""

from __future__ import annotations

import colorsys
import copy
import hashlib
import json
import os
import re
import secrets
import shutil
import sys
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
_UNNAMED_RE = re.compile(r"^무명-(\d+)$")
_EXCLUDE_RE = re.compile(r"^제외-(\d+)$")
_IMAGE_KINDS = {
    "original": "floor_original.png",
    "wall": "floor_wall_original.png",
    "validated": "floor_wall_validated.png",
    "label": "floor_label_detected.png",
}
_LABEL_JSON = "floor_label_detected.json"
_MODIFIED_JSON = "floor_label_modified.json"
_PENDING_JSON = "floor_label_pending.json"
_ORIGINAL_META = "floor_original_meta.json"
_VALIDATED_META = "floor_wall_validated_meta.json"
_VALIDATED_DXF = "floor_wall_validated.dxf"


def _named_label_count(payload: dict | None) -> int:
    if not isinstance(payload, dict):
        return 0
    count = 0
    for item in payload.get("labels") or []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("label") or item.get("layer") or "").strip()
        if name:
            count += 1
    return count


def _floor_label_count(floor_dir: Path) -> int:
    """Working-set size: modified if present, otherwise detected. Detected file is kept for logging."""
    modified = _load_label_file(floor_dir / _MODIFIED_JSON)
    if modified is not None:
        return _named_label_count(modified)
    return _named_label_count(_load_label_file(floor_dir / _LABEL_JSON))


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


class DrawingMemoBody(BaseModel):
    text: str


class ManualLabelBody(BaseModel):
    """Point click (x_px,y_px) or drag rectangle (x0/y0 → x1/y1) in PNG pixels.

    Point mode ``boundary``: ``wall`` (WALL 레이어 실) or ``entity`` (모든 선 엔티티).
    ``kind``: ``unnamed`` (무명-N) or ``exclude`` (제외-N, 면적합계에서 제외).
    """

    x_px: float | None = None
    y_px: float | None = None
    x0_px: float | None = None
    y0_px: float | None = None
    x1_px: float | None = None
    y1_px: float | None = None
    boundary: str | None = None
    kind: str | None = None


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
            item["memo_count"] = _memo_count(artifacts, item["folder"])
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


def _load_label_file(path: Path) -> dict | None:
    if not path.is_file():
        return None
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return loaded if isinstance(loaded, dict) else None


def _overlay_from_meta(path: Path, floor_dir: Path) -> dict | None:
    resolved = path.resolve()
    if not _contained(resolved, floor_dir) or not resolved.is_file():
        return None
    try:
        meta = json.loads(resolved.read_text(encoding="utf-8"))
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


def _load_original_overlay(floor_dir: Path) -> dict | None:
    """Pixel transform for label polygons on floor PNGs (validated meta preferred)."""
    for name in (_VALIDATED_META, _ORIGINAL_META):
        overlay = _overlay_from_meta(floor_dir / name, floor_dir)
        if overlay is not None:
            return overlay
    return None


def _mm_to_px(x: float, y: float, overlay: dict) -> list[float]:
    px = (x - overlay["xmin"]) / overlay["span_x"] * overlay["content_w"]
    py = _PNG_PAD_TOP + (overlay["ymax"] - y) / overlay["span_y"] * overlay["content_h"]
    return [round(px, 2), round(py, 2)]


def _px_to_mm(px: float, py: float, overlay: dict) -> tuple[float, float]:
    x = overlay["xmin"] + (float(px) / overlay["content_w"]) * overlay["span_x"]
    y = overlay["ymax"] - ((float(py) - _PNG_PAD_TOP) / overlay["content_h"]) * overlay["span_y"]
    return x, y


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


def _slim_label_rows(
    items: list,
    overlay_tf: dict | None,
    *,
    source: str,
    detected_faces: set[str] | None = None,
) -> list[dict]:
    labels: list[dict] = []
    for item in items:
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
        face_ids = {face["id"] for face in faces}
        if source == "detected":
            from_detected = True
        elif source == "pending":
            from_detected = False
        elif detected_faces is not None:
            from_detected = bool(face_ids & detected_faces)
        else:
            from_detected = False
        labels.append(
            {
                "label": name,
                "color": color,
                "area_m2": round(area, 4),
                "faces": faces,
                "polygons_px": _polygons_px(item, overlay_tf),
                "tags": _instance_tags_px(item, overlay_tf, name),
                "source": source,
                "from_detected": from_detected,
            }
        )
    return labels


def _pending_path(floor_dir: Path) -> Path:
    return (floor_dir / _PENDING_JSON).resolve()


def _modified_labels_payload(validated: Path, labels: list) -> dict:
    return {
        "source_dxf": str(validated) if validated.is_file() else "",
        "label_count": len(labels),
        "instance_count": len(labels),
        "unique_face_area_m2": round(
            sum(float(item.get("area_m2") or 0) for item in labels if isinstance(item, dict)),
            4,
        ),
        "labels": labels,
    }


def _clone_label_items(payload: dict | None) -> list[dict]:
    items: list[dict] = []
    for item in (payload or {}).get("labels") or []:
        if isinstance(item, dict):
            items.append(copy.deepcopy(item))
    return items


def _modified_is_additions_only(modified: dict, detected: dict | None) -> bool:
    """Legacy modified files stored only manual adds (no overlap with detected faces)."""
    det_faces = _collect_face_ids(detected)
    if not det_faces:
        return False
    mod_faces = _collect_face_ids(modified)
    if not mod_faces:
        return True
    return len(mod_faces & det_faces) == 0


def _ensure_modified_working_set(floor_dir: Path) -> dict | None:
    """If modified is additions-only, seed detected labels into it. Never deletes detected."""
    modified_path = (floor_dir / _MODIFIED_JSON).resolve()
    if not _contained(modified_path, floor_dir):
        return None
    modified = _load_label_file(modified_path)
    if modified is None:
        return None
    detected = _load_label_file(floor_dir / _LABEL_JSON)
    if not _modified_is_additions_only(modified, detected):
        return modified
    merged = _clone_label_items(detected)
    merged.extend(
        item for item in (modified.get("labels") or []) if isinstance(item, dict)
    )
    validated = floor_dir / _VALIDATED_DXF
    payload = _modified_labels_payload(validated, merged)
    _write_modified_labels(modified_path, payload)
    return payload


def list_floor_labels(artifacts: Path, drawing_id: str, floor: str) -> dict:
    """Slim label legend from the working set (modified, else detected) plus pending.

    ``floor_label_detected.json`` is kept on disk for logging even when modified exists.
    """
    catalog, floor_dir = _floor_dir(artifacts, drawing_id, floor)
    detected_path = (floor_dir / _LABEL_JSON).resolve()
    modified_path = (floor_dir / _MODIFIED_JSON).resolve()
    pending_path = _pending_path(floor_dir)
    if (
        not _contained(detected_path, floor_dir)
        or not _contained(modified_path, floor_dir)
        or not _contained(pending_path, floor_dir)
    ):
        raise HTTPException(status_code=400, detail="Invalid label path")

    detected = _load_label_file(detected_path)
    modified = _load_label_file(modified_path)
    pending = _load_label_file(pending_path)
    if detected is None and modified is None and pending is None:
        if detected_path.is_file() or modified_path.is_file() or pending_path.is_file():
            raise HTTPException(status_code=500, detail="라벨 JSON을 읽지 못했습니다.")
        raise HTTPException(status_code=404, detail="Label data not found")

    if modified is not None:
        modified = _ensure_modified_working_set(floor_dir) or modified

    overlay_tf = _load_original_overlay(floor_dir)
    detected_faces = _collect_face_ids(detected)
    if modified is not None:
        labels = _slim_label_rows(
            (modified or {}).get("labels") or [],
            overlay_tf,
            source="modified",
            detected_faces=detected_faces,
        )
    else:
        labels = _slim_label_rows(
            (detected or {}).get("labels") or [],
            overlay_tf,
            source="detected",
            detected_faces=detected_faces,
        )
    pending_rows = _slim_label_rows(
        (pending or {}).get("labels") or [],
        overlay_tf,
        source="pending",
        detected_faces=detected_faces,
    )
    labels.extend(pending_rows)
    labels.sort(key=lambda row: (-float(row["area_m2"]), str(row["label"])))

    unique_faces: dict[str, float] = {}
    exclude_faces: dict[str, float] = {}
    for row in labels:
        target = (
            exclude_faces
            if _is_exclude_area_label(str(row.get("label") or ""))
            else unique_faces
        )
        for face in row["faces"]:
            target.setdefault(face["id"], float(face["area_m2"]))
    unique_area = round(sum(unique_faces.values()) - sum(exclude_faces.values()), 4)
    reported = unique_area
    if detected is not None and modified is None and pending is None:
        try:
            reported = float(detected.get("unique_face_area_m2"))
        except (TypeError, ValueError):
            reported = unique_area

    payload: dict = {
        "drawing_id": catalog["drawing_id"],
        "floor": _safe_id(floor),
        "label_count": len(labels),
        "pending_count": len(pending_rows),
        "unique_face_area_m2": reported if reported > 0 else unique_area,
        "labels": labels,
    }
    if overlay_tf is not None:
        payload["overlay"] = {
            "width": overlay_tf["width"],
            "height": overlay_tf["height"],
        }
    return payload


def _next_numbered_label(existing: list, pattern: re.Pattern[str], prefix: str) -> str:
    max_n = 0
    for item in existing:
        if not isinstance(item, dict):
            continue
        match = pattern.match(str(item.get("label") or "").strip())
        if match:
            max_n = max(max_n, int(match.group(1)))
    return f"{prefix}-{max_n + 1}"


def _next_unnamed_label(existing: list) -> str:
    return _next_numbered_label(existing, _UNNAMED_RE, "무명")


def _next_exclude_label(existing: list) -> str:
    return _next_numbered_label(existing, _EXCLUDE_RE, "제외")


def _is_exclude_area_label(label: str) -> bool:
    return bool(_EXCLUDE_RE.match(str(label or "").strip()))


def _normalize_manual_label_kind(kind: str | None) -> str:
    text = str(kind or "unnamed").strip().lower()
    return text if text in ("unnamed", "exclude") else "unnamed"


def _rgb_for_index(index: int) -> list[int]:
    hue = (index * 0.618033988749895) % 1.0
    red, green, blue = colorsys.hsv_to_rgb(hue, 0.58, 0.90)
    return [int(red * 255), int(green * 255), int(blue * 255)]


def _collect_face_ids(*payloads: dict | None) -> set[str]:
    ids: set[str] = set()
    for payload in payloads:
        if not isinstance(payload, dict):
            continue
        for item in payload.get("labels") or []:
            if not isinstance(item, dict):
                continue
            for face in _label_faces(item):
                ids.add(face["id"])
    return ids


def _write_modified_labels(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _import_detect_labels():
    app_dir = Path(__file__).resolve().parents[1]
    candidates = (
        app_dir / "skills" / "drawing-totalroom" / "scripts",
        app_dir.parent
        / "runtime_agent"
        / "strands"
        / "skills"
        / "drawing-totalroom"
        / "scripts",
    )
    scripts = next((path for path in candidates if path.is_dir()), None)
    if scripts is None:
        raise HTTPException(
            status_code=500,
            detail="drawing-totalroom scripts를 찾을 수 없습니다.",
        )
    scripts_str = str(scripts)
    if scripts_str not in sys.path:
        sys.path.insert(0, scripts_str)
    import detect_labels as labels  # noqa: WPS433
    import evaluate_room as room  # noqa: WPS433

    return labels, room


def _detect_face_at(
    validated_dxf: Path,
    x: float,
    y: float,
    *,
    boundary: str = "entity",
) -> tuple[dict | None, str | None, float, float]:
    """Face at the click seed.

    ``boundary=wall``: WALL/WINDOW/COLUMN/DOOR segments (기존 라벨과 동일).
    ``boundary=entity``: 모든 LINE/LWPOLYLINE 엔티티.
    No nudge — manual clicks must stay on the pointed region.
    """
    try:
        import ezdxf
    except ImportError as exc:
        raise HTTPException(status_code=500, detail="ezdxf가 설치되어 있지 않습니다.") from exc
    labels, room = _import_detect_labels()
    use_wall = str(boundary or "").strip().lower() == "wall"
    try:
        doc = ezdxf.readfile(str(validated_dxf))
        msp = doc.modelspace()
        boxes = room.column_boxes(msp)
        segs = room.wall_segments(msp, boxes) if use_wall else room.entity_segments(msp, boxes)
        # Automatic totalroom uses detect_one_with_nudge for OCR seeds that fall in
        # tiny pockets. Manual UI clicks must not jump into a neighbor room.
        found, reason = labels.detect_one(msp, boxes, segs, x, y, door="close")
        if found is None and not use_wall and reason == "벽이 실을 닫지 않습니다":
            reason = "엔티티가 영역을 닫지 않습니다"
    except Exception as exc:  # noqa: BLE001
        return None, str(exc) or exc.__class__.__name__, x, y
    return found, reason, x, y


def _shoelace_m2(pts: list[list[float]]) -> float:
    if len(pts) < 3:
        return 0.0
    total = 0.0
    for index, point in enumerate(pts):
        nxt = pts[(index + 1) % len(pts)]
        total += float(point[0]) * float(nxt[1]) - float(nxt[0]) * float(point[1])
    return abs(total) * 0.5 / 1_000_000.0


def add_manual_floor_label(
    artifacts: Path,
    drawing_id: str,
    floor: str,
    *,
    x_px: float | None = None,
    y_px: float | None = None,
    x0_px: float | None = None,
    y0_px: float | None = None,
    x1_px: float | None = None,
    y1_px: float | None = None,
    boundary: str | None = None,
    kind: str | None = None,
) -> dict:
    """Append ``무명-N`` or ``제외-N`` to pending.

    Point click: wall- or entity-bounded face. Rect drag: free-form rectangle.
    ``kind=exclude`` names the label ``제외-N`` (omitted from area totals).
    """
    catalog, floor_dir = _floor_dir(artifacts, drawing_id, floor)
    overlay_tf = _load_original_overlay(floor_dir)
    if overlay_tf is None:
        raise HTTPException(status_code=400, detail="좌표 변환용 메타를 찾지 못했습니다.")

    label_kind = _normalize_manual_label_kind(kind)
    validated = (floor_dir / _VALIDATED_DXF).resolve()
    # validated DXF is only required for click-to-face; rect mode needs meta only.
    rect_mode = None not in (x0_px, y0_px, x1_px, y1_px)
    point_mode = x_px is not None and y_px is not None
    if not rect_mode and not point_mode:
        raise HTTPException(status_code=400, detail="클릭 좌표 또는 사각형 좌표가 필요합니다.")

    if rect_mode:
        assert x0_px is not None and y0_px is not None and x1_px is not None and y1_px is not None
        left, right = sorted((float(x0_px), float(x1_px)))
        top, bottom = sorted((float(y0_px), float(y1_px)))
        if right - left < 4 or bottom - top < 4:
            raise HTTPException(status_code=400, detail="사각형이 너무 작습니다.")
        width = float(overlay_tf["width"])
        height = float(overlay_tf["height"])
        for px, py in ((left, top), (right, bottom)):
            if not (0 <= px <= width and 0 <= py <= height):
                raise HTTPException(status_code=400, detail="사각형이 도면 범위를 벗어났습니다.")
        corners_px = [(left, top), (right, top), (right, bottom), (left, bottom)]
        polygon_mm = [
            [round(xy[0], 1), round(xy[1], 1)]
            for xy in (_px_to_mm(px, py, overlay_tf) for px, py in corners_px)
        ]
        # PNG y grows down; mm y grows up — keep CCW-ish by using mm corners in order.
        xs = [p[0] for p in polygon_mm]
        ys = [p[1] for p in polygon_mm]
        x_lo, x_hi = min(xs), max(xs)
        y_lo, y_hi = min(ys), max(ys)
        polygon_mm = [
            [x_lo, y_lo],
            [x_hi, y_lo],
            [x_hi, y_hi],
            [x_lo, y_hi],
        ]
        area = _shoelace_m2(polygon_mm)
        if area <= 0:
            raise HTTPException(status_code=400, detail="사각형 면적이 0입니다.")
        seed_x = (x_lo + x_hi) / 2.0
        seed_y = (y_lo + y_hi) / 2.0
        print(
            f"[manual-label] {drawing_id}/{floor} rect "
            f"px=({left:.1f},{top:.1f})-({right:.1f},{bottom:.1f}) "
            f"area={area:.2f} m2",
            flush=True,
        )
        return _append_pending_unnamed(
            artifacts,
            catalog,
            floor_dir,
            floor,
            polygon_mm=polygon_mm,
            seed_x=seed_x,
            seed_y=seed_y,
            area=area,
            width_m=(x_hi - x_lo) / 1000.0,
            height_m=(y_hi - y_lo) / 1000.0,
            kind=label_kind,
        )

    assert x_px is not None and y_px is not None
    if not (0 <= x_px <= overlay_tf["width"] and 0 <= y_px <= overlay_tf["height"]):
        raise HTTPException(status_code=400, detail="클릭 좌표가 도면 범위를 벗어났습니다.")
    if not _contained(validated, floor_dir) or not validated.is_file():
        raise HTTPException(status_code=404, detail="floor_wall_validated.dxf를 찾지 못했습니다.")

    x_mm, y_mm = _px_to_mm(x_px, y_px, overlay_tf)
    face_boundary = str(boundary or "entity").strip().lower()
    if face_boundary not in ("wall", "entity"):
        face_boundary = "entity"
    print(
        f"[manual-label] {drawing_id}/{floor} kind={label_kind} boundary={face_boundary} "
        f"px=({x_px:.1f},{y_px:.1f}) mm=({x_mm:.1f},{y_mm:.1f})",
        flush=True,
    )
    found, reason, seed_x, seed_y = _detect_face_at(
        validated, x_mm, y_mm, boundary=face_boundary
    )
    if found is None:
        print(f"[manual-label] miss: {reason}", flush=True)
        fallback = (
            "벽이 실을 닫지 않아 라벨을 추가할 수 없습니다."
            if face_boundary == "wall"
            else "엔티티가 영역을 닫지 않아 라벨을 추가할 수 없습니다."
        )
        raise HTTPException(status_code=422, detail=reason or fallback)
    print(
        f"[manual-label] hit area={float(found['area_m2']):.2f} m2 "
        f"seed=({seed_x:.1f},{seed_y:.1f})",
        flush=True,
    )
    polygon_mm = [[round(float(px), 1), round(float(py), 1)] for px, py in found["pts"]]
    return _append_pending_unnamed(
        artifacts,
        catalog,
        floor_dir,
        floor,
        polygon_mm=polygon_mm,
        seed_x=seed_x,
        seed_y=seed_y,
        area=float(found["area_m2"]),
        width_m=float(found.get("width_m") or 0),
        height_m=float(found.get("height_m") or 0),
        column_protrusion_m2=float(found.get("column_protrusion_m2") or 0),
        drawing_area_m2=found.get("drawing_area_m2"),
        boundary_doors=found.get("boundary_doors") or [],
        kind=label_kind,
    )


def _append_pending_unnamed(
    artifacts: Path,
    catalog: dict,
    floor_dir: Path,
    floor: str,
    *,
    polygon_mm: list[list[float]],
    seed_x: float,
    seed_y: float,
    area: float,
    width_m: float,
    height_m: float,
    column_protrusion_m2: float = 0.0,
    drawing_area_m2: object = None,
    boundary_doors: list | None = None,
    kind: str | None = None,
) -> dict:
    validated = floor_dir / _VALIDATED_DXF
    pending_path = _pending_path(floor_dir)
    if not _contained(pending_path, floor_dir):
        raise HTTPException(status_code=400, detail="Invalid label path")
    detected = _load_label_file(floor_dir / _LABEL_JSON)
    modified = _load_label_file(floor_dir / _MODIFIED_JSON)
    if modified is not None:
        modified = _ensure_modified_working_set(floor_dir) or modified
    pending = _load_label_file(pending_path) or {"labels": []}
    face_id = _face_id(polygon_mm)
    # Working set is modified when present; otherwise detected. Detected file stays on disk.
    existing_faces = (
        _collect_face_ids(modified, pending)
        if modified is not None
        else _collect_face_ids(detected, pending)
    )
    if face_id and face_id in existing_faces:
        raise HTTPException(status_code=409, detail="이미 라벨이 있는 실입니다.")

    modified_labels = [
        item for item in (modified or {}).get("labels") or [] if isinstance(item, dict)
    ]
    pending_labels = [item for item in pending.get("labels") or [] if isinstance(item, dict)]
    label_kind = _normalize_manual_label_kind(kind)
    existing_named = modified_labels + pending_labels
    name = (
        _next_exclude_label(existing_named)
        if label_kind == "exclude"
        else _next_unnamed_label(existing_named)
    )
    color = _rgb_for_index(len(modified_labels) + len(pending_labels))
    area = round(float(area), 4)
    instance = {
        "text": name,
        "x": round(float(seed_x), 1),
        "y": round(float(seed_y), 1),
        "area_m2": area,
        "column_protrusion_m2": round(float(column_protrusion_m2), 4),
        "width_m": round(float(width_m), 4),
        "height_m": round(float(height_m), 4),
        "drawing_area_m2": drawing_area_m2,
        "boundary_doors": boundary_doors or [],
        "polygon_mm": polygon_mm,
    }
    pending_labels.append(
        {
            "label": name,
            "layer": name,
            "color": color,
            "count": 1,
            "area_m2": area,
            "instances": [instance],
        }
    )
    _write_modified_labels(pending_path, _modified_labels_payload(validated, pending_labels))
    return list_floor_labels(artifacts, catalog["drawing_id"], floor)


def _empty_floor_labels(catalog: dict, floor: str, floor_dir: Path) -> dict:
    payload: dict = {
        "drawing_id": catalog["drawing_id"],
        "floor": _safe_id(floor),
        "label_count": 0,
        "pending_count": 0,
        "unique_face_area_m2": 0.0,
        "labels": [],
    }
    overlay_tf = _load_original_overlay(floor_dir)
    if overlay_tf is not None:
        payload["overlay"] = {
            "width": overlay_tf["width"],
            "height": overlay_tf["height"],
        }
    return payload


def undo_manual_floor_label(
    artifacts: Path,
    drawing_id: str,
    floor: str,
    *,
    label: str | None = None,
) -> dict:
    """Remove one pending manual label (by name, or the last pending one)."""
    catalog, floor_dir = _floor_dir(artifacts, drawing_id, floor)
    pending_path = _pending_path(floor_dir)
    if not _contained(pending_path, floor_dir):
        raise HTTPException(status_code=400, detail="Invalid label path")
    pending = _load_label_file(pending_path)
    existing = [item for item in (pending or {}).get("labels") or [] if isinstance(item, dict)]
    if not existing:
        raise HTTPException(status_code=404, detail="되돌릴 임시 라벨이 없습니다.")

    target = str(label or "").strip()
    if target:
        index = next(
            (i for i, item in enumerate(existing) if str(item.get("label") or "").strip() == target),
            None,
        )
        if index is None:
            raise HTTPException(status_code=404, detail=f"'{target}' 임시 라벨을 찾지 못했습니다.")
        existing.pop(index)
    else:
        existing.pop()

    if not existing:
        if pending_path.is_file():
            pending_path.unlink()
    else:
        validated = floor_dir / _VALIDATED_DXF
        _write_modified_labels(pending_path, _modified_labels_payload(validated, existing))

    if (
        _load_label_file(floor_dir / _LABEL_JSON) is None
        and _load_label_file(floor_dir / _MODIFIED_JSON) is None
        and _load_label_file(pending_path) is None
    ):
        return _empty_floor_labels(catalog, floor, floor_dir)
    return list_floor_labels(artifacts, catalog["drawing_id"], floor)


def cancel_pending_floor_labels(artifacts: Path, drawing_id: str, floor: str) -> dict:
    """Discard all pending manual labels without saving."""
    catalog, floor_dir = _floor_dir(artifacts, drawing_id, floor)
    pending_path = _pending_path(floor_dir)
    if not _contained(pending_path, floor_dir):
        raise HTTPException(status_code=400, detail="Invalid label path")
    if pending_path.is_file():
        pending_path.unlink()
    if (
        _load_label_file(floor_dir / _LABEL_JSON) is None
        and _load_label_file(floor_dir / _MODIFIED_JSON) is None
    ):
        return _empty_floor_labels(catalog, floor, floor_dir)
    return list_floor_labels(artifacts, catalog["drawing_id"], floor)


def commit_pending_floor_labels(artifacts: Path, drawing_id: str, floor: str) -> dict:
    """Merge pending into ``floor_label_modified.json`` (seeded from detected) and clear pending.

    ``floor_label_detected.json`` is left untouched for logging / baseline.
    """
    catalog, floor_dir = _floor_dir(artifacts, drawing_id, floor)
    pending_path = _pending_path(floor_dir)
    modified_path = (floor_dir / _MODIFIED_JSON).resolve()
    detected_path = (floor_dir / _LABEL_JSON).resolve()
    if (
        not _contained(pending_path, floor_dir)
        or not _contained(modified_path, floor_dir)
        or not _contained(detected_path, floor_dir)
    ):
        raise HTTPException(status_code=400, detail="Invalid label path")
    pending = _load_label_file(pending_path)
    pending_labels = [
        item for item in (pending or {}).get("labels") or [] if isinstance(item, dict)
    ]
    if not pending_labels:
        raise HTTPException(status_code=404, detail="저장할 임시 라벨이 없습니다.")

    detected = _load_label_file(detected_path)
    modified = _load_label_file(modified_path)
    if modified is None:
        merged = _clone_label_items(detected)
    else:
        modified = _ensure_modified_working_set(floor_dir) or modified
        merged = [
            item for item in (modified.get("labels") or []) if isinstance(item, dict)
        ]
    pending_faces = _collect_face_ids(pending)
    merged = [
        item
        for item in merged
        if not ({face["id"] for face in _label_faces(item)} & pending_faces)
    ]
    merged.extend(pending_labels)
    validated = floor_dir / _VALIDATED_DXF
    _write_modified_labels(modified_path, _modified_labels_payload(validated, merged))
    if pending_path.is_file():
        pending_path.unlink()
    return list_floor_labels(artifacts, catalog["drawing_id"], floor)


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


_MEMO_FILE = "drawing_memos.json"
_MEMO_TEXT_MAX = 2000


def _memo_path(artifacts: Path, folder: str) -> Path:
    return _folder_dir(artifacts, folder) / _MEMO_FILE


def _load_memos(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if isinstance(loaded, list):
        items = loaded
    elif isinstance(loaded, dict) and isinstance(loaded.get("memos"), list):
        items = loaded["memos"]
    else:
        return []
    out: list[dict] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        text = str(item.get("text") or "").strip()
        created = str(item.get("created_at") or "").strip()
        memo_id = str(item.get("id") or "").strip()
        if not text or not created:
            continue
        out.append(
            {
                "id": memo_id or secrets.token_hex(8),
                "text": text,
                "created_at": created,
            }
        )
    out.sort(key=lambda item: item["created_at"], reverse=True)
    return out


def _memo_count(artifacts: Path, folder: str) -> int:
    try:
        return len(_load_memos(_memo_path(artifacts, folder)))
    except HTTPException:
        return 0


def _write_memos(path: Path, memos: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"memos": memos}
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def list_drawing_memos(artifacts: Path, drawing_id: str) -> dict:
    """Return memo comments for a drawing, newest first."""
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
    memos = _load_memos(_memo_path(artifacts, summary["folder"]))
    return {
        "drawing_id": drawing_id,
        "folder": summary["folder"],
        "memos": memos,
    }


def add_drawing_memo(artifacts: Path, drawing_id: str, text: str) -> dict:
    """Append a short comment. Does not bump drawing list order."""
    drawing_id = _safe_id(drawing_id) or ""
    if not drawing_id:
        raise HTTPException(status_code=400, detail="Invalid drawing id")
    cleaned = unicodedata.normalize("NFC", str(text or "")).strip()
    if not cleaned:
        raise HTTPException(status_code=400, detail="메모 내용을 입력하세요.")
    if len(cleaned) > _MEMO_TEXT_MAX:
        raise HTTPException(
            status_code=400,
            detail=f"메모는 {_MEMO_TEXT_MAX}자 이하여야 합니다.",
        )
    catalog = load_drawing_catalog(artifacts)
    entry = _find_entry(catalog, drawing_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="Drawing not found")
    summary = _summary(entry, artifacts)
    if summary is None:
        raise HTTPException(status_code=400, detail="Invalid drawing id")
    path = _memo_path(artifacts, summary["folder"])
    memos = _load_memos(path)
    memo = {
        "id": secrets.token_hex(8),
        "text": cleaned,
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
    }
    memos.insert(0, memo)
    _write_memos(path, memos)
    return {
        "ok": True,
        "drawing_id": drawing_id,
        "folder": summary["folder"],
        "memo": memo,
        "memos": memos,
    }


def update_drawing_memo(artifacts: Path, drawing_id: str, memo_id: str, text: str) -> dict:
    """Update one memo's text. Does not bump drawing list order or reorder memos."""
    drawing_id = _safe_id(drawing_id) or ""
    memo_id = str(memo_id or "").strip()
    if not drawing_id:
        raise HTTPException(status_code=400, detail="Invalid drawing id")
    if not memo_id or not re.fullmatch(r"[0-9a-fA-F]{8,64}", memo_id):
        raise HTTPException(status_code=400, detail="Invalid memo id")
    cleaned = unicodedata.normalize("NFC", str(text or "")).strip()
    if not cleaned:
        raise HTTPException(status_code=400, detail="메모 내용을 입력하세요.")
    if len(cleaned) > _MEMO_TEXT_MAX:
        raise HTTPException(
            status_code=400,
            detail=f"메모는 {_MEMO_TEXT_MAX}자 이하여야 합니다.",
        )
    catalog = load_drawing_catalog(artifacts)
    entry = _find_entry(catalog, drawing_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="Drawing not found")
    summary = _summary(entry, artifacts)
    if summary is None:
        raise HTTPException(status_code=400, detail="Invalid drawing id")
    path = _memo_path(artifacts, summary["folder"])
    memos = _load_memos(path)
    updated = None
    for item in memos:
        if item["id"] == memo_id:
            item["text"] = cleaned
            updated = item
            break
    if updated is None:
        raise HTTPException(status_code=404, detail="Memo not found")
    _write_memos(path, memos)
    return {
        "ok": True,
        "drawing_id": drawing_id,
        "folder": summary["folder"],
        "memo": updated,
        "memos": memos,
    }


def delete_drawing_memo(artifacts: Path, drawing_id: str, memo_id: str) -> dict:
    """Remove one memo by id. Does not bump drawing list order."""
    drawing_id = _safe_id(drawing_id) or ""
    memo_id = str(memo_id or "").strip()
    if not drawing_id:
        raise HTTPException(status_code=400, detail="Invalid drawing id")
    if not memo_id or not re.fullmatch(r"[0-9a-fA-F]{8,64}", memo_id):
        raise HTTPException(status_code=400, detail="Invalid memo id")
    catalog = load_drawing_catalog(artifacts)
    entry = _find_entry(catalog, drawing_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="Drawing not found")
    summary = _summary(entry, artifacts)
    if summary is None:
        raise HTTPException(status_code=400, detail="Invalid drawing id")
    path = _memo_path(artifacts, summary["folder"])
    memos = _load_memos(path)
    kept = [item for item in memos if item["id"] != memo_id]
    if len(kept) == len(memos):
        raise HTTPException(status_code=404, detail="Memo not found")
    _write_memos(path, kept)
    return {
        "ok": True,
        "drawing_id": drawing_id,
        "folder": summary["folder"],
        "memos": kept,
    }


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


@router.post("/{drawing_id}/floors/{floor}/labels/manual")
def post_drawing_floor_label_manual(
    drawing_id: str,
    floor: str,
    body: ManualLabelBody,
    request: Request,
) -> dict:
    user_id = require_user_id(request)
    return add_manual_floor_label(
        _artifacts_dir(user_id),
        drawing_id,
        floor,
        x_px=body.x_px,
        y_px=body.y_px,
        x0_px=body.x0_px,
        y0_px=body.y0_px,
        x1_px=body.x1_px,
        y1_px=body.y1_px,
        boundary=body.boundary,
        kind=body.kind,
    )


@router.delete("/{drawing_id}/floors/{floor}/labels/manual")
def delete_drawing_floor_label_manual(
    drawing_id: str,
    floor: str,
    request: Request,
    label: str | None = None,
) -> dict:
    user_id = require_user_id(request)
    return undo_manual_floor_label(
        _artifacts_dir(user_id),
        drawing_id,
        floor,
        label=label,
    )


@router.post("/{drawing_id}/floors/{floor}/labels/manual/commit")
def post_drawing_floor_label_manual_commit(
    drawing_id: str,
    floor: str,
    request: Request,
) -> dict:
    user_id = require_user_id(request)
    return commit_pending_floor_labels(_artifacts_dir(user_id), drawing_id, floor)


@router.post("/{drawing_id}/floors/{floor}/labels/manual/cancel")
def post_drawing_floor_label_manual_cancel(
    drawing_id: str,
    floor: str,
    request: Request,
) -> dict:
    user_id = require_user_id(request)
    return cancel_pending_floor_labels(_artifacts_dir(user_id), drawing_id, floor)


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


@router.get("/{drawing_id}/memos")
def get_drawing_memos(drawing_id: str, request: Request) -> dict:
    user_id = require_user_id(request)
    return list_drawing_memos(_artifacts_dir(user_id), drawing_id)


@router.post("/{drawing_id}/memos")
def post_drawing_memo(drawing_id: str, body: DrawingMemoBody, request: Request) -> dict:
    user_id = require_user_id(request)
    return add_drawing_memo(_artifacts_dir(user_id), drawing_id, body.text)


@router.patch("/{drawing_id}/memos/{memo_id}")
def patch_drawing_memo(
    drawing_id: str, memo_id: str, body: DrawingMemoBody, request: Request
) -> dict:
    user_id = require_user_id(request)
    return update_drawing_memo(_artifacts_dir(user_id), drawing_id, memo_id, body.text)


@router.delete("/{drawing_id}/memos/{memo_id}")
def delete_drawing_memo_route(drawing_id: str, memo_id: str, request: Request) -> dict:
    user_id = require_user_id(request)
    return delete_drawing_memo(_artifacts_dir(user_id), drawing_id, memo_id)


@router.delete("/{drawing_id}")
def delete_drawing_route(drawing_id: str, request: Request) -> dict:
    user_id = require_user_id(request)
    return delete_drawing(_artifacts_dir(user_id), drawing_id)
