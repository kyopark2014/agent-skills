"""DWG 입력을 층 추출이 읽는 DXF로 바꾼다.

우선순위는 ODA File Converter, 없으면 libredwg ``dwg2dxf``다.
이미 만든 DXF가 DWG보다 새로우면 다시 변환하지 않는다.

LibreDWG가 만든 DXF는 ``SORTENTSTABLE``에 ``(331, 331)`` 쌍을 넣는 경우가
있다. ezdxf는 ``(331, 5)``만 받아 ``Invalid sort handle code 331, expected 5``로
연다. 변환·재사용 직후 그 쌍만 ``(331, 5)``로 고친다.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path


class DwgConvertError(RuntimeError):
    """DWG를 DXF로 바꾸지 못했다."""


def ensure_dxf(source: Path, dest_dir: Path | None = None, *, convert=None) -> Path:
    """DXF는 그대로 반환하고, DWG는 ``dest_dir``(없으면 DWG 옆)에 DXF를 만든다."""
    source = Path(source)
    if source.suffix.lower() != ".dwg":
        path = source
        if path.suffix.lower() == ".dxf":
            _repair_libredwg_sortents(path)
        return path
    if not source.is_file():
        raise FileNotFoundError(f"DWG 없음: {source}")

    folder = Path(dest_dir) if dest_dir is not None else source.parent
    folder.mkdir(parents=True, exist_ok=True)
    dest = folder / f"{source.stem}.dxf"
    if _fresh(dest, source):
        print(f"reuse dxf {dest}", flush=True)
        _repair_libredwg_sortents(dest)
        return dest

    print(f"converting {source.name} -> {dest}", flush=True)
    runner = convert or convert_dwg
    runner(source, dest)
    if not dest.is_file() or dest.stat().st_size == 0:
        raise DwgConvertError(f"DXF가 만들어지지 않았습니다: {dest}")
    _repair_libredwg_sortents(dest)
    return dest


def convert_dwg(source: Path, dest: Path) -> None:
    """ODA File Converter 뒤에 ``dwg2dxf``를 시도한다."""
    errors: list[str] = []
    if _oda_available():
        try:
            _convert_with_oda(source, dest)
            return
        except Exception as exc:  # noqa: BLE001
            errors.append(f"ODA File Converter: {exc}")
    exe = shutil.which("dwg2dxf")
    if exe:
        try:
            _convert_with_dwg2dxf(exe, source, dest)
            return
        except Exception as exc:  # noqa: BLE001
            errors.append(f"dwg2dxf: {exc}")
    if not errors:
        raise DwgConvertError(
            "DWG를 DXF로 바꾸려면 ODA File Converter 또는 dwg2dxf(libredwg)가 필요합니다."
        )
    raise DwgConvertError("DWG 변환 실패\n" + "\n".join(errors))


def _fresh(dest: Path, source: Path) -> bool:
    if not dest.is_file() or dest.stat().st_size <= 0:
        return False
    return dest.stat().st_mtime >= source.stat().st_mtime


def _oda_available() -> bool:
    if shutil.which("ODAFileConverter"):
        return True
    mac = Path("/Applications/ODAFileConverter.app/Contents/MacOS/ODAFileConverter")
    return mac.is_file()


def _convert_with_oda(source: Path, dest: Path) -> None:
    mac = Path("/Applications/ODAFileConverter.app/Contents/MacOS/ODAFileConverter")
    if mac.is_file() and not shutil.which("ODAFileConverter"):
        shutil_path = str(mac.parent)
        import os

        os.environ["PATH"] = shutil_path + os.pathsep + os.environ.get("PATH", "")
    from ezdxf.addons import odafc

    tmp = dest.with_name(f".{dest.stem}.converting.dxf")
    if tmp.exists():
        tmp.unlink()
    odafc.convert(source, tmp, version="R2018", audit=True, replace=True)
    if not tmp.is_file() or tmp.stat().st_size == 0:
        tmp.unlink(missing_ok=True)
        raise DwgConvertError("ODA File Converter가 DXF를 만들지 못했습니다")
    tmp.replace(dest)


def _convert_with_dwg2dxf(exe: str, source: Path, dest: Path) -> None:
    tmp = dest.with_name(f".{dest.stem}.converting.dxf")
    if tmp.exists():
        tmp.unlink()
    proc = subprocess.run(
        [exe, "-y", "-o", str(tmp), str(source)],
        capture_output=True,
        text=True,
    )
    detail = (proc.stderr or proc.stdout or "").strip()
    if proc.returncode != 0 or not tmp.is_file() or tmp.stat().st_size == 0:
        tmp.unlink(missing_ok=True)
        tail = detail[-2000:] if detail else f"dwg2dxf exit {proc.returncode}"
        raise DwgConvertError(tail)
    warnings = detail.count("Warning:")
    if warnings:
        print(f"dwg2dxf warnings: {warnings}", file=sys.stderr)
    tmp.replace(dest)


def _repair_libredwg_sortents(path: Path) -> int:
    """LibreDWG ``SORTENTSTABLE``의 ``(331, 331)`` 쌍을 ``(331, 5)``로 고친다.

    도면 기하·레이어는 건드리지 않는다. 고친 쌍 개수를 반환한다.
    """
    path = Path(path)
    if not path.is_file() or path.suffix.lower() != ".dxf":
        return 0
    if not _sortents_has_bad_pairs(path):
        return 0
    fixed = _rewrite_sortents_pairs(path)
    if fixed:
        print(f"repaired LibreDWG SORTENTSTABLE pairs: {fixed} in {path}", flush=True)
    return fixed


def _sortents_has_bad_pairs(path: Path) -> bool:
    in_sort = False
    with path.open("r", encoding="utf-8", errors="surrogateescape", newline="") as fh:
        while True:
            raw = fh.readline()
            if not raw:
                return False
            token = raw.strip()
            if token == "SORTENTSTABLE":
                in_sort = True
                continue
            if not in_sort:
                continue
            if token == "0":
                in_sort = False
                continue
            if token != "331":
                continue
            # 331 / entity-handle / sort-code / sort-handle
            if not fh.readline():
                return False
            code_line = fh.readline()
            if not code_line:
                return False
            if code_line.strip() == "331":
                return True
            fh.readline()  # sort-handle value when code is already valid


def _rewrite_sortents_pairs(path: Path) -> int:
    tmp = path.with_name(f".{path.stem}.sortents.dxf")
    fixed = 0
    in_sort = False
    with path.open("r", encoding="utf-8", errors="surrogateescape", newline="") as src, tmp.open(
        "w", encoding="utf-8", errors="surrogateescape", newline=""
    ) as dst:
        while True:
            raw = src.readline()
            if not raw:
                break
            token = raw.strip()
            if token == "SORTENTSTABLE":
                in_sort = True
                dst.write(raw)
                continue
            if in_sort and token == "0":
                in_sort = False
                dst.write(raw)
                continue
            if in_sort and token == "331":
                handle = src.readline()
                code = src.readline()
                value = src.readline() if code else ""
                dst.write(raw)
                if handle:
                    dst.write(handle)
                if code and code.strip() == "331":
                    # Preserve original whitespace style when possible.
                    dst.write("5\n" if code.endswith("\n") else "5")
                    fixed += 1
                elif code:
                    dst.write(code)
                if value:
                    dst.write(value)
                continue
            dst.write(raw)
    if fixed:
        tmp.replace(path)
    else:
        tmp.unlink(missing_ok=True)
    return fixed
