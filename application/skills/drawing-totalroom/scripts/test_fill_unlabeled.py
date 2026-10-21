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

    def test_vision_only_x_bbox_does_not_skip(self):
        """기하 X 없이 review X 만 있으면 문 스윙 오검으로 보고 무명을 남긴다."""
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
        self.assertEqual(len(synth), 1)

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


if __name__ == "__main__":
    unittest.main()
