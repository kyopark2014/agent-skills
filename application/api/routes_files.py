import logging
import os
import uuid
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse

from application.api.routes_auth import require_user_id
from application import utils
from application.viewer_html import build_markdown_viewer_page

logger = logging.getLogger("routes_files")

router = APIRouter(prefix="/api/files", tags=["files"])

IMAGE_ALLOWED_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".gif"}

TEXT_VIEWER_EXTENSIONS = {
    ".txt",
    ".md",
    ".markdown",
    ".csv",
    ".json",
    ".py",
    ".js",
    ".ts",
    ".tsx",
    ".jsx",
    ".yml",
    ".yaml",
    ".xml",
    ".rst",
    ".html",
    ".htm",
}

INLINE_BINARY_EXTENSIONS = {
    ".pdf",
    ".png",
    ".jpg",
    ".jpeg",
    ".webp",
    ".gif",
}

TEXT_VIEWER_MAX_BYTES = 2 * 1024 * 1024  # 2 MiB


def _validate_image_filename(filename: str) -> str:
    name = os.path.basename(filename or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="File name is required")
    ext = os.path.splitext(name)[1].lower()
    if ext not in IMAGE_ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported image type: {ext or '(none)'}",
        )
    # Avoid collisions when multiple pastes share a generic name
    stem = os.path.splitext(name)[0] or "pasted"
    unique = uuid.uuid4().hex[:10]
    return f"{stem}_{unique}{ext}"


@router.post("/upload")
async def upload_file(request: Request, file: UploadFile = File(...)):
    """Upload an image to S3 (images/{user_id}/) for chat attachment. No Knowledge Base sync."""
    user_id = require_user_id(request)

    file_name = _validate_image_filename(file.filename or "pasted.png")
    file_bytes = await file.read()
    if not file_bytes:
        raise HTTPException(status_code=400, detail="Empty file")

    upload_result = utils.upload_to_s3(file_bytes, file_name, user_id=user_id)
    if not upload_result:
        raise HTTPException(status_code=500, detail="Failed to upload file to S3")
    if not upload_result.get("url"):
        raise HTTPException(
            status_code=500,
            detail="File uploaded but sharing URL is not configured",
        )

    logger.info(
        "File upload complete: user=%s file=%s s3_key=%s url=%s",
        user_id,
        file_name,
        upload_result.get("s3_key"),
        upload_result.get("url"),
    )

    return {
        "ok": True,
        "file_name": upload_result["file_name"],
        "s3_key": upload_result["s3_key"],
        "url": upload_result["url"],
        "content_type": upload_result.get("content_type"),
    }


@router.post("/load")
async def load_file(request: Request, file: UploadFile = File(...)):
    """Save a Load-files attachment under ``.session_storage/{user}/upload/``.

    Returns the absolute ``workspace_path`` for chat ``files``.
    """
    user_id = require_user_id(request)
    name = (file.filename or "").strip() or "upload.bin"
    try:
        file_bytes = await file.read()
        result = utils.save_session_upload(name, file_bytes, user_id=user_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    except Exception:
        logger.exception("Load-file upload failed: user=%s file=%s", user_id, name)
        raise HTTPException(status_code=500, detail="Failed to save uploaded file") from None

    return {
        "ok": True,
        "file_name": result["file_name"],
        "workspace_path": result["workspace_path"],
        "content_type": result.get("content_type"),
        "bytes": result.get("bytes"),
    }


def _build_simple_text_viewer(filename: str, body: str) -> str:
    escaped_name = (
        filename.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )
    escaped_body = (
        body.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    )
    return f"""<!DOCTYPE html>
<html lang="ko">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{escaped_name}</title>
  <style>
    body {{ margin: 0; font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
           background: #0d1117; color: #e6edf3; }}
    header {{ padding: 12px 16px; border-bottom: 1px solid #30363d; font-size: 14px; }}
    pre {{ margin: 0; padding: 16px; white-space: pre-wrap; word-break: break-word;
          font-size: 13px; line-height: 1.5; }}
  </style>
</head>
<body>
  <header>{escaped_name}</header>
  <pre>{escaped_body}</pre>
</body>
</html>
"""


@router.get("/view/{filename:path}")
async def view_loaded_file(request: Request, filename: str):
    """Open a Load-files attachment from the user's upload directory."""
    user_id = require_user_id(request)
    safe_name = os.path.basename(filename or "").strip()
    path = utils.resolve_session_upload_path(user_id, safe_name)
    if not path:
        raise HTTPException(status_code=404, detail="File not found")

    ext = Path(safe_name).suffix.lower()
    content_type = utils._session_upload_content_type(safe_name)

    if ext in TEXT_VIEWER_EXTENSIONS:
        size = os.path.getsize(path)
        if size <= TEXT_VIEWER_MAX_BYTES:
            raw = Path(path).read_bytes()
            try:
                text = raw.decode("utf-8")
            except UnicodeDecodeError:
                text = raw.decode("utf-8", errors="replace")
            if ext in {".md", ".markdown"}:
                page = build_markdown_viewer_page(safe_name, text)
                return HTMLResponse(content=page, media_type="text/html; charset=utf-8")
            return HTMLResponse(content=_build_simple_text_viewer(safe_name, text))

    disposition = "inline" if ext in INLINE_BINARY_EXTENSIONS | TEXT_VIEWER_EXTENSIONS else "attachment"
    headers = {
        "Content-Disposition": f"{disposition}; filename*=UTF-8''{quote(safe_name)}"
    }
    return FileResponse(path, media_type=content_type, headers=headers)
