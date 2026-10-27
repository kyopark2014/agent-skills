#!/usr/bin/env python3
"""도곽(도면 테두리)과 층 제목으로 층을 구분한다.

XA-S-{N}F 평면 블록이 없는 도면용 폴백.
modelspace와 얕은 INSERT에서 큰 축정렬 사각형과
'1층 평면도', 'B1F', '지하1층' 같은 제목을 짝짓는다.
제목이 도곽 바로 위(바깥)에 있어도 붙인다.
'4~5층'은 시작 층, '옥탑지붕'은 PH로 읽는다.
시트 블록(ooa-sheet 등) INSERT의 전역 bbox도 도곽으로 쓴다.
건물 평면이 최상위 INSERT에만 있을 때는 그 INSERT의 기하 bbox로 층을 둔다.
층 표기가 없고 시설 평면도 제목과 도면번호만 있으면 도면번호로 나눈다.
제목·도면번호가 없어도 크기 조건을 만족하는 도곽이면 sheet_01부터 남긴다.
그것도 없으면 종이공간 제목과 켜진 평면 뷰포트를 쓴다.
"""

from __future__ import annotations

import math
import re
from collections import defaultdict
from dataclasses import dataclass, field

MIN_EDGE_MM = 10_000.0
MAX_EDGE_MM = 500_000.0
MAX_ASPECT = 8.0
AXIS_TOL_MM = 20.0
MAX_STORED_LINES = 20_000
_BAND_MM = 10_000.0
_COMPANION_GAP_MM = 15_000.0
_COMPANION_MIN_LINES = 1_000
_BAND_MIN_LINES = 50


def normalize_floor_token(floor: str) -> str:
    """'5', '5F', 'b1', '1F_2', 'sheet_3' → '5F' / 'B1F' / '1F_2' / 'RF' / 'PH' / 'sheet_03'."""
    s = floor.strip().upper().replace(" ", "")
    m = re.fullmatch(r"SHEET_(\d+)", s)
    if m:
        number = int(m.group(1))
        if number < 1:
            raise ValueError(f"층 형식 오류: {floor!r} (예: 5F, B1F, 1F_2, RF, sheet_01)")
        return f"sheet_{number:02d}"
    m = re.fullmatch(r"(\d+)F(?:_(\d+))?", s)
    if m:
        suffix = f"_{int(m.group(2))}" if m.group(2) else ""
        return f"{int(m.group(1))}F{suffix}"
    m = re.fullmatch(r"(\d+)", s)
    if m:
        return f"{int(m.group(1))}F"
    m = re.fullmatch(r"B(\d+)F(?:_(\d+))?", s)
    if m:
        suffix = f"_{int(m.group(2))}" if m.group(2) else ""
        return f"B{int(m.group(1))}F{suffix}"
    m = re.fullmatch(r"(RF|ROOF)(?:_(\d+))?", s)
    if m:
        suffix = f"_{int(m.group(2))}" if m.group(2) else ""
        return f"RF{suffix}"
    m = re.fullmatch(r"(PH|PENTHOUSE)(?:_(\d+))?", s)
    if m:
        suffix = f"_{int(m.group(2))}" if m.group(2) else ""
        return f"PH{suffix}"
    matched = re.fullmatch(r"([A-Z]{2,6})-?(\d{2,4})(?:_(\d+))?", s)
    if matched and matched.group(1) not in {"RF", "PH", "SHEET"}:
        suffix = f"_{int(matched.group(3))}" if matched.group(3) else ""
        return f"{matched.group(1)}{matched.group(2)}{suffix}"
    raise ValueError(f"층 형식 오류: {floor!r} (예: 5F, B1F, 1F_2, RF, RF_2, sheet_01, AA406)")


def is_floor_name(name: str) -> bool:
    try:
        normalize_floor_token(name)
    except ValueError:
        return False
    return True


def floor_sort_key(floor: str) -> tuple:
    matched = re.fullmatch(r"B(\d+)F(?:_(\d+))?", floor)
    if matched:
        return (0, -int(matched.group(1)), int(matched.group(2) or 1), floor)
    matched = re.fullmatch(r"(\d+)F(?:_(\d+))?", floor)
    if matched:
        return (1, int(matched.group(1)), int(matched.group(2) or 1), floor)
    matched = re.fullmatch(r"RF(?:_(\d+))?", floor)
    if matched:
        return (3, int(matched.group(1) or 1), 0, floor)
    matched = re.fullmatch(r"PH(?:_(\d+))?", floor)
    if matched:
        return (4, int(matched.group(1) or 1), 0, floor)
    matched = re.fullmatch(r"sheet_(\d+)", floor)
    if matched:
        return (5, int(matched.group(1)), 0, floor)
    matched = re.fullmatch(r"([A-Z]{2,6})(\d{2,4})(?:_(\d+))?", floor)
    if matched:
        return (6, matched.group(1), int(matched.group(2)), int(matched.group(3) or 1))
    return (2, 0, 0, floor)


def plain_text(value: str) -> str:
    text = value.replace("\\P", " ").replace("\\~", " ")
    text = re.sub(r"\\[A-Za-z][^;]*;", "", text)
    text = text.replace("{", "").replace("}", "")
    return re.sub(r"\s+", " ", text).strip()


_FLOOR_HEAD = (
    r"(?:지상\s*)?"
    r"(?:지하\s*(?P<b>\d+)\s*층"
    r"|(?:제\s*)?(?P<bf>B\s*\d+)\s*F?"
    r"|(?:제\s*)?(?P<n>\d+)\s*(?:층|F))"
)
_PLAN_TAIL = r"(?:평면도|평면|PLAN|FLOOR|배치도|배치|공조조닝도|조닝도)"
_TRAIL_PAREN = r"(?:\s*\([^)]{0,20}\))?"


def _token_from_match(matched: re.Match[str]) -> str | None:
    if matched.group("b"):
        return f"B{int(matched.group('b'))}F"
    if matched.group("bf"):
        num = re.search(r"\d+", matched.group("bf"))
        return f"B{int(num.group(0))}F" if num else None
    if matched.group("n"):
        return f"{int(matched.group('n'))}F"
    return None


def _collapse_spaced_glyphs(raw: str) -> str:
    """글자마다 띄운 제목만 붙인다. '1 층 평 면 도' → '1층평면도'.

    '1층 냉난방 평면도'처럼 단어 사이 공백은 유지한다.
    한 글자(한글·숫자)가 연속된 구간만 붙이고, 두 글자 이상인 단어는 그대로 둔다.
    """
    tokens = raw.split()
    if len(tokens) < 2:
        return raw
    glued: list[str] = []
    buf: list[str] = []

    def flush() -> None:
        if buf:
            glued.append("".join(buf))
            buf.clear()

    for token in tokens:
        if len(token) == 1 and re.fullmatch(r"[가-힣\d]", token):
            buf.append(token)
            continue
        flush()
        glued.append(token)
    flush()
    return " ".join(glued)


_FLOORISH_LEAD = re.compile(r"^(?:RF|ROOF|PH|PENTHOUSE|B\d+F?)$", re.I)


def _strip_equipment_prefix(raw: str) -> str:
    """'EHP 1층 …'처럼 층 표기 앞의 영문 설비 코드를 뺀다. 층 토큰 자체는 남긴다."""
    tokens = raw.split()
    index = 0
    while index < len(tokens) - 1:
        token = tokens[index]
        if _FLOORISH_LEAD.fullmatch(token):
            break
        if re.fullmatch(r"[A-Za-z][A-Za-z0-9]{1,12}", token):
            index += 1
            continue
        break
    return " ".join(tokens[index:])


_DRAWING_INDEX_DOT = re.compile(r"^\d{1,3}[.)]\s*")
_DRAWING_INDEX_SPACE = re.compile(
    r"^\d{1,3}\s+(?=(?:지상\s*)?(?:지하|\d+\s*(?:층|F)|B\s*\d|옥|지붕|ROOF|RF|PH))",
    re.I,
)


def _strip_drawing_index_prefix(raw: str) -> str:
    """'01. 4F 평면도', '01) 2F 평면도', '01 3F 평면도'처럼 앞 도면 순번을 뺀다."""
    stripped = _DRAWING_INDEX_DOT.sub("", raw, count=1)
    if stripped != raw:
        return stripped
    return _DRAWING_INDEX_SPACE.sub("", raw, count=1)


_DRAWING_NUMBER = re.compile(r"^[A-Za-z]{2,6}-\d{2,4}$")
_PLAN_WORD = re.compile(
    r"평면도|단면도|입면도|배치도|공조조닝도|조닝도|\bPLAN\b|\bSECTION\b|\bELEVATION\b",
    re.I,
)
_REPEATED_FLOOR_NOTE = 8
_SHEET_INSERT_NAME = re.compile(
    r"(?:^|[^A-Za-z0-9])(?:ooa[_-]?)?sheet(?:$|[^A-Za-z0-9])|"
    r"도[곽각]|a-sheet|x-sheet|dwg.?sheet",
    re.I,
)
_BUILDING_INSERT_NAME = re.compile(
    r"^PL\d+|평면|클럽|관리동?|스타트|티하우스|티\d|경비|골프|club|building",
    re.I,
)
_INSERT_FLOOR_B = re.compile(r"-B(\d+)\s*$", re.I)
_INSERT_FLOOR_N = re.compile(r"-(\d+)\s*$")
_MIN_INSERT_GEOM = 200
_MIN_FRAME_GEOM = 30
_INSERT_BBOX_SAMPLE = 40_000
_INSERT_BBOX_TRIM = 0.02
# 층 제목이 도곽 테두리 바로 위(약 12–20 m)에 있는 도면용.
_TITLE_ABOVE_GAP_MM = 25_000.0
_TITLE_ABOVE_BAND_MM = 5_000.0
# 부모 격자 도곽 대비 이 비율 이상이면 실제 시트, 미만이면 복제 제목 칸.
_SHEET_CHILD_AREA_RATIO = 0.15
# 짧은 변 50 m 이상이면 면적 비율과 무관하게 실제 시트로 본다.
_SHEET_MIN_EDGE_MM = 50_000.0


def _is_construction_layer(name: str) -> bool:
    """비출력 보조선 레이어. Defpoint·Defpoints LINE은 도곽으로 쓰지 않는다.

    닫힌 폴리라인 도곽은 일반 레이어에 테두리가 없을 때만 폴백으로 쓴다.
    """
    token = re.sub(r"[^a-z]", "", (name or "").lower())
    return token.startswith("defpoint")


def _repeated_floor_notes(texts: list[_TextHit]) -> set[str]:
    """배치도에 반복된 '29F' 같은 층 코드는 도면 제목이 아니다.

    평면도·단면도 문구가 들어 있는 제목은 여러 도곽에 있어도 남긴다.
    """
    counts: dict[str, int] = {}
    for hit in texts:
        raw = plain_text(hit.text)
        if _PLAN_WORD.search(raw):
            continue
        counts[raw] = counts.get(raw, 0) + 1
    return {raw for raw, count in counts.items() if count >= _REPEATED_FLOOR_NOTE}


def parse_drawing_number(value: str) -> str | None:
    """표제란 도면번호. 'AA-406' → 'AA406'. 층 토큰이나 실명은 아니다."""
    raw = plain_text(value).replace(" ", "")
    if not _DRAWING_NUMBER.fullmatch(raw):
        return None
    letters, number = raw.split("-")
    return f"{letters.upper()}{number}"


def parse_plan_title(value: str) -> str | None:
    """층 번호 없이 시설 이름만 있는 평면도 제목.

    '피트니스센터, 에듀센터, 관리사무소 평면도', '스카이커뮤니티 평,입,단면도'처럼
    층 표기가 없어도 도곽 제목으로 쓴다. 이미 층 제목인 문자열은 여기서 다루지 않는다.
    """
    raw = _collapse_spaced_glyphs(plain_text(value))
    if not raw or len(raw) > 96 or len(raw) < 4:
        return None
    if parse_floor_label(raw):
        return None
    if not _PLAN_WORD.search(raw):
        return None
    if raw.upper() in {"평면도", "단면도", "입면도", "배치도", "PLAN", "SECTION", "ELEVATION"}:
        return None
    return raw


def parse_floor_label(value: str) -> str | None:
    """짧은 층 제목만 인정한다. 치수·실명·참조 문구는 제외.

    '1층 평면도'뿐 아니라 '1층 냉난방 평면도', '1층 냉난방 배관평면도',
    'EHP 1층 냉난방 제어 평면도', 'EHP 지붕층 냉난방 철거 평면도',
    '지붕층 냉난방 배관평면도'처럼 앞에 설비 코드가 있고
    층과 평면도 사이에 용도가 있는 제목도 인정한다.
    '01. 4F 평면도', '01 2F 평면도'처럼 앞에 도면 순번이 있어도 인정한다.
    '4~5층 평면도'처럼 한 시트에 묶인 층 구간은 시작 층(4F)으로 둔다.
    '옥탑지붕 평면도'는 옥탑(PH)으로 둔다.
    """
    raw = _collapse_spaced_glyphs(plain_text(value))
    if not raw or len(raw) > 48:
        return None
    if re.fullmatch(r"[\d.,\s]+", raw):
        return None
    body = _strip_drawing_index_prefix(_strip_equipment_prefix(raw))
    body = re.sub(r"(\d+)\s*[~\-～〜]\s*(\d+)", r"\1~\2", body)
    range_hit = re.fullmatch(
        rf"(?P<a>\d+)~(?P<b>\d+)\s*(?:층|F)(?:\s*{_PLAN_TAIL})?{_TRAIL_PAREN}$",
        body,
        re.I,
    )
    if range_hit:
        return f"{int(range_hit.group('a'))}F"
    range_titled = re.fullmatch(
        rf"(?P<a>\d+)~(?P<b>\d+)\s*(?:층|F)(?:\s+\S+){{1,4}}\s*{_PLAN_TAIL}{_TRAIL_PAREN}$",
        body,
        re.I,
    )
    if range_titled:
        return f"{int(range_titled.group('a'))}F"
    if len(set(re.findall(r"(\d+)\s*층", body))) > 1:
        return None
    if re.fullmatch(rf"옥탑지붕(?:층)?(?:\s*{_PLAN_TAIL})?", body, re.I):
        return "PH"
    if re.fullmatch(rf"옥탑지붕(?:층)?(?:\s+\S+){{0,3}}\s*{_PLAN_TAIL}", body, re.I):
        return "PH"
    if re.fullmatch(rf"(?:옥상|지붕층|지붕|ROOF|RF)(?:\s*{_PLAN_TAIL})?", body, re.I):
        return "RF"
    # `\s*` — '지붕층 냉난방 배관평면도'처럼 용도+평면도가 붙어 있어도 인정한다.
    if re.fullmatch(rf"(?:옥상|지붕층|지붕|ROOF|RF)(?:\s+\S+){{1,4}}\s*{_PLAN_TAIL}", body, re.I):
        return "RF"
    if re.fullmatch(rf"(?:옥탑층|옥탑|PH|PENTHOUSE)(?:\s*{_PLAN_TAIL})?", body, re.I):
        return "PH"
    if re.fullmatch(rf"(?:옥탑층|옥탑|PH|PENTHOUSE)(?:\s+\S+){{1,4}}\s*{_PLAN_TAIL}", body, re.I):
        return "PH"
    strict = re.fullmatch(rf"{_FLOOR_HEAD}(?:\s*{_PLAN_TAIL})?{_TRAIL_PAREN}$", body, re.I)
    if strict:
        return _token_from_match(strict)
    titled = re.fullmatch(rf"{_FLOOR_HEAD}(?:\s+\S+){{1,4}}\s*{_PLAN_TAIL}{_TRAIL_PAREN}$", body, re.I)
    if titled:
        return _token_from_match(titled)
    return None


@dataclass
class SheetFrame:
    floor: str
    title: str
    bbox: tuple[float, float, float, float]
    title_height: float

    def as_dict(self) -> dict:
        x0, y0, x1, y1 = self.bbox
        return {
            "floor": self.floor,
            "title": self.title,
            "bbox_mm": {"xmin": x0, "ymin": y0, "xmax": x1, "ymax": y1},
            "width_m": (x1 - x0) / 1000.0,
            "height_m": (y1 - y0) / 1000.0,
            "title_height": self.title_height,
        }


@dataclass
class LayoutDiscovery:
    method: str
    floors: list[str]
    sheets: list[SheetFrame] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "method": self.method,
            "floors": self.floors,
            "sheets": [s.as_dict() for s in self.sheets],
            "warnings": self.warnings,
        }


@dataclass
class _Seg:
    x0: float
    y0: float
    x1: float
    y1: float
    horizontal: bool

    @property
    def length(self) -> float:
        return (self.x1 - self.x0) if self.horizontal else (self.y1 - self.y0)


@dataclass
class _TextHit:
    text: str
    floor: str
    x: float
    y: float
    height: float


def _area(b: tuple[float, float, float, float]) -> float:
    return max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1])


def _contains(outer, inner, tol: float = 200.0) -> bool:
    return (
        outer[0] <= inner[0] + tol
        and outer[1] <= inner[1] + tol
        and outer[2] >= inner[2] - tol
        and outer[3] >= inner[3] - tol
    )


def _iou(a, b) -> float:
    ix0, iy0 = max(a[0], b[0]), max(a[1], b[1])
    ix1, iy1 = min(a[2], b[2]), min(a[3], b[3])
    iw, ih = ix1 - ix0, iy1 - iy0
    if iw <= 0 or ih <= 0:
        return 0.0
    inter = iw * ih
    union = _area(a) + _area(b) - inter
    return inter / union if union else 0.0


def _accept_size(w: float, h: float) -> bool:
    if w < MIN_EDGE_MM or h < MIN_EDGE_MM:
        return False
    if w > MAX_EDGE_MM or h > MAX_EDGE_MM:
        return False
    short = min(w, h)
    return short > 0 and max(w, h) / short <= MAX_ASPECT


def _rect_from_points(pts: list[tuple[float, float]], closed: bool):
    if len(pts) >= 2 and abs(pts[0][0] - pts[-1][0]) <= AXIS_TOL_MM and abs(pts[0][1] - pts[-1][1]) <= AXIS_TOL_MM:
        pts = pts[:-1]
        closed = True
    if not closed or len(pts) != 4:
        return None
    for i in range(4):
        x1, y1 = pts[i]
        x2, y2 = pts[(i + 1) % 4]
        if abs(x1 - x2) > AXIS_TOL_MM and abs(y1 - y2) > AXIS_TOL_MM:
            return None
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    x0, x1 = min(xs), max(xs)
    y0, y1 = min(ys), max(ys)
    if not _accept_size(x1 - x0, y1 - y0):
        return None
    return (x0, y0, x1, y1)


def _entity_text(entity) -> str:
    kind = entity.dxftype()
    if kind == "TEXT":
        return str(entity.dxf.text or "")
    if kind == "MTEXT":
        try:
            return str(entity.plain_text() or "")
        except Exception:
            return str(getattr(entity, "text", "") or "")
    return ""


def _text_height(entity) -> float:
    if entity.dxftype() == "TEXT":
        return float(entity.dxf.height or 0)
    return float(getattr(entity.dxf, "char_height", 0) or 0)


def _poly_points(entity):
    kind = entity.dxftype()
    if kind == "LWPOLYLINE":
        pts: list[tuple[float, float]] = []
        try:
            raw = list(entity.get_points("xyb"))
        except Exception:
            return None
        for p in raw:
            bulge = float(p[2]) if len(p) > 2 else 0.0
            if abs(bulge) > 1e-4:
                return None
            pts.append((float(p[0]), float(p[1])))
        return pts, bool(entity.closed)
    if kind == "POLYLINE":
        try:
            if entity.is_3d_polyline or entity.is_polygon_mesh:
                return None
            pts = [(float(v.dxf.location.x), float(v.dxf.location.y)) for v in entity.vertices]
        except Exception:
            return None
        return pts, bool(entity.is_closed)
    return None


def _add_line(entity, lines: list[_Seg]) -> None:
    try:
        x0, y0 = float(entity.dxf.start.x), float(entity.dxf.start.y)
        x1, y1 = float(entity.dxf.end.x), float(entity.dxf.end.y)
    except Exception:
        return
    if abs(y1 - y0) <= AXIS_TOL_MM:
        xa, xb = (x0, x1) if x0 <= x1 else (x1, x0)
        if xb - xa >= MIN_EDGE_MM:
            lines.append(_Seg(xa, (y0 + y1) / 2, xb, (y0 + y1) / 2, True))
    elif abs(x1 - x0) <= AXIS_TOL_MM:
        ya, yb = (y0, y1) if y0 <= y1 else (y1, y0)
        if yb - ya >= MIN_EDGE_MM:
            lines.append(_Seg((x0 + x1) / 2, ya, (x0 + x1) / 2, yb, False))


def _merge_collinear(segs: list[_Seg], *, horizontal: bool) -> list[_Seg]:
    groups: dict[int, list[_Seg]] = defaultdict(list)
    for seg in segs:
        key = round((seg.y0 if horizontal else seg.x0) / 2.0)
        groups[key].append(seg)
    merged: list[_Seg] = []
    for items in groups.values():
        items.sort(key=lambda s: s.x0 if horizontal else s.y0)
        cur = items[0]
        for seg in items[1:]:
            if horizontal and seg.x0 <= cur.x1 + 50:
                cur = _Seg(cur.x0, cur.y0, max(cur.x1, seg.x1), cur.y1, True)
            elif not horizontal and seg.y0 <= cur.y1 + 50:
                cur = _Seg(cur.x0, cur.y0, cur.x1, max(cur.y1, seg.y1), False)
            else:
                merged.append(cur)
                cur = seg
        merged.append(cur)
    return [s for s in merged if s.length >= MIN_EDGE_MM]


def _rects_from_lines(lines: list[_Seg]) -> list[tuple[float, float, float, float]]:
    horiz = _merge_collinear([s for s in lines if s.horizontal], horizontal=True)
    vert = _merge_collinear([s for s in lines if not s.horizontal], horizontal=False)
    if len(horiz) < 2 or len(vert) < 2:
        return []
    v_by_x: dict[int, list[_Seg]] = defaultdict(list)
    for seg in vert:
        v_by_x[round(seg.x0 / 100.0)].append(seg)

    def has_vertical(x_target: float, y0: float, y1: float) -> bool:
        tol = max(300.0, (y1 - y0) * 0.02)
        lo = round((x_target - tol) / 100.0)
        hi = round((x_target + tol) / 100.0)
        span = y1 - y0
        for key in range(lo, hi + 1):
            for seg in v_by_x.get(key, []):
                if seg.y0 <= y0 + tol and seg.y1 >= y1 - tol and seg.length >= span * 0.85:
                    return True
        return False

    buckets: dict[int, list[_Seg]] = defaultdict(list)
    for seg in horiz:
        cx = round(((seg.x0 + seg.x1) / 2) / 1000.0) * 1000
        buckets[cx].append(seg)

    found: list[tuple[float, float, float, float]] = []
    for items in buckets.values():
        items.sort(key=lambda s: s.y0)
        for i, top in enumerate(items):
            for bot in items[i + 1 :]:
                dy = bot.y0 - top.y0
                if dy < MIN_EDGE_MM:
                    continue
                if dy > MAX_EDGE_MM:
                    break
                shorter = min(top.length, bot.length)
                longer = max(top.length, bot.length)
                if shorter / longer < 0.9:
                    continue
                overlap = min(top.x1, bot.x1) - max(top.x0, bot.x0)
                if overlap < shorter * 0.9:
                    continue
                x0 = min(top.x0, bot.x0)
                x1 = max(top.x1, bot.x1)
                y0, y1 = top.y0, bot.y0
                if y0 > y1:
                    y0, y1 = y1, y0
                if not _accept_size(x1 - x0, y1 - y0):
                    continue
                if has_vertical(x0, y0, y1) and has_vertical(x1, y0, y1):
                    found.append((x0, y0, x1, y1))
    return found


def _coverage_fraction(outer, inners, grid: int = 24) -> float:
    """outer 영역이 inners(격자 샘플링)로 얼마나 덮이는지 0~1로 추정한다.

    단순 면적 합은 inners가 겹칠 때 1.0을 넘어 과대평가되므로,
    실제 겹치지 않는 면적 비율을 격자 샘플로 근사한다.
    """
    x0, y0, x1, y1 = outer
    w, h = x1 - x0, y1 - y0
    if w <= 0 or h <= 0:
        return 0.0
    step_x, step_y = w / grid, h / grid
    hit = 0
    for i in range(grid):
        cx = x0 + (i + 0.5) * step_x
        for j in range(grid):
            cy = y0 + (j + 0.5) * step_y
            for inner in inners:
                if inner[0] <= cx <= inner[2] and inner[1] <= cy <= inner[3]:
                    hit += 1
                    break
    return hit / (grid * grid)


def _children_are_nested(
    children: list[tuple[float, float, float, float]],
) -> bool:
    """동심 테두리처럼 한쪽이 다른 쪽을 품으면 True. 좌우 격자 칸은 False."""
    for index, left in enumerate(children):
        for right in children[index + 1 :]:
            if _contains(left, right) or _contains(right, left):
                return True
    return False


def _dedupe_rects(rects: list[tuple[float, float, float, float]]):
    """같은 테두리의 중복은 치우되, 진짜 바깥 도곽(코너·치수선 조각들의 부모)만 남긴다.

    예전 버전은 "안에 들어온 작은 사각형이 2개 이상이면 부모를 지운다"는 규칙만 썼다.
    치수선·심볼 조각이 큰 도곽 구석에 우연히 2개 겹치면 그 큰 도곽 자체(=실제 도면 테두리)가
    통째로 사라져 "도곽 안에 기하가 없습니다" 오류로 이어졌다. 자식들이 바깥 사각형의
    대부분(코너 2개 조각 정도가 아니라 과반)을 덮을 때만 부모를 중복으로 보고 지운다.
    다만 동심(숨김선·보조) 테두리가 겹쳐 덮는 경우는 격자 칸이 아니므로 부모 시트를 남긴다.
    """
    ordered = sorted(rects, key=_area, reverse=True)
    kept: list[tuple[float, float, float, float]] = []
    for rect in ordered:
        if any(_iou(rect, prev) >= 0.8 for prev in kept):
            continue
        if any(_contains(prev, rect) and _area(prev) < _area(rect) * 1.25 for prev in kept):
            continue
        kept.append(rect)
    parents = set()
    for i, outer in enumerate(kept):
        children = [
            inner
            for j, inner in enumerate(kept)
            if i != j and _contains(outer, inner) and _area(inner) < _area(outer) * 0.85
        ]
        if (
            len(children) >= 2
            and _coverage_fraction(outer, children) >= 0.6
            and not _children_are_nested(children)
        ):
            parents.add(i)
    return [rect for i, rect in enumerate(kept) if i not in parents]


def _inside(rect, x: float, y: float, pad: float = 0.0) -> bool:
    return rect[0] - pad <= x <= rect[2] + pad and rect[1] - pad <= y <= rect[3] + pad


def _title_overhead(rect, x: float, y: float, max_gap: float) -> bool:
    """제목이 도곽 바로 위(바깥)에 있고 x 구간이 겹치면 True."""
    x0, _y0, x1, y1 = rect
    if y < y1 or y > y1 + max_gap:
        return False
    slack = (x1 - x0) * 0.05
    return x0 - slack <= x <= x1 + slack


def _title_claimed_by_other(rect, x: float, y: float, rects) -> bool:
    """제목이 다른 도곽에 더 잘 속하면 True.

    복제 제목 칸(같거나 더 작은 도곽)이 제목을 품으면 가로채지 않는다.
    격자 부모·세로로 긴 조각처럼 더 큰 도곽만 제목을 포함하면
    아래쪽 실제 시트가 위쪽 매칭으로 가져갈 수 있다.
    """
    for other in rects:
        if other == rect:
            continue
        if not _inside(other, x, y):
            continue
        if _contains(other, rect):
            continue
        if _area(other) > _area(rect) * 1.25:
            continue
        return True
    return False


def _hit_is_header_of_better_sheet(rect, hit: _TextHit, rects) -> bool:
    """이 도곽 안의 제목이 더 작은 실제 시트의 머리글이면 True."""
    for other in rects:
        if other == rect:
            continue
        if _area(other) >= _area(rect):
            continue
        other_edge = min(other[2] - other[0], other[3] - other[1])
        if other_edge < _SHEET_MIN_EDGE_MM:
            continue
        gap = min(_TITLE_ABOVE_GAP_MM, max(12_000.0, (other[3] - other[1]) * 0.3))
        if _title_overhead(other, hit.x, hit.y, gap):
            return True
    return False


def _hits_for_rect(rect, texts: list[_TextHit], rects) -> list[_TextHit]:
    """도곽 안·가장자리·바로 위 제목을 모은다.

    제목이 다른 비부모 도곽 안에 있으면 그 도곽에만 맡긴다.
    세로로 긴 조각 안의 제목이 아래 시트의 머리글이면 그 시트에 맡긴다.
    위쪽 제목이 여러 줄이면 가장 가까운 줄만 쓴다.
    """
    found = [
        hit
        for hit in texts
        if _inside(rect, hit.x, hit.y) and not _hit_is_header_of_better_sheet(rect, hit, rects)
    ]
    if found:
        return found
    pad = min(rect[2] - rect[0], rect[3] - rect[1]) * 0.08
    found = [
        hit
        for hit in texts
        if _inside(rect, hit.x, hit.y, pad)
        and not _title_claimed_by_other(rect, hit.x, hit.y, rects)
        and not _hit_is_header_of_better_sheet(rect, hit, rects)
    ]
    if found:
        return found
    gap = min(_TITLE_ABOVE_GAP_MM, max(12_000.0, (rect[3] - rect[1]) * 0.3))
    overhead = [
        hit
        for hit in texts
        if _title_overhead(rect, hit.x, hit.y, gap)
        and not _title_claimed_by_other(rect, hit.x, hit.y, rects)
    ]
    if not overhead:
        return []
    nearest_y = min(hit.y for hit in overhead)
    return [hit for hit in overhead if hit.y <= nearest_y + _TITLE_ABOVE_BAND_MM]


def _title_score(hit: _TextHit) -> float:
    bonus = (
        3.0
        if re.search(r"평면|단면|입면|PLAN|FLOOR|SECTION|배치|조닝|공조", hit.text, re.I)
        else 1.0
    )
    return max(hit.height, 1.0) * bonus


def _split_multi(rect, hits: list[_TextHit]):
    best: dict[str, _TextHit] = {}
    for hit in hits:
        prev = best.get(hit.floor)
        if prev is None or _title_score(hit) > _title_score(prev):
            best[hit.floor] = hit
    titles = list(best.values())
    if len(titles) < 2:
        return []

    def bands(axis: str):
        width = rect[2] - rect[0]
        height = rect[3] - rect[1]
        if axis == "x":
            ordered = sorted(titles, key=lambda h: h.x)
            gap_need = max(15_000.0, width * 0.12)
            gaps = [ordered[i + 1].x - ordered[i].x for i in range(len(ordered) - 1)]
        else:
            ordered = sorted(titles, key=lambda h: h.y)
            gap_need = max(15_000.0, height * 0.12)
            gaps = [ordered[i + 1].y - ordered[i].y for i in range(len(ordered) - 1)]
        if not gaps or max(gaps) < gap_need:
            return []
        edges = [rect[0] if axis == "x" else rect[1]]
        for i in range(len(ordered) - 1):
            if axis == "x":
                edges.append((ordered[i].x + ordered[i + 1].x) / 2)
            else:
                edges.append((ordered[i].y + ordered[i + 1].y) / 2)
        edges.append(rect[2] if axis == "x" else rect[3])
        out = []
        for i, hit in enumerate(ordered):
            if axis == "x":
                sub = (edges[i], rect[1], edges[i + 1], rect[3])
            else:
                sub = (rect[0], edges[i], rect[2], edges[i + 1])
            if not _accept_size(sub[2] - sub[0], sub[3] - sub[1]):
                return []
            if not _inside(sub, hit.x, hit.y):
                return []
            out.append((hit, sub))
        return out

    split = bands("x") or bands("y")
    return split


def _frames_from(rects, texts: list[_TextHit], warnings: list[str]) -> list[SheetFrame]:
    frames: list[SheetFrame] = []
    pending: list[tuple[tuple[float, float, float, float], list[str]]] = []
    for rect in rects:
        hits = _hits_for_rect(rect, texts, rects)
        if not hits:
            continue
        ranked = sorted(hits, key=_title_score, reverse=True)
        best = ranked[0]
        # 같은 층 제목이 둘이면(제어 평면도와 철거 평면도) 도곽 전체를 그 층 하나로 둔다.
        # 서로 다른 층이 한 도곽에 있을 때만 영역을 나누거나 미확정으로 남긴다.
        rivals = [h for h in ranked[1:] if h.floor != best.floor and _title_score(h) >= _title_score(best) * 0.67]
        if rivals:
            split = _split_multi(rect, hits)
            if not split:
                pending.append((rect, sorted({h.floor for h in ranked})))
                continue
            for hit, sub in split:
                frames.append(SheetFrame(hit.floor, hit.text, sub, hit.height))
            continue
        frames.append(SheetFrame(best.floor, best.text, rect, best.height))

    named = _assign_sheet_ids(frames, warnings)
    return named + _assign_unresolved_ids(pending, named, warnings)


def _assign_unresolved_ids(
    pending: list[tuple[tuple[float, float, float, float], list[str]]],
    named: list[SheetFrame],
    warnings: list[str],
) -> list[SheetFrame]:
    """층 표기가 한 도곽에 뭉쳐 나누지 못한 테두리를 위치 순번으로 남긴다.

    이미 층이 확정된 도곽과 겹치거나, 더 큰 도곽 안에 들어간 테두리는 뺀다.
    남은 도곽은 왼쪽·아래부터 sheet_01, sheet_02 이다. 표기 중 하나를 층 이름으로 쓰지 않는다.
    """
    candidates: list[tuple[tuple[float, float, float, float], list[str]]] = []
    for rect, labels in pending:
        if any(_sheets_overlap(rect, frame.bbox) for frame in named):
            continue
        candidates.append((rect, labels))
    candidates.sort(key=lambda item: _area(item[0]), reverse=True)
    selected: list[tuple[tuple[float, float, float, float], list[str]]] = []
    for rect, labels in candidates:
        if any(_sheets_overlap(rect, prev) for prev, _labels in selected):
            continue
        selected.append((rect, labels))
    selected.sort(key=lambda item: (item[0][0], item[0][1]))

    out: list[SheetFrame] = []
    for index, (rect, labels) in enumerate(selected, start=1):
        token = f"sheet_{index:02d}"
        title = ("미확정: " + ", ".join(labels)) if labels else "미확정"
        out.append(SheetFrame(token, title, rect, 0.0))
    if out:
        names = ", ".join(frame.floor for frame in out)
        if any(labels for _rect, labels in selected):
            warnings.append(
                f"층 이름을 확정하지 못한 도곽 {len(out)}곳입니다. "
                f"왼쪽·아래부터 {names} 입니다."
            )
        else:
            warnings.append(
                f"층 제목·도면번호가 없어 도곽만으로 나눈 {len(out)}곳입니다. "
                f"왼쪽·아래부터 {names} 입니다."
            )
    return out


def _sheets_overlap(a: tuple[float, float, float, float], b: tuple[float, float, float, float]) -> bool:
    return _iou(a, b) >= 0.5 or _contains(a, b) or _contains(b, a)


def _assign_sheet_ids(frames: list[SheetFrame], warnings: list[str]) -> list[SheetFrame]:
    """겹치는 같은 층 도곽은 하나로 합치고, 떨어진 같은 제목은 왼쪽부터 1F, 1F_2 로 둔다."""
    clusters: list[list[SheetFrame]] = []
    for frame in frames:
        placed = False
        for group in clusters:
            if group[0].floor == frame.floor and any(_sheets_overlap(frame.bbox, prev.bbox) for prev in group):
                group.append(frame)
                placed = True
                break
        if not placed:
            clusters.append([frame])

    picked: list[SheetFrame] = []
    for group in clusters:
        # 격자 부모·실제 시트가 한 클러스터면 시트를 남긴다(면적 최대가 아님).
        resolved = _prefer_largest_same_floor(group)
        if not resolved:
            continue
        best = max(
            resolved,
            key=lambda frame: (
                _title_score(_TextHit(frame.title, frame.floor, 0, 0, frame.title_height)),
                _area(frame.bbox),
            ),
        )
        picked.append(best)

    by_floor: dict[str, list[SheetFrame]] = defaultdict(list)
    for frame in picked:
        by_floor[frame.floor].append(frame)

    out: list[SheetFrame] = []
    for floor in sorted(by_floor.keys(), key=floor_sort_key):
        # 같은 층이 여러 동에 있으면 큰 도면(본관 평면)이 접미 없는 이름을 받는다.
        group = sorted(
            by_floor[floor],
            key=lambda frame: (-_area(frame.bbox), frame.bbox[0], frame.bbox[1]),
        )
        if len(group) > 1:
            names = [floor] + [f"{floor}_{i}" for i in range(2, len(group) + 1)]
            warnings.append(
                f"제목 {group[0].title!r} 도곽이 {len(group)}곳입니다. "
                f"면적이 큰 순서로 {', '.join(names)} 입니다."
            )
        for index, frame in enumerate(group, start=1):
            token = floor if index == 1 else f"{floor}_{index}"
            out.append(SheetFrame(token, frame.title, frame.bbox, frame.title_height))
    return out


def _hits_inside(rect, hits: list[_TextHit], rects) -> list[_TextHit]:
    return _hits_for_rect(rect, hits, rects)


def _frames_from_plan_sheets(
    rects,
    plans: list[_TextHit],
    numbers: list[_TextHit],
    named: list[SheetFrame],
    warnings: list[str],
    *,
    bare_rects: list[tuple[float, float, float, float]] | None = None,
) -> list[SheetFrame]:
    """층 번호가 없는 시설 평면도를 도곽마다 도면번호로 남긴다.

    안쪽 칸이 많아 부모 테두리가 빠져도, 도면번호가 들어 있는 바깥 도곽은 남긴다.
    한 도곽 안의 하위 제목은 나누지 않는다. 도면번호가 없으면 sheet_XX 다.
    """
    bare = set(bare_rects or rects)
    candidates = []
    for rect in rects:
        if any(_sheets_overlap(rect, frame.bbox) for frame in named):
            continue
        inside_plans = _hits_inside(rect, plans, rects)
        if not inside_plans:
            continue
        best = max(inside_plans, key=_title_score)
        inside_numbers = _hits_inside(rect, numbers, rects)
        token = None
        if inside_numbers:
            unique: dict[str, _TextHit] = {}
            for hit in inside_numbers:
                prev = unique.get(hit.floor)
                if prev is None or _title_score(hit) > _title_score(prev):
                    unique[hit.floor] = hit
            if len(unique) == 1:
                token = next(iter(unique))
            else:
                token = min(
                    unique,
                    key=lambda floor: (unique[floor].x - best.x) ** 2 + (unique[floor].y - best.y) ** 2,
                )
        if token is None and rect not in bare:
            continue
        candidates.append((rect, best, token, _peer_plan_titles(inside_plans)))
    candidates.sort(key=lambda item: _area(item[0]), reverse=True)
    selected = []
    for item in candidates:
        if any(_sheets_overlap(item[0], prev[0]) for prev in selected):
            continue
        selected.append(item)

    framed: list[SheetFrame] = []
    pending: list[tuple[tuple[float, float, float, float], list[str]]] = []
    numbered_boxes = [rect for rect, _best, token, _labels in selected if token]
    for rect, best, token, labels in selected:
        if token:
            framed.append(SheetFrame(token, plain_text(best.text), rect, best.height))
            continue
        if any(_inside(box, best.x, best.y) for box in numbered_boxes):
            continue
        pending.append((rect, labels))
    named_plans = _assign_sheet_ids(framed, warnings)
    unresolved = _assign_unresolved_ids(pending, named + named_plans, warnings)
    if named_plans:
        warnings.append(
            "층 표기가 없어 도면번호로 나눈 도곽입니다. "
            + ", ".join(frame.floor for frame in named_plans)
            + " 입니다."
        )
    return named_plans + unresolved


def _peer_plan_titles(hits: list[_TextHit]) -> list[str]:
    """비슷한 크기의 제목은 모두 남긴다. 더 작은 하위 제목은 빼니다."""
    best_score = _title_score(max(hits, key=_title_score))
    labels: list[str] = []
    seen: set[str] = set()
    for hit in sorted(hits, key=_title_score, reverse=True):
        if _title_score(hit) < best_score * 0.9:
            continue
        text = plain_text(hit.text)
        if text in seen:
            continue
        seen.add(text)
        labels.append(text)
    return labels


def _collect_primitives(doc):
    rects: list[tuple[float, float, float, float]] = []
    lines: list[_Seg] = []
    texts: list[_TextHit] = []
    plans: list[_TextHit] = []
    numbers: list[_TextHit] = []
    truncated = False

    def take(entity) -> str | None:
        nonlocal truncated
        kind = entity.dxftype()
        layer = str(getattr(entity.dxf, "layer", "") or "")
        if kind == "LINE" and _is_construction_layer(layer):
            return None
        if kind in {"LWPOLYLINE", "POLYLINE"} and _is_construction_layer(layer):
            # LINE 보조선과 달리 닫힌 폴리라인은 폴백 후보로만 따로 모은다.
            return None
        if kind == "LINE":
            if len(lines) < MAX_STORED_LINES:
                _add_line(entity, lines)
            else:
                truncated = True
            return None
        if kind in {"LWPOLYLINE", "POLYLINE"}:
            parsed = _poly_points(entity)
            if not parsed:
                return None
            rect = _rect_from_points(parsed[0], parsed[1])
            if rect:
                rects.append(rect)
            return None
        if kind in {"TEXT", "MTEXT"}:
            raw = _entity_text(entity)
            label = parse_floor_label(raw)
            number = None if label else parse_drawing_number(raw)
            title = None if label or number else parse_plan_title(raw)
            if not label and not number and not title:
                return None
            try:
                x, y = float(entity.dxf.insert.x), float(entity.dxf.insert.y)
            except Exception:
                return None
            height = _text_height(entity)
            if label:
                texts.append(_TextHit(raw, label, x, y, height))
            elif number:
                numbers.append(_TextHit(raw, number, x, y, height))
            else:
                plans.append(_TextHit(raw, "", x, y, height))
            return None
        if kind == "INSERT":
            return "descend"
        return None

    def walk(entities, depth: int) -> None:
        for entity in entities:
            action = take(entity)
            if action == "descend" and depth < 2:
                try:
                    children = entity.virtual_entities()
                except Exception:
                    continue
                walk(children, depth + 1)

    walk(doc.modelspace(), 0)
    return rects, lines, texts, plans, numbers, truncated


def _collect_construction_poly_rects(doc) -> list[tuple[float, float, float, float]]:
    """일반 도곽이 없을 때 쓸 Defpoints 등 보조선 레이어의 닫힌 폴리라인 사각형."""
    rects: list[tuple[float, float, float, float]] = []

    def take(entity) -> str | None:
        kind = entity.dxftype()
        layer = str(getattr(entity.dxf, "layer", "") or "")
        if kind in {"LWPOLYLINE", "POLYLINE"} and _is_construction_layer(layer):
            parsed = _poly_points(entity)
            if not parsed:
                return None
            rect = _rect_from_points(parsed[0], parsed[1])
            if rect:
                rects.append(rect)
            return None
        if kind == "INSERT":
            return "descend"
        return None

    def walk(entities, depth: int) -> None:
        for entity in entities:
            action = take(entity)
            if action == "descend" and depth < 2:
                try:
                    children = entity.virtual_entities()
                except Exception:
                    continue
                walk(children, depth + 1)

    walk(doc.modelspace(), 0)
    return rects


def _frames_from_sheet_rects(
    rects: list[tuple[float, float, float, float]],
    texts: list[_TextHit],
    plans: list[_TextHit],
    numbers: list[_TextHit],
    warnings: list[str],
) -> list[SheetFrame]:
    """도곽 사각형 목록에서 층/시트 프레임을 만든다."""
    deduped = _dedupe_rects(rects)
    frames = _frames_from(deduped, texts, warnings)
    frames.extend(
        _frames_from_plan_sheets(rects, plans, numbers, frames, warnings, bare_rects=deduped)
    )
    if not frames and deduped:
        frames = _assign_unresolved_ids([(rect, []) for rect in deduped], [], warnings)
    return frames


def _viewport_model_bbox(viewport) -> tuple[float, float, float, float] | None:
    """켜진 평면 뷰포트가 보는 모델 범위. 배치용(id 1)과 꺼진 뷰포트는 뺀다."""
    dxf = viewport.dxf
    if int(dxf.status or 0) <= 0:
        return None
    if int(getattr(dxf, "id", 0) or 0) == 1:
        return None
    if int(getattr(dxf, "flags", 0) or 0) & 0x20000:
        return None
    try:
        if not viewport.is_top_view:
            return None
        x0, y0, x1, y1 = viewport.get_modelspace_limits()
    except Exception:
        return None
    if not _accept_size(x1 - x0, y1 - y0):
        return None
    return (float(x0), float(y0), float(x1), float(y1))


def _paperspace_titles(layout) -> list[_TextHit]:
    hits: list[_TextHit] = []
    for entity in layout:
        kind = entity.dxftype()
        if kind in {"TEXT", "MTEXT"}:
            raw = _entity_text(entity)
            label = parse_floor_label(raw)
            if not label:
                continue
            try:
                x, y = float(entity.dxf.insert.x), float(entity.dxf.insert.y)
            except Exception:
                x, y = 0.0, 0.0
            hits.append(_TextHit(raw, label, x, y, _text_height(entity)))
            continue
        if kind != "INSERT":
            continue
        try:
            attribs = list(entity.attribs)
        except Exception:
            attribs = []
        for attrib in attribs:
            raw = str(getattr(attrib.dxf, "text", "") or "")
            label = parse_floor_label(raw)
            if not label:
                continue
            height = float(getattr(attrib.dxf, "height", 0) or 0)
            hits.append(_TextHit(raw, label, 0.0, 0.0, height))
    return hits


def _companion_window(doc, window: tuple[float, float, float, float]):
    """뷰포트와 같은 x 구간에서 아래로 떨어진 선 군집의 범위.

    건축 밑그림과 설비 평면이 모델스페이스에 위아래로 놓인 경우다.
    뷰포트 안에 선이 더 많으면 군집을 만들지 않는다.
    """
    x0, y0, x1, y1 = window
    pad_x = max(5_000.0, (x1 - x0) * 0.15)
    xa, xb = x0 - pad_x, x1 + pad_x
    y_bins: dict[int, int] = defaultdict(int)
    x_of_y: dict[int, dict[int, int]] = defaultdict(lambda: defaultdict(int))
    inside = 0
    for entity in doc.modelspace().query("LINE"):
        mx = (float(entity.dxf.start.x) + float(entity.dxf.end.x)) / 2.0
        my = (float(entity.dxf.start.y) + float(entity.dxf.end.y)) / 2.0
        if mx < xa or mx > xb:
            continue
        if x0 <= mx <= x1 and y0 <= my <= y1:
            inside += 1
        yk = math.floor(my / _BAND_MM)
        xk = math.floor(mx / _BAND_MM)
        y_bins[yk] += 1
        x_of_y[yk][xk] += 1

    def near(yk: int) -> bool:
        low = yk * _BAND_MM
        high = low + _BAND_MM
        return high > y0 - _COMPANION_GAP_MM and low < y1 + _COMPANION_GAP_MM

    keys = sorted(k for k, count in y_bins.items() if count >= _BAND_MIN_LINES and not near(k))
    if not keys:
        return None
    groups: list[list[int]] = [[keys[0]]]
    for key in keys[1:]:
        if key == groups[-1][-1] + 1:
            groups[-1].append(key)
        else:
            groups.append([key])
    best = max(groups, key=lambda group: sum(y_bins[key] for key in group))
    total = sum(y_bins[key] for key in best)
    if total < _COMPANION_MIN_LINES or total < inside:
        return None
    xs: dict[int, int] = defaultdict(int)
    for key in best:
        for xk, count in x_of_y[key].items():
            xs[xk] += count
    x_keys = [key for key, count in xs.items() if count >= _BAND_MIN_LINES]
    if not x_keys:
        return None
    box = (
        min(x_keys) * _BAND_MM,
        min(best) * _BAND_MM,
        (max(x_keys) + 1) * _BAND_MM,
        (max(best) + 1) * _BAND_MM,
    )
    if not _accept_size(box[2] - box[0], box[3] - box[1]):
        return None
    if _iou(box, window) >= 0.2:
        return None
    return box


def _dedupe_frames(frames: list[SheetFrame]) -> list[SheetFrame]:
    unique: list[SheetFrame] = []
    for frame in frames:
        if any(_iou(frame.bbox, prev.bbox) >= 0.5 for prev in unique):
            continue
        unique.append(frame)
    return unique


def discover_paperspace_floors(doc) -> LayoutDiscovery:
    """모델스페이스 도곽이 없을 때 종이공간 제목과 뷰포트로 층을 나눈다."""
    warnings: list[str] = []
    frames: list[SheetFrame] = []
    companion_note = False
    for layout in doc.layouts:
        if not layout.is_any_paperspace:
            continue
        hits = _paperspace_titles(layout)
        floors = {hit.floor for hit in hits}
        if len(floors) != 1:
            if len(floors) > 1:
                warnings.append(
                    f"종이공간 {layout.name!r}에 서로 다른 층 제목이 있어 그 레이아웃은 빼니다."
                )
            continue
        best = max(hits, key=_title_score)
        windows = []
        for viewport in layout.query("VIEWPORT"):
            box = _viewport_model_bbox(viewport)
            if box:
                windows.append(box)
        windows = _dedupe_rects(windows)
        if not windows:
            warnings.append(
                f"종이공간 {layout.name!r}의 제목 {best.text!r}에 켜진 평면 뷰포트가 없습니다."
            )
            continue
        title = plain_text(best.text)
        for box in windows:
            frames.append(SheetFrame(best.floor, title, box, best.height))
            companion = _companion_window(doc, box)
            if companion is None:
                continue
            frames.append(SheetFrame(best.floor, title, companion, best.height))
            companion_note = True
    if companion_note:
        warnings.append(
            "뷰포트 밖에 선이 몰린 구간이 있어 같은 층을 둘로 나눕니다. 아래·왼쪽이 먼저입니다."
        )
    frames = _dedupe_frames(frames)
    named = _assign_sheet_ids(frames, warnings) if frames else []
    if not named:
        warnings.append("종이공간 제목과 뷰포트로 구분할 층을 찾지 못했습니다.")
    return LayoutDiscovery(
        method="paperspace",
        floors=[frame.floor for frame in named],
        sheets=named,
        warnings=warnings,
    )


def discover_block_floors(doc) -> list[str]:
    found: set[str] = set()
    for entity in doc.modelspace().query("INSERT"):
        matched = re.search(r"XA-S-(\d+)F\s*평면$", entity.dxf.name)
        if matched:
            found.add(f"{matched.group(1)}F")
    return sorted(found, key=floor_sort_key)


def parse_floor_from_block_name(name: str) -> str | None:
    """'PL250224-클럽-B1' → B1F, 'PL260224-클럽-1' → 1F."""
    raw = str(name or "").strip()
    if not raw or _SHEET_INSERT_NAME.search(raw):
        return None
    matched = _INSERT_FLOOR_B.search(raw)
    if matched:
        return f"B{int(matched.group(1))}F"
    matched = _INSERT_FLOOR_N.search(raw)
    if matched:
        return f"{int(matched.group(1))}F"
    return None


def _ezbbox_module():
    try:
        from ezdxf import bbox as ezbbox
    except Exception:
        return None
    return ezbbox


def _insert_extents_bbox(entity, cache) -> tuple[float, float, float, float] | None:
    ezbbox = _ezbbox_module()
    if ezbbox is None:
        return None
    try:
        ext = ezbbox.extents([entity], cache=cache)
    except Exception:
        return None
    if not ext.has_data:
        return None
    return (
        float(ext.extmin.x),
        float(ext.extmin.y),
        float(ext.extmax.x),
        float(ext.extmax.y),
    )


def _percentile_trim_range(values: list[float], trim: float) -> tuple[float, float] | None:
    if len(values) < 20:
        return None
    ordered = sorted(values)
    lo = int(len(ordered) * trim)
    hi = max(lo + 1, int(len(ordered) * (1.0 - trim)))
    hi = min(hi, len(ordered))
    clipped = ordered[lo:hi]
    if not clipped:
        return None
    return clipped[0], clipped[-1]


def _primary_insert_bbox(entity, cache) -> tuple[float, float, float, float] | None:
    """INSERT 기하의 이상치를 뺀 주 군집 bbox. 삽입점도 포함해 추출 clip에 잡히게 한다."""
    ezbbox = _ezbbox_module()
    if ezbbox is None:
        return None
    xs: list[float] = []
    ys: list[float] = []
    try:
        children = entity.virtual_entities()
    except Exception:
        return _insert_extents_bbox(entity, cache)
    for child in children:
        if child.dxftype() not in {"LINE", "LWPOLYLINE", "ARC", "CIRCLE", "POLYLINE"}:
            continue
        try:
            ext = ezbbox.extents([child], cache=cache)
        except Exception:
            continue
        if not ext.has_data:
            continue
        xs.append((float(ext.extmin.x) + float(ext.extmax.x)) / 2.0)
        ys.append((float(ext.extmin.y) + float(ext.extmax.y)) / 2.0)
        if len(xs) >= _INSERT_BBOX_SAMPLE:
            break
    if len(xs) < _MIN_INSERT_GEOM:
        return None
    xr = _percentile_trim_range(xs, _INSERT_BBOX_TRIM)
    yr = _percentile_trim_range(ys, _INSERT_BBOX_TRIM)
    if xr is None or yr is None:
        return None
    x0, x1 = xr
    y0, y1 = yr
    try:
        ix = float(entity.dxf.insert.x)
        iy = float(entity.dxf.insert.y)
    except Exception:
        ix = iy = None
    if ix is not None:
        x0, x1 = min(x0, ix), max(x1, ix)
        y0, y1 = min(y0, iy), max(y1, iy)
    pad = max(500.0, 0.02 * max(x1 - x0, y1 - y0))
    box = (x0 - pad, y0 - pad, x1 + pad, y1 + pad)
    if not _accept_size(box[2] - box[0], box[3] - box[1]):
        return None
    return box


def _collect_sheet_insert_rects(doc) -> list[tuple[float, float, float, float]]:
    """시트 틀 블록(ooa-sheet 등) INSERT의 전역 bbox를 도곽으로 모은다."""
    ezbbox = _ezbbox_module()
    if ezbbox is None:
        return []
    cache = ezbbox.Cache()
    rects: list[tuple[float, float, float, float]] = []
    for entity in doc.modelspace().query("INSERT"):
        name = str(getattr(entity.dxf, "name", "") or "")
        if not _SHEET_INSERT_NAME.search(name):
            continue
        box = _insert_extents_bbox(entity, cache)
        if box is None:
            continue
        if _accept_size(box[2] - box[0], box[3] - box[1]):
            rects.append(box)
    return rects


def _nearest_floor_hit(
    texts: list[_TextHit], x: float, y: float, *, max_dist: float
) -> _TextHit | None:
    best: _TextHit | None = None
    best_dist = max_dist
    for hit in texts:
        dist = math.hypot(hit.x - x, hit.y - y)
        if dist < best_dist:
            best = hit
            best_dist = dist
    return best


def _building_insert_frames(
    doc,
    texts: list[_TextHit],
    existing: list[SheetFrame],
    warnings: list[str],
) -> list[SheetFrame]:
    """건물 평면이 최상위 INSERT에만 있을 때 그 기하 bbox로 층을 추가한다.

    이미 도곽으로 덮인 INSERT는 건너뛴다. 층 이름은 블록명 접미(-B1, -1) 또는
    가장 가까운 층 제목이다.
    """
    ezbbox = _ezbbox_module()
    if ezbbox is None:
        return []
    cache = ezbbox.Cache()
    added: list[SheetFrame] = []
    for entity in doc.modelspace().query("INSERT"):
        name = str(getattr(entity.dxf, "name", "") or "")
        if not _BUILDING_INSERT_NAME.search(name):
            continue
        if _SHEET_INSERT_NAME.search(name):
            continue
        if re.search(r"XA-S-\d+F\s*평면$", name):
            continue
        box = _primary_insert_bbox(entity, cache)
        if box is None:
            continue
        if any(_iou(box, frame.bbox) >= 0.2 or _contains(frame.bbox, box) for frame in existing):
            continue
        if any(_iou(box, frame.bbox) >= 0.2 or _contains(frame.bbox, box) for frame in added):
            continue
        floor = parse_floor_from_block_name(name)
        title = name
        height = 0.0
        cx = (box[0] + box[2]) / 2.0
        cy = (box[1] + box[3]) / 2.0
        span = max(box[2] - box[0], box[3] - box[1])
        near = _nearest_floor_hit(texts, cx, cy, max_dist=max(40_000.0, span * 1.5))
        if near is not None:
            if floor is None:
                floor = near.floor
            title = plain_text(near.text)
            height = near.height
        if floor is None:
            continue
        added.append(SheetFrame(floor, title, box, height))
    if added:
        warnings.append(
            "건물 평면 INSERT의 전역 bbox로 층을 추가했습니다. "
            + ", ".join(frame.floor for frame in added)
            + " 입니다."
        )
    return added


def _point_in_bbox(bbox, x: float, y: float, pad: float = 0.0) -> bool:
    return bbox[0] - pad <= x <= bbox[2] + pad and bbox[1] - pad <= y <= bbox[3] + pad


def _frame_geometry_count(doc, bbox: tuple[float, float, float, float]) -> int:
    """도곽 clip 안에 잡힐 modelspace 기하·INSERT 개수(대략)."""
    count = 0
    for entity in doc.modelspace():
        kind = entity.dxftype()
        if kind == "INSERT":
            try:
                ix = float(entity.dxf.insert.x)
                iy = float(entity.dxf.insert.y)
            except Exception:
                continue
            if _point_in_bbox(bbox, ix, iy):
                count += _MIN_FRAME_GEOM
            continue
        if kind not in {"LINE", "LWPOLYLINE", "ARC", "CIRCLE", "POLYLINE", "TEXT", "MTEXT"}:
            continue
        try:
            if kind == "LINE":
                mx = (float(entity.dxf.start.x) + float(entity.dxf.end.x)) / 2.0
                my = (float(entity.dxf.start.y) + float(entity.dxf.end.y)) / 2.0
            else:
                mx = float(entity.dxf.insert.x)
                my = float(entity.dxf.insert.y)
        except Exception:
            continue
        if _point_in_bbox(bbox, mx, my):
            count += 1
            if count >= _MIN_FRAME_GEOM * 4:
                break
    return count


def _drop_empty_frames(
    doc, frames: list[SheetFrame], warnings: list[str]
) -> list[SheetFrame]:
    """기하·INSERT가 거의 없는 도곽(복제 제목 칸 등)을 뺀다.

    기하가 있는 도곽이 하나라도 있으면 빈 칸만 제외한다.
    전부 비어 있으면(시설 평면도·PDF 임포트 등) 도곽은 그대로 둔다.
    """
    if not frames:
        return frames
    kept: list[SheetFrame] = []
    dropped: list[str] = []
    for frame in frames:
        if _frame_geometry_count(doc, frame.bbox) >= _MIN_FRAME_GEOM:
            kept.append(frame)
        else:
            dropped.append(frame.floor)
    if dropped and kept:
        warnings.append(
            "도곽 안에 기하가 없어 제외한 층: " + ", ".join(dropped) + " 입니다."
        )
        return kept
    return frames if not kept else kept


def _base_floor_token(floor: str) -> str:
    """'1F_2' → '1F'. 복제 접미만 벗긴다. 'sheet_01' 은 그대로 둔다."""
    matched = re.fullmatch(r"(.+)_(\d+)$", floor)
    if not matched:
        return floor
    base = matched.group(1)
    if base == "sheet" or base.startswith("sheet_"):
        return floor
    try:
        normalize_floor_token(base)
    except ValueError:
        return floor
    return base


def _is_substantial_sheet_child(
    child: tuple[float, float, float, float],
    parent: tuple[float, float, float, float],
) -> bool:
    """부모 격자 안의 자식이 실제 시트인지(복제 제목 칸이 아닌지) 본다."""
    if _area(child) >= _area(parent) * _SHEET_CHILD_AREA_RATIO:
        return True
    return min(child[2] - child[0], child[3] - child[1]) >= _SHEET_MIN_EDGE_MM


def _prefer_largest_same_floor(frames: list[SheetFrame]) -> list[SheetFrame]:
    """같은 층이 겹치면 실제 시트 도곽을 남긴다.

    복제 제목용 작은 칸은 버리고 시트를 쓴다.
    격자 부모 도곽 안의 실제 층 시트는 부모 대신 자식을 남긴다.
    겹치지 않는 같은 층(서로 다른 동)은 그대로 둔다.
    """
    if len(frames) < 2:
        return frames
    ordered = sorted(frames, key=lambda frame: _area(frame.bbox), reverse=True)
    kept: list[SheetFrame] = []
    for frame in ordered:
        base = _base_floor_token(frame.floor)
        dominated = False
        replace: list[int] = []
        for index, prev in enumerate(kept):
            if _base_floor_token(prev.floor) != base:
                continue
            nested = _contains(prev.bbox, frame.bbox) or _contains(frame.bbox, prev.bbox)
            if not nested and _iou(prev.bbox, frame.bbox) < 0.15:
                continue
            if _contains(prev.bbox, frame.bbox):
                if _is_substantial_sheet_child(frame.bbox, prev.bbox):
                    replace.append(index)
                else:
                    dominated = True
                continue
            if _contains(frame.bbox, prev.bbox):
                if _is_substantial_sheet_child(prev.bbox, frame.bbox):
                    dominated = True
                else:
                    replace.append(index)
                continue
            # 겹치기만 하는 경우: 짧은 변이 50 m 이상인 쪽(실제 시트)을 남긴다.
            frame_edge = min(frame.bbox[2] - frame.bbox[0], frame.bbox[3] - frame.bbox[1])
            prev_edge = min(prev.bbox[2] - prev.bbox[0], prev.bbox[3] - prev.bbox[1])
            if frame_edge >= _SHEET_MIN_EDGE_MM and (
                prev_edge < _SHEET_MIN_EDGE_MM or _area(frame.bbox) < _area(prev.bbox)
            ):
                replace.append(index)
                continue
            dominated = True
        if dominated:
            continue
        for index in sorted(replace, reverse=True):
            kept.pop(index)
        kept.append(frame)
    return kept


def _rebase_floor_frames(frames: list[SheetFrame]) -> list[SheetFrame]:
    """접미가 붙은 층 이름을 다시 매기기 위해 base 토큰으로 되돌린다."""
    return [
        SheetFrame(_base_floor_token(frame.floor), frame.title, frame.bbox, frame.title_height)
        for frame in frames
    ]


def _named_floor_count(frames: list[SheetFrame]) -> int:
    """sheet_XX·미확정이 아닌 층 제목이 붙은 도곽 수."""
    return sum(1 for frame in frames if not str(frame.floor).startswith("sheet"))


def _median_min_edge_mm(frames: list[SheetFrame]) -> float:
    if not frames:
        return 0.0
    edges = sorted(
        min(frame.bbox[2] - frame.bbox[0], frame.bbox[3] - frame.bbox[1]) for frame in frames
    )
    return edges[len(edges) // 2]


def _prefer_construction_over_frames(
    frames: list[SheetFrame], alt: list[SheetFrame]
) -> bool:
    """Defpoints 등 보조선 도곽이 일반 도곽보다 나으면 True.

    방·구역 조각(제목 없는 sheet_XX 다수)보다, 층 제목이 붙은 실제 시트
    (짧은 변 50 m 이상)를 우선한다. 제목 수가 같으면 개수가 더 많은 쪽.
    """
    if not alt:
        return False
    if not frames:
        return True
    named_alt = _named_floor_count(alt)
    named_cur = _named_floor_count(frames)
    if named_alt != named_cur:
        return named_alt > named_cur
    edge_alt = _median_min_edge_mm(alt)
    edge_cur = _median_min_edge_mm(frames)
    if named_alt > 0 and edge_alt >= _SHEET_MIN_EDGE_MM and edge_cur < _SHEET_MIN_EDGE_MM:
        return True
    if named_cur == 0 and edge_alt > edge_cur * 2.0 and edge_alt >= _SHEET_MIN_EDGE_MM:
        return True
    return len(alt) > len(frames)


# INSERT 블록명에 붙은 층 평면 접미. 제목 TEXT가 없는 지하 시트 등에 쓴다.
_INSERT_FLOOR_PLAN = (
    (re.compile(r"BASEMENT\s+FLOOR\s+PLAN", re.I), "B1F"),
    (re.compile(r"1ST_?\s*FLOOR\s+PLAN", re.I), "1F"),
    (re.compile(r"2ND\s+FLOOR\s+PLAN", re.I), "2F"),
    (re.compile(r"3RD\s+FLOOR\s+PLAN", re.I), "3F"),
    (re.compile(r"4TH\s+FLOOR\s+PLAN", re.I), "4F"),
    (re.compile(r"5TH\s+FLOOR\s+PLAN", re.I), "5F"),
    (re.compile(r"ROOF\s+FLOOR\s+PLAN", re.I), "RF"),
)
_ROOM_CODE_FLOOR = re.compile(r"\bB(\d+)\.\d", re.I)


def _infer_floor_from_clip(doc, bbox: tuple[float, float, float, float]) -> tuple[str, str] | None:
    """도곽 안 INSERT·실명 코드로 층을 추론한다. ('B1F', 'BASEMENT…') 또는 None."""
    votes: dict[str, int] = defaultdict(int)
    evidence: dict[str, str] = {}
    x0, y0, x1, y1 = bbox
    for insert in doc.modelspace().query("INSERT"):
        try:
            ix = float(insert.dxf.insert.x)
            iy = float(insert.dxf.insert.y)
        except Exception:  # noqa: BLE001
            continue
        if not (x0 <= ix <= x1 and y0 <= iy <= y1):
            continue
        name = str(insert.dxf.name or "")
        for pattern, floor in _INSERT_FLOOR_PLAN:
            if pattern.search(name):
                votes[floor] += 1
                evidence.setdefault(floor, name[:80])
                break
    if votes:
        floor = max(votes.items(), key=lambda item: item[1])[0]
        return floor, evidence.get(floor, floor)

    for entity in doc.modelspace():
        if entity.dxftype() not in {"TEXT", "MTEXT"}:
            continue
        try:
            ix = float(entity.dxf.insert.x)
            iy = float(entity.dxf.insert.y)
        except Exception:  # noqa: BLE001
            continue
        if not (x0 <= ix <= x1 and y0 <= iy <= y1):
            continue
        raw = plain_text(_entity_text(entity))
        matched = _ROOM_CODE_FLOOR.search(raw)
        if matched:
            floor = f"B{int(matched.group(1))}F"
            return floor, raw
    return None


def _claim_untitled_large_frames(
    doc,
    frames: list[SheetFrame],
    rects: list[tuple[float, float, float, float]],
    warnings: list[str],
) -> list[SheetFrame]:
    """제목 없는 큰 도곽을 INSERT 층 접미로 붙인다.

    067처럼 지하 시트에 '지하층 … 평면도' TEXT가 없고 Defpoints 도곽만 있을 때
    BASEMENT FLOOR PLAN INSERT로 B1F를 채운다. 이미 제목으로 잡힌 층은 건드리지 않는다.
    """
    if not rects:
        return frames
    taken_floors = {_base_floor_token(frame.floor) for frame in frames}
    candidates: list[SheetFrame] = []
    for rect in rects:
        edge = min(rect[2] - rect[0], rect[3] - rect[1])
        if edge < _SHEET_MIN_EDGE_MM:
            continue
        if any(_sheets_overlap(rect, frame.bbox) for frame in frames):
            continue
        inferred = _infer_floor_from_clip(doc, rect)
        if inferred is None:
            continue
        floor, evidence = inferred
        if floor in taken_floors:
            continue
        if _frame_geometry_count(doc, rect) < _MIN_FRAME_GEOM:
            continue
        candidates.append(SheetFrame(floor, f"{floor} (INSERT: {evidence})", rect, 0.0))
    if not candidates:
        return frames
    # 바깥·안쪽 Defpoints가 같이 잡히면 실시트(안쪽)만 남긴다.
    claimed = [
        frame
        for frame in _prefer_largest_same_floor(candidates)
        if _base_floor_token(frame.floor) not in taken_floors
    ]
    if claimed:
        warnings.append(
            "제목 없는 도곽을 INSERT 층 접미로 보완했습니다. "
            + ", ".join(frame.floor for frame in claimed)
            + " 입니다."
        )
    return frames + claimed


def discover_sheet_floors(doc) -> LayoutDiscovery:
    rects, lines, texts, plans, numbers, truncated = _collect_primitives(doc)
    warnings: list[str] = []
    notes = _repeated_floor_notes(texts)
    if notes:
        texts = [hit for hit in texts if plain_text(hit.text) not in notes]
        warnings.append(
            "같은 층 코드가 여러 번 나와 도면 제목에서 빼니다. "
            + ", ".join(sorted(notes))
            + " 입니다."
        )
    if truncated:
        warnings.append("긴 선이 많아 일부 선분 도곽은 건너뛰고 폴리라인 테두리를 우선합니다.")
    rects.extend(_rects_from_lines(lines))
    sheet_inserts = _collect_sheet_insert_rects(doc)
    if sheet_inserts:
        rects.extend(sheet_inserts)
        warnings.append(
            f"시트 블록 INSERT {len(sheet_inserts)}곳을 도곽으로 사용했습니다."
        )
    frames = _frames_from_sheet_rects(rects, texts, plans, numbers, warnings)
    # Defpoints LINE은 쓰지 않지만, 닫힌 폴리라인 도곽은 후보로 둔다.
    # 일반 도곽이 없거나(도면틀 끊김), 방·구역 조각만 잡히거나, 층 제목이 더 적게
    # 붙으면 Defpoints 닫힌 도곽을 쓴다. 개수만 비교하지 않는다.
    construction = _collect_construction_poly_rects(doc)
    if construction:
        alt_notes: list[str] = []
        alt = _frames_from_sheet_rects(construction, texts, plans, numbers, alt_notes)
        if _prefer_construction_over_frames(frames, alt):
            # 버린 일반 도곽의 sheet_XX·미확정 경고는 남기지 않는다.
            warnings[:] = [
                note
                for note in warnings
                if "도곽만으로 나눈" not in note
                and "층 이름을 확정하지 못한 도곽" not in note
            ]
            if frames:
                warnings.append(
                    "일반 도곽이 층·크기를 제대로 못 잡아 Defpoints 등 보조선 레이어의 "
                    "닫힌 도곽으로 층을 다시 나눴습니다."
                )
            else:
                warnings.append(
                    "일반 도곽이 없어 Defpoints 등 보조선 레이어의 닫힌 도곽을 사용했습니다."
                )
            frames = alt
            warnings.extend(alt_notes)
            frames = _claim_untitled_large_frames(doc, frames, construction, warnings)
        elif _named_floor_count(frames) > 0:
            # 제목 있는 층은 있는데 지하처럼 제목 없는 Defpoints만 남은 경우
            frames = _claim_untitled_large_frames(doc, frames, construction, warnings)
    frames = _prefer_largest_same_floor(frames)
    building = _building_insert_frames(doc, texts, frames, warnings)
    if building:
        frames = _prefer_largest_same_floor(_rebase_floor_frames(frames) + building)
    frames = _drop_empty_frames(doc, frames, warnings)
    # 큰 시트만 남기거나 빈 칸을 뺀 뒤 접미(1F_2)를 다시 매긴다.
    renumber: list[str] = []
    frames = _assign_sheet_ids(_rebase_floor_frames(frames), renumber)
    warnings[:] = [
        note
        for note in warnings
        if "도곽이" not in note or "왼쪽부터" not in note
    ]
    warnings.extend(renumber)
    if not frames:
        warnings.append("도곽과 층 제목으로 구분할 층을 찾지 못했습니다.")
    return LayoutDiscovery(
        method="sheet",
        floors=[f.floor for f in frames],
        sheets=frames,
        warnings=warnings,
    )


def discover_layout(doc, *, mode: str = "auto") -> LayoutDiscovery:
    """mode: auto | block | sheet.

    auto는 XA-S-{N}F 평면 블록, 모델스페이스 도곽·층 제목(시트·건물 INSERT 포함),
    종이공간 제목·뷰포트 순으로 층을 나눈다.
    """
    if mode not in {"auto", "block", "sheet"}:
        raise ValueError(f"layout 오류: {mode!r}")
    blocks = [] if mode == "sheet" else discover_block_floors(doc)
    if blocks and mode != "sheet":
        return LayoutDiscovery(method="block", floors=blocks)
    if mode == "block":
        return LayoutDiscovery(
            method="block",
            floors=[],
            warnings=["XA-S-{N}F 평면 블록이 없습니다."],
        )
    sheet = discover_sheet_floors(doc)
    if sheet.floors:
        if mode == "auto":
            sheet.warnings.insert(0, "XA-S-{N}F 평면 블록이 없어 도곽과 층 제목으로 층을 구분했습니다.")
        return sheet
    if mode == "auto":
        paperspace = discover_paperspace_floors(doc)
        if paperspace.floors:
            paperspace.warnings.insert(
                0,
                "XA-S-{N}F 평면 블록과 모델스페이스 도곽이 없어 종이공간 제목과 뷰포트를 사용했습니다.",
            )
            return paperspace
    if mode == "auto":
        sheet.warnings.insert(0, "XA-S-{N}F 평면 블록이 없어 도곽과 층 제목으로 층을 구분했습니다.")
    return sheet
