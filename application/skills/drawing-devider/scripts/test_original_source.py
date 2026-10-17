"""Catalog source stays the uploaded file, not a renamed artifacts copy."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from extract_2d import original_upload_source, upsert_drawing_list


class OriginalUploadSourceTests(unittest.TestCase):
    def test_renamed_artifacts_copy_uses_the_upload_name(self):
        with self._tree() as (root, upload, artifacts):
            original = upload / "073.경주부티호텔 (1).dxf"
            original.write_bytes(b"same-bytes")
            copy = artifacts / "073_gyeongju_boutique_hotel.dxf"
            copy.write_bytes(b"same-bytes")
            chosen = original_upload_source(copy)
            self.assertEqual(chosen, original.resolve())

    def test_upload_path_stays_put(self):
        with self._tree() as (root, upload, artifacts):
            original = upload / "plan.dxf"
            original.write_bytes(b"plan")
            self.assertEqual(original_upload_source(original), original.resolve())

    def test_ambiguous_size_keeps_the_given_path(self):
        with self._tree() as (root, upload, artifacts):
            (upload / "a.dxf").write_bytes(b"same")
            (upload / "b.dxf").write_bytes(b"same")
            copy = artifacts / "slug.dxf"
            copy.write_bytes(b"same")
            self.assertEqual(original_upload_source(copy), copy.resolve())

    def test_upsert_resets_rating_to_pending(self):
        import json
        from types import SimpleNamespace

        with self._tree() as (root, upload, artifacts):
            source = upload / "plan.dxf"
            source.write_bytes(b"plan")
            (artifacts / "drawing_list.json").write_text(
                json.dumps(
                    {
                        "drawings": [
                            {
                                "drawing_id": "hotel",
                                "folder": "hotel",
                                "rating": "good",
                                "floors": [],
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            layout = SimpleNamespace(method="test", floors=["1F"], sheets=[], warnings=[])
            upsert_drawing_list(artifacts, "hotel", source, layout, [])
            catalog = json.loads((artifacts / "drawing_list.json").read_text(encoding="utf-8"))
            self.assertEqual(catalog["drawings"][0]["rating"], "pending")

    def _tree(self):
        import tempfile

        raw = tempfile.TemporaryDirectory()
        root = Path(raw.name)
        upload = root / "upload"
        artifacts = root / "artifacts"
        upload.mkdir()
        artifacts.mkdir()
        return _Tree(raw, root, upload, artifacts)


class _Tree:
    def __init__(self, raw, root, upload, artifacts):
        self.raw = raw
        self.root = root
        self.upload = upload
        self.artifacts = artifacts

    def __enter__(self):
        return self.root, self.upload, self.artifacts

    def __exit__(self, *exc):
        self.raw.cleanup()


if __name__ == "__main__":
    unittest.main()
