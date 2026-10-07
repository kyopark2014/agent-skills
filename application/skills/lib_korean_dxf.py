"""AutoCAD에서 한글이 보이도록 DXF 코드페이지와 기본 글꼴을 맞춘다.

한글이 깨지지 않는 도면은 UTF-8 본문에 `$DWGCODEPAGE` `ANSI_949`와
맑은 고딕(`malgun.ttf`, 한글 문자셋)을 쓴다. ezdxf 기본값은 `ANSI_1252`와
`txt.shx`라서 AutoCAD가 한글을 서유럽 코드페이지와 영문 셰이프 글꼴로 그린다.
"""

from __future__ import annotations

KOREAN_CODEPAGE = "ANSI_949"
KOREAN_FONT_FILE = "malgun.ttf"
KOREAN_FONT_FAMILY = "Malgun Gothic"
# HANGUL_CHARSET(129) << 8 | FF_MODERN(0x30) | VARIABLE_PITCH(0x02)
# AutoCAD가 맑은 고딕 스타일에 기록하는 확장 글꼴 플래그.
KOREAN_FONT_FLAGS = 33074

_CODEPAGE_FROM = b"$DWGCODEPAGE\n  3\nANSI_1252\n"
_CODEPAGE_TO = b"$DWGCODEPAGE\n  3\nANSI_949\n"
_STYLE_FROM = (
    b"AcDbTextStyleTableRecord\n  2\nStandard\n 70\n0\n 40\n0.0\n 41\n1.0\n"
    b" 50\n0.0\n 71\n0\n 42\n2.5\n  3\ntxt\n  4\n\n  0\n"
)
_STYLE_TO = (
    b"AcDbTextStyleTableRecord\n  2\nStandard\n 70\n0\n 40\n0.0\n 41\n1.0\n"
    b" 50\n0.0\n 71\n0\n 42\n2.5\n  3\nmalgun.ttf\n  4\n\n"
    b"1001\nACAD\n1000\nMalgun Gothic\n1071\n    33074\n  0\n"
)


def apply_korean_text(doc) -> None:
    """새 DXF 또는 ezdxf 문서의 코드페이지와 Standard 글꼴을 한글용으로 둔다.

    R2007 이후 본문은 UTF-8로 저장된다. ezdxf는 저장 직전에 `$DWGCODEPAGE`를
    `doc.encoding`에서 다시 쓰므로, 헤더만 바꾸면 `ANSI_1252`로 돌아간다.
    """
    doc.encoding = "cp949"
    doc.header["$DWGCODEPAGE"] = KOREAN_CODEPAGE
    if "ACAD" not in doc.appids:
        doc.appids.add("ACAD")
    style = doc.styles.get("Standard")
    style.dxf.font = KOREAN_FONT_FILE
    if style.has_xdata("ACAD"):
        style.discard_xdata("ACAD")
    style.set_xdata("ACAD", [(1000, KOREAN_FONT_FAMILY), (1071, KOREAN_FONT_FLAGS)])


def read_dxf(path: str):
    """Open a DXF even when the path's Hangul is NFD on disk and NFC in the call.

    macOS uploads keep decomposed filenames. Linux ``open`` does not fold them,
    so ``ezdxf.readfile`` fails inside ``is_binary_dxf_file`` with
    FileNotFoundError.
    """
    import sys
    from pathlib import Path

    import ezdxf

    root = str(Path(__file__).resolve().parents[1])
    if root not in sys.path:
        sys.path.insert(0, root)
    from unicode_paths import resolve_existing_path

    return ezdxf.readfile(resolve_existing_path(str(path)))


def patch_korean_dxf_bytes(data: bytes) -> bytes | None:
    """ezdxf 기본 헤더로 저장된 DXF만 코드페이지와 Standard 글꼴을 고친다.

    도면 엔티티는 다시 쓰지 않는다. 이미 고친 파일이면 None.
    """
    if _CODEPAGE_FROM not in data or _STYLE_FROM not in data:
        return None
    if data.count(_CODEPAGE_FROM) != 1 or data.count(_STYLE_FROM) != 1:
        return None
    return data.replace(_CODEPAGE_FROM, _CODEPAGE_TO, 1).replace(_STYLE_FROM, _STYLE_TO, 1)
