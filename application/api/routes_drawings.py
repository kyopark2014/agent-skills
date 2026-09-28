"""Floor-plan previews under ``.session_storage/{user}/artifacts/sk_yongin_jiwon/floors``."""

from __future__ import annotations

import os
import re
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse

from application.api.routes_auth import require_user_id
from application import utils

router = APIRouter(prefix="/api/drawings", tags=["drawings"])

DRAWING_ID = "sk_yongin_jiwon"
FLOOR_MIN = 5
FLOOR_MAX = 12
_FLOOR_RE = re.compile(r"^(\d+)F$")
_IMAGE_KINDS = {
    "original": "floor_original.png",
    "wall": "floor_wall_original.png",
    "validated": "floor_wall_validated.png",
}


def _floors_dir(user_id: str) -> Path:
    artifacts = Path(utils.get_user_artifacts_dir(user_id)).resolve()
    floors = (artifacts / DRAWING_ID / "floors").resolve()
    try:
        if os.path.commonpath([str(floors), str(artifacts)]) != str(artifacts):
            raise HTTPException(status_code=400, detail="Invalid drawing path")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid drawing path") from exc
    return floors


def _floor_number(floor: str) -> int | None:
    match = _FLOOR_RE.fullmatch(floor or "")
    if not match:
        return None
    number = int(match.group(1))
    if number < FLOOR_MIN or number > FLOOR_MAX:
        return None
    return number


def list_floor_catalog(user_id: str) -> dict:
    floors_dir = _floors_dir(user_id)
    floors = []
    for number in range(FLOOR_MIN, FLOOR_MAX + 1):
        floor_id = f"{number}F"
        floor_dir = floors_dir / floor_id
        floors.append(
            {
                "id": floor_id,
                "original": (floor_dir / _IMAGE_KINDS["original"]).is_file(),
                "wall": (floor_dir / _IMAGE_KINDS["wall"]).is_file(),
                "validated": (floor_dir / _IMAGE_KINDS["validated"]).is_file(),
            }
        )
    return {"drawing_id": DRAWING_ID, "floors": floors}


def floor_image_path(user_id: str, floor: str, kind: str) -> Path:
    if kind not in _IMAGE_KINDS:
        raise HTTPException(status_code=400, detail="Unknown image kind")
    if _floor_number(floor) is None:
        raise HTTPException(status_code=400, detail="Floor must be between 5F and 12F")
    path = (_floors_dir(user_id) / floor / _IMAGE_KINDS[kind]).resolve()
    floors_dir = _floors_dir(user_id)
    try:
        if os.path.commonpath([str(path), str(floors_dir)]) != str(floors_dir):
            raise HTTPException(status_code=400, detail="Invalid image path")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid image path") from exc
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Image not found")
    return path


@router.get("/floors")
def get_drawing_floors(request: Request) -> dict:
    user_id = require_user_id(request)
    return list_floor_catalog(user_id)


@router.get("/floors/{floor}/{kind}")
def get_drawing_floor_image(floor: str, kind: str, request: Request) -> FileResponse:
    user_id = require_user_id(request)
    path = floor_image_path(user_id, floor, kind)
    filename = f"{floor}_{path.name}"
    return FileResponse(
        path,
        media_type="image/png",
        filename=filename,
        content_disposition_type="inline",
        headers={"Cache-Control": "private, max-age=60"},
    )
