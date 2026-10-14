"""Upload-folder drawing picker: extension filter, catalog status, delete guard."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from application.services.upload_drawing_service import (
    UploadDrawingError,
    delete_upload_drawing,
    list_upload_drawings,
)


def _catalog(*entries: dict) -> dict:
    return {"drawings": list(entries)}


class UploadDrawingTests(unittest.TestCase):
    def test_lists_cad_files_and_marks_catalog_usage(self):
        with tempfile.TemporaryDirectory() as raw:
            folder = Path(raw)
            (folder / "ready-plan.dwg").write_bytes(b"dwg")
            (folder / "used-plan.dxf").write_bytes(b"dxf-body")
            (folder / "sheet.dwf").write_bytes(b"dwf")
            (folder / "notes.pdf").write_bytes(b"pdf")
            os.utime(folder / "used-plan.dxf", (1_700_000_000, 1_700_000_300))
            os.utime(folder / "ready-plan.dwg", (1_700_000_000, 1_700_000_200))
            os.utime(folder / "sheet.dwf", (1_700_000_000, 1_700_000_100))
            catalog = _catalog(
                {
                    "drawing_id": "older",
                    "source_filename": "used-plan.dxf",
                    "created_at": "2026-10-01T00:00:00+00:00",
                },
                {
                    "drawing_id": "newer",
                    "source_filename": "used-plan.dxf",
                    "created_at": "2026-10-10T00:00:00+00:00",
                },
            )
            with patch("application.utils.get_user_upload_dir", return_value=str(folder)):
                files = list_upload_drawings("ksdyb", catalog)

        names = [item["file_name"] for item in files]
        self.assertEqual(names, ["used-plan.dxf", "ready-plan.dwg", "sheet.dwf"])
        self.assertNotIn("notes.pdf", names)
        used = files[0]
        self.assertEqual(used["status"], "used")
        self.assertEqual(used["drawing_id"], "newer")
        self.assertEqual(used["size"], len(b"dxf-body"))
        self.assertTrue(used["workspace_path"].endswith("used-plan.dxf"))
        ready = next(item for item in files if item["file_name"] == "ready-plan.dwg")
        self.assertEqual(ready["status"], "ready")
        self.assertIsNone(ready["drawing_id"])

    def test_source_path_counts_as_used(self):
        with tempfile.TemporaryDirectory() as raw:
            folder = Path(raw)
            (folder / "plan.dwg").write_bytes(b"abc")
            with patch("application.utils.get_user_upload_dir", return_value=str(folder)):
                files = list_upload_drawings(
                    "ksdyb",
                    _catalog(
                        {
                            "drawing_id": "plan",
                            "source_path": "/tmp/upload/plan.dwg",
                        }
                    ),
                )
        self.assertEqual(files[0]["status"], "used")
        self.assertEqual(files[0]["drawing_id"], "plan")

    def test_login_without_uploads_reads_ksdyb_upload(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            (root / "lge" / "upload").mkdir(parents=True)
            ksdyb = root / "ksdyb" / "upload"
            ksdyb.mkdir(parents=True)
            (ksdyb / "hotel.dxf").write_bytes(b"dxf")
            (ksdyb / "notes.pdf").write_bytes(b"pdf")
            with patch("application.utils.SESSION_STORAGE_DIR", raw):
                files = list_upload_drawings("lge", {"drawings": []})
        self.assertEqual([item["file_name"] for item in files], ["hotel.dxf"])
        self.assertTrue(files[0]["workspace_path"].endswith(str(ksdyb / "hotel.dxf")))

    def test_delete_rejects_other_types_and_removes_existing_file(self):
        with self.assertRaises(UploadDrawingError) as rejected:
            delete_upload_drawing("ksdyb", "../secret.dwg")
        self.assertEqual(rejected.exception.status_code, 400)

        with self.assertRaises(UploadDrawingError) as other:
            delete_upload_drawing("ksdyb", "notes.pdf")
        self.assertEqual(other.exception.status_code, 400)

        with tempfile.TemporaryDirectory() as raw:
            folder = Path(raw)
            target = folder / "plan.dwf"
            target.write_bytes(b"dwf")
            with patch("application.utils.get_user_upload_dir", return_value=str(folder)):
                result = delete_upload_drawing("ksdyb", "plan.dwf")
                with self.assertRaises(UploadDrawingError) as missing:
                    delete_upload_drawing("ksdyb", "gone.dxf")
        self.assertEqual(result, {"ok": True, "file_name": "plan.dwf"})
        self.assertFalse(target.exists())
        self.assertEqual(missing.exception.status_code, 404)


if __name__ == "__main__":
    unittest.main()
