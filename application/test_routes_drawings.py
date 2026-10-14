"""Drawing catalog ids may be Hangul. Path segments still cannot escape artifacts."""

from __future__ import annotations

import json
import tempfile
import unicodedata
import unittest
from pathlib import Path

from fastapi import HTTPException

from unittest.mock import patch

from application.api.routes_drawings import _artifacts_dir, list_drawings, list_floor_catalog


class DrawingIdTests(unittest.TestCase):
    def test_hangul_drawing_is_listed_and_floors_open(self):
        with tempfile.TemporaryDirectory() as raw:
            artifacts = Path(raw)
            drawing_id = "복요리전문점"
            floor_dir = artifacts / drawing_id / "floors" / "1F"
            floor_dir.mkdir(parents=True)
            (floor_dir / "floor_original.png").write_bytes(b"png")
            catalog = {
                "drawings": [
                    {
                        "drawing_id": drawing_id,
                        "folder": drawing_id,
                        "source_filename": "복요리전문점.dxf",
                        "created_at": "2026-10-14T08:35:14+09:00",
                        "status": "ready",
                        "floors": [
                            {
                                "floor": "1F",
                                "title": "1 층 평 면 도",
                                "status": "ready",
                            }
                        ],
                    }
                ]
            }
            (artifacts / "drawing_list.json").write_text(
                json.dumps(catalog, ensure_ascii=False),
                encoding="utf-8",
            )

            listed = list_drawings(artifacts)
            self.assertEqual([item["drawing_id"] for item in listed["drawings"]], [drawing_id])

            nfd_id = unicodedata.normalize("NFD", drawing_id)
            floors = list_floor_catalog(artifacts, nfd_id)
            self.assertEqual(floors["drawing_id"], drawing_id)
            self.assertEqual(floors["floors"][0]["id"], "1F")
            self.assertTrue(floors["floors"][0]["original"])

    def test_path_escape_ids_are_rejected(self):
        with tempfile.TemporaryDirectory() as raw:
            artifacts = Path(raw)
            (artifacts / "drawing_list.json").write_text(
                json.dumps(
                    {
                        "drawings": [
                            {"drawing_id": "../secret", "folder": "../secret", "status": "ready"},
                            {"drawing_id": "ok/no", "folder": "ok/no", "status": "ready"},
                            {"drawing_id": "clubhouse_multi", "folder": "clubhouse_multi", "status": "ready"},
                        ]
                    }
                ),
                encoding="utf-8",
            )
            listed = list_drawings(artifacts)
            self.assertEqual([item["drawing_id"] for item in listed["drawings"]], ["clubhouse_multi"])
            with self.assertRaises(HTTPException):
                list_floor_catalog(artifacts, "../secret")

    def test_local_login_without_catalog_reads_ksdyb_drawing_list(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            ksdyb = root / "ksdyb" / "artifacts"
            ksdyb.mkdir(parents=True)
            (ksdyb / "drawing_list.json").write_text(
                json.dumps(
                    {
                        "drawings": [
                            {
                                "drawing_id": "garak_plaza",
                                "folder": "garak_plaza",
                                "source_filename": "garak.dxf",
                                "created_at": "2026-10-14T00:00:00+09:00",
                                "status": "ready",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            (root / "lge" / "artifacts").mkdir(parents=True)
            with patch("application.utils.SESSION_STORAGE_DIR", raw):
                artifacts = _artifacts_dir("lge")
            self.assertEqual(artifacts, ksdyb.resolve())
            self.assertEqual(
                [item["drawing_id"] for item in list_drawings(artifacts)["drawings"]],
                ["garak_plaza"],
            )
            own = root / "lge" / "artifacts"
            (own / "drawing_list.json").write_text(
                json.dumps({"drawings": [{"drawing_id": "own_plan", "folder": "own_plan", "status": "ready"}]}),
                encoding="utf-8",
            )
            with patch("application.utils.SESSION_STORAGE_DIR", raw):
                self.assertEqual(_artifacts_dir("lge"), own.resolve())

    def test_list_orders_by_latest_update(self):
        with tempfile.TemporaryDirectory() as raw:
            artifacts = Path(raw)
            (artifacts / "drawing_list.json").write_text(
                json.dumps(
                    {
                        "drawings": [
                            {
                                "drawing_id": "older_edit",
                                "folder": "older_edit",
                                "created_at": "2026-10-15T01:00:00+09:00",
                                "updated_at": "2026-10-15T02:00:00+09:00",
                                "status": "ready",
                            },
                            {
                                "drawing_id": "newer_edit",
                                "folder": "newer_edit",
                                "created_at": "2026-10-14T01:00:00+09:00",
                                "updated_at": "2026-10-15T07:00:00+09:00",
                                "status": "ready",
                            },
                            {
                                "drawing_id": "created_only",
                                "folder": "created_only",
                                "created_at": "2026-10-15T03:00:00+09:00",
                                "status": "ready",
                            },
                        ]
                    }
                ),
                encoding="utf-8",
            )
            listed = list_drawings(artifacts)
            self.assertEqual(
                [item["drawing_id"] for item in listed["drawings"]],
                ["newer_edit", "created_only", "older_edit"],
            )


if __name__ == "__main__":
    unittest.main()
