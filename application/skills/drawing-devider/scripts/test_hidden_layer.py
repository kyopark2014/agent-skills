"""Hidden lines on an off layer are dropped. Dashed lines on an on layer stay."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

import ezdxf

sys.path.insert(0, str(Path(__file__).resolve().parent))

import lib_structure


class OffLayerHiddenTests(unittest.TestCase):
    def test_only_hidden_lines_on_an_off_layer_are_dropped(self):
        doc = ezdxf.new()
        doc.layers.add("EE", color=43)
        doc.layers.add("back", color=7)
        ee = doc.layers.get("EE")
        ee.off()
        msp = doc.modelspace()
        hidden_off = msp.add_line((0, 0), (0, 1000), dxfattribs={"layer": "EE", "linetype": "HID2"})
        solid_off = msp.add_line((10, 0), (10, 1000), dxfattribs={"layer": "EE", "linetype": "CONTINUOUS"})
        hidden_on = msp.add_line((20, 0), (20, 1000), dxfattribs={"layer": "back", "linetype": "1-plan$0$HIDDEN"})
        solid_on = msp.add_line((30, 0), (30, 1000), dxfattribs={"layer": "back"})

        kept, skipped = lib_structure.drop_off_layer_hidden(
            doc, [hidden_off, solid_off, hidden_on, solid_on]
        )
        self.assertEqual(skipped, 1)
        self.assertEqual(kept, [solid_off, hidden_on, solid_on])
        self.assertTrue(lib_structure.is_hidden_linetype("1-plan$0$HID2"))
        self.assertFalse(lib_structure.is_hidden_linetype("CENTER"))
        self.assertFalse(lib_structure.is_hidden_linetype("BYLAYER"))


class AxisLayerTests(unittest.TestCase):
    def test_axis_layer_names(self):
        self.assertTrue(lib_structure.is_axis_layer("A-ST-AXIS-CEN"))
        self.assertTrue(lib_structure.is_axis_layer("A-ST-AXIS-BUB"))
        self.assertTrue(lib_structure.is_axis_layer("A-ST-AXIS-DIM"))
        self.assertTrue(lib_structure.is_axis_layer("AXIS-BUB"))
        self.assertTrue(lib_structure.is_axis_layer("AA-AXIS-LINE"))
        self.assertTrue(lib_structure.is_axis_layer("XR_PLAN(B동)$0$AA-AXIS-LINE"))
        self.assertTrue(lib_structure.is_axis_layer("축선"))
        self.assertTrue(lib_structure.is_axis_layer("CEN"))
        self.assertTrue(lib_structure.is_axis_layer("cen"))
        self.assertTrue(lib_structure.is_axis_layer("A-CEN"))
        self.assertTrue(lib_structure.is_axis_layer("XREF-어린이집$0$A-CEN"))
        self.assertTrue(lib_structure.is_axis_layer("A-CEN-COL"))
        self.assertTrue(lib_structure.is_axis_layer("N-CNTL"))
        self.assertTrue(lib_structure.is_axis_layer("N-CNTL-CEN1"))
        self.assertTrue(lib_structure.is_axis_layer("N-CNTL-CEN2"))
        self.assertTrue(lib_structure.is_axis_layer("지하1층평면도$0$N-CNTL-CEN2"))
        self.assertTrue(lib_structure.is_axis_layer("Column(B1)$0$N-CNTL-AXIS"))
        self.assertTrue(lib_structure.is_axis_layer("101동_지하1층$0$N-CNTL"))
        self.assertTrue(lib_structure.is_axis_layer("GRID"))
        self.assertFalse(lib_structure.is_axis_layer("A-ST-CONC"))
        self.assertFalse(lib_structure.is_axis_layer("G-CEN"))
        self.assertFalse(lib_structure.is_axis_layer("ARCH_CEN"))
        self.assertFalse(lib_structure.is_axis_layer("A-WALL"))
        self.assertFalse(lib_structure.is_axis_layer("GRIDLINE"))
        self.assertFalse(lib_structure.is_axis_layer("AA-XXXX-DIMS"))
        self.assertFalse(lib_structure.is_axis_layer("AA-XXXX-CNTL"))

    def test_drop_axis_layers(self):
        doc = ezdxf.new()
        msp = doc.modelspace()
        axis = msp.add_line((0, 0), (0, 1000), dxfattribs={"layer": "A-ST-AXIS-CEN"})
        bub = msp.add_text("X7", dxfattribs={"layer": "A-ST-AXIS-BUB", "insert": (0, 0), "height": 100})
        cen = msp.add_line((5, 0), (5, 1000), dxfattribs={"layer": "CEN"})
        acen = msp.add_line((7, 0), (7, 1000), dxfattribs={"layer": "A-CEN"})
        ncntl = msp.add_line((6, 0), (6, 1000), dxfattribs={"layer": "N-CNTL-CEN1"})
        wall = msp.add_line((10, 0), (10, 1000), dxfattribs={"layer": "A-WALL"})
        kept, skipped, info = lib_structure.drop_axis_layers(
            [axis, bub, cen, acen, ncntl, wall], doc=doc
        )
        self.assertEqual(skipped, 5)
        self.assertEqual(kept, [wall])
        self.assertEqual(info["trait_layers"], [])

    def test_drop_axis_layers_by_center_grid_traits(self):
        """이름 미매칭이어도 CEN linetype + 긴 직교 격자 레이어는 제거."""
        doc = ezdxf.new(setup=True)
        if "CENTER" not in doc.linetypes:
            doc.linetypes.add("CENTER", pattern=[0.0, 31.75, -6.35, 6.35, -6.35])
        ax = doc.layers.add("MISC-AX", color=1)
        ax.dxf.linetype = "CENTER"
        doc.layers.add("A-WALL", color=7)
        gcen_layer = doc.layers.add("G-CEN", color=3)
        gcen_layer.dxf.linetype = "CENTER"
        msp = doc.modelspace()
        # 수직 축 4줄 (피치 6000, 길이 40000) + 수평 축 3줄
        axis_ents = []
        for x in (0, 6000, 12000, 18000):
            axis_ents.append(
                msp.add_line((x, 0), (x, 40000), dxfattribs={"layer": "MISC-AX", "linetype": "CENTER"})
            )
        for y in (0, 6000, 12000):
            axis_ents.append(
                msp.add_line((0, y), (18000, y), dxfattribs={"layer": "MISC-AX", "linetype": "CENTER"})
            )
        wall = msp.add_line((100, 100), (100, 5000), dxfattribs={"layer": "A-WALL"})
        # 짧은 벽 중심선 — 유지
        gcen = msp.add_line((200, 200), (200, 800), dxfattribs={"layer": "G-CEN", "linetype": "CENTER"})

        kept, skipped, info = lib_structure.drop_axis_layers(
            axis_ents + [wall, gcen], doc=doc
        )
        self.assertIn("MISC-AX", info["trait_layers"])
        self.assertEqual(skipped, len(axis_ents))
        self.assertEqual(kept, [wall, gcen])
        self.assertFalse(lib_structure.is_axis_layer("MISC-AX"))
        self.assertTrue(lib_structure.layer_has_axis_grid(doc, axis_ents + [wall, gcen], "MISC-AX"))

    def test_continuous_long_parallels_are_not_axis_traits(self):
        """Continuous 외벽 장축 격자는 특성 제거 대상이 아니다."""
        doc = ezdxf.new(setup=True)
        doc.layers.add("A-WALL", dxfattribs={"linetype": "CONTINUOUS", "color": 7})
        msp = doc.modelspace()
        walls = []
        for x in (0, 6000, 12000, 18000):
            walls.append(msp.add_line((x, 0), (x, 40000), dxfattribs={"layer": "A-WALL"}))
        kept, skipped, info = lib_structure.drop_axis_layers(walls, doc=doc)
        self.assertEqual(skipped, 0)
        self.assertEqual(info["trait_layers"], [])
        self.assertEqual(kept, walls)

    def test_select_droppable_center_layer_not_named(self):
        """이름 미등록 CENTER 축 레이어는 Vision 후보에서 drop 허용."""
        doc = ezdxf.new(setup=True)
        if "CENTER" not in doc.linetypes:
            doc.linetypes.add("CENTER", pattern=[0.0, 31.75, -6.35, 6.35, -6.35])
        ax = doc.layers.add("MISC-AX2", color=1)
        ax.dxf.linetype = "CENTER"
        doc.layers.add("A-WALL", color=7)
        msp = doc.modelspace()
        ents = []
        for x in (0, 6000, 12000):
            ents.append(
                msp.add_line(
                    (x, 0), (x, 8000), dxfattribs={"layer": "MISC-AX2", "linetype": "CENTER"}
                )
            )
        ents.append(msp.add_line((100, 0), (100, 2000), dxfattribs={"layer": "A-WALL"}))
        dropped, skipped = lib_structure.select_droppable_axis_layers(
            doc, ents, {"MISC-AX2": 3, "A-WALL": 1}
        )
        self.assertIn("MISC-AX2", dropped)
        self.assertTrue(any(item["layer"] == "A-WALL" for item in skipped))

    def test_g_cen_not_droppable(self):
        doc = ezdxf.new(setup=True)
        if "CENTER" not in doc.linetypes:
            doc.linetypes.add("CENTER", pattern=[0.0, 31.75, -6.35, 6.35, -6.35])
        layer = doc.layers.add("G-CEN", color=3)
        layer.dxf.linetype = "CENTER"
        msp = doc.modelspace()
        ents = [
            msp.add_line((0, 0), (0, 8000), dxfattribs={"layer": "G-CEN", "linetype": "CENTER"})
        ]
        dropped, skipped = lib_structure.select_droppable_axis_layers(doc, ents, ["G-CEN"])
        self.assertEqual(dropped, [])
        self.assertEqual(skipped[0]["reason"], "keep_centerline")

    def test_wall_pair_layer_not_droppable(self):
        doc = ezdxf.new()
        doc.layers.add("A-WALL", color=7)
        msp = doc.modelspace()
        ents = []
        # 200 mm 벽 이중선 여러 쌍
        for x in (0, 200, 6000, 6200, 12000, 12200):
            ents.append(msp.add_line((x, 0), (x, 8000), dxfattribs={"layer": "A-WALL"}))
        ok, reason = lib_structure.layer_is_safe_to_drop_as_axis(doc, ents, "A-WALL")
        self.assertFalse(ok)
        self.assertEqual(reason, "wall_pairs")

    def test_schedule_leaf_not_droppable(self):
        doc = ezdxf.new()
        doc.layers.add("장비일람", color=7)
        msp = doc.modelspace()
        ents = [
            msp.add_line((x, 0), (x, 8000), dxfattribs={"layer": "장비일람"})
            for x in (0, 6000, 12000)
        ]
        ok, reason = lib_structure.layer_is_safe_to_drop_as_axis(doc, ents, "장비일람")
        self.assertFalse(ok)
        self.assertEqual(reason, "annotation_leaf")

    def test_continuous_grid_alone_not_droppable(self):
        doc = ezdxf.new()
        doc.layers.add("MISC-TABLE", color=7)
        msp = doc.modelspace()
        ents = [
            msp.add_line((x, 0), (x, 20000), dxfattribs={"layer": "MISC-TABLE"})
            for x in (0, 6000, 12000, 18000)
        ]
        ok, reason = lib_structure.layer_is_safe_to_drop_as_axis(doc, ents, "MISC-TABLE")
        self.assertFalse(ok)
        self.assertEqual(reason, "no_center_linetype")

    def test_drop_named_layers(self):
        doc = ezdxf.new()
        msp = doc.modelspace()
        a = msp.add_line((0, 0), (0, 1000), dxfattribs={"layer": "FOO"})
        b = msp.add_line((1, 0), (1, 1000), dxfattribs={"layer": "BAR"})
        kept, skipped = lib_structure.drop_named_layers([a, b], {"FOO"})
        self.assertEqual(skipped, 1)
        self.assertEqual(kept, [b])

    def test_layer_leaf_name(self):
        self.assertEqual(lib_structure.layer_leaf_name("A-CEN"), "A-CEN")
        self.assertEqual(
            lib_structure.layer_leaf_name("XREF-어린이집$0$A-CEN"), "A-CEN"
        )


if __name__ == "__main__":
    unittest.main()
