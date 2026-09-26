#!/usr/bin/env python3
"""
DXF 치수·고해상도 렌더 라이브러리 (+ 선택적 CLI).

`extract_2d.py` / (레거시) `split_floor.py`가 아래를 import 한다:
  · build_dim_plan / render_hires_png / write_clean_dxf / render_floor_original_preview

CLI는 클린 DXF(또는 원본)에서 bbox 크롭·치수·고해상도 PNG를 뽑을 때 사용.
층은 `--floor`로 지정 (특정 층 전용 아님).

좌표 기준:
  · --from-clean: 클린 DXF 로컬 좌표
  · --from-raw: 월드 좌표 → 필터 후 로컬로 재이동

Usage:
  python lib_render.py \\
    --floor 6F --from-clean $ARTIFACTS_DIR/floor_6F_clean.dxf \\
    --auto-primary --out $ARTIFACTS_DIR/6f_hires --px-width 14000

  python lib_render.py --floor 12F --from-raw --raw-dxf $ARTIFACTS_DIR/input.dxf \\
    --auto-primary --out $ARTIFACTS_DIR/12f_hires --px-width 12000
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
from collections import Counter
from pathlib import Path

import ezdxf
from ezdxf.document import Drawing
from ezdxf.entities import DXFEntity, Insert
from ezdxf.enums import TextEntityAlignment

SCRIPTS_DIR = Path(__file__).resolve().parent
ROOT = SCRIPTS_DIR  # 하위 호환 alias
def resolve_artifacts_dir() -> Path:
    env = os.environ.get("ARTIFACTS_DIR") or os.environ.get("ARTIFACT_DIR")
    return Path(env) if env else Path.cwd()


def floor_token(floor: str) -> str:
    """'5', '5F', '5f' → '5F'."""
    s = floor.strip().upper().replace(" ", "")
    m = re.fullmatch(r"(\d+)F?", s)
    if not m:
        raise ValueError(f"층 형식 오류: {floor!r} (예: 6F)")
    return f"{m.group(1)}F"


FURNITURE_KEYWORDS = (
    "가구", "chair", "피트니스", "화)", "DOOR", "DOR_", "도어", "자동문",
    "락커", "라커", "샤워", "신발", "러닝", "파우더", "큐비클", "대변기",
    "소변", "객석", "좌석", "모바일", "회의", "업다운", "절취", "RAIN",
    "rain", "입면", "슬라이딩", "접견",
)

GEOM_TYPES = frozenset(
    {"LINE", "LWPOLYLINE", "POLYLINE", "ARC", "CIRCLE", "ELLIPSE", "SPLINE", "HATCH"}
)
TEXT_TYPES = frozenset({"TEXT", "MTEXT"})

# 레거시: 특정 조사본의 primary bbox (mm). --use-default-primary 전용.
DEFAULT_PRIMARY_BBOX = {
    "xmin": 333892.58339006884,
    "ymin": -1429.8399999999674,
    "xmax": 485202.58339000940,
    "ymax": 46080.16000000003,
}


def is_furniture(name: str) -> bool:
    lower = name.lower()
    return any(k.lower() in lower for k in FURNITURE_KEYWORDS)


def _xy(p) -> tuple[float, float]:
    return float(p[0]), float(p[1])


def find_floor_inserts(doc: Drawing, floor: str) -> list[Insert]:
    """해당 층의 평면/기둥/코어 INSERT."""
    n = floor_token(floor)[:-1]
    patterns = [
        re.compile(rf"^XA-S-{n}F\s*평면$"),
        re.compile(rf"^XA-S-{n}F\s*코어$"),
        re.compile(rf"^XS-S-{n}F\s*기둥$"),
    ]
    hits: list[Insert] = []
    for e in doc.modelspace().query("INSERT"):
        if any(p.search(e.dxf.name) for p in patterns):
            hits.append(e)
    return hits


def explode_insert(
    insert: Insert,
    *,
    skip_furniture: bool = True,
    max_depth: int = 12,
) -> list[DXFEntity]:
    out: list[DXFEntity] = []

    def walk(ins: Insert, depth: int) -> None:
        if depth > max_depth:
            return
        try:
            entities = list(ins.virtual_entities())
        except Exception as exc:  # noqa: BLE001
            print(f"  [warn] virtual_entities ({ins.dxf.name}): {exc}", file=sys.stderr)
            return
        for e in entities:
            if e.dxftype() == "INSERT":
                if skip_furniture and is_furniture(e.dxf.name):
                    continue
                walk(e, depth + 1)
            else:
                out.append(e)

    walk(insert, 0)
    return out


def entity_points(e: DXFEntity) -> list[tuple[float, float]]:
    pts: list[tuple[float, float]] = []
    t = e.dxftype()
    try:
        if t == "LINE":
            pts.extend([_xy(e.dxf.start), _xy(e.dxf.end)])
        elif t in {"CIRCLE", "ARC"}:
            c, r = _xy(e.dxf.center), float(e.dxf.radius)
            pts.extend([(c[0] - r, c[1] - r), (c[0] + r, c[1] + r), c])
        elif t == "LWPOLYLINE":
            pts.extend(_xy(p) for p in e.get_points("xy"))
        elif t in TEXT_TYPES:
            pts.append(_xy(e.dxf.insert))
        elif t == "ELLIPSE":
            pts.append(_xy(e.dxf.center))
    except Exception:  # noqa: BLE001
        return []
    return [(x, y) for x, y in pts if math.isfinite(x) and math.isfinite(y)]


def entity_centroid(e: DXFEntity) -> tuple[float, float] | None:
    pts = entity_points(e)
    if not pts:
        return None
    return sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts)


def filter_bbox(
    entities: list[DXFEntity],
    xmin: float,
    ymin: float,
    xmax: float,
    ymax: float,
    *,
    mode: str = "centroid",
) -> list[DXFEntity]:
    """mode=centroid: 중심점이 bbox 안. mode=overlap: 한 점이라도 bbox 안."""
    kept: list[DXFEntity] = []
    for e in entities:
        pts = entity_points(e)
        if not pts:
            continue
        if mode == "overlap":
            if any(xmin <= x <= xmax and ymin <= y <= ymax for x, y in pts):
                kept.append(e)
        else:
            cx = sum(p[0] for p in pts) / len(pts)
            cy = sum(p[1] for p in pts) / len(pts)
            if xmin <= cx <= xmax and ymin <= cy <= ymax:
                kept.append(e)
    return kept


def find_primary_cluster_bbox(entities: list[DXFEntity]) -> tuple[float, float, float, float]:
    """LINE 중심 X 히스토그램으로 가장 벽선이 많은 클러스터 bbox를 찾는다."""
    line_cx: list[float] = []
    line_cy: list[float] = []
    for e in entities:
        if e.dxftype() != "LINE":
            continue
        c = entity_centroid(e)
        if c:
            line_cx.append(c[0])
            line_cy.append(c[1])
    if len(line_cx) < 50:
        # fallback: 전체 centroid
        xs, ys = [], []
        for e in entities:
            c = entity_centroid(e)
            if c:
                xs.append(c[0])
                ys.append(c[1])
        if not xs:
            raise RuntimeError("primary cluster를 찾을 엔티티 없음")
        return min(xs), min(ys), max(xs), max(ys)

    xs = sorted(line_cx)
    # 가장 큰 X gap으로 클러스터 분리
    gaps = [(xs[i + 1] - xs[i], i) for i in range(len(xs) - 1)]
    gaps.sort(reverse=True)
    best_gap, best_i = gaps[0]
    if best_gap < 20_000:  # 20m 미만이면 단일 클러스터로 간주
        pad = 500.0
        return min(line_cx) - pad, min(line_cy) - pad, max(line_cx) + pad, max(line_cy) + pad

    split = (xs[best_i] + xs[best_i + 1]) / 2
    left = [x for x in line_cx if x < split]
    right = [x for x in line_cx if x >= split]
    # LINE이 더 많은 쪽
    use_right = len(right) >= len(left)
    chosen_x = right if use_right else left
    # 해당 X 범위의 모든 엔티티로 Y 확장
    xmin, xmax = min(chosen_x), max(chosen_x)
    ys: list[float] = []
    for e in entities:
        c = entity_centroid(e)
        if c and xmin <= c[0] <= xmax:
            ys.append(c[1])
    ymin, ymax = (min(ys), max(ys)) if ys else (min(line_cy), max(line_cy))
    pad = 1000.0
    side = "RIGHT" if use_right else "LEFT"
    print(
        f"  auto-primary: {side} cluster  "
        f"gap={best_gap:.1f}mm  LINEs L={len(left)} R={len(right)}"
    )
    return xmin - pad, ymin - pad, xmax + pad, ymax + pad


def load_entities_from_clean(dxf_path: Path) -> list[DXFEntity]:
    doc = ezdxf.readfile(str(dxf_path))
    return list(doc.modelspace())


def load_entities_from_raw(
    dxf_path: Path,
    floor: str,
    *,
    skip_furniture: bool,
) -> tuple[list[DXFEntity], list[str]]:
    print(f"loading raw {dxf_path} ...", flush=True)
    doc = ezdxf.readfile(str(dxf_path))
    inserts = find_floor_inserts(doc, floor)
    if not inserts:
        raise RuntimeError(f"{floor} INSERT(평면/기둥/코어) 없음")
    names = [i.dxf.name for i in inserts]
    ents: list[DXFEntity] = []
    for ins in inserts:
        part = explode_insert(ins, skip_furniture=skip_furniture)
        print(f"  exploded {ins.dxf.name}: {len(part)}")
        ents.extend(part)
    return ents, names


def write_clean_dxf(
    entities: list[DXFEntity],
    out_path: Path,
    *,
    include_text: bool,
    origin_shift: tuple[float, float],
    with_dims: bool = False,
    dim_plan: dict | None = None,
) -> Counter:
    doc = ezdxf.new("R2010")
    msp = doc.modelspace()
    # 치수 스타일
    if "EZDXF" not in doc.dimstyles:
        doc.dimstyles.new("EZDXF")
    counts: Counter = Counter()
    ox, oy = origin_shift

    def sh(p) -> tuple[float, float, float]:
        return float(p[0]) - ox, float(p[1]) - oy, float(p[2]) if len(p) > 2 else 0.0

    for e in entities:
        t = e.dxftype()
        if t not in GEOM_TYPES and not (include_text and t in TEXT_TYPES):
            continue
        try:
            if t == "LINE":
                msp.add_line(sh(e.dxf.start), sh(e.dxf.end), dxfattribs={"layer": e.dxf.layer})
            elif t == "CIRCLE":
                msp.add_circle(sh(e.dxf.center), e.dxf.radius, dxfattribs={"layer": e.dxf.layer})
            elif t == "ARC":
                msp.add_arc(
                    sh(e.dxf.center),
                    e.dxf.radius,
                    e.dxf.start_angle,
                    e.dxf.end_angle,
                    dxfattribs={"layer": e.dxf.layer},
                )
            elif t == "LWPOLYLINE":
                pts = [(p[0] - ox, p[1] - oy, p[2] if len(p) > 2 else 0) for p in e.get_points("xyb")]
                pl = msp.add_lwpolyline(pts, dxfattribs={"layer": e.dxf.layer})
                pl.closed = bool(e.closed)
            elif t == "TEXT":
                ins = sh(e.dxf.insert)
                msp.add_text(
                    e.dxf.text,
                    height=e.dxf.height,
                    dxfattribs={"layer": e.dxf.layer, "insert": ins},
                ).set_placement(ins, align=TextEntityAlignment.LEFT)
            elif t == "MTEXT":
                msp.add_mtext(
                    e.text,
                    dxfattribs={
                        "layer": e.dxf.layer,
                        "insert": sh(e.dxf.insert),
                        "char_height": getattr(e.dxf, "char_height", 2.5) or 2.5,
                    },
                )
            else:
                continue
            counts[t] += 1
        except Exception as exc:  # noqa: BLE001
            counts[f"skip:{t}"] += 1
            if counts[f"skip:{t}"] <= 3:
                print(f"  [warn] copy {t}: {exc}", file=sys.stderr)

    if with_dims and dim_plan:
        if "DIMS" not in doc.layers:
            doc.layers.add("DIMS", color=1)  # red
        n = add_ezdxf_dimensions(msp, dim_plan)
        counts["DIMENSION"] = n

    out_path.parent.mkdir(parents=True, exist_ok=True)
    doc.saveas(out_path)
    return counts


def collect_column_centers(
    entities: list[DXFEntity],
    origin: tuple[float, float],
) -> list[tuple[float, float, float, float]]:
    """로컬 좌표 기둥 중심 (cx, cy, w, h)."""
    ox, oy = origin
    cols: list[tuple[float, float, float, float]] = []
    for e in entities:
        if e.dxftype() != "LWPOLYLINE" or not getattr(e, "closed", False):
            continue
        pts = list(e.get_points("xy"))
        if len(pts) < 3:
            continue
        xs = [p[0] - ox for p in pts]
        ys = [p[1] - oy for p in pts]
        w, h = max(xs) - min(xs), max(ys) - min(ys)
        if 600 <= w <= 1500 and 600 <= h <= 1500 and abs(w - h) <= 300:
            cols.append(((min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2, w, h))
    return cols


def content_extents_local(
    entities: list[DXFEntity],
    origin: tuple[float, float],
) -> tuple[float, float, float, float] | None:
    ox, oy = origin
    xs: list[float] = []
    ys: list[float] = []
    for e in entities:
        if e.dxftype() not in {"LINE", "LWPOLYLINE", "ARC", "CIRCLE"}:
            continue
        for x, y in entity_points(e):
            xs.append(x - ox)
            ys.append(y - oy)
    if not xs:
        return None
    return min(xs), min(ys), max(xs), max(ys)


def build_dim_plan(
    entities: list[DXFEntity],
    origin: tuple[float, float],
    *,
    min_span_mm: float = 3000.0,
    detail: bool = False,
) -> dict:
    """전체 외곽 + 주요 기둥 그리드 치수 계획.

    detail=True 이면 기둥 1개 축도 인정하고 min_span을 더 작게 잡아 세부 치수.
    """
    ext = content_extents_local(entities, origin)
    if not ext:
        raise RuntimeError("치수용 extents 없음")
    x0, y0, x1, y1 = ext
    cols = collect_column_centers(entities, origin)

    from collections import Counter

    xc = Counter(round(c[0]) for c in cols)
    yc = Counter(round(c[1]) for c in cols)
    min_count = 1 if detail else 2
    span_min = 1500.0 if detail else min_span_mm
    maj_x = sorted(x for x, n in xc.items() if n >= min_count)
    maj_y = sorted(y for y, n in yc.items() if n >= min_count)

    x_spans: list[tuple[float, float, float]] = []
    for a, b in zip(maj_x, maj_x[1:]):
        d = b - a
        if d >= span_min:
            x_spans.append((float(a), float(b), float(d)))

    y_spans: list[tuple[float, float, float]] = []
    for a, b in zip(maj_y, maj_y[1:]):
        d = b - a
        if d >= span_min:
            y_spans.append((float(a), float(b), float(d)))

    plan = {
        "overall": {
            "x0": x0,
            "y0": y0,
            "x1": x1,
            "y1": y1,
            "width_mm": x1 - x0,
            "height_mm": y1 - y0,
            "width_m": (x1 - x0) / 1000.0,
            "height_m": (y1 - y0) / 1000.0,
        },
        "maj_x": maj_x,
        "maj_y": maj_y,
        "x_spans": x_spans,
        "y_spans": y_spans,
        "n_columns": len(cols),
        "detail": detail,
        "min_span_mm": span_min,
    }
    print(
        f"  dims: overall {plan['overall']['width_m']:.2f}m × "
        f"{plan['overall']['height_m']:.2f}m  "
        f"grid X={len(maj_x)} Y={len(maj_y)}  cols={len(cols)}  "
        f"detail={detail}"
    )
    return plan


def add_ezdxf_dimensions(msp, plan: dict) -> int:
    """DXF에 선형 치수 추가. 반환: 추가 개수."""
    o = plan["overall"]
    x0, y0, x1, y1 = o["x0"], o["y0"], o["x1"], o["y1"]
    n = 0
    # 전체 가로 (하단)
    dim = msp.add_linear_dim(
        base=((x0 + x1) / 2, y0 - 3500),
        p1=(x0, y0),
        p2=(x1, y0),
        dimstyle="EZDXF",
        dxfattribs={"layer": "DIMS"},
        override={"dimtxt": 800, "dimexe": 400, "dimexo": 200, "dimasz": 400},
    )
    dim.render()
    n += 1
    # 전체 세로 (좌측)
    dim = msp.add_linear_dim(
        base=(x0 - 3500, (y0 + y1) / 2),
        p1=(x0, y0),
        p2=(x0, y1),
        angle=90,
        dimstyle="EZDXF",
        dxfattribs={"layer": "DIMS"},
        override={"dimtxt": 800, "dimexe": 400, "dimexo": 200, "dimasz": 400},
    )
    dim.render()
    n += 1
    # 상단 X 베이 수치는 생략 (제목/여백과 겹침 방지). 필요 시 plan["bay_top"]=True
    if plan.get("bay_top"):
        spans = plan["x_spans"]
        max_n = 40 if plan.get("detail") else 24
        if len(spans) > max_n:
            step = max(1, len(spans) // (max_n - 2))
            spans = spans[::step]
        y_base = y1 + 2500
        txt = 400 if plan.get("detail") else 500
        for a, b, _d in spans:
            dim = msp.add_linear_dim(
                base=((a + b) / 2, y_base),
                p1=(a, y1),
                p2=(b, y1),
                dimstyle="EZDXF",
                dxfattribs={"layer": "DIMS"},
                override={"dimtxt": txt, "dimexe": 250, "dimexo": 150, "dimasz": 250},
            )
            dim.render()
            n += 1
    return n


def draw_dims_matplotlib(
    ax,
    plan: dict,
    *,
    color: str = "#c0392b",
    title: str | None = None,
    overall_left: bool = False,
    overall_bottom: bool = False,
    bay_top: bool = False,
    bay_right: bool = False,
) -> None:
    """PNG용 치수 오버레이 (라벨은 m).

    제목에 전체 크기(W×H)가 있으면 좌·하단 전체치수는 기본 생략.
    상단·우측 베이 수치는 기본 생략 (제목/여백과 겹침 방지).
    """
    o = plan["overall"]
    x0, y0, x1, y1 = o["x0"], o["y0"], o["x1"], o["y1"]
    span_y = y1 - y0
    tick = max(span_y * 0.025, 1000)
    lw = 1.2
    fs_main = 36
    fs_bay = 18 if plan.get("detail") else 16
    fs_title = 40

    def h_dim(xa, xb, y, label, fontsize):
        ax.plot([xa, xa], [y - tick * 0.25, y + tick * 0.25], color=color, lw=lw, solid_capstyle="butt")
        ax.plot([xb, xb], [y - tick * 0.25, y + tick * 0.25], color=color, lw=lw, solid_capstyle="butt")
        ax.plot([xa, xb], [y, y], color=color, lw=lw)
        ax.text(
            (xa + xb) / 2,
            y + tick * 0.2,
            label,
            ha="center",
            va="bottom",
            color=color,
            fontsize=fontsize,
            fontweight="bold",
            clip_on=False,
        )

    def v_dim(ya, yb, x, label, fontsize):
        ax.plot([x - tick * 0.25, x + tick * 0.25], [ya, ya], color=color, lw=lw)
        ax.plot([x - tick * 0.25, x + tick * 0.25], [yb, yb], color=color, lw=lw)
        ax.plot([x, x], [ya, yb], color=color, lw=lw)
        ax.text(
            x - tick * 0.35,
            (ya + yb) / 2,
            label,
            ha="right",
            va="center",
            color=color,
            fontsize=fontsize,
            fontweight="bold",
            rotation=90,
            clip_on=False,
        )

    if overall_bottom:
        h_dim(x0, x1, y0 - tick * 1.6, f"{o['width_m']:.2f} m", fs_main)
    if overall_left:
        v_dim(y0, y1, x0 - tick * 1.6, f"{o['height_m']:.2f} m", fs_main)
    if title is None:
        title = f"overall  {o['width_m']:.2f} m × {o['height_m']:.2f} m"
    # 제목 높이(대략 tick×2)만큼 도면에서 띄움 — 도면 안 침범 방지
    title_gap = tick * (2.8 if bay_top else 1.6)
    title_h = tick * 2.0  # ≈ fontsize 40 한 줄 높이
    title_y = y1 + title_gap + title_h
    ax.text(
        (x0 + x1) / 2,
        title_y,
        title,
        ha="center",
        va="bottom",
        color=color,
        fontsize=fs_title,
        fontweight="bold",
        clip_on=False,
    )

    if bay_top:
        spans = plan["x_spans"]
        max_spans = 40 if plan.get("detail") else 20
        if len(spans) > max_spans:
            step = max(1, len(spans) // (max_spans - 2))
            spans = spans[::step]
        y_bay = y1 + tick * 0.85
        for a, b, d in spans:
            ax.plot([a, a], [y1, y_bay], color=color, lw=0.8, alpha=0.85)
            ax.plot([b, b], [y1, y_bay], color=color, lw=0.8, alpha=0.85)
            ax.plot([a, b], [y_bay, y_bay], color=color, lw=0.8, alpha=0.85)
            ax.text(
                (a + b) / 2,
                y_bay + tick * 0.08,
                f"{d / 1000:.1f}",
                ha="center",
                va="bottom",
                color=color,
                fontsize=fs_bay,
                clip_on=False,
            )

    if bay_right:
        x_bay = x1 + tick * 0.85
        for a, b, d in plan["y_spans"]:
            ax.plot([x1, x_bay], [a, a], color=color, lw=0.8, alpha=0.85)
            ax.plot([x1, x_bay], [b, b], color=color, lw=0.8, alpha=0.85)
            ax.plot([x_bay, x_bay], [a, b], color=color, lw=0.8, alpha=0.85)
            ax.text(
                x_bay + tick * 0.12,
                (a + b) / 2,
                f"{d / 1000:.1f} m",
                ha="left",
                va="center",
                color=color,
                fontsize=fs_bay,
                clip_on=False,
            )


def render_hires_png(
    entities: list[DXFEntity],
    png_path: Path,
    *,
    xmin: float,
    ymin: float,
    xmax: float,
    ymax: float,
    dpi: int,
    px_width: int | None,
    linewidth: float,
    origin: tuple[float, float],
    dim_plan: dict | None = None,
    dim_title: str | None = None,
    dim_margin_mm: float | None = None,
) -> tuple[int, int]:
    """bbox를 그대로 그려 고해상도 PNG 저장. 반환: (width_px, height_px).

    dim_margin_mm: 치수 바깥 여백(mm). None이면 기존(최대 6%/12%, 최소 5 m).
    """
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.collections import LineCollection
    from matplotlib.patches import Arc, Circle

    ox, oy = origin
    # 로컬 좌표로 변환된 뷰 범위
    vx0, vy0 = xmin - ox, ymin - oy
    vx1, vy1 = xmax - ox, ymax - oy
    # 치수 여백 — title(상)·베이(상/우)·전체치수(좌/하)가 도면을 가리지 않게
    if dim_plan:
        o = dim_plan["overall"]
        span_y = max(o["y1"] - o["y0"], 1.0)
        tick = max(span_y * 0.025, 1000.0)
        if dim_margin_mm is not None:
            base = float(dim_margin_mm)
            m_left = max(base, tick * 2.0)
            m_bottom = max(base * 1.0, tick * 2.5)
            m_right = max(base * 1.0, tick * 2.5)
            # 제목( gap + 한 줄 높이 ) + 소폭 여유
            m_top = max(base * 2.0, tick * 6.5, 9000)
        else:
            # 상·우 베이 치수 없음 → 여백 타이트
            m_right = max(tick * 2.0, 3000)
            m_top = max(tick * 6.5, 9000)
            m_bottom = max(tick * 2.5, 4000)
            m_left = max(tick * 0.8, 1500)
        vx0 = min(vx0, o["x0"] - m_left)
        vy0 = min(vy0, o["y0"] - m_bottom)
        vx1 = max(vx1, o["x1"] + m_right)
        vy1 = max(vy1, o["y1"] + m_top)

    span_x = max(vx1 - vx0, 1.0)
    span_y = max(vy1 - vy0, 1.0)
    aspect = span_y / span_x

    if px_width and px_width > 0:
        fig_w = px_width / dpi
        fig_h = fig_w * aspect
    else:
        fig_w = 24.0
        fig_h = 24.0 * aspect

    max_px = 20000
    if fig_w * dpi > max_px:
        fig_w = max_px / dpi
        fig_h = fig_w * aspect
    if fig_h * dpi > max_px:
        fig_h = max_px / dpi
        fig_w = fig_h / aspect

    fig, ax = plt.subplots(figsize=(fig_w, fig_h), dpi=dpi)
    segs: list[list[tuple[float, float]]] = []
    texts: list[tuple[float, float, str, float, float]] = []

    for e in entities:
        t = e.dxftype()
        try:
            if t == "LINE":
                x0, y0 = float(e.dxf.start.x) - ox, float(e.dxf.start.y) - oy
                x1, y1 = float(e.dxf.end.x) - ox, float(e.dxf.end.y) - oy
                segs.append([(x0, y0), (x1, y1)])
            elif t == "LWPOLYLINE":
                pts = [(float(p[0]) - ox, float(p[1]) - oy) for p in e.get_points("xy")]
                if len(pts) >= 2:
                    if e.closed and pts[0] != pts[-1]:
                        pts = pts + [pts[0]]
                    segs.append(pts)
            elif t == "CIRCLE":
                c = e.dxf.center
                r = float(e.dxf.radius)
                ax.add_patch(
                    Circle(
                        (c.x - ox, c.y - oy),
                        r,
                        fill=False,
                        edgecolor="black",
                        linewidth=linewidth,
                    )
                )
            elif t == "ARC":
                c = e.dxf.center
                r = float(e.dxf.radius)
                ax.add_patch(
                    Arc(
                        (c.x - ox, c.y - oy),
                        2 * r,
                        2 * r,
                        angle=0,
                        theta1=float(e.dxf.start_angle),
                        theta2=float(e.dxf.end_angle),
                        color="black",
                        linewidth=linewidth,
                    )
                )
            elif t == "TEXT":
                texts.append(
                    (
                        float(e.dxf.insert.x) - ox,
                        float(e.dxf.insert.y) - oy,
                        str(e.dxf.text or ""),
                        float(e.dxf.height or 2.5),
                        float(getattr(e.dxf, "rotation", 0) or 0),
                    )
                )
            elif t == "MTEXT":
                raw = e.text or ""
                # {\f...;} 등 간단 제거
                import re as _re

                plain = _re.sub(r"\{[^;]*;", "", raw)
                plain = plain.replace("}", "").replace("\\P", "\n")
                plain = _re.sub(r"\\[A-Za-z][^;]*;", "", plain)
                h = float(getattr(e.dxf, "char_height", 2.5) or 2.5)
                texts.append(
                    (
                        float(e.dxf.insert.x) - ox,
                        float(e.dxf.insert.y) - oy,
                        plain.strip(),
                        h,
                        float(getattr(e.dxf, "rotation", 0) or 0),
                    )
                )
        except Exception:  # noqa: BLE001
            continue

    if segs:
        ax.add_collection(
            LineCollection(segs, colors="black", linewidths=linewidth, antialiased=True)
        )

    # CAD 실명 라벨 (접견실 등) — Viewer와 같이 빨강, CAD 높이에 비례한 폰트
    if texts:
        import matplotlib.font_manager as fm

        font_name = None
        for cand in (
            "AppleGothic",
            "Apple SD Gothic Neo",
            "NanumGothic",
            "Malgun Gothic",
            "Noto Sans CJK KR",
            "Noto Sans KR",
        ):
            matches = [f for f in fm.fontManager.ttflist if cand in f.name]
            if matches:
                font_name = matches[0].name
                break
        # 데이터 mm → pt: CAD height × 스케일, 실명 라벨은 최대 7pt
        mm_per_inch = span_x / max(fig_w, 0.01)
        for tx, ty, s, th, rot in texts:
            if not s or not s.strip():
                continue
            fs = (max(th, 1.0) / mm_per_inch) * 72.0 * 1.6
            fs = max(5.0, min(7.0, fs))
            ax.text(
                tx,
                ty,
                s,
                color="#1a5fb4",
                fontsize=fs,
                rotation=rot,
                ha="left",
                va="bottom",
                fontname=font_name,
                clip_on=True,
                zorder=5,
            )

    if dim_plan:
        draw_dims_matplotlib(ax, dim_plan, title=dim_title)

    ax.set_xlim(vx0, vx1)
    ax.set_ylim(vy0, vy1)
    ax.set_aspect("equal", adjustable="box", anchor="C")
    # equal aspect 후에도 한도 재고정 (마진이 잠식되지 않게)
    ax.set_xlim(vx0, vx1)
    ax.set_ylim(vy0, vy1)
    ax.axis("off")
    ax.set_facecolor("white")
    fig.patch.set_facecolor("white")
    # 치수가 있으면 axes 여백을 유지 — tight가 상·하·우 마진을 다시 잘라냄
    if dim_plan:
        fig.subplots_adjust(left=0.0, right=1.0, bottom=0.0, top=1.0)
        save_kwargs = {"bbox_inches": None, "pad_inches": 0}
    else:
        fig.subplots_adjust(left=0.02, right=0.98, bottom=0.04, top=0.96)
        save_kwargs = {"bbox_inches": "tight", "pad_inches": 0.05}

    png_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(
        png_path,
        dpi=dpi,
        facecolor="white",
        edgecolor="none",
        **save_kwargs,
    )
    plt.close(fig)

    # 치수 라벨이 캔버스 끝에 붙지 않도록 PNG에 픽셀 패딩 추가
    if dim_plan:
        try:
            from PIL import Image

            im = Image.open(png_path).convert("RGB")
            pr, pb, pt = 200, 120, 160  # right, bottom, top — 타이트 여백
            w, h = im.size
            canvas = Image.new("RGB", (w + pr, h + pb + pt), (255, 255, 255))
            canvas.paste(im, (0, pt))
            canvas.save(png_path, optimize=True)
        except Exception:  # noqa: BLE001
            pass

    try:
        from PIL import Image

        im = Image.open(png_path)
        return im.size
    except Exception:  # noqa: BLE001
        return int(fig_w * dpi), int(fig_h * dpi)


def render_floor_original_preview(
    dxf_path: Path,
    *,
    floor: str,
    plan_bbox: dict | tuple[float, float, float, float] | None = None,
    dpi: int = 300,
    px_width: int = 14000,
    linewidth: float = 0.3,
) -> dict:
    """floor_original.dxf → floor_original.png (+ _meta.json).

    plan/original bbox를 핵으로 두고 상·하단 외벽까지 확장한 뒤 렌더한다.
    상·우 베이 치수는 그리지 않는다 (draw_dims 기본값).
    """
    from lib_split import expand_bbox_include_outer_walls, filter_bbox

    dxf_path = Path(dxf_path)
    png_path = dxf_path.with_name("floor_original.png")
    meta_path = dxf_path.with_name("floor_original_meta.json")

    doc = ezdxf.readfile(str(dxf_path))
    raw = list(doc.modelspace())

    if isinstance(plan_bbox, dict):
        core = (
            float(plan_bbox["xmin"]),
            float(plan_bbox["ymin"]),
            float(plan_bbox["xmax"]),
            float(plan_bbox["ymax"]),
        )
    elif plan_bbox is not None:
        core = tuple(plan_bbox)  # type: ignore[assignment]
    else:
        # 기존 floor_original_meta 또는 LINE primary
        prev = dxf_path.parent / "floor_original_meta.json"
        if prev.is_file():
            data = json.loads(prev.read_text(encoding="utf-8"))
            bb = data.get("bbox_mm") or {}
            core = (
                float(bb["xmin"]),
                float(bb["ymin"]),
                float(bb["xmax"]),
                float(bb["ymax"]),
            )
        else:
            from lib_split import find_primary_line_bbox

            core = find_primary_line_bbox(raw)
            if core is None:
                raise RuntimeError(f"{floor}: preview bbox를 계산할 수 없음")

    # 외벽 탐색용으로 넉넉히 로드 후 Y 확장
    pad = 40_000.0
    wide = filter_bbox(
        raw, core[0] - pad, core[1] - pad, core[2] + pad, core[3] + pad
    )
    ox0, oy0, ox1, oy1 = expand_bbox_include_outer_walls(wide, core)
    ox0 -= 1500.0
    oy0 -= 1500.0
    ox1 += 2000.0
    oy1 += 1500.0

    w_m = (ox1 - ox0) / 1000.0
    h_m = (oy1 - oy0) / 1000.0
    entities = filter_bbox(raw, ox0, oy0, ox1, oy1)
    n_text = sum(1 for e in entities if e.dxftype() in {"TEXT", "MTEXT"})

    origin = (ox0, oy0)
    dim = build_dim_plan(entities, origin, detail=False)
    dim["overall"] = {
        "x0": 0.0,
        "y0": 0.0,
        "x1": ox1 - ox0,
        "y1": oy1 - oy0,
        "width_mm": ox1 - ox0,
        "height_mm": oy1 - oy0,
        "width_m": w_m,
        "height_m": h_m,
    }
    dim["maj_x"] = [v for v in dim.get("maj_x", []) if 0 <= v <= ox1 - ox0]
    dim["maj_y"] = [v for v in dim.get("maj_y", []) if 0 <= v <= oy1 - oy0]
    dim["x_spans"] = [
        s for s in dim.get("x_spans", []) if 0 <= s[0] and s[1] <= ox1 - ox0
    ]
    dim["y_spans"] = [
        s for s in dim.get("y_spans", []) if 0 <= s[0] and s[1] <= oy1 - oy0
    ]

    px = render_hires_png(
        entities,
        png_path,
        xmin=ox0,
        ymin=oy0,
        xmax=ox1,
        ymax=oy1,
        dpi=dpi,
        px_width=px_width,
        linewidth=linewidth,
        origin=origin,
        dim_plan=dim,
        dim_title=f"{floor} ORIGINAL  {w_m:.2f}×{h_m:.2f} m",
    )

    meta = {
        "floor": floor,
        "role": "floor_original_preview",
        "bbox_mm": {"xmin": ox0, "ymin": oy0, "xmax": ox1, "ymax": oy1},
        "size_m": {"width": w_m, "height": h_m},
        "n_entities": len(entities),
        "n_labels": n_text,
        "crop": "plan_expand_outer_walls",
        "files": {"dxf": str(dxf_path), "png": str(png_path)},
        "labels": "modelspace_TEXT_included",
        "label_font_scale": 1.6,
        "label_color": "#1a5fb4",
        "label_fontsize_max": 7.0,
        "no_top_bay_dims": True,
        "no_right_bay_dims": True,
        "png_size": {"width": px[0], "height": px[1]},
    }
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  PNG → {png_path}  {px[0]}x{px[1]}  {w_m:.1f}×{h_m:.1f} m")
    return meta


def entity_to_record(e: DXFEntity, origin: tuple[float, float]) -> dict | None:
    ox, oy = origin
    t = e.dxftype()
    layer = e.dxf.layer if e.dxf.hasattr("layer") else "0"
    base = {"type": t, "layer": layer}

    def loc(p) -> tuple[float, float]:
        return float(p[0]) - ox, float(p[1]) - oy

    if t == "LINE":
        s, end = loc(e.dxf.start), loc(e.dxf.end)
        return {
            **base,
            "start": s,
            "end": end,
            "length": math.hypot(end[0] - s[0], end[1] - s[1]),
        }
    if t == "CIRCLE":
        c = loc(e.dxf.center)
        r = float(e.dxf.radius)
        return {**base, "center": c, "radius": r, "diameter": r * 2}
    if t == "ARC":
        return {
            **base,
            "center": loc(e.dxf.center),
            "radius": float(e.dxf.radius),
            "start_angle": float(e.dxf.start_angle),
            "end_angle": float(e.dxf.end_angle),
        }
    if t == "LWPOLYLINE":
        pts = [loc(p) for p in e.get_points("xy")]
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        return {
            **base,
            "points": pts,
            "closed": bool(e.closed),
            "width": (max(xs) - min(xs)) if xs else 0.0,
            "height": (max(ys) - min(ys)) if ys else 0.0,
        }
    return None


def parse_args() -> argparse.Namespace:
    art = resolve_artifacts_dir()
    p = argparse.ArgumentParser(
        description="층 DXF 고정밀 크롭·치수·고해상도 PNG (lib_render)",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument("--floor", default="12F", help="층 (예: 6F, 12F)")
    src = p.add_mutually_exclusive_group()
    src.add_argument(
        "--from-clean",
        type=Path,
        default=None,
        help="클린 DXF 경로 (기본: $ARTIFACTS_DIR/floor_{FLOOR}_clean.dxf)",
    )
    src.add_argument(
        "--from-raw",
        action="store_true",
        help="원본 DXF에서 해당 층 INSERT를 explode (--raw-dxf 또는 ARTIFACTS_DIR/*.dxf)",
    )
    p.add_argument("--raw-dxf", type=Path, default=None, help="원본 DXF 경로")

    p.add_argument("--xmin", type=float, default=None, help="크롭 min X (mm)")
    p.add_argument("--ymin", type=float, default=None, help="크롭 min Y (mm)")
    p.add_argument("--xmax", type=float, default=None, help="크롭 max X (mm)")
    p.add_argument("--ymax", type=float, default=None, help="크롭 max Y (mm)")
    p.add_argument(
        "--auto-primary",
        action="store_true",
        help="LINE이 많은 클러스터 bbox를 자동 선택 (좌우 이중 배치 대응)",
    )
    p.add_argument(
        "--use-default-primary",
        action="store_true",
        help="레거시 기본 primary bbox 사용 (조사본 좌표)",
    )
    p.add_argument(
        "--filter-mode",
        choices=("centroid", "overlap"),
        default="centroid",
        help="bbox 포함 판정 방식",
    )

    p.add_argument("--out", type=Path, default=None, help="출력 디렉터리")
    p.add_argument("--stem", default=None, help="출력 파일 stem (기본: floor_{FLOOR}_hires)")
    p.add_argument("--dpi", type=int, default=600, help="PNG DPI")
    p.add_argument(
        "--px-width",
        type=int,
        default=14000,
        help="목표 가로 픽셀 (0이면 figsize×dpi만 사용)",
    )
    p.add_argument("--linewidth", type=float, default=0.25, help="선 두께 (pt)")
    p.add_argument(
        "--with-dims",
        action="store_true",
        default=True,
        help="전체·기둥 그리드 치수 자동 추가 (기본 on)",
    )
    p.add_argument("--no-dims", action="store_true", help="치수 추가 안 함")
    p.add_argument(
        "--split",
        type=int,
        default=0,
        help="도면을 N등분 (예: 4). 장축(X) 분할이 기본",
    )
    p.add_argument(
        "--split-mode",
        choices=("x", "grid2x2"),
        default="x",
        help="x=장축 N등분, grid2x2=2×2 (split=4일 때)",
    )
    p.add_argument(
        "--split-overlap",
        type=float,
        default=2000.0,
        help="분할 경계 겹침 (mm). 벽이 잘리지 않게",
    )
    p.add_argument(
        "--split-only",
        action="store_true",
        help="전체 도면은 생략하고 분할본만 출력",
    )
    p.add_argument("--keep-furniture", action="store_true")
    p.add_argument("--no-text", action="store_true")
    p.add_argument("--no-dxf", action="store_true")
    p.add_argument("--no-png", action="store_true")
    p.add_argument("--no-json", action="store_true")
    return p.parse_args()


def resolve_bbox(args: argparse.Namespace, entities: list[DXFEntity]) -> tuple[float, float, float, float]:
    coords = (args.xmin, args.ymin, args.xmax, args.ymax)
    if all(v is not None for v in coords):
        xmin, ymin, xmax, ymax = coords  # type: ignore[misc]
        if not (xmin < xmax and ymin < ymax):
            raise ValueError(f"bbox 오류: ({xmin},{ymin})-({xmax},{ymax})")
        print(
            f"  bbox (manual): "
            f"({xmin:.10f}, {ymin:.10f}) - ({xmax:.10f}, {ymax:.10f})"
        )
        return float(xmin), float(ymin), float(xmax), float(ymax)

    if args.auto_primary:
        bbox = find_primary_cluster_bbox(entities)
        print(
            f"  bbox (auto-primary): "
            f"({bbox[0]:.10f}, {bbox[1]:.10f}) - ({bbox[2]:.10f}, {bbox[3]:.10f})"
        )
        return bbox

    if args.use_default_primary or any(v is None for v in coords):
        d = DEFAULT_PRIMARY_BBOX
        xmin = args.xmin if args.xmin is not None else d["xmin"]
        ymin = args.ymin if args.ymin is not None else d["ymin"]
        xmax = args.xmax if args.xmax is not None else d["xmax"]
        ymax = args.ymax if args.ymax is not None else d["ymax"]
        print(
            f"  bbox (default-primary / merged): "
            f"({xmin:.10f}, {ymin:.10f}) - ({xmax:.10f}, {ymax:.10f})"
        )
        print("  tip: 네 좌표를 모두 넣으면 수동 크롭이 됩니다.")
        return xmin, ymin, xmax, ymax

    raise ValueError("bbox를 지정하세요 (--xmin..--ymax 또는 --auto-primary)")


def make_split_boxes(
    xmin: float,
    ymin: float,
    xmax: float,
    ymax: float,
    *,
    n: int,
    mode: str,
    overlap: float,
    floor: str = "",
) -> list[dict]:
    """분할 bbox 목록. 각 항목: id, label, xmin..ymax."""
    prefix = f"{floor} " if floor else ""
    boxes: list[dict] = []
    if mode == "grid2x2":
        mx = (xmin + xmax) / 2
        my = (ymin + ymax) / 2
        o = overlap / 2
        cells = [
            ("P1", "NW", xmin, my - o, mx + o, ymax),
            ("P2", "NE", mx - o, my - o, xmax, ymax),
            ("P3", "SW", xmin, ymin, mx + o, my + o),
            ("P4", "SE", mx - o, ymin, xmax, my + o),
        ]
        for pid, tag, a, b, c, d in cells:
            boxes.append(
                {
                    "id": pid,
                    "label": f"{prefix}{pid} ({tag})",
                    "xmin": a,
                    "ymin": b,
                    "xmax": c,
                    "ymax": d,
                }
            )
        return boxes

    # 장축(X) N등분
    if n < 2:
        raise ValueError("--split 은 2 이상")
    width = xmax - xmin
    step = width / n
    for i in range(n):
        x0 = xmin + i * step - (overlap if i > 0 else 0)
        x1 = xmin + (i + 1) * step + (overlap if i < n - 1 else 0)
        x0 = max(xmin, x0)
        x1 = min(xmax, x1)
        pid = f"P{i + 1}"
        boxes.append(
            {
                "id": pid,
                "label": f"{prefix}{pid}/{n} (X-strip)",
                "xmin": x0,
                "ymin": ymin,
                "xmax": x1,
                "ymax": ymax,
                "index": i + 1,
                "of": n,
            }
        )
    return boxes


def export_view(
    entities: list[DXFEntity],
    *,
    floor: str,
    xmin: float,
    ymin: float,
    xmax: float,
    ymax: float,
    out_dir: Path,
    stem: str,
    source_blocks: list[str],
    filter_mode: str,
    with_dims: bool,
    detail_dims: bool,
    dim_title: str | None,
    dpi: int,
    px_width: int,
    linewidth: float,
    include_text: bool,
    do_dxf: bool,
    do_png: bool,
    do_json: bool,
) -> dict:
    """단일 뷰(전체 또는 분할 조각)를 DXF/PNG/JSON으로 저장."""
    origin = (xmin, ymin)
    type_counts = Counter(e.dxftype() for e in entities)
    print(f"\n== {stem} ==")
    print(f"  entities: {len(entities)}  {dict(type_counts)}")
    print(f"  span: {(xmax - xmin) / 1000:.3f} m × {(ymax - ymin) / 1000:.3f} m")
    print(
        f"  bbox: ({xmin:.4f},{ymin:.4f})-({xmax:.4f},{ymax:.4f})"
    )

    dim_plan = None
    if with_dims and entities:
        dim_plan = build_dim_plan(entities, origin, detail=detail_dims)
        if dim_title is None and dim_plan:
            o = dim_plan["overall"]
            dim_title = f"{stem}  {o['width_m']:.2f} m × {o['height_m']:.2f} m"

    meta = {
        "floor": floor,
        "stem": stem,
        "source_blocks": source_blocks,
        "bbox_mm": {
            "xmin": xmin,
            "ymin": ymin,
            "xmax": xmax,
            "ymax": ymax,
            "width_mm": xmax - xmin,
            "height_mm": ymax - ymin,
            "width_m": (xmax - xmin) / 1000.0,
            "height_m": (ymax - ymin) / 1000.0,
        },
        "origin_shift": {"ox": xmin, "oy": ymin},
        "filter_mode": filter_mode,
        "entity_counts": dict(type_counts),
        "dimensions": dim_plan,
        "render": {
            "dpi": dpi,
            "px_width": px_width,
            "linewidth": linewidth,
            "with_dims": with_dims,
            "detail_dims": detail_dims,
            "dim_title": dim_title,
        },
        "files": {},
    }

    out_path = out_dir / stem
    if do_dxf:
        dxf_path = Path(f"{out_path}.dxf")
        copied = write_clean_dxf(
            entities,
            dxf_path,
            include_text=include_text,
            origin_shift=origin,
            with_dims=with_dims,
            dim_plan=dim_plan,
        )
        print(f"  DXF → {dxf_path}  {dict(copied)}")
        meta["files"]["dxf"] = str(dxf_path)

    if do_png:
        png_path = Path(f"{out_path}.png")
        px = render_hires_png(
            entities,
            png_path,
            xmin=xmin,
            ymin=ymin,
            xmax=xmax,
            ymax=ymax,
            dpi=dpi,
            px_width=px_width if px_width > 0 else None,
            linewidth=linewidth,
            origin=origin,
            dim_plan=dim_plan,
            dim_title=dim_title,
        )
        print(f"  PNG → {png_path}  pixels={px[0]}x{px[1]}")
        meta["files"]["png"] = str(png_path)
        meta["render"]["pixels"] = {"width": px[0], "height": px[1]}

    if do_json:
        json_path = Path(f"{out_path}_geom.json")
        records = []
        for e in entities:
            if e.dxftype() in GEOM_TYPES:
                rec = entity_to_record(e, origin)
                if rec:
                    records.append(rec)
        json_path.write_text(
            json.dumps({"meta": meta, "entities": records}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"  JSON → {json_path}  records={len(records)}")
        meta["files"]["json"] = str(json_path)

    meta_path = Path(f"{out_path}_meta.json")
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  META → {meta_path}")
    meta["files"]["meta"] = str(meta_path)
    return meta


def main() -> int:
    args = parse_args()
    art = resolve_artifacts_dir()
    floor = floor_token(args.floor)
    out_dir: Path = args.out or (art / f"{floor.lower()}_hires")
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = args.stem or f"floor_{floor}_hires"

    source_blocks: list[str] = ["from-clean"]
    if args.from_raw:
        raw = args.raw_dxf
        if raw is None:
            matches = sorted(art.glob("*.dxf"))
            raw = matches[0] if matches else None
        if not raw:
            raise FileNotFoundError(
                "원본 DXF 없음 — --raw-dxf <path> 또는 ARTIFACTS_DIR/*.dxf 필요"
            )
        entities, source_blocks = load_entities_from_raw(
            raw, floor, skip_furniture=not args.keep_furniture
        )
    else:
        clean = args.from_clean or (art / f"floor_{floor}_clean.dxf")
        if not clean.is_file():
            raise FileNotFoundError(
                f"클린 DXF 없음: {clean}\n"
                f"먼저 `python extract_2d.py --floor {floor} --out $ARTIFACTS_DIR` "
                f"를 실행하거나 --from-raw 사용"
            )
        print(f"loading clean {clean} ...", flush=True)
        entities = load_entities_from_clean(clean)
        print(f"  loaded entities: {len(entities)}")

    xmin, ymin, xmax, ymax = resolve_bbox(args, entities)
    before = len(entities)
    entities = filter_bbox(entities, xmin, ymin, xmax, ymax, mode=args.filter_mode)
    print(f"  filter ({args.filter_mode}): {before} → {len(entities)}")
    if not entities:
        raise RuntimeError("크롭 결과 엔티티 0개 — 좌표를 확인하세요")

    with_dims = not args.no_dims
    common = dict(
        floor=floor,
        source_blocks=source_blocks,
        filter_mode=args.filter_mode,
        with_dims=with_dims,
        dpi=args.dpi,
        px_width=args.px_width,
        linewidth=args.linewidth,
        include_text=not args.no_text,
        do_dxf=not args.no_dxf,
        do_png=not args.no_png,
        do_json=not args.no_json,
    )

    summary: list[dict] = []

    if not args.split_only:
        summary.append(
            export_view(
                entities,
                xmin=xmin,
                ymin=ymin,
                xmax=xmax,
                ymax=ymax,
                out_dir=out_dir,
                stem=stem,
                detail_dims=False,
                dim_title=None,
                **common,
            )
        )

    if args.split and args.split >= 2:
        mode = args.split_mode
        if mode == "grid2x2" and args.split != 4:
            print("[warn] grid2x2는 4분할 고정 — --split 4 로 처리", file=sys.stderr)
        boxes = make_split_boxes(
            xmin,
            ymin,
            xmax,
            ymax,
            n=4 if mode == "grid2x2" else args.split,
            mode=mode,
            overlap=args.split_overlap,
            floor=floor,
        )
        split_dir = out_dir / "parts"
        split_dir.mkdir(parents=True, exist_ok=True)
        print(f"\n== split {len(boxes)} parts → {split_dir} ==")
        for box in boxes:
            part_ents = filter_bbox(
                entities,
                box["xmin"],
                box["ymin"],
                box["xmax"],
                box["ymax"],
                mode=args.filter_mode,
            )
            if not part_ents:
                print(f"  [warn] {box['id']}: empty, skip")
                continue
            summary.append(
                export_view(
                    part_ents,
                    xmin=box["xmin"],
                    ymin=box["ymin"],
                    xmax=box["xmax"],
                    ymax=box["ymax"],
                    out_dir=split_dir,
                    stem=f"{stem}_{box['id']}",
                    detail_dims=True,
                    dim_title=box["label"],
                    **common,
                )
            )

    index_path = out_dir / f"{stem}_index.json"
    index_path.write_text(
        json.dumps(
            {
                "floor": floor,
                "full_bbox": {
                    "xmin": xmin,
                    "ymin": ymin,
                    "xmax": xmax,
                    "ymax": ymax,
                },
                "split": args.split,
                "split_mode": args.split_mode if args.split else None,
                "parts": [
                    {
                        "stem": s.get("stem"),
                        "bbox_mm": s.get("bbox_mm"),
                        "files": s.get("files"),
                        "dimensions_overall": (s.get("dimensions") or {}).get("overall"),
                    }
                    for s in summary
                ],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"\nindex → {index_path}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:  # noqa: BLE001
        print(f"[error] {exc}", file=sys.stderr)
        raise SystemExit(1)
