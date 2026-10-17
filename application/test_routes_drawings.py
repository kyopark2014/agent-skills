"""Drawing catalog ids may be Hangul. Path segments still cannot escape artifacts."""

from __future__ import annotations

import json
import tempfile
import unicodedata
import unittest
from pathlib import Path

from fastapi import HTTPException

from unittest.mock import patch

from application.api.routes_drawings import (
    _artifacts_dir,
    list_drawings,
    list_floor_catalog,
    list_floor_labels,
    remember_drawing_task,
    set_drawing_rating,
)


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
            self.assertEqual(
                listed["drawings"][0]["floors"],
                [{"id": "1F", "title": "1 층 평 면 도"}],
            )

            nfd_id = unicodedata.normalize("NFD", drawing_id)
            floors = list_floor_catalog(artifacts, nfd_id)
            self.assertEqual(floors["drawing_id"], drawing_id)
            self.assertEqual(floors["floors"][0]["id"], "1F")
            self.assertTrue(floors["floors"][0]["original"])
            self.assertEqual(floors["floors"][0]["label_count"], 0)

            (floor_dir / "floor_label_detected.json").write_text(
                json.dumps(
                    {
                        "labels": [
                            {"label": "회의실", "area_m2": 12.0, "color": [1, 2, 3]},
                            {"label": "", "area_m2": 1.0, "color": [1, 2, 3]},
                        ]
                    }
                ),
                encoding="utf-8",
            )
            floors = list_floor_catalog(artifacts, drawing_id)
            self.assertEqual(floors["floors"][0]["label_count"], 1)

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

    def test_latest_start_is_the_drawing_log_task(self):
        with tempfile.TemporaryDirectory() as raw:
            artifacts = Path(raw)
            (artifacts / "drawing_list.json").write_text(
                json.dumps(
                    {
                        "drawings": [
                            {
                                "drawing_id": "hotel",
                                "folder": "hotel",
                                "source_filename": "073.경주부티호텔 (1).dxf",
                                "created_at": "2026-10-15T01:00:00+09:00",
                                "status": "ready",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            remember_drawing_task(
                artifacts,
                file_name="073.경주부티호텔 (1).dxf",
                task_id="11111111-1111-1111-1111-111111111111",
                started_at="2026-10-15T01:00:00+09:00",
            )
            remember_drawing_task(
                artifacts,
                file_name="073.경주부티호텔 (1).dxf",
                task_id="22222222-2222-2222-2222-222222222222",
                drawing_id="hotel",
                started_at="2026-10-15T07:00:00+09:00",
            )
            remember_drawing_task(
                artifacts,
                file_name="073.경주부티호텔 (1).dxf",
                task_id="00000000-0000-0000-0000-000000000000",
                started_at="2026-10-15T02:00:00+09:00",
            )
            listed = list_drawings(artifacts)
            self.assertEqual(listed["drawings"][0]["task_id"], "22222222-2222-2222-2222-222222222222")

    def test_floor_labels_come_from_detected_json(self):
        with tempfile.TemporaryDirectory() as raw:
            artifacts = Path(raw)
            drawing_id = "hotel"
            floor_dir = artifacts / drawing_id / "floors" / "1F"
            floor_dir.mkdir(parents=True)
            (artifacts / "drawing_list.json").write_text(
                json.dumps(
                    {
                        "drawings": [
                            {
                                "drawing_id": drawing_id,
                                "folder": drawing_id,
                                "source_filename": "hotel.dxf",
                                "status": "ready",
                                "floors": [{"floor": "1F", "title": "1F", "status": "ready"}],
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            shared_poly = [[0.0, 0.0], [1000.0, 0.0], [1000.0, 1000.0], [0.0, 1000.0]]
            (floor_dir / "floor_original_meta.json").write_text(
                json.dumps(
                    {
                        "bbox_mm": {
                            "xmin": 0.0,
                            "ymin": 0.0,
                            "xmax": 10000.0,
                            "ymax": 8000.0,
                        },
                        "png_size": {"width": 1400, "height": 1280},
                    }
                ),
                encoding="utf-8",
            )
            (floor_dir / "floor_label_detected.json").write_text(
                json.dumps(
                    {
                        "label_count": 3,
                        "instance_count": 3,
                        "unique_face_area_m2": 127.87,
                        "labels": [
                            {
                                "label": "미화",
                                "layer": "미화",
                                "color": [229, 96, 96],
                                "count": 1,
                                "area_m2": 7.37,
                                "instances": [
                                    {
                                        "text": "미화",
                                        "x": 15.0,
                                        "y": 15.0,
                                        "area_m2": 7.37,
                                        "polygon_mm": [[10, 10], [20, 10], [20, 20], [10, 20]],
                                    }
                                ],
                            },
                            {
                                "label": "보일러",
                                "layer": "보일러",
                                "color": [40, 180, 99],
                                "count": 1,
                                "area_m2": 14.8295,
                                "instances": [
                                    {
                                        "text": "보일러",
                                        "x": 500.0,
                                        "y": 500.0,
                                        "area_m2": 14.8295,
                                        "polygon_mm": shared_poly,
                                    }
                                ],
                            },
                            {
                                "label": "현관",
                                "layer": "현관",
                                "color": [96, 160, 229],
                                "count": 1,
                                "area_m2": 14.8295,
                                "instances": [
                                    {
                                        "text": "현관",
                                        "x": 500.0,
                                        "y": 500.0,
                                        "area_m2": 14.8295,
                                        "polygon_mm": shared_poly,
                                    }
                                ],
                            },
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            labels = list_floor_labels(artifacts, drawing_id, "1F")
            self.assertEqual(labels["drawing_id"], drawing_id)
            self.assertEqual(labels["floor"], "1F")
            self.assertEqual(labels["label_count"], 3)
            self.assertEqual(
                [item["label"] for item in labels["labels"]],
                ["보일러", "현관", "미화"],
            )
            self.assertEqual(labels["labels"][0]["area_m2"], 14.8295)
            self.assertEqual(labels["unique_face_area_m2"], 127.87)
            face_ids = {face["id"] for item in labels["labels"] for face in item["faces"]}
            self.assertEqual(len(face_ids), 2)
            self.assertNotIn("instances", labels["labels"][0])
            self.assertEqual(labels["overlay"], {"width": 1400, "height": 1280})
            for item in labels["labels"]:
                self.assertTrue(item["polygons_px"])
                self.assertTrue(item["tags"])
                for poly in item["polygons_px"]:
                    self.assertGreaterEqual(len(poly), 3)
                    for x, y in poly:
                        self.assertGreaterEqual(x, 0)
                        self.assertGreaterEqual(y, 0)
                        self.assertLessEqual(x, 1400)
                        self.assertLessEqual(y, 1280)
                for tag in item["tags"]:
                    self.assertEqual(tag["text"], item["label"])
                    self.assertGreaterEqual(tag["x"], 0)
                    self.assertGreaterEqual(tag["y"], 0)
                    self.assertLessEqual(tag["x"], 1400)
                    self.assertLessEqual(tag["y"], 1280)
                    self.assertGreater(tag["area_m2"], 0)

    def test_drawing_rating_defaults_to_pending_and_updates(self):
        with tempfile.TemporaryDirectory() as raw:
            artifacts = Path(raw)
            drawing_id = "hotel"
            (artifacts / drawing_id / "floors" / "1F").mkdir(parents=True)
            (artifacts / "drawing_list.json").write_text(
                json.dumps(
                    {
                        "drawings": [
                            {
                                "drawing_id": drawing_id,
                                "folder": drawing_id,
                                "source_filename": "hotel.dxf",
                                "status": "ready",
                                "floors": [],
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            listed = list_drawings(artifacts)
            self.assertEqual(listed["drawings"][0]["rating"], "pending")
            self.assertIsNone(listed["drawings"][0]["size"])
            floors = list_floor_catalog(artifacts, drawing_id)
            self.assertEqual(floors["rating"], "pending")
            updated = set_drawing_rating(artifacts, drawing_id, "good")
            self.assertEqual(updated["rating"], "good")
            listed = list_drawings(artifacts)
            self.assertEqual(listed["drawings"][0]["rating"], "good")
            floors = list_floor_catalog(artifacts, drawing_id)
            self.assertEqual(floors["rating"], "good")
            reviewed = set_drawing_rating(artifacts, drawing_id, "review")
            self.assertEqual(reviewed["rating"], "review")
            listed = list_drawings(artifacts)
            self.assertEqual(listed["drawings"][0]["rating"], "review")
            confirmed = set_drawing_rating(artifacts, drawing_id, "confirmed")
            self.assertEqual(confirmed["rating"], "confirmed")
            retested = set_drawing_rating(artifacts, drawing_id, "retest")
            self.assertEqual(retested["rating"], "retest")
            waiting = set_drawing_rating(artifacts, drawing_id, "pending")
            self.assertEqual(waiting["rating"], "pending")
            with self.assertRaises(HTTPException):
                set_drawing_rating(artifacts, drawing_id, "excellent")

    def test_drawing_rating_does_not_bump_list_order(self):
        with tempfile.TemporaryDirectory() as raw:
            artifacts = Path(raw)
            (artifacts / "drawing_list.json").write_text(
                json.dumps(
                    {
                        "drawings": [
                            {
                                "drawing_id": "newer_edit",
                                "folder": "newer_edit",
                                "created_at": "2026-10-14T01:00:00+09:00",
                                "updated_at": "2026-10-15T07:00:00+09:00",
                                "status": "ready",
                            },
                            {
                                "drawing_id": "older_edit",
                                "folder": "older_edit",
                                "created_at": "2026-10-15T01:00:00+09:00",
                                "updated_at": "2026-10-15T02:00:00+09:00",
                                "status": "ready",
                            },
                        ]
                    }
                ),
                encoding="utf-8",
            )
            before = list_drawings(artifacts)
            self.assertEqual(
                [item["drawing_id"] for item in before["drawings"]],
                ["newer_edit", "older_edit"],
            )
            set_drawing_rating(artifacts, "older_edit", "good")
            after = list_drawings(artifacts)
            self.assertEqual(
                [item["drawing_id"] for item in after["drawings"]],
                ["newer_edit", "older_edit"],
            )
            self.assertEqual(after["drawings"][1]["rating"], "good")
            self.assertEqual(
                after["drawings"][1]["updated_at"],
                "2026-10-15T02:00:00+09:00",
            )

    def test_drawing_list_includes_source_size_bytes(self):
        with tempfile.TemporaryDirectory() as raw:
            artifacts = Path(raw)
            drawing_id = "hotel"
            (artifacts / drawing_id / "floors" / "1F").mkdir(parents=True)
            (artifacts / "drawing_list.json").write_text(
                json.dumps(
                    {
                        "drawings": [
                            {
                                "drawing_id": drawing_id,
                                "folder": drawing_id,
                                "source_filename": "hotel.dxf",
                                "source_size_bytes": 3670016,
                                "status": "ready",
                                "floors": [],
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            listed = list_drawings(artifacts)
            self.assertEqual(listed["drawings"][0]["size"], 3670016)

    def test_status_done_when_all_floors_have_label_png(self):
        with tempfile.TemporaryDirectory() as raw:
            artifacts = Path(raw)
            drawing_id = "clubhouse_multi"
            for floor in ("1F", "2F"):
                floor_dir = artifacts / drawing_id / "floors" / floor
                floor_dir.mkdir(parents=True)
            (artifacts / "drawing_list.json").write_text(
                json.dumps(
                    {
                        "drawings": [
                            {
                                "drawing_id": drawing_id,
                                "folder": drawing_id,
                                "source_filename": "clubhouse.dxf",
                                "status": "ready",
                                "floors": [
                                    {"floor": "1F", "title": "1F", "status": "ready"},
                                    {"floor": "2F", "title": "2F", "status": "ready"},
                                ],
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            listed = list_drawings(artifacts)
            self.assertEqual(listed["drawings"][0]["status"], "ready")

            (artifacts / drawing_id / "floors" / "1F" / "floor_label_detected.png").write_bytes(b"png")
            listed = list_drawings(artifacts)
            self.assertEqual(listed["drawings"][0]["status"], "ready")

            (artifacts / drawing_id / "floors" / "2F" / "floor_label_detected.png").write_bytes(b"png")
            listed = list_drawings(artifacts)
            self.assertEqual(listed["drawings"][0]["status"], "done")
            floors = list_floor_catalog(artifacts, drawing_id)
            self.assertEqual(floors["status"], "done")


if __name__ == "__main__":
    unittest.main()
