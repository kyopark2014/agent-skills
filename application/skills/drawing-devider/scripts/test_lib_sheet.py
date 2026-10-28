"""Floor titles keep word spaces and accept an equipment-code prefix."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import lib_sheet


class ParseFloorLabelTests(unittest.TestCase):
    def test_word_spaces_and_equipment_prefix(self):
        cases = {
            "1층 평면도": "1F",
            "1 층 평 면 도": "1F",
            "옥 상 평 면 도": "RF",
            "1층 냉난방 평면도": "1F",
            "1층 냉난방 제어 평면도": "1F",
            "EHP 1층 냉난방 제어 평면도": "1F",
            "EHP 1층 냉난방 철거 평면도 ": "1F",
            "EHP 4층 냉난방 제어 평면도": "4F",
            "EHP 지붕층 냉난방 제어 평면도": "RF",
            "EHP 지붕층 냉난방 철거 평면도": "RF",
            "1층 냉난방 배관평면도": "1F",
            "지붕층 냉난방 배관평면도": "RF",
            "지상1층 공조조닝도": "1F",
            "지상7층 공조조닝도": "7F",
            "3층 조닝도": "3F",
            "지하1층": "B1F",
            "제2층": "2F",
            "B1F": "B1F",
            "12F PLAN": "12F",
            "27F FLOOR PLAN": "27F",
            "01. 2F 평면도": "2F",
            "01. 3F 평면도": "3F",
            "01. 4F 평면도": "4F",
            "01) 4F 평면도": "4F",
            "01 2F 평면도": "2F",
            "4~5층 평면도": "4F",
            "4 ~5 층 평 면 도": "4F",
            "옥탑지붕 평면도": "PH",
            "옥 탑 지 붕 평 면 도": "PH",
            "옥탑지붕 평 면 도": "PH",
            "2층 참조": None,
            "복도-1": None,
            "피트니스센터, 에듀센터, 관리사무소 평면도": None,
            "스카이커뮤니티 평,입,단면도": None,
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(lib_sheet.parse_floor_label(text), expected)

    def test_facility_plan_title_and_drawing_number(self):
        self.assertEqual(lib_sheet.parse_drawing_number("AA-406"), "AA406")
        self.assertEqual(lib_sheet.normalize_floor_token("AA-406"), "AA406")
        self.assertEqual(lib_sheet.normalize_floor_token("aa406"), "AA406")
        self.assertIsNone(lib_sheet.parse_drawing_number("복도-1"))
        self.assertEqual(
            lib_sheet.parse_plan_title("피트니스센터, 에듀센터, 관리사무소 평면도"),
            "피트니스센터, 에듀센터, 관리사무소 평면도",
        )
        self.assertEqual(
            lib_sheet.parse_plan_title("스카이커뮤니티 평,입,단면도"),
            "스카이커뮤니티 평,입,단면도",
        )
        self.assertIsNone(lib_sheet.parse_plan_title("1층 평면도"))
        self.assertIsNone(lib_sheet.parse_plan_title("평면도"))
        self.assertIsNone(lib_sheet.parse_plan_title("PLANNING.ARCHITECTURE.ENGINEERING"))

    def test_same_floor_control_and_demo_stay_one_sheet(self):
        rect = (0.0, 0.0, 126_000.0, 89_000.0)
        hits = [
            lib_sheet._TextHit("EHP 1층 냉난방 제어 평면도", "1F", 80_000.0, 70_000.0, 500.0),
            lib_sheet._TextHit("EHP 1층 냉난방 철거 평면도", "1F", 40_000.0, 20_000.0, 500.0),
        ]
        warnings: list[str] = []
        frames = lib_sheet._frames_from([rect], hits, warnings)
        self.assertEqual([frame.floor for frame in frames], ["1F"])
        self.assertEqual(warnings, [])

    def test_side_by_side_zoning_titles_split_one_sheet(self):
        """한 도곽에 지상N층 공조조닝도가 좌우로 있으면 영역을 나눈다."""
        rect = (336_400.0, 0.0, 504_600.0, 119_200.0)
        hits = [
            lib_sheet._TextHit("지상3층 공조조닝도", "3F", 379_807.0, 6_377.0, 500.0),
            lib_sheet._TextHit("지상4층 공조조닝도", "4F", 451_168.0, 6_377.0, 500.0),
        ]
        warnings: list[str] = []
        frames = lib_sheet._frames_from([rect], hits, warnings)
        self.assertEqual([frame.floor for frame in frames], ["3F", "4F"])
        self.assertEqual(warnings, [])
        self.assertLess(frames[0].bbox[2], frames[1].bbox[0] + 1.0)


class FacilityPlanSheetTests(unittest.TestCase):
    def test_borders_without_floor_numbers_use_drawing_numbers(self):
        import ezdxf

        doc = ezdxf.new()
        msp = doc.modelspace()

        def border(x, y, w, h):
            msp.add_lwpolyline([(x, y), (x + w, y), (x + w, y + h), (x, y + h)], close=True)

        border(0, 0, 120_000, 83_000)
        border(0, -100_000, 80_000, 55_000)
        msp.add_text(
            "피트니스센터, 에듀센터, 관리사무소 평면도",
            dxfattribs={"insert": (40_000, 4_000), "height": 1200},
        )
        msp.add_text("AA-406", dxfattribs={"insert": (100_000, 4_000), "height": 1200})
        msp.add_text(
            "경비실-2, 티카페, 경로당 평면도",
            dxfattribs={"insert": (20_000, -96_000), "height": 800},
        )
        msp.add_text("경로당 평면도", dxfattribs={"insert": (20_000, -90_000), "height": 700})
        msp.add_text("AA-409", dxfattribs={"insert": (60_000, -96_000), "height": 800})

        found = lib_sheet.discover_layout(doc, mode="sheet")

        self.assertEqual(found.floors, ["AA406", "AA409"])
        titles = {sheet.floor: sheet.title for sheet in found.sheets}
        self.assertEqual(titles["AA409"], "경비실-2, 티카페, 경로당 평면도")
        self.assertEqual(titles["AA406"], "피트니스센터, 에듀센터, 관리사무소 평면도")
        self.assertTrue(any("도면번호" in note for note in found.warnings))

    def test_defpoint_guides_and_repeated_floor_codes_stay_out_of_sheets(self):
        import ezdxf

        doc = ezdxf.new()
        msp = doc.modelspace()

        def border(x, y, w, h, layer="0"):
            msp.add_lwpolyline(
                [(x, y), (x + w, y), (x + w, y + h), (x, y + h)],
                close=True,
                dxfattribs={"layer": layer},
            )

        border(0, 0, 60_000, 40_000)
        border(80_000, 0, 60_000, 40_000)
        border(0, -30_000, 40_000, 12_000)
        msp.add_line((0, 0), (200_000, 0), dxfattribs={"layer": "Defpoint"})
        msp.add_line((0, 40_000), (200_000, 40_000), dxfattribs={"layer": "Defpoint"})
        msp.add_line((0, 0), (0, 40_000), dxfattribs={"layer": "Defpoint"})
        msp.add_line((200_000, 0), (200_000, 40_000), dxfattribs={"layer": "Defpoint"})
        msp.add_text("경로당 평면도", dxfattribs={"insert": (20_000, 5_000), "height": 800})
        msp.add_text(
            "근린생활시설 1층 평면도",
            dxfattribs={"insert": (90_000, 8_000), "height": 800},
        )
        msp.add_text(
            "근린생활시설 2층 평면도",
            dxfattribs={"insert": (90_000, 28_000), "height": 800},
        )
        for index in range(8):
            msp.add_text(
                "29F",
                dxfattribs={"insert": (2_000 + index * 500, -22_000), "height": 400},
            )

        found = lib_sheet.discover_layout(doc, mode="sheet")

        self.assertEqual([frame.floor for frame in found.sheets], ["sheet_01", "sheet_02"])
        self.assertEqual(found.sheets[0].title, "미확정: 경로당 평면도")
        self.assertEqual(
            found.sheets[1].title,
            "미확정: 근린생활시설 1층 평면도, 근린생활시설 2층 평면도",
        )
        self.assertLess(found.sheets[0].bbox[2] - found.sheets[0].bbox[0], 70_000)
        self.assertTrue(any("29F" in note for note in found.warnings))

    def test_borders_without_titles_become_sheet_ids(self):
        import ezdxf

        doc = ezdxf.new()
        msp = doc.modelspace()

        def border(x, y, w, h):
            msp.add_lwpolyline([(x, y), (x + w, y), (x + w, y + h), (x, y + h)], close=True)

        border(0, 0, 120_000, 83_000)
        border(140_000, 0, 120_000, 83_000)

        found = lib_sheet.discover_layout(doc, mode="sheet")

        self.assertEqual([frame.floor for frame in found.sheets], ["sheet_01", "sheet_02"])
        self.assertEqual(found.sheets[0].title, "미확정")
        self.assertEqual(found.sheets[1].title, "미확정")
        self.assertTrue(any("도곽만으로" in note for note in found.warnings))

    def test_defpoints_closed_polyline_used_when_no_other_frame(self):
        """도면틀 LINE이 끊긴 경우 Defpoints 닫힌 도곽만으로 sheet를 만든다."""
        import ezdxf

        doc = ezdxf.new()
        msp = doc.modelspace()
        # 짧은 틱만 있는 도면틀 — 사각형이 되지 않음
        msp.add_line((69_202, 12_744), (69_202, 12_944), dxfattribs={"layer": "000-도면틀"})
        msp.add_line((109_191, 12_744), (109_191, 12_944), dxfattribs={"layer": "000-도면틀"})
        msp.add_line((69_202, 12_744), (109_191, 12_744), dxfattribs={"layer": "000-도면틀"})
        msp.add_lwpolyline(
            [(68_702, 10_444), (109_691, 10_444), (109_691, 39_131), (68_702, 39_131)],
            close=True,
            dxfattribs={"layer": "Defpoints"},
        )
        msp.add_mtext("FLOOR PLAN", dxfattribs={"insert": (86_000, 11_394), "char_height": 280})

        found = lib_sheet.discover_layout(doc, mode="sheet")

        self.assertEqual([frame.floor for frame in found.sheets], ["sheet_01"])
        self.assertAlmostEqual(found.sheets[0].bbox[0], 68_702, delta=1)
        self.assertTrue(any("Defpoints" in note for note in found.warnings))

    def test_defpoints_preferred_when_partial_frame_misses_floors(self):
        """일반 도곽이 한 층만 잡아도 Defpoints 닫힌 도곽이 더 많으면 그쪽을 쓴다.

        260713처럼 큰 테두리 하나에 짧은 '5F'만 있고, 실제 층 테두리는
        Defpoint 닫힌 폴리라인에만 있는 경우를 막는다.
        """
        import ezdxf

        doc = ezdxf.new()
        msp = doc.modelspace()
        # 일부 층만 담는 일반 레이어 큰 도곽 + 짧은 층 코드
        msp.add_lwpolyline(
            [(0, 0), (200_000, 0), (200_000, 150_000), (0, 150_000)],
            close=True,
            dxfattribs={"layer": "0"},
        )
        msp.add_text("5F", dxfattribs={"insert": (20_000, 20_000), "height": 5000})
        # Defpoints 에 층별 도곽
        msp.add_lwpolyline(
            [(0, 0), (180_000, 0), (180_000, 120_000), (0, 120_000)],
            close=True,
            dxfattribs={"layer": "Defpoints"},
        )
        msp.add_text("5층 평면도", dxfattribs={"insert": (30_000, 10_000), "height": 3000})
        msp.add_lwpolyline(
            [(0, 200_000), (180_000, 200_000), (180_000, 320_000), (0, 320_000)],
            close=True,
            dxfattribs={"layer": "Defpoints"},
        )
        msp.add_text("13층 평면도", dxfattribs={"insert": (30_000, 210_000), "height": 3000})

        found = lib_sheet.discover_layout(doc, mode="sheet")

        self.assertEqual(found.floors, ["5F", "13F"])
        self.assertTrue(any("다시 나눴" in note for note in found.warnings))

    def test_defpoints_preferred_over_many_untitled_room_scraps(self):
        """방·구역 조각(제목 없는 sheet)이 많아도 Defpoints 실시트·층 제목을 쓴다.

        067_estgen_bio처럼 PY_01 등 작은 테두리가 도곽으로 잡히고,
        실제 시트는 Defpoints 닫힌 폴리라인에만 있는 경우를 막는다.
        """
        import ezdxf

        doc = ezdxf.new()
        msp = doc.modelspace()

        def scrap(x0, y0):
            msp.add_lwpolyline(
                [(x0, y0), (x0 + 14_000, y0), (x0 + 14_000, y0 + 18_000), (x0, y0 + 18_000)],
                close=True,
                dxfattribs={"layer": "PY_01"},
            )
            for index in range(40):
                msp.add_line(
                    (x0 + 1_000 + index * 200, y0 + 2_000),
                    (x0 + 1_200 + index * 200, y0 + 2_200),
                )

        for col in range(4):
            for row in range(4):
                scrap(col * 40_000, row * 40_000)

        def sheet(x0, y0, title):
            msp.add_lwpolyline(
                [(x0, y0), (x0 + 210_000, y0), (x0 + 210_000, y0 + 169_000), (x0, y0 + 169_000)],
                close=True,
                dxfattribs={"layer": "Defpoints"},
            )
            msp.add_text(title, dxfattribs={"insert": (x0 + 80_000, y0 + 8_000), "height": 2000})
            for index in range(80):
                msp.add_line(
                    (x0 + 20_000 + (index % 20) * 8_000, y0 + 30_000 + (index // 20) * 20_000),
                    (x0 + 20_500 + (index % 20) * 8_000, y0 + 30_200 + (index // 20) * 20_000),
                )

        sheet(600_000, 0, "1층 냉난방 배관평면도")
        sheet(900_000, 0, "2층 냉난방 배관평면도")
        sheet(1_200_000, 0, "지붕층 냉난방 배관평면도")

        found = lib_sheet.discover_layout(doc, mode="sheet")

        self.assertEqual(found.floors, ["1F", "2F", "RF"])
        for frame in found.sheets:
            self.assertGreaterEqual(
                min(frame.bbox[2] - frame.bbox[0], frame.bbox[3] - frame.bbox[1]),
                100_000,
            )
        self.assertTrue(any("다시 나눴" in note for note in found.warnings))

    def test_untitled_basement_inferred_from_insert_suffix(self):
        """제목 TEXT 없는 지하 Defpoints 도곽을 BASEMENT INSERT로 B1F로 붙인다."""
        import ezdxf

        doc = ezdxf.new()
        msp = doc.modelspace()
        basement = doc.blocks.new("Grid-4042931-BASEMENT FLOOR PLAN")
        basement.add_line((0, 0), (1000, 0))

        def sheet(x0, y0, title=None, insert_name=None):
            msp.add_lwpolyline(
                [(x0, y0), (x0 + 210_000, y0), (x0 + 210_000, y0 + 169_000), (x0, y0 + 169_000)],
                close=True,
                dxfattribs={"layer": "Defpoints"},
            )
            for index in range(80):
                msp.add_line(
                    (x0 + 20_000 + (index % 20) * 8_000, y0 + 30_000 + (index // 20) * 20_000),
                    (x0 + 20_500 + (index % 20) * 8_000, y0 + 30_200 + (index // 20) * 20_000),
                )
            if title:
                msp.add_text(title, dxfattribs={"insert": (x0 + 80_000, y0 + 8_000), "height": 2000})
            if insert_name:
                msp.add_blockref(insert_name, insert=(x0 + 40_000, y0 + 40_000))

        sheet(0, 0, insert_name="Grid-4042931-BASEMENT FLOOR PLAN")
        sheet(300_000, 0, title="1층 냉난방 배관평면도")

        found = lib_sheet.discover_layout(doc, mode="sheet")

        self.assertEqual(found.floors, ["B1F", "1F"])
        self.assertTrue(any("INSERT" in note for note in found.warnings))

    def test_title_above_frame_and_empty_title_cell_dropped(self):
        """제목이 도곽 바로 위에 있고, 빈 복제 제목 칸만 제목을 품은 경우.

        경기도소방학교처럼 DEFPOINTS 시트 위에 제목이 있고, 어긋난 3층 제목이
        빈 작은 칸에만 들어갈 때를 막는다.
        """
        import ezdxf

        doc = ezdxf.new()
        msp = doc.modelspace()

        def sheet(x0, y0, w, h, layer="Defpoints"):
            msp.add_lwpolyline(
                [(x0, y0), (x0 + w, y0), (x0 + w, y0 + h), (x0, y0 + h)],
                close=True,
                dxfattribs={"layer": layer},
            )

        def fill_geom(x0, y0, w, h, n=80):
            for index in range(n):
                x = x0 + 5_000 + (index % 10) * (w / 12)
                y = y0 + 5_000 + (index // 10) * (h / 12)
                msp.add_line((x, y), (x + 800, y + 200))

        # 실제 3층·4~5층 시트 (제목은 테두리 밖 위쪽)
        sheet(0, 0, 120_000, 83_000)
        fill_geom(0, 0, 120_000, 83_000)
        msp.add_text("3 층  평 면 도", dxfattribs={"insert": (60_000, 98_000), "height": 3000})

        sheet(0, 200_000, 120_000, 83_000)
        fill_geom(0, 200_000, 120_000, 83_000, n=90)
        msp.add_text("4 ~5 층  평 면 도", dxfattribs={"insert": (55_000, 295_000), "height": 3000})

        # 어긋난 3층 제목 + 기하 없는 작은 칸
        sheet(50_000, 300_000, 27_000, 40_000, layer="0")
        msp.add_text("3 층  평 면 도", dxfattribs={"insert": (60_000, 310_000), "height": 3000})

        found = lib_sheet.discover_layout(doc, mode="sheet")

        self.assertIn("3F", found.floors)
        self.assertIn("4F", found.floors)
        three = next(s for s in found.sheets if s.floor == "3F")
        four = next(s for s in found.sheets if s.floor == "4F")
        self.assertLess(three.bbox[3] - three.bbox[1], 100_000)
        self.assertAlmostEqual(three.bbox[1], 0, delta=1)
        self.assertAlmostEqual(four.bbox[1], 200_000, delta=1)
        self.assertTrue(
            any("기하가 없어" in note for note in found.warnings)
            or "3F_2" not in found.floors
        )

    def test_grid_parent_yields_to_sheet_child(self):
        """같은 층 제목이 격자 부모·실제 시트에 겹치면 시트를 남긴다."""
        import ezdxf

        doc = ezdxf.new()
        msp = doc.modelspace()
        # 부모 격자
        msp.add_lwpolyline(
            [(0, 0), (333_000, 0), (333_000, 430_000), (0, 430_000)],
            close=True,
            dxfattribs={"layer": "Defpoints"},
        )
        # 실제 시트
        msp.add_lwpolyline(
            [(20_000, 50_000), (140_000, 50_000), (140_000, 133_000), (20_000, 133_000)],
            close=True,
            dxfattribs={"layer": "Defpoints"},
        )
        for index in range(60):
            msp.add_line((30_000 + index * 1_000, 70_000), (30_500 + index * 1_000, 70_200))
        msp.add_text("2층 평면도", dxfattribs={"insert": (70_000, 145_000), "height": 3000})

        found = lib_sheet.discover_layout(doc, mode="sheet")

        self.assertEqual(found.floors, ["2F"])
        self.assertLess(found.sheets[0].bbox[2] - found.sheets[0].bbox[0], 200_000)


class BlockNameAndSheetInsertTests(unittest.TestCase):
    def test_parse_floor_from_block_name(self):
        self.assertEqual(lib_sheet.parse_floor_from_block_name("PL250224-클럽-B1"), "B1F")
        self.assertEqual(lib_sheet.parse_floor_from_block_name("PL260224-클럽-1"), "1F")
        self.assertEqual(lib_sheet.parse_floor_from_block_name("PL260224-관리-2"), "2F")
        self.assertIsNone(lib_sheet.parse_floor_from_block_name("ooa-sheet-1"))
        self.assertIsNone(lib_sheet.parse_floor_from_block_name("door-handle"))

    def test_concentric_inner_borders_keep_outer_sheet(self):
        """동심 숨김 테두리가 있어도 바깥 시트 도곽을 지우지 않는다."""
        outer = (-140_449.0, 47_523.0, -77_449.0, 92_073.0)
        inners = [
            (-132_190.0, 51_052.0, -85_708.0, 90_832.0),
            (-131_440.0, 51_802.0, -86_458.0, 90_082.0),
            (-130_690.0, 52_552.0, -87_208.0, 89_332.0),
            (-129_190.0, 54_052.0, -88_708.0, 87_832.0),
        ]
        kept = lib_sheet._dedupe_rects([outer, *inners])
        self.assertTrue(
            any(
                abs(rect[0] - outer[0]) < 1 and abs(rect[1] - outer[1]) < 1
                for rect in kept
            ),
            kept,
        )

    def test_side_by_side_children_still_drop_grid_parent(self):
        """좌우로 나뉜 자식 시트가 있으면 격자 부모 도곽은 제거한다."""
        parent = (0.0, 0.0, 200_000.0, 100_000.0)
        left = (5_000.0, 5_000.0, 95_000.0, 95_000.0)
        right = (105_000.0, 5_000.0, 195_000.0, 95_000.0)
        kept = lib_sheet._dedupe_rects([parent, left, right])
        self.assertEqual(len(kept), 2)
        self.assertFalse(
            any(abs(rect[0] - parent[0]) < 1 and abs(rect[2] - parent[2]) < 1 for rect in kept)
        )

    def test_drawing_index_titles_and_concentric_borders_keep_all_floors(self):
        """'01. NF 평면도' + 동심 테두리가 있어도 시트 INSERT 층을 모두 남긴다."""
        import ezdxf

        doc = ezdxf.new()
        msp = doc.modelspace()
        sheet = doc.blocks.new("세미 도곽")
        sheet.add_lwpolyline(
            [(0, 0), (63_000, 0), (63_000, 44_500), (0, 44_500)],
            close=True,
        )

        def add_floor(insert_y: float, title: str, *, concentric: bool) -> None:
            msp.add_blockref("세미 도곽", insert=(0, insert_y))
            msp.add_mtext(
                title,
                dxfattribs={
                    "insert": (2_000, insert_y + 1_800),
                    "char_height": 560,
                    "layer": "5.글자_실명",
                },
            )
            if concentric:
                for inset in (8_000, 10_000, 12_000, 15_000):
                    msp.add_lwpolyline(
                        [
                            (inset, insert_y + inset * 0.7),
                            (63_000 - inset, insert_y + inset * 0.7),
                            (63_000 - inset, insert_y + 44_500 - inset * 0.7),
                            (inset, insert_y + 44_500 - inset * 0.7),
                        ],
                        close=True,
                        dxfattribs={"layer": "14.숨김선"},
                    )
            for index in range(40):
                x = 10_000 + (index % 8) * 5_000
                y = insert_y + 10_000 + (index // 8) * 5_000
                msp.add_line((x, y), (x + 2_000, y + 500))

        add_floor(0.0, "01. 4F 평면도", concentric=False)
        add_floor(55_000.0, "01. 3F 평면도", concentric=False)
        add_floor(110_000.0, "01. 2F 평면도", concentric=True)

        found = lib_sheet.discover_layout(doc, mode="sheet")

        self.assertEqual(found.floors, ["2F", "3F", "4F"])
        self.assertTrue(any("시트 블록 INSERT" in note for note in found.warnings))

    def test_sheet_insert_bbox_beats_tiny_duplicate_title_cell(self):
        """시트 INSERT 큰 도곽이 있으면 복제 제목용 작은 칸은 같은 층으로 흡수된다."""
        import ezdxf

        doc = ezdxf.new()
        msp = doc.modelspace()
        sheet = doc.blocks.new("ooa-sheet-1")
        sheet.add_lwpolyline(
            [(0, 0), (168_000, 0), (168_000, 118_800), (0, 118_800)],
            close=True,
        )
        msp.add_blockref("ooa-sheet-1", insert=(0, 0))
        # 실제 도면 제목(시트 안)
        msp.add_text(
            "지하1층 전열설비 평면도(클럽하우스)",
            dxfattribs={"insert": (60_000, 10_000), "height": 1200},
        )
        # 복제 제목이 들어간 작은 칸(시트 안 오른쪽)
        msp.add_lwpolyline(
            [(140_000, 5_000), (156_000, 5_000), (156_000, 22_600), (140_000, 22_600)],
            close=True,
        )
        msp.add_text(
            "지하1층 전열설비 평면도(클럽하우스)",
            dxfattribs={"insert": (142_000, 12_000), "height": 1200},
        )
        # 시트 안에 잡힐 건물 INSERT(기하)
        plan = doc.blocks.new("PL250224-클럽-B1")
        for index in range(250):
            x = 40_000 + (index % 20) * 500
            y = 40_000 + (index // 20) * 500
            plan.add_line((x, y), (x + 400, y))
        msp.add_blockref("PL250224-클럽-B1", insert=(50_000, 50_000))

        found = lib_sheet.discover_layout(doc, mode="sheet")

        self.assertIn("B1F", found.floors)
        self.assertNotIn("B1F_2", found.floors)
        b1 = next(sheet for sheet in found.sheets if sheet.floor == "B1F")
        self.assertGreater(b1.bbox[2] - b1.bbox[0], 100_000)
        self.assertTrue(any("시트 블록 INSERT" in note for note in found.warnings))

    def test_building_insert_used_when_no_sheet_frame(self):
        """도곽·시트 블록이 없으면 건물 INSERT 기하 bbox로 층을 만든다."""
        import ezdxf

        doc = ezdxf.new()
        msp = doc.modelspace()
        plan = doc.blocks.new("PL260224-클럽-1")
        for index in range(400):
            x = (index % 20) * 2_000
            y = (index // 20) * 1_500
            plan.add_line((x, y), (x + 1_200, y + 200))
        msp.add_blockref("PL260224-클럽-1", insert=(0, 0))
        msp.add_text(
            "1층 전열설비 평면도(클럽하우스)",
            dxfattribs={"insert": (5_000, -5_000), "height": 1000},
        )

        found = lib_sheet.discover_layout(doc, mode="sheet")

        self.assertEqual(found.floors, ["1F"])
        self.assertTrue(any("건물 평면 INSERT" in note for note in found.warnings))
        self.assertGreater(found.sheets[0].bbox[2] - found.sheets[0].bbox[0], 10_000)


class FloorNameMentionTests(unittest.TestCase):
    def test_floors_mentioned_in_block_names(self):
        cases = {
            "B5 지하외벽": {"B5F"},
            "코어_지하5층": {"B5F"},
            "지하4층 주차": {"B4F"},
            "COL_B5": {"B5F"},
            "COL_B4F": {"B4F"},
            "COL_B3~B1": {"B1F", "B2F", "B3F"},
            "B3F~B4F 램프": {"B3F", "B4F"},
            # leaf 우선: B5~B2 부모가 있어도 leaf 범위만
            "XF_PLAN B5~B2$0$B3F~B4F 램프": {"B3F", "B4F"},
            "XF_PLAN B5~B2$0$From": {"B2F", "B3F", "B4F", "B5F"},
            "DOOR_1100": None,
            "XR_평면도$0$주차_일반": None,
        }
        for name, expected in cases.items():
            with self.subTest(name=name):
                got = lib_sheet.floors_mentioned_in_name(name)
                if expected is None:
                    self.assertIsNone(got)
                else:
                    self.assertEqual(set(got or ()), expected)

    def test_name_matches_floor(self):
        self.assertTrue(lib_sheet.name_matches_floor("DOOR_1100", "B5F"))
        self.assertTrue(lib_sheet.name_matches_floor("B5 지하외벽", "B5F"))
        self.assertFalse(lib_sheet.name_matches_floor("B4 지하외벽", "B5F"))
        self.assertTrue(lib_sheet.name_matches_floor("B3F~B4F 램프", "B3F"))
        self.assertFalse(lib_sheet.name_matches_floor("B3F~B4F 램프", "B5F"))
        # 멀티층 패키지 아래 무태그는 겹침 방지를 위해 제외
        self.assertFalse(
            lib_sheet.name_matches_floor("XF_PLAN B5~B2$0$주차_일반", "B5F")
        )
        self.assertTrue(
            lib_sheet.name_matches_floor("XF_PLAN B5~B2$0$B5 지하외벽", "B5F")
        )
        self.assertFalse(
            lib_sheet.name_matches_floor("XF_PLAN B5~B2$0$B3F~B4F 램프", "B5F")
        )


class AutodeskFormSheetTests(unittest.TestCase):
    def test_stacked_xr_form_beats_merged_outer_rect(self):
        """Autodesk 표제란 xr-form이 세로로 3장이면 합본 큰 도곽 대신 3시트로 나눈다."""
        import ezdxf

        doc = ezdxf.new()
        msp = doc.modelspace()
        form = doc.blocks.new("xr-form")
        form.add_lwpolyline(
            [(0, 0), (126_000, 0), (126_000, 89_100), (0, 89_100)],
            close=True,
        )
        for index in range(40):
            form.add_line((2_000 + index * 2_000, 5_000), (3_000 + index * 2_000, 80_000))

        # 세로로 이어 붙인 합본 테두리(옛 파이프라인이 하나로 잡던 패턴)
        msp.add_lwpolyline(
            [(0, -89_100), (126_000, -89_100), (126_000, 178_200), (0, 178_200)],
            close=True,
            dxfattribs={"layer": "Defpoints"},
        )
        titles = ("지하1층 평면도", "1층 평면도", "2층 평면도")
        for index, (title, y0) in enumerate(
            zip(titles, (89_100.0, 0.0, -89_100.0), strict=True)
        ):
            msp.add_blockref("xr-form", insert=(0.0, y0))
            msp.add_text(
                title,
                dxfattribs={"insert": (20_000, y0 + 5_000), "height": 2000},
            )

        found = lib_sheet.discover_layout(doc, mode="sheet")
        self.assertEqual(found.floors, ["B1F", "1F", "2F"])
        self.assertTrue(
            any("Autodesk 표제란 FORM" in note for note in found.warnings)
        )
        heights = sorted(s.bbox[3] - s.bbox[1] for s in found.sheets)
        self.assertTrue(all(80_000 < h < 100_000 for h in heights))

    def test_is_form_sheet_name(self):
        self.assertTrue(lib_sheet._is_form_sheet_name("xr-form"))
        self.assertTrue(lib_sheet._is_form_sheet_name("XR_FORM"))
        self.assertTrue(lib_sheet._is_form_sheet_name("XF_FORM"))
        self.assertFalse(lib_sheet._is_form_sheet_name("XR_평면도"))
        self.assertFalse(lib_sheet._is_form_sheet_name("XR_PLAN"))


class EmptyFrameGeometryTests(unittest.TestCase):
    def _sheet_form_block(self, doc, name: str = "XR_FORM"):
        if name in doc.blocks:
            return
        block = doc.blocks.new(name)
        # 시트 크기(168×118.8 m) FORM. 삽입점이 왼쪽 아래.
        block.add_lwpolyline(
            [(0, 0), (168_000, 0), (168_000, 118_800), (0, 118_800)],
            close=True,
        )
        for index in range(40):
            block.add_line((2_000 + index * 3_000, 10_000), (3_000 + index * 3_000, 100_000))

    def test_float_edge_form_keeps_sheet_and_drops_empty_neighbor(self):
        """Defpoints 도곽이 FORM 삽입점보다 1e-6 mm 커도 해당 층은 남기고,
        모서리만 맞닿은 빈 옆 칸·전열 공유 평면 INSERT만 있는 칸은 뺀다.
        """
        import ezdxf

        doc = ezdxf.new()
        msp = doc.modelspace()
        self._sheet_form_block(doc, "XR_FORM")
        shared = doc.blocks.new("XR_PLAN")
        shared.add_lwpolyline(
            [(0, 0), (500_000, 0), (500_000, 120_000), (0, 120_000)],
            close=True,
        )

        # B5F: 도곽 xmin이 FORM 삽입점보다 약간 큼(실도면 float 어긋남).
        form_x = 1_260_000.0
        frame_xmin = form_x + 1e-6
        msp.add_lwpolyline(
            [
                (frame_xmin, 0.0),
                (frame_xmin + 168_000, 0.0),
                (frame_xmin + 168_000, 118_800),
                (frame_xmin, 118_800),
            ],
            close=True,
            dxfattribs={"layer": "Defpoints"},
        )
        msp.add_text(
            "지하5층 평면도",
            dxfattribs={"insert": (form_x + 30_000, 5_000), "height": 2000},
        )
        msp.add_blockref("XR_FORM", insert=(form_x, 0.0))

        # 빈 옆 칸: 제목만 있고 FORM 내용은 왼쪽 시트에만 있다.
        empty_x0 = form_x - 168_000
        msp.add_lwpolyline(
            [
                (empty_x0, 0.0),
                (form_x + 1e-6, 0.0),
                (form_x + 1e-6, 118_800),
                (empty_x0, 118_800),
            ],
            close=True,
            dxfattribs={"layer": "Defpoints"},
        )
        msp.add_text(
            "PIT층 평면도",
            dxfattribs={"insert": (empty_x0 + 30_000, 5_000), "height": 2000},
        )
        # 전 열을 덮는 공유 평면 — 빈 칸을 채우면 안 된다.
        msp.add_blockref("XR_PLAN", insert=(form_x - 50_000, -20_000))

        found = lib_sheet.discover_layout(doc, mode="sheet")
        self.assertEqual(found.floors, ["B5F"])
        self.assertTrue(
            any("기하가 없어 제외" in note for note in found.warnings)
            or "PIT" not in ",".join(found.floors)
        )

    def test_insert_counts_in_frame_ignores_edge_touch_and_mega(self):
        import ezdxf
        from ezdxf import bbox as ezbbox

        doc = ezdxf.new()
        msp = doc.modelspace()
        self._sheet_form_block(doc, "XR_FORM")
        shared = doc.blocks.new("XR_PLAN")
        shared.add_lwpolyline(
            [(0, 0), (500_000, 0), (500_000, 120_000), (0, 120_000)],
            close=True,
        )
        form = msp.add_blockref("XR_FORM", insert=(0.0, 0.0))
        mega = msp.add_blockref("XR_PLAN", insert=(-10_000.0, -10_000.0))
        bbox = (168_000.0, 0.0, 336_000.0, 118_800.0)  # 오른쪽 빈 칸
        cache = ezbbox.Cache()
        self.assertFalse(lib_sheet._insert_counts_in_frame(form, bbox, cache))
        self.assertFalse(lib_sheet._insert_counts_in_frame(mega, bbox, cache))
        own = (0.0, 0.0, 168_000.0, 118_800.0)
        self.assertTrue(lib_sheet._insert_counts_in_frame(form, own, cache))
        # float 어긋난 도곽도 FORM local extent로 잡는다.
        floated = (1e-6, 0.0, 168_000.0 + 1e-6, 118_800.0)
        self.assertTrue(lib_sheet._insert_counts_in_frame(form, floated, cache))


class PaperspaceFloorTests(unittest.TestCase):
    def _doc(self, *, status: int = 1, viewport_id: int | None = None):
        import ezdxf

        doc = ezdxf.new("R2010")
        msp = doc.modelspace()
        for index in range(1200):
            y = (index % 40) * 1000.0
            msp.add_line((0, y), (60_000, y))
        for y in range(100_000, 140_000, 8_000):
            msp.add_line((0, y), (60_000, y))
        layout = doc.layouts.new("FLOOR PLAN")
        layout.add_text(
            "27F FLOOR PLAN",
            dxfattribs={"insert": (10, 10), "height": 400},
        )
        viewport = layout.add_viewport(
            center=(400, 300),
            size=(300, 200),
            view_center_point=(30_000, 120_000),
            view_height=50_000,
            status=status,
        )
        if viewport_id is not None:
            viewport.dxf.id = viewport_id
        return doc

    def test_viewport_and_lower_band_split_one_floor(self):
        found = lib_sheet.discover_layout(self._doc(), mode="auto")
        self.assertEqual(found.method, "paperspace")
        self.assertEqual(found.floors, ["27F", "27F_2"])
        boxes = [frame.bbox for frame in found.sheets]
        lower = min(boxes, key=lambda box: box[1])
        upper = max(boxes, key=lambda box: box[1])
        self.assertLess(lower[3], 90_000)
        self.assertGreater(upper[1], 90_000)
        self.assertIn("종이공간", found.warnings[0])

    def test_off_viewport_finds_no_floor(self):
        found = lib_sheet.discover_layout(self._doc(status=0), mode="auto")
        self.assertEqual(found.floors, [])

    def test_layout_viewport_id_is_ignored(self):
        found = lib_sheet.discover_layout(self._doc(viewport_id=1), mode="auto")
        self.assertEqual(found.floors, [])


if __name__ == "__main__":
    unittest.main()
