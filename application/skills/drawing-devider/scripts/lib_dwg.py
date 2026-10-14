"""DWG 입력을 층 추출이 읽는 DXF로 바꾼다.

우선순위는 ODA File Converter, 없으면 libredwg ``dwg2dxf``다.
이미 만든 DXF가 DWG보다 새로우면 다시 변환하지 않는다.
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
        return source
    if not source.is_file():
        raise FileNotFoundError(f"DWG 없음: {source}")

    folder = Path(dest_dir) if dest_dir is not None else source.parent
    folder.mkdir(parents=True, exist_ok=True)
    dest = folder / f"{source.stem}.dxf"
    if _fresh(dest, source):
        print(f"reuse dxf {dest}", flush=True)
        return dest

    print(f"converting {source.name} -> {dest}", flush=True)
    runner = convert or convert_dwg
    runner(source, dest)
    if not dest.is_file() or dest.stat().st_size == 0:
        raise DwgConvertError(f"DXF가 만들어지지 않았습니다: {dest}")
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
        [exe, "-o", str(tmp), str(source)],
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
