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
        band_1f = doc.blocks.new("1F 평면코어")
        band_1f.add_line((1000, 1000), (8000, 1000))
        band_2f = doc.blocks.new("2F 평면코어")
        band_2f.add_line((1000, 200_000), (80_000, 200_000))
        band_2f.add_line((1000, 200_000), (1000, 250_000))
        plan.add_blockref("1F 평면코어", insert=(0, 0))
        plan.add_blockref("2F 평면코어", insert=(0, 0))

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
            dxf_path = Path(result["files"]["dxf"])
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

    def test_explode_respects_xclip(self):
        """Autodesk XCLIP 밖 기하는 explode에서 제외한다."""
        doc = ezdxf.new("R2010")
        blk = doc.blocks.new("PLAN")
        blk.add_line((0, 0), (100, 0))
        blk.add_line((5000, 0), (5100, 0))  # outside clip
        msp = doc.modelspace()
        ins = msp.add_blockref("PLAN", insert=(0, 0))
        from ezdxf.xclip import XClip

        xc = XClip(ins)
        xc.set_wcs_clipping_path([(0, -10), (200, -10), (200, 10), (0, 10)])
        xc.enable_clipping()

        clipped = extract_2d.explode_insert(ins, skip_furniture=False, respect_xclip=True)
        self.assertEqual(len(clipped), 1)
        c = extract_2d._centroid(clipped[0])
        self.assertIsNotNone(c)
        self.assertLess(c[0], 200)

        raw = extract_2d.explode_insert(ins, skip_furniture=False, respect_xclip=False)
        self.assertEqual(len(raw), 2)

    def test_sheet_absorb_uses_xclip_not_full_extent(self):
        """XCLIP이 도곽과 안 겹치면 공유 평면을 그 층에 넣지 않는다."""
        doc = ezdxf.new("R2010")
        plan = doc.blocks.new("XR_평면도")
        # full extent는 왼쪽까지 닿지만, XCLIP은 오른쪽만 (Autodesk와 동일)
        plan.add_line((10_000, 10_000), (80_000, 10_000))
        band = doc.blocks.new("1F 평면코어")
        band.add_line((200_000, 10_000), (280_000, 10_000))
        plan.add_blockref("1F 평면코어", insert=(0, 0))
        msp = doc.modelspace()
        ins = msp.add_blockref("XR_평면도", insert=(0, 0))
        from ezdxf.xclip import XClip

        xc = XClip(ins)
        xc.set_wcs_clipping_path(
            [(160_000, 0), (320_000, 0), (320_000, 90_000), (160_000, 90_000)]
        )
        xc.enable_clipping()
        for i in range(40):
            y = 1000 + i * 100
            msp.add_line((-1000, y), (120_000, y))

        left = lib_sheet.SheetFrame(
            "sheet_01",
            "표지",
            (0.0, 0.0, 120_000.0, 90_000.0),
            500.0,
        )
        right = lib_sheet.SheetFrame(
            "1F",
            "1층 평면도",
            (168_000.0, 0.0, 288_000.0, 90_000.0),
            500.0,
        )
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            # 왼쪽 도곽: XCLIP 밖 → 평면 선(y=10000)이 들어가지 않는다.
            left_result = extract_2d.extract_sheet_floor(
                doc,
                "sheet_01",
                out,
                left,
                do_png=False,
                do_dxf=True,
                do_json=False,
            )
            left_doc = ezdxf.readfile(str(left_result["files"]["dxf"]))
            left_plan = [
                e
                for e in left_doc.modelspace()
                if e.dxftype() == "LINE" and abs(float(e.dxf.start.y) - 10_000) < 1
            ]
            self.assertEqual(left_plan, [], msg="XCLIP 밖 도곽에 평면이 들어감")

            right_result = extract_2d.extract_sheet_floor(
                doc,
                "1F",
                out,
                right,
                do_png=False,
                do_dxf=True,
                do_json=False,
            )
            out_doc = ezdxf.readfile(str(right_result["files"]["dxf"]))
            xs = [
                float(e.dxf.start.x)
                for e in out_doc.modelspace()
                if e.dxftype() == "LINE" and abs(float(e.dxf.start.y) - 10_000) < 1
            ]
        self.assertTrue(any(x >= 160_000 for x in xs), msg=f"right plan missing: {xs}")
        self.assertFalse(any(x < 100_000 for x in xs), msg=f"left plan leaked: {xs}")

    def test_xclip_mega_keeps_shared_package_text(self):
        """XCLIP+도곽이면 strict_floor 없이 공유 패키지 실명을 남긴다."""
        doc = ezdxf.new("R2010")
        plan = doc.blocks.new("XR_평면도")
        # 이름에 B2F가 없는 공유 패키지 (성남 XF_PLAN B5~B2 / ARCH-TEXT 형태)
        shared = doc.blocks.new("XF_PLAN B5~B2$0$ARCH-TEXT")
        shared.add_text("전기실", dxfattribs={"height": 500}).set_placement((200_000, 20_000))
        shared.add_line((190_000, 15_000), (250_000, 15_000))
        # XCLIP 밖(왼쪽) 유출용
        shared.add_text("유출", dxfattribs={"height": 500}).set_placement((20_000, 20_000))
        plan.add_blockref("XF_PLAN B5~B2$0$ARCH-TEXT", insert=(0, 0))
        # mega extent
        plan.add_line((-50_000, 0), (400_000, 0))

        msp = doc.modelspace()
        ins = msp.add_blockref("XR_평면도", insert=(0, 0))
        from ezdxf.xclip import XClip

        xc = XClip(ins)
        xc.set_wcs_clipping_path(
            [(160_000, 0), (320_000, 0), (320_000, 90_000), (160_000, 90_000)]
        )
        xc.enable_clipping()
        for i in range(40):
            y = 1000 + i * 100
            msp.add_line((168_000, y), (288_000, y))

        sheet = lib_sheet.SheetFrame(
            "B2F",
            "지하2층 지열 냉매배관 평면도",
            (168_000.0, 0.0, 288_000.0, 90_000.0),
            500.0,
        )
        with tempfile.TemporaryDirectory() as tmp:
            result = extract_2d.extract_sheet_floor(
                doc,
                "B2F",
                Path(tmp),
                sheet,
                do_png=False,
                do_dxf=True,
                do_json=False,
            )
            out_doc = ezdxf.readfile(str(result["files"]["dxf"]))
            texts = [
                (e.dxf.get("text") or "")
                for e in out_doc.modelspace()
                if e.dxftype() == "TEXT"
            ]
        self.assertIn("전기실", texts)
        self.assertNotIn("유출", texts)

    def test_mega_plan_skips_other_floor_nested_inserts(self):
        """공유 평면 INSERT 안 다른 층 블록은 대상 층 추출에서 빠진다."""
        doc = ezdxf.new()
        plan = doc.blocks.new("XR_평면도")
        b5 = doc.blocks.new("B5 지하외벽")
        b5.add_line((10_000, 10_000), (80_000, 10_000))
        b4 = doc.blocks.new("B4 지하외벽")
        b4.add_line((10_000, 20_000), (80_000, 20_000))
        plan.add_blockref("B5 지하외벽", insert=(0, 0))
        plan.add_blockref("B4 지하외벽", insert=(0, 0))
        # 시트보다 훨씬 넓은 extent가 되도록 양끝 점
        plan.add_line((-50_000, 0), (400_000, 0))

        msp = doc.modelspace()
        msp.add_blockref("XR_평면도", insert=(0, 0))
        for i in range(40):
            y = 1000 + i * 100
            msp.add_line((-1000, y), (120_000, y))

        sheet = lib_sheet.SheetFrame(
            "B5F",
            "지하5층 평면도",
            (0.0, 0.0, 120_000.0, 90_000.0),
            500.0,
        )
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            result = extract_2d.extract_sheet_floor(
                doc,
                "B5F",
                out,
                sheet,
                do_png=False,
                do_dxf=True,
                do_json=False,
            )
            out_doc = ezdxf.readfile(str(result["files"]["dxf"]))
            ys = sorted(
                {
                    round(float(e.dxf.start.y))
                    for e in out_doc.modelspace()
                    if e.dxftype() == "LINE"
                }
            )
        self.assertIn(10_000, ys)
        self.assertNotIn(20_000, ys)


if __name__ == "__main__":
    unittest.main()
