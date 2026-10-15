"""Project drawings listed in ``{user}/artifacts/drawing_list.json``."""

from __future__ import annotations

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


def _summary(entry: dict) -> dict | None:
    drawing_id = _safe_id(entry.get("drawing_id"))
    if not drawing_id:
        return None
    folder = _safe_id(entry.get("folder")) or drawing_id
    return {
        "drawing_id": drawing_id,
        "folder": folder,
        "source_filename": str(entry.get("source_filename") or "").strip(),
        "created_at": str(entry.get("created_at") or "").strip(),
        "updated_at": str(entry.get("updated_at") or "").strip(),
        "status": str(entry.get("status") or "pending").strip() or "pending",
        "floors": _floor_refs(entry),
    }


def _list_stamp(item: dict) -> str:
    return str(item.get("updated_at") or item.get("created_at") or "")


def list_drawings(artifacts: Path) -> dict:
    catalog = load_drawing_catalog(artifacts)
    files = _load_task_map(artifacts)["files"]
    drawings = []
    for entry in catalog["drawings"]:
        item = _summary(entry)
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
    summary = _summary(entry)
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
            }
        )
    return {
        "drawing_id": summary["drawing_id"],
        "folder": summary["folder"],
        "source_filename": summary["source_filename"],
        "status": summary["status"],
        "floors": floors,
    }


def floor_image_path(artifacts: Path, drawing_id: str, floor: str, kind: str) -> Path:
    if kind not in _IMAGE_KINDS:
        raise HTTPException(status_code=400, detail="Unknown image kind")
    floor_id = _safe_id(floor)
    if not floor_id:
        raise HTTPException(status_code=400, detail="Invalid floor")
    catalog = list_floor_catalog(artifacts, drawing_id)
    if floor_id not in {item["id"] for item in catalog["floors"]}:
        raise HTTPException(status_code=404, detail="Floor not found")
    floors_dir = (_folder_dir(artifacts, catalog["folder"]) / "floors").resolve()
    path = (floors_dir / floor_id / _IMAGE_KINDS[kind]).resolve()
    if not _contained(path, floors_dir):
        raise HTTPException(status_code=400, detail="Invalid image path")
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Image not found")
    return path


def _delete_source_file(user_root: Path, entry: dict) -> bool:
    """Delete the uploaded DXF when it lives inside this user's workspace."""
    deleted = False
    source = str(entry.get("source_path") or "").strip()
    if source:
        try:
            src = Path(source).resolve()
        except OSError:
            src = None
        if src and src.is_file() and _contained(src, user_root):
            src.unlink()
            deleted = True
    filename = Path(str(entry.get("source_filename") or "")).name
    if filename and filename not in {".", ".."} and "/" not in filename and "\\" not in filename:
        upload = (user_root / "upload" / filename).resolve()
        if upload.is_file() and _contained(upload, user_root):
            upload.unlink(missing_ok=True)
            deleted = True
    return deleted


def delete_drawing(artifacts: Path, drawing_id: str) -> dict:
    drawing_id = _safe_id(drawing_id) or ""
    if not drawing_id:
        raise HTTPException(status_code=400, detail="Invalid drawing id")
    catalog = load_drawing_catalog(artifacts)
    entry = _find_entry(catalog, drawing_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="Drawing not found")
    summary = _summary(entry)
    if summary is None:
        raise HTTPException(status_code=400, detail="Invalid drawing id")
    folder_dir = _folder_dir(artifacts, summary["folder"])
    if folder_dir.is_dir():
        shutil.rmtree(folder_dir)
    elif folder_dir.exists():
        raise HTTPException(status_code=400, detail="Drawing folder is not a directory")
    user_root = artifacts.resolve().parent
    source_deleted = _delete_source_file(user_root, entry)
    catalog["drawings"] = [
        item
        for item in catalog["drawings"]
        if _safe_id(item.get("drawing_id")) != drawing_id
    ]
    _write_catalog(artifacts, catalog)
    return {
        "ok": True,
        "drawing_id": drawing_id,
        "folder": summary["folder"],
        "source_deleted": source_deleted,
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


@router.delete("/{drawing_id}")
def delete_drawing_route(drawing_id: str, request: Request) -> dict:
    user_id = require_user_id(request)
    return delete_drawing(_artifacts_dir(user_id), drawing_id)
