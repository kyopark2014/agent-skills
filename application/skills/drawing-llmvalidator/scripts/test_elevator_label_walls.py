"""ELEV·승강기 글자로 코어 외곽을 올린다."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

import ezdxf

sys.path.insert(0, str(Path(__file__).resolve().parent))

import lib_llm_correct


def _rect(msp, x0, y0, x1, y1, *, gap=150.0, layer="BASE"):
    """안팎 이중 사각."""
    lines = []
    for inset in (0.0, gap):
        xa, ya, xb, yb = x0 - inset, y0 - inset, x1 + inset, y1 + inset
        lines.extend(
            [
                msp.add_line((xa, ya), (xb, ya), dxfattribs={"layer": layer}),
                msp.add_line((xb, ya), (xb, yb), dxfattribs={"layer": layer}),
                msp.add_line((xb, yb), (xa, yb), dxfattribs={"layer": layer}),
                msp.add_line((xa, yb), (xa, ya), dxfattribs={"layer": layer}),
            ]
        )
    return lines


class ElevatorLabelTokenTests(unittest.TestCase):
    def test_elevator_tokens_loaded_from_json(self):
        self.assertIn("elevator", lib_llm_correct.LABEL_TOKENS)
        self.assertIn("ELEV", lib_llm_correct.ELEVATOR_LABEL_TOKENS)
        self.assertIn("승강기", lib_llm_correct.ELEVATOR_LABEL_TOKENS)
        self.assertTrue(lib_llm_correct._is_elevator_label("ELEV"))
        self.assertTrue(lib_llm_correct._is_elevator_label("ELEV. HALL"))
        self.assertTrue(lib_llm_correct._is_elevator_label("E.V#2"))
        self.assertTrue(lib_llm_correct._is_elevator_label("엘리베이터"))
        self.assertFalse(lib_llm_correct._is_elevator_label("LEVEL"))
        self.assertFalse(lib_llm_correct._is_elevator_label("창고"))


class ElevatorLabelWallTests(unittest.TestCase):
    def test_elev_label_promotes_enclosure(self):
        doc = ezdxf.new()
        msp = doc.modelspace()
        msp.add_text("ELEV", dxfattribs={"insert": (1000.0, 1000.0), "height": 200.0})
        walls = _rect(msp, 0, 0, 2000, 2000)
        far = msp.add_line((20000, 0), (20000, 2000), dxfattribs={"layer": "BASE"})

        promoted = lib_llm_correct.promote_service_core_walls(msp)

        self.assertGreaterEqual(promoted, 1)
        self.assertTrue(any(line.dxf.layer == "WALL" for line in walls))
        self.assertEqual(far.dxf.layer, "BASE")


if __name__ == "__main__":
    unittest.main()
