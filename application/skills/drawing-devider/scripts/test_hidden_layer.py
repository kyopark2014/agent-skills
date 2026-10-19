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
        self.assertTrue(lib_structure.is_axis_layer("축선"))
        self.assertTrue(lib_structure.is_axis_layer("CEN"))
        self.assertTrue(lib_structure.is_axis_layer("cen"))
        self.assertFalse(lib_structure.is_axis_layer("A-ST-CONC"))
        self.assertFalse(lib_structure.is_axis_layer("G-CEN"))
        self.assertFalse(lib_structure.is_axis_layer("ARCH_CEN"))
        self.assertFalse(lib_structure.is_axis_layer("A-WALL"))

    def test_drop_axis_layers(self):
        doc = ezdxf.new()
        msp = doc.modelspace()
        axis = msp.add_line((0, 0), (0, 1000), dxfattribs={"layer": "A-ST-AXIS-CEN"})
        bub = msp.add_text("X7", dxfattribs={"layer": "A-ST-AXIS-BUB", "insert": (0, 0), "height": 100})
        cen = msp.add_line((5, 0), (5, 1000), dxfattribs={"layer": "CEN"})
        wall = msp.add_line((10, 0), (10, 1000), dxfattribs={"layer": "A-WALL"})
        kept, skipped = lib_structure.drop_axis_layers([axis, bub, cen, wall])
        self.assertEqual(skipped, 3)
        self.assertEqual(kept, [wall])


if __name__ == "__main__":
    unittest.main()
