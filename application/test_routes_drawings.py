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
    add_manual_floor_label,
    cancel_pending_floor_labels,
    commit_pending_floor_labels,
    list_drawings,
    list_floor_catalog,
    list_floor_labels,
    remember_drawing_task,
    set_drawing_rating,
    undo_manual_floor_label,
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

    def test_list_floor_labels_merges_modified(self):
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
                        "unique_face_area_m2": 10.0,
                        "labels": [
                            {
                                "label": "회의실",
                                "color": [229, 96, 96],
                                "area_m2": 10.0,
                                "instances": [
                                    {
                                        "text": "회의실",
                                        "x": 100.0,
                                        "y": 100.0,
                                        "area_m2": 10.0,
                                        "polygon_mm": [[0, 0], [1000, 0], [1000, 1000], [0, 1000]],
                                    }
                                ],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            (floor_dir / "floor_label_modified.json").write_text(
                json.dumps(
                    {
                        "labels": [
                            {
                                "label": "무명-1",
                                "color": [96, 160, 229],
                                "area_m2": 5.5,
                                "instances": [
                                    {
                                        "text": "무명-1",
                                        "x": 2000.0,
                                        "y": 2000.0,
                                        "area_m2": 5.5,
                                        "polygon_mm": [
                                            [1500, 1500],
                                            [2500, 1500],
                                            [2500, 2500],
                                            [1500, 2500],
                                        ],
                                    }
                                ],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            labels = list_floor_labels(artifacts, drawing_id, "1F")
            self.assertEqual(labels["label_count"], 2)
            by_name = {item["label"]: item for item in labels["labels"]}
            self.assertEqual(by_name["회의실"]["source"], "detected")
            self.assertEqual(by_name["무명-1"]["source"], "modified")
            self.assertTrue(by_name["무명-1"]["polygons_px"])
            self.assertTrue(by_name["무명-1"]["tags"])
            floors = list_floor_catalog(artifacts, drawing_id)
            self.assertEqual(floors["floors"][0]["label_count"], 2)

    def test_list_floor_labels_modified_only(self):
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
            (floor_dir / "floor_label_modified.json").write_text(
                json.dumps(
                    {
                        "labels": [
                            {
                                "label": "무명-1",
                                "color": [96, 160, 229],
                                "area_m2": 3.0,
                                "instances": [
                                    {
                                        "text": "무명-1",
                                        "x": 10.0,
                                        "y": 10.0,
                                        "area_m2": 3.0,
                                        "polygon_mm": [[0, 0], [100, 0], [100, 100], [0, 100]],
                                    }
                                ],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            labels = list_floor_labels(artifacts, drawing_id, "1F")
            self.assertEqual(labels["label_count"], 1)
            self.assertEqual(labels["labels"][0]["source"], "modified")

    def test_add_manual_floor_label_writes_unnamed_and_increments(self):
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
            (floor_dir / "floor_wall_validated.dxf").write_text("0\nEOF\n", encoding="utf-8")
            (floor_dir / "floor_label_modified.json").write_text(
                json.dumps(
                    {
                        "labels": [
                            {
                                "label": "무명-1",
                                "color": [1, 2, 3],
                                "area_m2": 4.0,
                                "instances": [
                                    {
                                        "text": "무명-1",
                                        "x": 10.0,
                                        "y": 10.0,
                                        "area_m2": 4.0,
                                        "polygon_mm": [[0, 0], [100, 0], [100, 100], [0, 100]],
                                    }
                                ],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            def fake_detect(_dxf, _x, _y):
                return (
                    {
                        "pts": [[2000.0, 2000.0], [3000.0, 2000.0], [3000.0, 3000.0], [2000.0, 3000.0]],
                        "area_m2": 1.0,
                        "column_protrusion_m2": 0.0,
                        "width_m": 1.0,
                        "height_m": 1.0,
                        "drawing_area_m2": None,
                        "boundary_doors": [],
                    },
                    None,
                    2500.0,
                    2500.0,
                )

            with patch(
                "application.api.routes_drawings._detect_face_at",
                side_effect=fake_detect,
            ):
                result = add_manual_floor_label(
                    artifacts,
                    drawing_id,
                    "1F",
                    x_px=700.0,
                    y_px=640.0,
                )
            names = [item["label"] for item in result["labels"]]
            self.assertIn("무명-2", names)
            self.assertEqual(
                next(item for item in result["labels"] if item["label"] == "무명-2")["source"],
                "pending",
            )
            self.assertEqual(result["pending_count"], 1)
            pending = json.loads((floor_dir / "floor_label_pending.json").read_text(encoding="utf-8"))
            self.assertEqual([item["label"] for item in pending["labels"]], ["무명-2"])
            saved = json.loads((floor_dir / "floor_label_modified.json").read_text(encoding="utf-8"))
            self.assertEqual([item["label"] for item in saved["labels"]], ["무명-1"])

    def test_undo_manual_floor_label_removes_named(self):
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
            (floor_dir / "floor_label_pending.json").write_text(
                json.dumps(
                    {
                        "labels": [
                            {
                                "label": "무명-1",
                                "color": [1, 2, 3],
                                "area_m2": 4.0,
                                "instances": [
                                    {
                                        "text": "무명-1",
                                        "x": 10.0,
                                        "y": 10.0,
                                        "area_m2": 4.0,
                                        "polygon_mm": [[0, 0], [100, 0], [100, 100], [0, 100]],
                                    }
                                ],
                            },
                            {
                                "label": "무명-2",
                                "color": [4, 5, 6],
                                "area_m2": 5.0,
                                "instances": [
                                    {
                                        "text": "무명-2",
                                        "x": 20.0,
                                        "y": 20.0,
                                        "area_m2": 5.0,
                                        "polygon_mm": [
                                            [200, 200],
                                            [300, 200],
                                            [300, 300],
                                            [200, 300],
                                        ],
                                    }
                                ],
                            },
                        ],
                    }
                ),
                encoding="utf-8",
            )
            result = undo_manual_floor_label(artifacts, drawing_id, "1F", label="무명-2")
            self.assertEqual([item["label"] for item in result["labels"]], ["무명-1"])
            self.assertEqual(result["pending_count"], 1)
            empty = undo_manual_floor_label(artifacts, drawing_id, "1F", label="무명-1")
            self.assertEqual(empty["label_count"], 0)
            self.assertEqual(empty.get("pending_count", 0), 0)
            self.assertFalse((floor_dir / "floor_label_pending.json").is_file())

    def test_commit_and_cancel_pending_floor_labels(self):
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
            pending_row = {
                "label": "무명-1",
                "color": [1, 2, 3],
                "area_m2": 4.0,
                "instances": [
                    {
                        "text": "무명-1",
                        "x": 10.0,
                        "y": 10.0,
                        "area_m2": 4.0,
                        "polygon_mm": [[0, 0], [100, 0], [100, 100], [0, 100]],
                    }
                ],
            }
            (floor_dir / "floor_label_pending.json").write_text(
                json.dumps({"labels": [pending_row]}),
                encoding="utf-8",
            )
            committed = commit_pending_floor_labels(artifacts, drawing_id, "1F")
            self.assertEqual(committed["pending_count"], 0)
            self.assertEqual(
                next(item for item in committed["labels"] if item["label"] == "무명-1")["source"],
                "modified",
            )
            self.assertFalse((floor_dir / "floor_label_pending.json").is_file())
            self.assertTrue((floor_dir / "floor_label_modified.json").is_file())

            (floor_dir / "floor_label_pending.json").write_text(
                json.dumps(
                    {
                        "labels": [
                            {
                                **pending_row,
                                "label": "무명-2",
                                "instances": [
                                    {
                                        **pending_row["instances"][0],
                                        "text": "무명-2",
                                        "polygon_mm": [
                                            [200, 200],
                                            [300, 200],
                                            [300, 300],
                                            [200, 300],
                                        ],
                                    }
                                ],
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            cancelled = cancel_pending_floor_labels(artifacts, drawing_id, "1F")
            self.assertEqual(cancelled["pending_count"], 0)
            self.assertFalse((floor_dir / "floor_label_pending.json").is_file())
            self.assertEqual(
                [item["label"] for item in cancelled["labels"]],
                ["무명-1"],
            )

    def test_add_manual_floor_label_rejects_open_face(self):
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
            (floor_dir / "floor_wall_validated.dxf").write_text("0\nEOF\n", encoding="utf-8")
            with patch(
                "application.api.routes_drawings._detect_face_at",
                return_value=(None, "벽이 실을 닫지 않습니다", 1.0, 1.0),
            ):
                with self.assertRaises(HTTPException) as ctx:
                    add_manual_floor_label(
                        artifacts,
                        drawing_id,
                        "1F",
                        x_px=100.0,
                        y_px=100.0,
                    )
            self.assertEqual(ctx.exception.status_code, 422)
            self.assertIn("닫지", str(ctx.exception.detail))


if __name__ == "__main__":
    unittest.main()
