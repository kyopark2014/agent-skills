#!/usr/bin/env python3
"""도곽(도면 테두리)과 층 제목으로 층을 구분한다.

XA-S-{N}F 평면 블록이 없는 도면용 폴백.
modelspace와 얕은 INSERT에서 큰 축정렬 사각형과
'1층 평면도', 'B1F', '지하1층' 같은 제목을 짝짓는다.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field

MIN_EDGE_MM = 10_000.0
MAX_EDGE_MM = 500_000.0
MAX_ASPECT = 8.0
AXIS_TOL_MM = 20.0
MAX_STORED_LINES = 20_000


def normalize_floor_token(floor: str) -> str:
    """'5', '5F', 'b1', '1F_2' → '5F' / 'B1F' / '1F_2' / 'RF' / 'PH'."""
    s = floor.strip().upper().replace(" ", "")
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
    if s in {"RF", "ROOF"}:
        return "RF"
    if s in {"PH", "PENTHOUSE"}:
        return "PH"
    raise ValueError(f"층 형식 오류: {floor!r} (예: 5F, B1F, 1F_2, RF)")


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
    if floor == "RF":
        return (3, 0, 0, floor)
    if floor == "PH":
        return (4, 0, 0, floor)
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
_PLAN_TAIL = r"(?:평면도|평면|PLAN|FLOOR)"


def _token_from_match(matched: re.Match[str]) -> str | None:
    if matched.group("b"):
        return f"B{int(matched.group('b'))}F"
    if matched.group("bf"):
        num = re.search(r"\d+", matched.group("bf"))
        return f"B{int(num.group(0))}F" if num else None
    if matched.group("n"):
        return f"{int(matched.group('n'))}F"
    return None


def parse_floor_label(value: str) -> str | None:
    """짧은 층 제목만 인정한다. 치수·실명·참조 문구는 제외.

    '1층 평면도'뿐 아니라 '1층 냉난방 평면도'처럼
    층과 평면도 사이에 용도가 있는 제목도 인정한다.
    """
    raw = plain_text(value)
    if not raw or len(raw) > 48:
        return None
    if re.fullmatch(r"[\d.,\s]+", raw):
        return None
    if len(set(re.findall(r"(\d+)\s*층", raw))) > 1:
        return None
    if re.fullmatch(rf"(?:옥상|지붕층|지붕|ROOF|RF)(?:\s+{_PLAN_TAIL})?", raw, re.I):
        return "RF"
    if re.fullmatch(rf"(?:옥상|지붕층|지붕|ROOF|RF)(?:\s+\S+){{1,4}}\s+{_PLAN_TAIL}", raw, re.I):
        return "RF"
    if re.fullmatch(rf"(?:옥탑층|옥탑|PH|PENTHOUSE)(?:\s+{_PLAN_TAIL})?", raw, re.I):
        return "PH"
    if re.fullmatch(rf"(?:옥탑층|옥탑|PH|PENTHOUSE)(?:\s+\S+){{1,4}}\s+{_PLAN_TAIL}", raw, re.I):
        return "PH"
    strict = re.fullmatch(rf"{_FLOOR_HEAD}(?:\s*{_PLAN_TAIL})?$", raw, re.I)
    if strict:
        return _token_from_match(strict)
    titled = re.fullmatch(rf"{_FLOOR_HEAD}(?:\s+\S+){{1,4}}\s*{_PLAN_TAIL}$", raw, re.I)
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


def _dedupe_rects(rects: list[tuple[float, float, float, float]]):
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
        children = 0
        for j, inner in enumerate(kept):
            if i == j:
                continue
            if _contains(outer, inner) and _area(inner) < _area(outer) * 0.85:
                children += 1
        if children >= 2:
            parents.add(i)
    return [rect for i, rect in enumerate(kept) if i not in parents]


def _inside(rect, x: float, y: float, pad: float = 0.0) -> bool:
    return rect[0] - pad <= x <= rect[2] + pad and rect[1] - pad <= y <= rect[3] + pad


def _title_score(hit: _TextHit) -> float:
    bonus = 3.0 if re.search(r"평면|PLAN|FLOOR", hit.text, re.I) else 1.0
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
    for rect in rects:
        hits = [t for t in texts if _inside(rect, t.x, t.y)]
        if not hits:
            pad = min(rect[2] - rect[0], rect[3] - rect[1]) * 0.08
            hits = [t for t in texts if _inside(rect, t.x, t.y, pad) and not any(
                _inside(other, t.x, t.y) for other in rects if other != rect
            )]
        if not hits:
            continue
        ranked = sorted(hits, key=_title_score, reverse=True)
        best = ranked[0]
        rivals = [h for h in ranked[1:] if h.floor != best.floor and _title_score(h) >= _title_score(best) * 0.67]
        if rivals:
            split = _split_multi(rect, hits)
            if not split:
                warnings.append(
                    f"도곽 ({rect[0]:.0f},{rect[1]:.0f})-({rect[2]:.0f},{rect[3]:.0f}) 안에 "
                    f"층 제목이 여럿입니다: {sorted({h.floor for h in ranked})}"
                )
                continue
            for hit, sub in split:
                frames.append(SheetFrame(hit.floor, hit.text, sub, hit.height))
            continue
        frames.append(SheetFrame(best.floor, best.text, rect, best.height))

    return _assign_sheet_ids(frames, warnings)


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
        best = max(
            group,
            key=lambda frame: (
                _title_score(_TextHit(frame.title, frame.floor, 0, 0, frame.title_height)),
                _area(frame.bbox),
            ),
        )
        picked.append(best)
    picked.sort(key=lambda frame: (frame.bbox[0], frame.bbox[1]))

    counts: dict[str, int] = {}
    for frame in picked:
        counts[frame.floor] = counts.get(frame.floor, 0) + 1
    seq: dict[str, int] = {}
    out: list[SheetFrame] = []
    for frame in picked:
        seq[frame.floor] = seq.get(frame.floor, 0) + 1
        token = frame.floor if seq[frame.floor] == 1 else f"{frame.floor}_{seq[frame.floor]}"
        if counts[frame.floor] > 1 and seq[frame.floor] == 2:
            names = [frame.floor] + [f"{frame.floor}_{i}" for i in range(2, counts[frame.floor] + 1)]
            warnings.append(
                f"제목 {frame.title!r} 도곽이 {counts[frame.floor]}곳입니다. "
                f"왼쪽부터 {', '.join(names)} 입니다."
            )
        out.append(SheetFrame(token, frame.title, frame.bbox, frame.title_height))
    return out


def _collect_primitives(doc):
    rects: list[tuple[float, float, float, float]] = []
    lines: list[_Seg] = []
    texts: list[_TextHit] = []
    truncated = False

    def take(entity) -> str | None:
        nonlocal truncated
        kind = entity.dxftype()
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
            label = parse_floor_label(_entity_text(entity))
            if not label:
                return None
            try:
                x, y = float(entity.dxf.insert.x), float(entity.dxf.insert.y)
            except Exception:
                return None
            texts.append(_TextHit(_entity_text(entity), label, x, y, _text_height(entity)))
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
    return rects, lines, texts, truncated


def discover_block_floors(doc) -> list[str]:
    found: set[str] = set()
    for entity in doc.modelspace().query("INSERT"):
        matched = re.search(r"XA-S-(\d+)F\s*평면$", entity.dxf.name)
        if matched:
            found.add(f"{matched.group(1)}F")
    return sorted(found, key=floor_sort_key)


def discover_sheet_floors(doc) -> LayoutDiscovery:
    rects, lines, texts, truncated = _collect_primitives(doc)
    warnings: list[str] = []
    if truncated:
        warnings.append("긴 선이 많아 일부 선분 도곽은 건너뛰고 폴리라인 테두리를 우선합니다.")
    rects.extend(_rects_from_lines(lines))
    rects = _dedupe_rects(rects)
    frames = _frames_from(rects, texts, warnings)
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

    auto는 XA-S-{N}F 평면 블록이 있으면 그 방식을 쓰고,
    없으면 도곽·층 제목으로 넘긴다.
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
    if mode == "auto":
        sheet.warnings.insert(0, "XA-S-{N}F 평면 블록이 없어 도곽과 층 제목으로 층을 구분했습니다.")
    return sheet
