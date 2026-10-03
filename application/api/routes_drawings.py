"""Project drawings listed in ``{user}/artifacts/drawing_list.json``."""

from __future__ import annotations

import json
import os
import re
import shutil
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse

from application.api.routes_auth import require_user_id
from application import utils

router = APIRouter(prefix="/api/drawings", tags=["drawings"])

_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,80}$")
_IMAGE_KINDS = {
    "original": "floor_original.png",
    "wall": "floor_wall_original.png",
    "validated": "floor_wall_validated.png",
}


def _artifacts_dir(user_id: str) -> Path:
    """``{user}/artifacts`` on the workspace mount, or local session storage."""
    root = Path(utils.get_user_artifacts_dir(user_id)).resolve()
    return root


def _contained(path: Path, root: Path) -> bool:
    try:
        return os.path.commonpath([str(path), str(root)]) == str(root)
    except ValueError:
        return False


def _safe_id(value: object) -> str | None:
    text = str(value or "").strip()
    if not _ID_RE.fullmatch(text):
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
        "status": str(entry.get("status") or "pending").strip() or "pending",
    }


def list_drawings(artifacts: Path) -> dict:
    catalog = load_drawing_catalog(artifacts)
    drawings = []
    for entry in catalog["drawings"]:
        item = _summary(entry)
        if item:
            drawings.append(item)
    drawings.sort(key=lambda item: item.get("created_at") or "", reverse=True)
    return {"drawings": drawings}


def _find_entry(catalog: dict, drawing_id: str) -> dict | None:
    for entry in catalog["drawings"]:
        if _safe_id(entry.get("drawing_id")) == drawing_id:
            return entry
    return None


def _folder_dir(artifacts: Path, folder: str) -> Path:
    root = artifacts.resolve()
    target = (root / folder).resolve()
    if not _contained(target, root):
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
