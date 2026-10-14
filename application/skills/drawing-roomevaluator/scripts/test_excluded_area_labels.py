"""방풍실, 보관실, 퇴식대, 샤워실, 고압, 저압, 계단실, 복도, EPS, OPEN, 홀-, 홈-, 입구, 출구, 양치공간, 청소도구함, 오픈, 조경, 배경, 평면도, 비품고을은 면적 실명이 아니다."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import evaluate_room


class ExcludedAreaLabelTests(unittest.TestCase):
    def test_named_non_area_rooms_are_not_labels(self):
        for text in ("방풍실-1", "소모품보관실", "퇴식대", "샤워실", "남샤워실-1", "고압", "저압반", "계단실-2", "복도", "복도-1", "E.P.S", "EPS", "P.S", "OPEN", "상부 OPEN", "홀-4", "홈-1", "입구", "출구", "장비반입구", "비상출구", "양치공간", "양치공간-1", "양치 공간-2", "청소도구함", "청소 도구함", "청소도구함-1", "오픈", "상부 오픈", "조경", "외부조경", "배경", "평면도", "1층 평면도", "비품고을", "비품 고을"):
            self.assertFalse(evaluate_room.is_room_label(text), text)
            self.assertTrue(evaluate_room.is_region_label(text), text)

    def test_ordinary_room_names_stay(self):
        self.assertTrue(evaluate_room.is_room_label("교실"))
        self.assertTrue(evaluate_room.is_room_label("개방형도서관"))
        self.assertFalse(evaluate_room.is_excluded_area_label("교실"))


if __name__ == "__main__":
    unittest.main()
