#!/usr/bin/env python3
"""Same room name on multiple places → #1/#2 postfix."""

from __future__ import annotations

import unittest

from detect_labels import disambiguate_duplicate_names


class TestDisambiguateDuplicateNames(unittest.TestCase):
    def test_unique_names_unchanged(self) -> None:
        detected = [
            {"text": "객실", "name": "객실", "x": 0.0, "y": 0.0},
            {"text": "복도", "name": "복도", "x": 1.0, "y": 1.0},
        ]
        disambiguate_duplicate_names(detected)
        self.assertEqual([item["text"] for item in detected], ["객실", "복도"])
        self.assertEqual([item["name"] for item in detected], ["객실", "복도"])

    def test_duplicates_get_postfix(self) -> None:
        detected = [
            {"text": "객실", "name": "객실", "x": 100.0, "y": 200.0},
            {"text": "객실", "name": "객실", "x": 300.0, "y": 400.0},
        ]
        disambiguate_duplicate_names(detected)
        # Higher y first → #1, then #2.
        by_xy = {(item["x"], item["y"]): item["text"] for item in detected}
        self.assertEqual(by_xy[(300.0, 400.0)], "객실#1")
        self.assertEqual(by_xy[(100.0, 200.0)], "객실#2")
        self.assertEqual({item["name"] for item in detected}, {"객실#1", "객실#2"})

    def test_skips_number_already_used_on_drawing(self) -> None:
        detected = [
            {"text": "객실#1", "name": "객실#1", "x": 0.0, "y": 500.0},
            {"text": "객실", "name": "객실", "x": 100.0, "y": 200.0},
            {"text": "객실", "name": "객실", "x": 300.0, "y": 400.0},
        ]
        disambiguate_duplicate_names(detected)
        texts = {item["text"] for item in detected}
        self.assertEqual(texts, {"객실#1", "객실#2", "객실#3"})
        self.assertEqual(len(texts), len(detected))


if __name__ == "__main__":
    unittest.main()
