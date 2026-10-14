"""방풍실, 보관실, 퇴식대, 샤워실, 고압, 저압, 계단실, 복도, 승강기, EPS, OPEN, 홀-, 홈-, 입구, 출구, 양치공간, 청소도구함, 청소도구실, 실외기실, 오픈, 조경, 배경, 평면도, 비품고을, 항습기, 화물은 면적 실명이 아니다."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import evaluate_room


class ExcludedAreaLabelTests(unittest.TestCase):
    def test_named_non_area_rooms_are_not_labels(self):
        for text in ("방풍실-1", "소모품보관실", "퇴식대", "샤워실", "남샤워실-1", "고압", "저압반", "계단실-2", "복도", "복도-1", "E.P.S", "EPS", "P.S", "OPEN", "상부 OPEN", "홀-4", "홈-1", "입구", "출구", "장비반입구", "비상출구", "양치공간", "양치공간-1", "양치 공간-2", "청소도구함", "청소 도구함", "청소도구함-1", "청소도구실", "청소 도구실", "청소도구실-1", "청소도구실#1", "실외기실", "실외기 실", "실외기실-1", "실외기실#1", "오픈", "상부 오픈", "조경", "외부조경", "배경", "평면도", "1층 평면도", "비품고을", "비품 고을", "승강기", "승강기-1", "승강기#2", "항습기", "항습기-1", "화물", "화물-1", "계단실#2", "계단실＃2", "복도#2", "E.V", "E.V#2", "E.V-1", "EV＃2", "휠체어", "휠체어#2", "린넨실", "린넨실#2", "오물처리실", "오물처리실#1", "(장애인)", "(장애인)화장실", "구급자", "응급환자", "의료가스실", "의료가스실-1", "기계실", "기계실#2", "분전함", "분전함-1", "세척실", "세척실(남)", "장비보관실", "물품보관실", "보관실#1", "탕비실", "탕비실#2", "물품", "물품#1", "스트레쳐", "스트레쳐#2", "스트레처#1"):
            self.assertFalse(evaluate_room.is_room_label(text), text)
            self.assertTrue(evaluate_room.is_region_label(text), text)

    def test_stair_shares_a_face_like_a_bathroom(self):
        for text in ("화장실", "화장실-1", "계단실", "계단실-2", "계단실#2", "계단실＃2", "WC"):
            self.assertTrue(evaluate_room.is_split_anchor(text), text)
            self.assertFalse(evaluate_room.is_room_label(text), text)
        self.assertFalse(evaluate_room.is_split_anchor("교실"))

    def test_equipment_callouts_are_not_area_labels(self):
        for text in (
            "수납장",
            "수납장-1",
            "하부장",
            "하부장-1",
            "상부장",
            "상부장-1",
            "주방상부장",
            "옷봉설치",
            "옷봉 설치",
            "장비",
            "장비-1",
            "데스크",
            "데스크#1",
            "서랍",
            "서랍#1",
            "키보드서랍",
            "온수기",
            "온수기-1",
            "살균기",
            "자외선살균기",
            "소독기",
            "소독기#1",
            "도어",
            "도어#1",
            "테이블",
            "테이블-1",
            "신발장",
            "정수기",
            "휴대폰장",
            "게시판",
            "렉장",
            "랙장",
            "냉장고",
            "제빙기",
            "머신",
            "커피",
            "커피머신",
            "전자랜지",
            "전자레인지",
            "템블러",
            "텀블러",
            "세척기",
            "식기세척기",
        ):
            self.assertFalse(evaluate_room.is_room_label(text), text)
            self.assertFalse(evaluate_room.is_region_label(text), text)
            self.assertTrue(evaluate_room.is_equipment_label(text), text)
        self.assertFalse(evaluate_room.is_equipment_label("ELEV"))
        self.assertFalse(evaluate_room.is_equipment_label("ELEV-1"))
        self.assertFalse(evaluate_room.is_equipment_label("E.V"))
        for text in ("ELEV", "ELEV.", "ELEV-1", "ELEV. HALL", "ELEV. 홀"):
            self.assertTrue(evaluate_room.is_excluded_area_label(text), text)
            self.assertFalse(evaluate_room.is_room_label(text), text)
        self.assertFalse(evaluate_room.is_excluded_area_label("HALL"))
        self.assertFalse(evaluate_room.is_excluded_area_label("홀"))

    def test_ordinary_room_names_stay(self):
        self.assertTrue(evaluate_room.is_room_label("교실"))
        self.assertTrue(evaluate_room.is_room_label("개방형도서관"))
        self.assertFalse(evaluate_room.is_excluded_area_label("교실"))


if __name__ == "__main__":
    unittest.main()
