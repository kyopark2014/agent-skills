"""Drawings already stored under ``{user}/upload`` for the chat picker.

Load files writes to ``.session_storage/{user}/upload``. This picker lists
those dwg/dxf/dwf files without uploading them again.
"""

from __future__ import annotations

import os
import unicodedata
from datetime import datetime, timezone

from application import unicode_paths
from application import utils
from application.api.routes_drawings import (
    _LOCAL_DRAWING_OWNER,
    _artifacts_dir,
    _file_key,
    _load_task_map,
    _safe_id,
)

DRAWING_UPLOAD_EXTENSIONS = {".dwg", ".dxf", ".dwf"}


class UploadDrawingError(Exception):
    """Business failure while listing or deleting an upload drawing."""

    def __init__(self, status_code: int, detail: str) -> None:
        self.status_code = status_code
        self.detail = detail
        super().__init__(detail)


def sanitize_drawing_upload_name(filename: str) -> str:
    """Return an NFC basename limited to drawing uploads."""
    raw = unicodedata.normalize("NFC", str(filename or "").strip())
    if (
        not raw
        or raw in {".", ".."}
        or "/" in raw
        or "\\" in raw
        or raw != os.path.basename(raw)
    ):
        raise UploadDrawingError(400, "파일 이름이 올바르지 않습니다.")
    name = raw.replace("\x00", "")
    if not name or name in {".", ".."}:
        raise UploadDrawingError(400, "파일 이름이 올바르지 않습니다.")
    ext = os.path.splitext(name)[1].lower()
    if ext not in DRAWING_UPLOAD_EXTENSIONS:
        raise UploadDrawingError(400, "dwg, dxf, dwf 파일만 다룰 수 있습니다.")
    return name


def _catalog_drawing_ids(catalog: dict) -> dict[str, str]:
    """Map an NFC source filename (and its casefold) to a drawing id.

    A later ``created_at`` wins when several drawings share one source file.
    """
    ranked: list[tuple[str, str, str]] = []
    for entry in catalog.get("drawings") or []:
        if not isinstance(entry, dict):
            continue
        drawing_id = _safe_id(entry.get("drawing_id"))
        if not drawing_id:
            continue
        created_at = str(entry.get("created_at") or "")
        names: list[str] = []
        for field in ("source_filename", "source_path"):
            raw = str(entry.get(field) or "").strip().replace("\\", "/")
            if not raw:
                continue
            base = unicodedata.normalize("NFC", os.path.basename(raw))
            if base and base not in {".", ".."}:
                names.append(base)
        for name in names:
            ranked.append((created_at, name, drawing_id))
    ranked.sort()
    index: dict[str, str] = {}
    for _created_at, name, drawing_id in ranked:
        index[name] = drawing_id
        index[name.casefold()] = drawing_id
    return index


def _drawing_id_for(index: dict[str, str], file_name: str) -> str | None:
    name = unicodedata.normalize("NFC", file_name)
    if not name:
        return None
    return index.get(name) or index.get(name.casefold())


def _dir_has_drawing(directory: str) -> bool:
    if not directory or not os.path.isdir(directory):
        return False
    try:
        entries = os.scandir(directory)
    except OSError:
        return False
    with entries:
        for entry in entries:
            try:
                if not entry.is_file():
                    continue
            except OSError:
                continue
            ext = os.path.splitext(entry.name)[1].lower()
            if ext in DRAWING_UPLOAD_EXTENSIONS:
                return True
    return False


def resolve_drawing_upload_dir(user_id: str | None) -> str:
    """Upload dir for the picker.

    Local drawings live in ``.session_storage/ksdyb/upload``. A login such as
    ``lge`` has no CAD files of its own, so the picker reads that folder.
    """
    own = os.path.abspath(utils.get_user_upload_dir(user_id))
    if _dir_has_drawing(own):
        return own
    storage = os.path.abspath(utils.SESSION_STORAGE_DIR)
    fallback = os.path.abspath(os.path.join(storage, _LOCAL_DRAWING_OWNER, utils.UPLOAD_SUBDIR))
    if fallback != own and _dir_has_drawing(fallback):
        try:
            if os.path.commonpath([fallback, storage]) == storage:
                return fallback
        except ValueError:
            return own
    return own


def _existing_upload_path(directory: str, file_name: str) -> str | None:
    root = os.path.abspath(directory)
    for spelling in unicode_paths.path_spellings(file_name) or [file_name]:
        path = os.path.abspath(os.path.join(root, spelling))
        try:
            if os.path.commonpath([path, root]) != root:
                continue
        except ValueError:
            continue
        if os.path.isfile(path):
            return path
    return None


def _list_local_uploads(user_id: str | None) -> list[dict]:
    directory = resolve_drawing_upload_dir(user_id)
    if not os.path.isdir(directory):
        return []
    merged: dict[str, dict] = {}
    try:
        entries = os.scandir(directory)
    except OSError as exc:
        raise UploadDrawingError(500, "업로드 도면 목록을 불러오지 못했습니다.") from exc
    with entries:
        for entry in entries:
            try:
                if not entry.is_file():
                    continue
                stat = entry.stat()
            except OSError:
                continue
            name = unicodedata.normalize("NFC", entry.name)
            if not name or "/" in name or "\\" in name:
                continue
            modified = datetime.fromtimestamp(stat.st_mtime, timezone.utc)
            current = {
                "file_name": name,
                "size": int(stat.st_size),
                "created_at": modified.isoformat(),
            }
            previous = merged.get(name)
            if previous is None or current["created_at"] >= previous["created_at"]:
                merged[name] = current
    return list(merged.values())


def _selection_times(user_id: str | None) -> dict[str, str]:
    """Map each uploaded file to the latest time 시작 was pressed for it."""
    try:
        loaded = _load_task_map(_artifacts_dir(user_id or ""))
    except Exception:
        return {}
    stamps: dict[str, str] = {}
    for item in loaded.get("files", {}).values():
        if not isinstance(item, dict):
            continue
        started = str(item.get("started_at") or "")
        if not started:
            continue
        key = _file_key(str(item.get("file_name") or ""))
        if key and started >= stamps.get(key, ""):
            stamps[key] = started
    return stamps


def _upload_list_rank(item: dict, selected: dict[str, str]) -> tuple[int, str]:
    stamp = selected.get(_file_key(str(item.get("file_name") or "")), "")
    if stamp:
        return (1, stamp)
    return (0, str(item.get("created_at") or ""))


def list_upload_drawings(user_id: str | None, catalog: dict) -> list[dict]:
    """List dwg/dxf/dwf uploads and mark ones already present in the drawing catalog."""
    index = _catalog_drawing_ids(catalog if isinstance(catalog, dict) else {})
    files: list[dict] = []
    for item in _list_local_uploads(user_id):
        try:
            name = sanitize_drawing_upload_name(str(item.get("file_name") or ""))
        except UploadDrawingError:
            continue
        drawing_id = _drawing_id_for(index, name)
        upload_dir = resolve_drawing_upload_dir(user_id)
        workspace_path = _existing_upload_path(upload_dir, name) or os.path.join(upload_dir, name)
        files.append(
            {
                "file_name": name,
                "size": int(item.get("size") or 0),
                "created_at": str(item.get("created_at") or ""),
                "status": "used" if drawing_id else "ready",
                "drawing_id": drawing_id,
                "workspace_path": workspace_path,
            }
        )
    selected = _selection_times(user_id)
    files.sort(key=lambda item: _upload_list_rank(item, selected), reverse=True)
    return files


def delete_upload_drawing(user_id: str | None, file_name: str) -> dict:
    """Delete one drawing from the user's Load-files upload directory."""
    safe_name = sanitize_drawing_upload_name(file_name)
    upload_dir = resolve_drawing_upload_dir(user_id)
    deleted = False
    for spelling in unicode_paths.path_spellings(safe_name) or [safe_name]:
        path = os.path.abspath(os.path.join(upload_dir, spelling))
        try:
            if os.path.commonpath([path, upload_dir]) != upload_dir:
                continue
        except ValueError:
            continue
        if os.path.isfile(path):
            os.remove(path)
            deleted = True
    if not deleted:
        resolved = utils.resolve_session_upload_path(user_id, safe_name)
        if resolved and os.path.isfile(resolved):
            os.remove(resolved)
            deleted = True
    if not deleted:
        raise UploadDrawingError(404, "파일을 찾지 못했습니다.")
    return {"ok": True, "file_name": safe_name}
