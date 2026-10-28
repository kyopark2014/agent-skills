"""Vision 잔여 축 → 레이어 drop 단위 테스트 (Vision 은 mock)."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import ezdxf

import lib_axis_vision as axis_vision
import lib_structure


class AxisVisionHelpersTest(unittest.TestCase):
    def test_norm_bbox_to_mm(self):
        window = (1000.0, 2000.0, 3000.0, 4000.0)
        mm = axis_vision.norm_bbox_to_mm([0.0, 0.0, 1.0, 1.0], window)
        self.assertEqual(mm, (1000.0, 2000.0, 3000.0, 4000.0))
        # 왼쪽 위 근처
        top = axis_vision.norm_bbox_to_mm([0.0, 0.0, 0.1, 0.1], window)
        self.assertIsNotNone(top)
        assert top is not None
        self.assertAlmostEqual(top[0], 1000.0)
        self.assertAlmostEqual(top[3], 4000.0)

    def test_parse_axis_observation(self):
        present, axes = axis_vision.parse_axis_observation(
            {"present": True, "axes": [{"bbox": [0.1, 0.2, 0.3, 0.9]}]}
        )
        self.assertTrue(present)
        self.assertEqual(len(axes), 1)
        present, axes = axis_vision.parse_axis_observation({"present": False, "axes": []})
        self.assertFalse(present)
        self.assertEqual(axes, [])

    def test_pick_center_crops(self):
        windows = axis_vision.pick_center_crops((0, 0, 80000, 60000), count=2)
        self.assertEqual(len(windows), 2)
        for window in windows:
            box = window["bbox_mm"]
            self.assertLess(box["xmax"] - box["xmin"], 26000)


class AxisVisionPassTest(unittest.TestCase):
    def _doc_with_mystery_axis(self):
        doc = ezdxf.new(setup=True)
        if "CENTER" not in doc.linetypes:
            doc.linetypes.add("CENTER", pattern=[0.0, 31.75, -6.35, 6.35, -6.35])
        ax = doc.layers.add("MYSTERY-AX", color=1)
        ax.dxf.linetype = "CENTER"
        doc.layers.add("A-WALL", color=7)
        msp = doc.modelspace()
        ents = []
        for x in (10000, 16000, 22000, 28000):
            ents.append(
                msp.add_line(
                    (x, 5000),
                    (x, 20000),
                    dxfattribs={"layer": "MYSTERY-AX", "linetype": "CENTER"},
                )
            )
        # 벽 (짧은 이중선)
        ents.append(msp.add_line((5000, 8000), (9000, 8000), dxfattribs={"layer": "A-WALL"}))
        ents.append(msp.add_line((5000, 8200), (9000, 8200), dxfattribs={"layer": "A-WALL"}))
        return doc, ents

    def test_run_axis_vision_pass_drops_center_layer(self):
        doc, ents = self._doc_with_mystery_axis()
        writes: list = []

        def fake_write(entities, path, include_text=True, origin_shift=None):
            writes.append(("dxf", path, [e.dxf.layer for e in entities]))
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("dxf", encoding="utf-8")

        def fake_render(path, floor=None, plan_bbox=None):
            writes.append(("png", path))
            return {"files": {"png": str(path.with_suffix(".png"))}, "size_m": {}, "n_labels": 0}

        def fake_vision(_png: Path) -> dict:
            return {"present": True, "axes": [{"bbox": [0.2, 0.1, 0.8, 0.9]}]}

        with tempfile.TemporaryDirectory() as tmp:
            floor_dir = Path(tmp)
            # crop 렌더는 실제 matplotlib 을 쓰므로 mock
            with mock.patch.object(
                axis_vision,
                "prepare_axis_samples",
                return_value=[
                    {
                        "id": "axis_01",
                        "png": str(floor_dir / "axis_01.png"),
                        "bbox_mm": {
                            "xmin": 8000.0,
                            "ymin": 4000.0,
                            "xmax": 30000.0,
                            "ymax": 22000.0,
                        },
                        "png_size": {"width": 100, "height": 100},
                    }
                ],
            ):
                (floor_dir / "axis_01.png").write_bytes(b"fake")
                kept, report = axis_vision.run_axis_vision_pass(
                    entities=ents,
                    doc=doc,
                    floor_dir=floor_dir,
                    floor="1F",
                    origin=(0.0, 0.0),
                    include_text=True,
                    write_dxf=fake_write,
                    render_png=fake_render,
                    invoke_vision=fake_vision,
                )
            self.assertTrue(report["present"])
            self.assertIn("MYSTERY-AX", report["dropped_layers"])
            self.assertIn("MYSTERY-AX", report["leaf_name_candidates"])
            self.assertTrue(all(e.dxf.layer != "MYSTERY-AX" for e in kept))
            self.assertTrue(any(e.dxf.layer == "A-WALL" for e in kept))
            self.assertTrue(writes)
            axis_json = json.loads((floor_dir / "axis_vision.json").read_text(encoding="utf-8"))
            self.assertEqual(axis_json["dropped_layers"], ["MYSTERY-AX"])

    def test_run_axis_vision_skips_without_center_candidates(self):
        doc = ezdxf.new()
        doc.layers.add("A-WALL", color=7)
        msp = doc.modelspace()
        ents = [msp.add_line((0, 0), (0, 1000), dxfattribs={"layer": "A-WALL"})]
        with tempfile.TemporaryDirectory() as tmp:
            floor_dir = Path(tmp)
            kept, report = axis_vision.run_axis_vision_pass(
                entities=ents,
                doc=doc,
                floor_dir=floor_dir,
                floor="1F",
                origin=(0.0, 0.0),
                include_text=True,
                write_dxf=lambda *a, **k: None,
                render_png=None,
                invoke_vision=lambda p: (_ for _ in ()).throw(AssertionError("should skip")),
            )
            self.assertTrue(report["skipped_no_candidates"])
            self.assertEqual(kept, ents)

    def test_run_axis_vision_keeps_g_cen(self):
        """G-CEN 만 있으면 후보가 아니므로 Vision 자체를 건너뛴다."""
        doc = ezdxf.new(setup=True)
        if "CENTER" not in doc.linetypes:
            doc.linetypes.add("CENTER", pattern=[0.0, 31.75, -6.35, 6.35, -6.35])
        layer = doc.layers.add("G-CEN", color=3)
        layer.dxf.linetype = "CENTER"
        msp = doc.modelspace()
        ents = []
        for x in (0, 6000, 12000):
            ents.append(
                msp.add_line(
                    (x, 0), (x, 15000), dxfattribs={"layer": "G-CEN", "linetype": "CENTER"}
                )
            )

        with tempfile.TemporaryDirectory() as tmp:
            floor_dir = Path(tmp)
            kept, report = axis_vision.run_axis_vision_pass(
                entities=ents,
                doc=doc,
                floor_dir=floor_dir,
                floor="1F",
                origin=(0.0, 0.0),
                include_text=True,
                write_dxf=lambda *a, **k: None,
                render_png=None,
                invoke_vision=lambda p: (_ for _ in ()).throw(AssertionError("skip G-CEN")),
            )
            self.assertTrue(report["skipped_no_candidates"])
            self.assertEqual(report["dropped_layers"], [])
            self.assertEqual(len(kept), len(ents))

        # G-CEN + 다른 CENTER 후보가 있으면 Vision은 돌되 G-CEN 은 유지
        ax = doc.layers.add("MISC-AX", color=1)
        ax.dxf.linetype = "CENTER"
        for x in (20000, 26000, 32000):
            ents.append(
                msp.add_line(
                    (x, 0), (x, 15000), dxfattribs={"layer": "MISC-AX", "linetype": "CENTER"}
                )
            )

        def fake_vision(_png: Path) -> dict:
            return {"present": True, "axes": [{"bbox": [0.1, 0.1, 0.9, 0.9]}]}

        with tempfile.TemporaryDirectory() as tmp:
            floor_dir = Path(tmp)
            with mock.patch.object(
                axis_vision,
                "prepare_axis_samples",
                return_value=[
                    {
                        "id": "axis_01",
                        "png": str(floor_dir / "a.png"),
                        "bbox_mm": {"xmin": -1000, "ymin": -1000, "xmax": 35000, "ymax": 16000},
                        "png_size": {"width": 10, "height": 10},
                    }
                ],
            ):
                (floor_dir / "a.png").write_bytes(b"x")
                kept, report = axis_vision.run_axis_vision_pass(
                    entities=ents,
                    doc=doc,
                    floor_dir=floor_dir,
                    floor="1F",
                    origin=(0.0, 0.0),
                    include_text=True,
                    write_dxf=lambda *a, **k: None,
                    render_png=None,
                    invoke_vision=fake_vision,
                )
            self.assertIn("MISC-AX", report["dropped_layers"])
            self.assertNotIn("G-CEN", report["dropped_layers"])
            self.assertTrue(any(e.dxf.layer == "G-CEN" for e in kept))

    def test_layers_near_bboxes(self):
        doc = ezdxf.new()
        msp = doc.modelspace()
        msp.add_line((0, 0), (0, 10000), dxfattribs={"layer": "AX"})
        msp.add_line((100, 0), (100, 500), dxfattribs={"layer": "SHORT"})
        counts = lib_structure.layers_near_bboxes(
            list(msp),
            [( -100, 0, 100, 12000)],
            min_len_mm=5000,
        )
        self.assertIn("AX", counts)
        self.assertNotIn("SHORT", counts)


if __name__ == "__main__":
    unittest.main()
