"""NFC lookup must find an NFD Korean filename (macOS upload on Linux)."""

from __future__ import annotations

import os
import sys
import unittest
import unicodedata
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

import unicode_paths


class UnicodePathTests(unittest.TestCase):
    def test_resolve_nfc_lookup_finds_nfd_file(self):
        parent = "/mnt/workspace/lge/upload"
        nfd_name = unicodedata.normalize("NFD", "성북.dxf")
        nfc_name = unicodedata.normalize("NFC", "성북.dxf")
        self.assertNotEqual(nfd_name, nfc_name)
        stored = f"{parent}/{nfd_name}"
        asked = f"{parent}/{nfc_name}"

        def lexists(path: str) -> bool:
            return path == stored

        with mock.patch("unicode_paths.os.path.lexists", lexists):
            self.assertEqual(unicode_paths.resolve_existing_path(asked), stored)

    def test_listdir_fallback_when_spelling_does_not_lexist(self):
        parent = "/mnt/workspace/lge/upload"
        nfd_name = unicodedata.normalize("NFD", "성북.dxf")
        nfc_name = unicodedata.normalize("NFC", "성북.dxf")
        asked = f"{parent}/{nfc_name}"
        ancestors = {
            "/",
            "/mnt",
            "/mnt/workspace",
            "/mnt/workspace/lge",
            parent,
        }

        def lexists(path: str) -> bool:
            return path in ancestors

        def listdir(path: str):
            if path == parent:
                return [nfd_name]
            raise OSError(path)

        with (
            mock.patch("unicode_paths.os.path.lexists", lexists),
            mock.patch("unicode_paths.os.listdir", listdir),
        ):
            self.assertEqual(
                unicode_paths.resolve_existing_path(asked),
                f"{parent}/{nfd_name}",
            )

    def test_rewrite_quoted_nfc_path_to_nfd(self):
        parent = "/mnt/workspace/lge/upload"
        nfd_name = unicodedata.normalize("NFD", "성북.dxf")
        nfc_name = unicodedata.normalize("NFC", "성북.dxf")
        stored = f"{parent}/{nfd_name}"
        asked = f"{parent}/{nfc_name}"

        def lexists(path: str) -> bool:
            return path == stored

        with mock.patch("unicode_paths.os.path.lexists", lexists):
            command = f'python3 extract_2d.py --dxf "{asked}"'
            rewritten = unicode_paths.rewrite_command_unicode_paths(command)
        self.assertIn(stored, rewritten)
        self.assertNotIn(nfc_name, rewritten)

    def test_ascii_command_is_unchanged(self):
        command = "python3 extract_2d.py --dxf /tmp/plain.dxf"
        self.assertEqual(
            unicode_paths.rewrite_command_unicode_paths(command),
            command,
        )


if __name__ == "__main__":
    unittest.main()
