"""DWG는 DXF로 바뀐 뒤에만 층 추출로 넘어간다."""

from __future__ import annotations

import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import lib_dwg


class EnsureDxfTests(unittest.TestCase):
    def test_dxf_is_unchanged(self):
        path = Path("/tmp/floor.dxf")
        self.assertEqual(lib_dwg.ensure_dxf(path), path)

    def test_dwg_is_converted_once(self):
        import tempfile

        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            source = root / "plan.dwg"
            source.write_bytes(b"AC1032")
            calls: list[Path] = []

            def convert(src: Path, dest: Path) -> None:
                calls.append(dest)
                dest.write_text("0\nSECTION\n", encoding="utf-8")

            dest_dir = root / "out"
            first = lib_dwg.ensure_dxf(source, dest_dir, convert=convert)
            time.sleep(0.02)
            second = lib_dwg.ensure_dxf(source, dest_dir, convert=convert)
            self.assertEqual(first, second)
            self.assertEqual(first.name, "plan.dxf")
            self.assertEqual(len(calls), 1)
            self.assertTrue(first.is_file())

    def test_stale_dxf_is_replaced(self):
        import os
        import tempfile

        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            source = root / "plan.dwg"
            source.write_bytes(b"AC1032")
            dest = root / "plan.dxf"
            dest.write_text("old", encoding="utf-8")
            os.utime(dest, (source.stat().st_atime - 10, source.stat().st_mtime - 10))

            def convert(src: Path, dest_path: Path) -> None:
                dest_path.write_text("new", encoding="utf-8")

            got = lib_dwg.ensure_dxf(source, root, convert=convert)
            self.assertEqual(got.read_text(encoding="utf-8"), "new")


if __name__ == "__main__":
    unittest.main()
