"""Shared multi-floor plan INSERT at (0,0) is absorbed when extent hits the sheet clip."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

import ezdxf

sys.path.insert(0, str(Path(__file__).resolve().parent))

import extract_2d
import lib_sheet


class SharedPlanInsertTests(unittest.TestCase):
    def test_plan_insert_at_origin_absorbed_into_upper_sheet(self):
        doc = ezdxf.new()
        plan = doc.blocks.new("XR_PLAN")
        # 1F-band geometry (should be clipped out of 2F)
        plan.add_line((1000, 1000), (8000, 1000))
        # 2F-band geometry (must be kept)
        plan.add_line((1000, 200_000), (80_000, 200_000))
        plan.add_line((1000, 200_000), (1000, 250_000))

        msp = doc.modelspace()
        msp.add_blockref("XR_PLAN", insert=(0, 0))
        # Duplicate same block at same point (common after XREF bind)
        msp.add_blockref("XR_PLAN", insert=(0, 0))
        # Sheet frame lines alone exceed the old LINE<30 gate
        for i in range(40):
            y = 148_500 + i * 100
            msp.add_line((-1000, y), (210_000, y))

        sheet = lib_sheet.SheetFrame(
            "2F",
            "지상2층 평면도",
            (0.0, 148_500.0, 210_000.0, 297_000.0),
            500.0,
        )
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            result = extract_2d.extract_sheet_floor(
                doc,
                "2F",
                out,
                sheet,
                do_png=False,
                do_dxf=True,
                do_json=False,
            )
            dxf_path = out / "floor_2F_original.dxf"
            self.assertTrue(dxf_path.exists(), msg=f"missing {dxf_path}; result={result}")
            out_doc = ezdxf.readfile(str(dxf_path))
            lines = [e for e in out_doc.modelspace() if e.dxftype() == "LINE"]

        # Absolute coords (origin_shift none): keep 2F plan segments
        plan_horiz = [
            e
            for e in lines
            if abs(float(e.dxf.start.y) - 200_000) < 1.0
            and abs(float(e.dxf.end.y) - 200_000) < 1.0
            and abs(float(e.dxf.end.x) - float(e.dxf.start.x)) > 10_000
        ]
        self.assertGreaterEqual(len(plan_horiz), 1, msg="2F plan line missing from extract")
        # 1F-band plan line must not leak into 2F
        low = [
            e
            for e in lines
            if abs(float(e.dxf.start.y) - 1000) < 1.0 and abs(float(e.dxf.end.y) - 1000) < 1.0
        ]
        self.assertEqual(low, [])

    def test_bbox_intersects(self):
        self.assertTrue(
            extract_2d._bbox_intersects((0, 0, 10, 10), (5, 5, 15, 15))
        )
        self.assertFalse(
            extract_2d._bbox_intersects((0, 0, 10, 10), (11, 0, 20, 10))
        )


if __name__ == "__main__":
    unittest.main()
