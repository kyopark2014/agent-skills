"""DWG는 DXF로 바뀐 뒤에만 층 추출로 넘어간다."""

from __future__ import annotations

import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import lib_dwg


def _dxf_with_sortents(*, sort_code: str = "5") -> str:
    """Minimal DXF containing one SORTENTSTABLE pair."""
    return (
        "0\nSECTION\n2\nHEADER\n0\nENDSEC\n"
        "0\nSECTION\n2\nOBJECTS\n"
        "0\nSORTENTSTABLE\n"
        "5\nAB\n"
        "100\nAcDbSortentsTable\n"
        "330\n1F\n"
        "331\n20\n"
        f"{sort_code}\n21\n"
        "0\nENDSEC\n"
        "0\nEOF\n"
    )


class EnsureDxfTests(unittest.TestCase):
    def test_dxf_is_unchanged(self):
        path = Path("/tmp/floor.dxf")
        self.assertEqual(lib_dwg.ensure_dxf(path), path)

    def test_dwg_is_converted_once(self):
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

    def test_libredwg_sortents_331_pairs_are_repaired(self):
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "broken.dxf"
            path.write_text(_dxf_with_sortents(sort_code="331"), encoding="utf-8")
            self.assertTrue(lib_dwg._sortents_has_bad_pairs(path))
            fixed = lib_dwg._repair_libredwg_sortents(path)
            self.assertEqual(fixed, 1)
            self.assertFalse(lib_dwg._sortents_has_bad_pairs(path))
            text = path.read_text(encoding="utf-8")
            self.assertIn("331\n20\n5\n21\n", text)
            self.assertNotIn("331\n20\n331\n21\n", text)

    def test_valid_sortents_are_left_alone(self):
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "ok.dxf"
            original = _dxf_with_sortents(sort_code="5")
            path.write_text(original, encoding="utf-8")
            self.assertEqual(lib_dwg._repair_libredwg_sortents(path), 0)
            self.assertEqual(path.read_text(encoding="utf-8"), original)

    def test_ensure_dxf_repairs_reused_conversion(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            source = root / "plan.dwg"
            source.write_bytes(b"AC1032")
            dest = root / "plan.dxf"
            dest.write_text(_dxf_with_sortents(sort_code="331"), encoding="utf-8")

            def convert(src: Path, dest_path: Path) -> None:
                raise AssertionError("should reuse existing dxf")

            got = lib_dwg.ensure_dxf(source, root, convert=convert)
            self.assertEqual(got, dest)
            self.assertIn("331\n20\n5\n21\n", got.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
