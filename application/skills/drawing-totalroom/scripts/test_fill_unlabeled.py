"""--fill-unlabeled: 래스터 connected components 로 무명을 붙이는지 확인한다."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import ezdxf
from shapely.geometry import Point, Polygon

import detect_labels as labels


def _wall_room(msp, points: list[tuple[float, float]]) -> None:
    wall = {"layer": "WALL"}
    for index, start in enumerate(points):
        end = points[(index + 1) % len(points)]
        msp.add_line(start, end, dxfattribs=wall)


def _two_rooms(msp) -> None:
    # 왼쪽 사무실(라벨 있음), 오른쪽 빈 실. 각 4 m × 4 m.
    # 바깥 박스 + 가운데 칸막이로 두 칸을 만든다(공유 벽 이중선 방지).
    outer = [(0.0, 0.0), (8000.0, 0.0), (8000.0, 4000.0), (0.0, 4000.0)]
    _wall_room(msp, outer)
    msp.add_line((4000.0, 0.0), (4000.0, 4000.0), dxfattribs={"layer": "WALL"})
    msp.add_text("사무실", dxfattribs={"layer": "0", "height": 300}).set_placement(
        (2000.0, 2000.0)
    )


class FillUnlabeledTest(unittest.TestCase):
    def test_adds_synthetic_for_unlabeled_room(self):
        doc = ezdxf.new()
        msp = doc.modelspace()
        _two_rooms(msp)
        boxes = labels.room.column_boxes(msp)
        segs = labels.room.wall_segments(msp, boxes)
        found, reason, sx, sy = labels.detect_one_with_nudge(
            msp, boxes, segs, 2000.0, 2000.0
        )
        self.assertIsNone(reason)
        self.assertIsNotNone(found)
        assert found is not None
        found.update(
            {"text": "사무실", "x": sx, "y": sy, "name": labels.room.norm_name("사무실")}
        )
        detected = [found]
        bbox = {"xmin": -500.0, "ymin": -500.0, "xmax": 8500.0, "ymax": 4500.0}
        detected, _skipped = labels.fill_unlabeled_rooms(
            msp,
            boxes,
            segs,
            detected,
            [],
            "close",
            bbox,
            sample_mm=100.0,
        )
        synth = [inst for inst in detected if inst.get("synthetic")]
        self.assertGreaterEqual(len(synth), 1)
        self.assertTrue(any(inst["text"].startswith("무명_") for inst in synth))
        right = [
            inst
            for inst in synth
            if labels.room.point_in_poly(6000.0, 2000.0, inst["pts"])
        ]
        self.assertEqual(len(right), 1)
        named = [inst for inst in detected if inst["text"] == "사무실"]
        self.assertEqual(len(named), 1)

    def test_same_face_is_one_component(self):
        doc = ezdxf.new()
        msp = doc.modelspace()
        _wall_room(msp, [(0.0, 0.0), (6000.0, 0.0), (6000.0, 6000.0), (0.0, 6000.0)])
        boxes = labels.room.column_boxes(msp)
        segs = labels.room.wall_segments(msp, boxes)
        bbox = {"xmin": -500.0, "ymin": -500.0, "xmax": 6500.0, "ymax": 6500.0}
        comps = labels.segment_unlabeled_components(segs, bbox, cell_mm=100.0)
        self.assertEqual(len(comps), 1)
        detected, _skipped = labels.fill_unlabeled_rooms(
            msp, boxes, segs, [], [], "close", bbox, sample_mm=100.0
        )
        synth = [inst for inst in detected if inst.get("synthetic")]
        self.assertEqual(len(synth), 1)
        self.assertEqual(synth[0]["text"], "무명_001")

    def test_fill_face_snaps_to_walls(self):
        """래스터 틈 대신 벽 안쪽 면까지 붙는다."""
        doc = ezdxf.new()
        msp = doc.modelspace()
        _wall_room(msp, [(0.0, 0.0), (6000.0, 0.0), (6000.0, 6000.0), (0.0, 6000.0)])
        boxes = labels.room.column_boxes(msp)
        segs = labels.room.wall_segments(msp, boxes)
        bbox = {"xmin": -500.0, "ymin": -500.0, "xmax": 6500.0, "ymax": 6500.0}
        comps = labels.segment_unlabeled_components(segs, bbox, cell_mm=100.0)
        self.assertEqual(len(comps), 1)
        raster = Polygon(comps[0]["pts"])
        detected, _skipped = labels.fill_unlabeled_rooms(
            msp, boxes, segs, [], [], "close", bbox, sample_mm=100.0
        )
        synth = [inst for inst in detected if inst.get("synthetic")]
        self.assertEqual(len(synth), 1)
        face = Polygon(synth[0]["pts"])
        # 격자 contour 보다 벽에 가깝고, 바깥 벽 좌표(0/6000)에 붙는다.
        self.assertGreater(face.area, raster.area)
        minx, miny, maxx, maxy = face.bounds
        self.assertLessEqual(abs(minx - 0.0), 1.0)
        self.assertLessEqual(abs(miny - 0.0), 1.0)
        self.assertLessEqual(abs(maxx - 6000.0), 1.0)
        self.assertLessEqual(abs(maxy - 6000.0), 1.0)

    def test_open_alcove_refine_uses_fill_segs(self):
        """개구로 큰 실과 이어진 알코브는 fill_segs refine 으로 벽에 붙는다.

        층 전체 door bridge 가 막은 칸을 원본 segs 로 refine 하면 개구로 새어
        면적 상한에 걸리고 래스터 contour(흰 테두리)가 남는다. 래스터와 같은
        fill_segs 를 쓰면 벽 안쪽 면으로 스냅한다.
        """
        doc = ezdxf.new()
        msp = doc.modelspace()
        wall = {"layer": "WALL"}
        # 홀 10×6 m + 알코브 3×2 m. 공유 벽 개구 2500 mm(> DOOR_GAP)라
        # 로컬 close bridge 로는 안 막고, fill_segs 에만 브리지를 넣는다.
        msp.add_line((0.0, 2000.0), (0.0, 8000.0), dxfattribs=wall)
        msp.add_line((0.0, 8000.0), (10000.0, 8000.0), dxfattribs=wall)
        msp.add_line((10000.0, 8000.0), (10000.0, 2000.0), dxfattribs=wall)
        msp.add_line((3000.0, 2000.0), (10000.0, 2000.0), dxfattribs=wall)
        msp.add_line((0.0, 0.0), (3000.0, 0.0), dxfattribs=wall)
        msp.add_line((3000.0, 0.0), (3000.0, 2000.0), dxfattribs=wall)
        msp.add_line((0.0, 0.0), (0.0, 2000.0), dxfattribs=wall)
        msp.add_line((0.0, 2000.0), (250.0, 2000.0), dxfattribs=wall)
        msp.add_line((2750.0, 2000.0), (3000.0, 2000.0), dxfattribs=wall)

        boxes = labels.room.column_boxes(msp)
        segs = labels.room.wall_segments(msp, boxes)
        bridge = (250.0, 2000.0, 2750.0, 2000.0)
        fill_segs = list(segs) + [bridge]
        seed_x, seed_y = 1500.0, 1000.0
        raster = Polygon(
            [(100.0, 100.0), (2900.0, 100.0), (2900.0, 1900.0), (100.0, 1900.0)]
        )
        raster_area = raster.area / 1_000_000.0

        leak, _ = labels.detect_one(msp, boxes, segs, seed_x, seed_y, door="close")
        self.assertIsNotNone(leak)
        assert leak is not None
        self.assertGreater(
            float(leak["area_m2"]),
            max(
                raster_area * labels.FILL_REFINE_MAX_AREA_RATIO,
                raster_area + labels.FILL_REFINE_MAX_AREA_EXTRA_M2,
            ),
        )
        self.assertIsNone(
            labels._refine_fill_to_wall_face(
                msp, boxes, segs, "close", seed_x, seed_y, raster, raster_area
            )
        )

        refined = labels._refine_fill_to_wall_face(
            msp, boxes, fill_segs, "close", seed_x, seed_y, raster, raster_area
        )
        self.assertIsNotNone(refined)
        assert refined is not None
        face = Polygon(refined["pts"])
        self.assertGreater(face.area, raster.area)
        self.assertLess(float(refined["area_m2"]), 10.0)
        minx, miny, maxx, maxy = face.bounds
        self.assertLessEqual(abs(minx - 0.0), 1.0)
        self.assertLessEqual(abs(miny - 0.0), 1.0)
        self.assertLessEqual(abs(maxx - 3000.0), 1.0)
        self.assertLessEqual(abs(maxy - 2000.0), 1.0)

    def test_skips_pocket_inside_named_room_bbox(self):
        """실명 면이 모서리를 비워 둔 칸은 무명으로 붙이지 않는다."""
        doc = ezdxf.new()
        msp = doc.modelspace()
        # 6 m × 6 m 바깥 + 왼쪽 위 2 m × 2 m 칸막이(모서리 주머니).
        _wall_room(msp, [(0.0, 0.0), (6000.0, 0.0), (6000.0, 6000.0), (0.0, 6000.0)])
        msp.add_lwpolyline(
            [(0.0, 4000.0), (2000.0, 4000.0), (2000.0, 6000.0)],
            dxfattribs={"layer": "WALL"},
        )
        boxes = labels.room.column_boxes(msp)
        segs = labels.room.wall_segments(msp, boxes)
        found, reason, sx, sy = labels.detect_one_with_nudge(
            msp, boxes, segs, 4000.0, 2000.0
        )
        self.assertIsNone(reason)
        self.assertIsNotNone(found)
        assert found is not None
        found.update(
            {"text": "객실", "x": sx, "y": sy, "name": labels.room.norm_name("객실")}
        )
        named = Polygon(found["pts"])
        self.assertFalse(named.covers(Point(1000.0, 5000.0)))
        detected, _skipped = labels.fill_unlabeled_rooms(
            msp,
            boxes,
            segs,
            [found],
            [],
            "close",
            {"xmin": -500.0, "ymin": -500.0, "xmax": 6500.0, "ymax": 6500.0},
            sample_mm=100.0,
        )
        synth = [inst for inst in detected if inst.get("synthetic")]
        pocket = [
            inst
            for inst in synth
            if labels.room.point_in_poly(1000.0, 5000.0, inst["pts"])
        ]
        self.assertEqual(pocket, [])

    def test_does_not_cover_existing_labeled_face(self):
        doc = ezdxf.new()
        msp = doc.modelspace()
        _two_rooms(msp)
        boxes = labels.room.column_boxes(msp)
        segs = labels.room.wall_segments(msp, boxes)
        found, reason, sx, sy = labels.detect_one_with_nudge(
            msp, boxes, segs, 2000.0, 2000.0
        )
        self.assertIsNone(reason)
        assert found is not None
        found.update(
            {"text": "사무실", "x": sx, "y": sy, "name": labels.room.norm_name("사무실")}
        )
        office_poly = Polygon(found["pts"])
        detected, _skipped = labels.fill_unlabeled_rooms(
            msp,
            boxes,
            segs,
            [found],
            [],
            "close",
            {"xmin": -500.0, "ymin": -500.0, "xmax": 8500.0, "ymax": 4500.0},
            sample_mm=100.0,
        )
        for inst in detected:
            if not inst.get("synthetic"):
                continue
            synth_poly = Polygon(inst["pts"])
            overlap = office_poly.intersection(synth_poly).area
            self.assertLessEqual(overlap / max(synth_poly.area, 1.0), labels.OCCUPIED_OVERLAP)

    def test_segment_finds_two_closed_rooms(self):
        doc = ezdxf.new()
        msp = doc.modelspace()
        _two_rooms(msp)
        boxes = labels.room.column_boxes(msp)
        segs = labels.room.wall_segments(msp, boxes)
        comps = labels.segment_unlabeled_components(
            segs,
            {"xmin": -500.0, "ymin": -500.0, "xmax": 8500.0, "ymax": 4500.0},
            cell_mm=100.0,
        )
        self.assertGreaterEqual(len(comps), 2)

    def test_skips_stair_bbox_from_review(self):
        doc = ezdxf.new()
        msp = doc.modelspace()
        _wall_room(msp, [(0.0, 0.0), (6000.0, 0.0), (6000.0, 6000.0), (0.0, 6000.0)])
        boxes = labels.room.column_boxes(msp)
        segs = labels.room.wall_segments(msp, boxes)
        bbox = {"xmin": -500.0, "ymin": -500.0, "xmax": 6500.0, "ymax": 6500.0}
        with tempfile.TemporaryDirectory() as tmp:
            review = Path(tmp) / "review.json"
            review.write_text(
                json.dumps(
                    {
                        "demote_bboxes": [],
                        "promote_bboxes": [
                            {
                                "xmin": 1000.0,
                                "ymin": 1000.0,
                                "xmax": 5000.0,
                                "ymax": 5000.0,
                                "label": "계단",
                            }
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            detected, _skipped = labels.fill_unlabeled_rooms(
                msp,
                boxes,
                segs,
                [],
                [],
                "close",
                bbox,
                sample_mm=100.0,
                review_path=review,
            )
        synth = [inst for inst in detected if inst.get("synthetic")]
        self.assertEqual(synth, [])

    def test_skips_bathroom_bbox_from_review(self):
        doc = ezdxf.new()
        msp = doc.modelspace()
        _wall_room(msp, [(0.0, 0.0), (6000.0, 0.0), (6000.0, 6000.0), (0.0, 6000.0)])
        boxes = labels.room.column_boxes(msp)
        segs = labels.room.wall_segments(msp, boxes)
        with tempfile.TemporaryDirectory() as tmp:
            review = Path(tmp) / "review.json"
            review.write_text(
                json.dumps(
                    {
                        "promote_bboxes": [
                            {
                                "xmin": 1000.0,
                                "ymin": 1000.0,
                                "xmax": 5000.0,
                                "ymax": 5000.0,
                                "label": "화장실",
                            }
                        ]
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            detected, _skipped = labels.fill_unlabeled_rooms(
                msp,
                boxes,
                segs,
                [],
                [],
                "close",
                {"xmin": -500.0, "ymin": -500.0, "xmax": 6500.0, "ymax": 6500.0},
                sample_mm=100.0,
                review_path=review,
            )
        self.assertEqual([inst for inst in detected if inst.get("synthetic")], [])

    def test_skips_elevator_outer_wall_bed_from_review(self):
        for label, key in (
            ("승강기", "promote_bboxes"),
            ("외벽", "promote_bboxes"),
            ("침대", "demote_bboxes"),
        ):
            with self.subTest(label=label):
                doc = ezdxf.new()
                msp = doc.modelspace()
                _wall_room(
                    msp, [(0.0, 0.0), (6000.0, 0.0), (6000.0, 6000.0), (0.0, 6000.0)]
                )
                boxes = labels.room.column_boxes(msp)
                segs = labels.room.wall_segments(msp, boxes)
                with tempfile.TemporaryDirectory() as tmp:
                    review = Path(tmp) / "review.json"
                    review.write_text(
                        json.dumps(
                            {
                                key: [
                                    {
                                        "xmin": 1000.0,
                                        "ymin": 1000.0,
                                        "xmax": 5000.0,
                                        "ymax": 5000.0,
                                        "label": label,
                                    }
                                ]
                            },
                            ensure_ascii=False,
                        ),
                        encoding="utf-8",
                    )
                    detected, _skipped = labels.fill_unlabeled_rooms(
                        msp,
                        boxes,
                        segs,
                        [],
                        [],
                        "close",
                        {
                            "xmin": -500.0,
                            "ymin": -500.0,
                            "xmax": 6500.0,
                            "ymax": 6500.0,
                        },
                        sample_mm=100.0,
                        review_path=review,
                    )
                self.assertEqual(
                    [inst for inst in detected if inst.get("synthetic")], []
                )

    def test_vision_x_bbox_skips_unlabeled_without_geom(self):
        """Vision X 는 기하 교차 없이도 크기와 관계없이 무명 라벨에서 뺀다."""
        doc = ezdxf.new()
        msp = doc.modelspace()
        _wall_room(msp, [(0.0, 0.0), (6000.0, 0.0), (6000.0, 6000.0), (0.0, 6000.0)])
        boxes = labels.room.column_boxes(msp)
        segs = labels.room.wall_segments(msp, boxes)
        with tempfile.TemporaryDirectory() as tmp:
            review = Path(tmp) / "review.json"
            review.write_text(
                json.dumps(
                    {
                        "promote_bboxes": [
                            {
                                "xmin": 1000.0,
                                "ymin": 1000.0,
                                "xmax": 5000.0,
                                "ymax": 5000.0,
                                "label": "X",
                            }
                        ]
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            detected, _skipped = labels.fill_unlabeled_rooms(
                msp,
                boxes,
                segs,
                [],
                [],
                "close",
                {"xmin": -500.0, "ymin": -500.0, "xmax": 6500.0, "ymax": 6500.0},
                sample_mm=100.0,
                review_path=review,
            )
        synth = [inst for inst in detected if inst.get("synthetic")]
        self.assertEqual(synth, [])

    def test_large_void_crossed_x_skips_unlabeled(self):
        """지에스이형 ~23×4.5 m 상부오픈 X 도 크기와 관계없이 무명에서 뺀다."""
        doc = ezdxf.new()
        msp = doc.modelspace()
        w, h = 23100.0, 4500.0
        _wall_room(msp, [(0.0, 0.0), (w, 0.0), (w, h), (0.0, h)])
        msp.add_line((0.0, 0.0), (w, h), dxfattribs={"layer": "0"})
        msp.add_line((w, 0.0), (0.0, h), dxfattribs={"layer": "0"})
        boxes = labels.room.column_boxes(msp)
        segs = labels.room.wall_segments(msp, boxes)
        self.assertEqual(len(labels.find_crossed_x_bboxes(msp)), 1)
        detected, _skipped = labels.fill_unlabeled_rooms(
            msp,
            boxes,
            segs,
            [],
            [],
            "close",
            {"xmin": -500.0, "ymin": -500.0, "xmax": w + 500.0, "ymax": h + 500.0},
            sample_mm=100.0,
        )
        synth = [inst for inst in detected if inst.get("synthetic")]
        self.assertEqual(synth, [])

    def test_slash_rect_skips_unlabeled(self):
        """네모 + 대각 / 하나 표식도 무명 라벨에서 뺀다."""
        doc = ezdxf.new()
        msp = doc.modelspace()
        _wall_room(msp, [(0.0, 0.0), (1200.0, 0.0), (1200.0, 3000.0), (0.0, 3000.0)])
        msp.add_line((0.0, 0.0), (1200.0, 3000.0), dxfattribs={"layer": "0"})
        boxes = labels.room.column_boxes(msp)
        segs = labels.room.wall_segments(msp, boxes)
        self.assertEqual(len(labels.find_slash_rect_bboxes(msp)), 1)
        detected, _skipped = labels.fill_unlabeled_rooms(
            msp,
            boxes,
            segs,
            [],
            [],
            "close",
            {"xmin": -500.0, "ymin": -500.0, "xmax": 1700.0, "ymax": 3500.0},
            sample_mm=100.0,
        )
        synth = [inst for inst in detected if inst.get("synthetic")]
        self.assertEqual(synth, [])

    def test_load_skip_bboxes_reads_stair_from_promote(self):
        with tempfile.TemporaryDirectory() as tmp:
            review = Path(tmp) / "review.json"
            review.write_text(
                json.dumps(
                    {
                        "promote_bboxes": [
                            {
                                "xmin": 1.0,
                                "ymin": 2.0,
                                "xmax": 3.0,
                                "ymax": 4.0,
                                "label": "계단",
                            },
                            {
                                "xmin": 10.0,
                                "ymin": 20.0,
                                "xmax": 30.0,
                                "ymax": 40.0,
                                "label": "사무실",
                            },
                        ]
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            boxes = labels.load_skip_bboxes_from_review(
                review, labels=labels.FILL_SKIP_REVIEW_AREA_LABELS
            )
        self.assertEqual(boxes, [(1.0, 2.0, 3.0, 4.0)])
        x_boxes = labels.load_skip_bboxes_from_review(
            review,
            labels=labels.FILL_SKIP_REVIEW_X_LABELS,
        )
        self.assertEqual(x_boxes, [])

    def test_skips_crossed_x_room(self):
        doc = ezdxf.new()
        msp = doc.modelspace()
        # 6 m × 6 m 칸 + 가운데 X (짧은 변 약 3 m, 긴 변 약 4.2 m)
        _wall_room(msp, [(0.0, 0.0), (6000.0, 0.0), (6000.0, 6000.0), (0.0, 6000.0)])
        msp.add_line((1500.0, 1500.0), (4500.0, 4500.0), dxfattribs={"layer": "0"})
        msp.add_line((1500.0, 4500.0), (4500.0, 1500.0), dxfattribs={"layer": "0"})
        boxes = labels.room.column_boxes(msp)
        segs = labels.room.wall_segments(msp, boxes)
        x_boxes = labels.find_crossed_x_bboxes(msp)
        self.assertEqual(len(x_boxes), 1)
        detected, _skipped = labels.fill_unlabeled_rooms(
            msp,
            boxes,
            segs,
            [],
            [],
            "close",
            {"xmin": -500.0, "ymin": -500.0, "xmax": 6500.0, "ymax": 6500.0},
            sample_mm=100.0,
        )
        synth = [inst for inst in detected if inst.get("synthetic")]
        self.assertEqual(synth, [])

    def _add_vanity_sink(self, msp, cx: float, cy: float) -> None:
        """이중 닫힌 LWPOLYLINE + 배수 CIRCLE 세면대."""
        base = {"layer": "BASE"}
        outer = msp.add_lwpolyline(
            [
                (cx - 400.0, cy - 250.0),
                (cx + 400.0, cy - 250.0),
                (cx + 400.0, cy + 250.0),
                (cx - 400.0, cy + 250.0),
            ],
            dxfattribs=base,
        )
        outer.closed = True
        inner = msp.add_lwpolyline(
            [
                (cx - 250.0, cy - 190.0),
                (cx + 250.0, cy - 190.0),
                (cx + 250.0, cy + 190.0),
                (cx - 250.0, cy + 190.0),
            ],
            dxfattribs=base,
        )
        inner.closed = True
        msp.add_circle((cx, cy), 17.5, dxfattribs=base)

    def test_find_sink_centers_nested_vanity(self):
        doc = ezdxf.new()
        msp = doc.modelspace()
        self._add_vanity_sink(msp, 2000.0, 2000.0)
        sinks = labels.find_sink_centers(msp)
        self.assertEqual(len(sinks), 1)
        self.assertLess(abs(sinks[0][0] - 2000.0), 1.0)
        self.assertLess(abs(sinks[0][1] - 2000.0), 1.0)

    def test_find_sink_centers_when_outer_promoted_to_wall(self):
        """llmvalidator 가 세면기 외곽을 WALL 로 올려도 화장실로 잡는다."""
        doc = ezdxf.new()
        msp = doc.modelspace()
        cx, cy = 2000.0, 2000.0
        outer = msp.add_lwpolyline(
            [
                (cx - 400.0, cy - 250.0),
                (cx + 400.0, cy - 250.0),
                (cx + 400.0, cy + 250.0),
                (cx - 400.0, cy + 250.0),
            ],
            dxfattribs={"layer": "WALL"},
        )
        outer.closed = True
        inner = msp.add_lwpolyline(
            [
                (cx - 250.0, cy - 190.0),
                (cx + 250.0, cy - 190.0),
                (cx + 250.0, cy + 190.0),
                (cx - 250.0, cy + 190.0),
            ],
            dxfattribs={"layer": "BASE"},
        )
        inner.closed = True
        msp.add_circle((cx, cy), 17.5, dxfattribs={"layer": "BASE"})
        sinks = labels.find_sink_centers(msp)
        self.assertEqual(len(sinks), 1)
        self.assertLess(abs(sinks[0][0] - cx), 1.0)
        self.assertLess(abs(sinks[0][1] - cy), 1.0)
        # 외곽 bbox 포함
        self.assertLess(abs(sinks[0][2] - (cx - 400.0)), 1.0)
        self.assertLess(abs(sinks[0][5] - (cy + 250.0)), 1.0)

    def test_poly_has_sink_when_vanity_carves_notch(self):
        """WALL 세면기 때문에 fill 칸에 노치가 생겨 중심이 밖이어도 외곽 교차로 잡는다."""
        from shapely.geometry import Polygon

        # 301호 화장실 무명_016 과 같은 노치 형태
        poly = Polygon(
            [
                (0.0, 1750.0),
                (850.0, 1750.0),
                (850.0, 1235.4),
                (1638.1, 1235.4),
                (1638.1, 1750.0),
                (2325.0, 1750.0),
                (2325.0, 0.0),
                (0.0, 0.0),
            ]
        )
        # 세면기 중심은 노치 안(칸 밖), 외곽은 칸과 맞닿음
        cx, cy = 1238.1, 1485.4
        sinks = [(cx, cy, cx - 400.0, cy - 250.0, cx + 400.0, cy + 250.0)]
        self.assertFalse(poly.covers(__import__("shapely.geometry", fromlist=["Point"]).Point(cx, cy)))
        self.assertTrue(labels._poly_has_sink(poly, sinks))

    def test_poly_has_sink_when_vanity_notch_has_gap(self):
        """세면기 외곽이 칸과 ~22 mm 떨어져도 근접으로 화장실로 본다(201호 무명_013)."""
        from shapely.geometry import Polygon, Point

        poly = Polygon(
            [
                (94000.0, 28050.0),
                (94000.0, 26550.0),
                (96100.0, 26550.0),
                (96200.0, 28050.0),
                (95600.0, 28050.0),
                (95500.0, 27550.0),
                (94700.0, 27550.0),
                (94600.0, 28050.0),
            ]
        )
        cx, cy = 95103.068, 27917.5
        sinks = [(cx, cy, 94703.068, 27677.5, 95503.068, 28177.5)]
        self.assertFalse(poly.covers(Point(cx, cy)))
        self.assertFalse(poly.intersects(__import__("shapely.geometry", fromlist=["box"]).box(*sinks[0][2:])))
        self.assertTrue(labels._poly_has_sink(poly, sinks))

    def test_skips_room_with_wall_layer_vanity_sink(self):
        """WALL 외곽 세면대가 있는 칸은 무명 fill 대상이 아니다."""
        doc = ezdxf.new()
        msp = doc.modelspace()
        _wall_room(msp, [(0.0, 0.0), (4000.0, 0.0), (4000.0, 4000.0), (0.0, 4000.0)])
        cx, cy = 2000.0, 2000.0
        outer = msp.add_lwpolyline(
            [
                (cx - 400.0, cy - 250.0),
                (cx + 400.0, cy - 250.0),
                (cx + 400.0, cy + 250.0),
                (cx - 400.0, cy + 250.0),
            ],
            dxfattribs={"layer": "WALL"},
        )
        outer.closed = True
        inner = msp.add_lwpolyline(
            [
                (cx - 250.0, cy - 190.0),
                (cx + 250.0, cy - 190.0),
                (cx + 250.0, cy + 190.0),
                (cx - 250.0, cy + 190.0),
            ],
            dxfattribs={"layer": "BASE"},
        )
        inner.closed = True
        msp.add_circle((cx, cy), 17.5, dxfattribs={"layer": "BASE"})
        boxes = labels.room.column_boxes(msp)
        segs = labels.room.wall_segments(msp, boxes)
        detected, _skipped = labels.fill_unlabeled_rooms(
            msp,
            boxes,
            segs,
            [],
            [],
            "close",
            {"xmin": -500.0, "ymin": -500.0, "xmax": 4500.0, "ymax": 4500.0},
            sample_mm=100.0,
        )
        synth = [inst for inst in detected if inst.get("synthetic")]
        self.assertEqual(synth, [])

    def test_keeps_fill_component_above_one_m2(self):
        """fill 최소 면적은 1 m². 1–2 m² 진입 공간도 무명으로 남긴다."""
        doc = ezdxf.new()
        msp = doc.modelspace()
        # 1.5 m × 1.2 m = 1.8 m² (이전 2 m² 임계에서는 버려짐)
        _wall_room(msp, [(0.0, 0.0), (1500.0, 0.0), (1500.0, 1200.0), (0.0, 1200.0)])
        boxes = labels.room.column_boxes(msp)
        segs = labels.room.wall_segments(msp, boxes)
        detected, _skipped = labels.fill_unlabeled_rooms(
            msp,
            boxes,
            segs,
            [],
            [],
            "close",
            {"xmin": -500.0, "ymin": -500.0, "xmax": 2000.0, "ymax": 1700.0},
            sample_mm=100.0,
        )
        synth = [inst for inst in detected if inst.get("synthetic")]
        self.assertEqual(len(synth), 1)
        self.assertGreater(synth[0]["area_m2"], labels.FILL_MIN_AREA_M2)
        self.assertLessEqual(synth[0]["area_m2"], labels.MIN_LABEL_AREA_M2)

    def test_skips_fill_component_at_or_below_one_m2(self):
        doc = ezdxf.new()
        msp = doc.modelspace()
        # 1.0 m × 1.0 m = 1.0 m² → 이하라 버림
        _wall_room(msp, [(0.0, 0.0), (1000.0, 0.0), (1000.0, 1000.0), (0.0, 1000.0)])
        boxes = labels.room.column_boxes(msp)
        segs = labels.room.wall_segments(msp, boxes)
        detected, _skipped = labels.fill_unlabeled_rooms(
            msp,
            boxes,
            segs,
            [],
            [],
            "close",
            {"xmin": -500.0, "ymin": -500.0, "xmax": 1500.0, "ymax": 1500.0},
            sample_mm=100.0,
        )
        synth = [inst for inst in detected if inst.get("synthetic")]
        self.assertEqual(synth, [])

    def test_skips_room_with_sink_symbol(self):
        doc = ezdxf.new()
        msp = doc.modelspace()
        _wall_room(msp, [(0.0, 0.0), (4000.0, 0.0), (4000.0, 4000.0), (0.0, 4000.0)])
        self._add_vanity_sink(msp, 2000.0, 2000.0)
        boxes = labels.room.column_boxes(msp)
        segs = labels.room.wall_segments(msp, boxes)
        detected, _skipped = labels.fill_unlabeled_rooms(
            msp,
            boxes,
            segs,
            [],
            [],
            "close",
            {"xmin": -500.0, "ymin": -500.0, "xmax": 4500.0, "ymax": 4500.0},
            sample_mm=100.0,
        )
        synth = [inst for inst in detected if inst.get("synthetic")]
        self.assertEqual(synth, [])

    def test_skips_room_with_semyeondae_text(self):
        doc = ezdxf.new()
        msp = doc.modelspace()
        _wall_room(msp, [(0.0, 0.0), (4000.0, 0.0), (4000.0, 4000.0), (0.0, 4000.0)])
        msp.add_text("세면대", dxfattribs={"layer": "0", "height": 200}).set_placement(
            (2000.0, 2000.0)
        )
        boxes = labels.room.column_boxes(msp)
        segs = labels.room.wall_segments(msp, boxes)
        detected, _skipped = labels.fill_unlabeled_rooms(
            msp,
            boxes,
            segs,
            [],
            [],
            "close",
            {"xmin": -500.0, "ymin": -500.0, "xmax": 4500.0, "ymax": 4500.0},
            sample_mm=100.0,
        )
        synth = [inst for inst in detected if inst.get("synthetic")]
        self.assertEqual(synth, [])

    def test_corner_bridge_seals_open_l_gap(self):
        # 5 m × 5 m 칸, NE 코너만 600 mm L 갭(기둥 자리)으로 끊김.
        segs = [
            (0.0, 0.0, 5000.0, 0.0),
            (5000.0, 0.0, 5000.0, 4400.0),
            (4400.0, 5000.0, 0.0, 5000.0),
            (0.0, 5000.0, 0.0, 0.0),
        ]
        bridges = labels.corner_bridge_segments(segs)
        # 가로 자유끝 (4400,5000) × 세로 자유끝 (5000,4400) → L 두 변
        self.assertEqual(len(bridges), 2)
        comps = labels.segment_unlabeled_components(
            segs,
            {"xmin": -500.0, "ymin": -500.0, "xmax": 5500.0, "ymax": 5500.0},
            cell_mm=100.0,
        )
        hit = [
            c for c in comps if labels.room.point_in_poly(2500.0, 2500.0, c["pts"])
        ]
        self.assertEqual(len(hit), 1)
        # 브리지 끄면 외곽 flood에 빨려 실내 성분이 없다.
        real = labels.corner_bridge_segments
        labels.corner_bridge_segments = lambda *_a, **_k: []  # type: ignore
        try:
            open_comps = labels.segment_unlabeled_components(
                segs,
                {"xmin": -500.0, "ymin": -500.0, "xmax": 5500.0, "ymax": 5500.0},
                cell_mm=100.0,
            )
        finally:
            labels.corner_bridge_segments = real  # type: ignore
        open_hit = [
            c
            for c in open_comps
            if labels.room.point_in_poly(2500.0, 2500.0, c["pts"])
        ]
        self.assertEqual(len(open_hit), 0)

    def test_corridor_door_gaps_get_unlabeled_when_bridged(self):
        """복도 벽 문 틈(≤2.4 m)이 열려 있어도 door=close fill 이면 무명으로 잡는다."""
        doc = ezdxf.new()
        msp = doc.modelspace()
        wall = {"layer": "WALL"}
        # 위·아래 실 + 가운데 복도. 복도 남북 벽에 900 mm 문 틈.
        # outer 12 m × 10 m, corridor band y=4000–6000.
        outer = [
            (0.0, 0.0),
            (12000.0, 0.0),
            (12000.0, 10000.0),
            (0.0, 10000.0),
        ]
        _wall_room(msp, outer)
        # 위실 바닥(복도 천장): 문 틈 4500–5400
        msp.add_line((0.0, 6000.0), (4500.0, 6000.0), dxfattribs=wall)
        msp.add_line((5400.0, 6000.0), (12000.0, 6000.0), dxfattribs=wall)
        # 아래실 천장(복도 바닥): 문 틈 4500–5400
        msp.add_line((0.0, 4000.0), (4500.0, 4000.0), dxfattribs=wall)
        msp.add_line((5400.0, 4000.0), (12000.0, 4000.0), dxfattribs=wall)
        msp.add_text("사무실", dxfattribs={"layer": "0", "height": 300}).set_placement(
            (6000.0, 8000.0)
        )
        msp.add_text("회의실", dxfattribs={"layer": "0", "height": 300}).set_placement(
            (6000.0, 2000.0)
        )

        boxes = labels.room.column_boxes(msp)
        segs = labels.room.wall_segments(msp, boxes)
        bbox = {"xmin": -500.0, "ymin": -500.0, "xmax": 12500.0, "ymax": 10500.0}

        detected = []
        for x, y, text in ((6000.0, 8000.0, "사무실"), (6000.0, 2000.0, "회의실")):
            found, reason, sx, sy = labels.detect_one_with_nudge(
                msp, boxes, segs, x, y, door="close"
            )
            self.assertIsNone(reason)
            assert found is not None
            found.update(
                {
                    "text": text,
                    "x": sx,
                    "y": sy,
                    "name": labels.room.norm_name(text),
                }
            )
            detected.append(found)

        # 문 틈을 안 이으면 복도가 실과 한 칸 → 실명과 겹쳐 무명이 안 생긴다.
        no_bridge = labels.fill_unlabeled_rooms(
            msp, boxes, segs, list(detected), [], "open", bbox, sample_mm=100.0
        )[0]
        corridor_open = [
            inst
            for inst in no_bridge
            if inst.get("synthetic")
            and labels.room.point_in_poly(6000.0, 5000.0, inst["pts"])
        ]
        self.assertEqual(len(corridor_open), 0)

        # door=close 브리지면 복도만 남아 무명.
        with_bridge = labels.fill_unlabeled_rooms(
            msp, boxes, segs, list(detected), [], "close", bbox, sample_mm=100.0
        )[0]
        corridor = [
            inst
            for inst in with_bridge
            if inst.get("synthetic")
            and labels.room.point_in_poly(6000.0, 5000.0, inst["pts"])
        ]
        self.assertEqual(len(corridor), 1)
        self.assertTrue(corridor[0]["text"].startswith("무명_"))


if __name__ == "__main__":
    unittest.main()
