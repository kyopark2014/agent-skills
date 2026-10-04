#!/usr/bin/env python3
"""도면 샘플 2장에서 LLM이 벽으로 본 이중선의 두께·길이를 조건으로 모은다."""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import ezdxf
from ezdxf.entities import DXFEntity

from lib_walls import Seg, detect_wall_keys, extract_segments, load_wall_conditions

SAMPLED_DIR = "wall_samples"
SAMPLED_FILE = "wall_conditions.json"
SAMPLES_FILE = "samples.json"
OBSERVATIONS_FILE = "observations.json"

WALL_SAMPLE_PROMPT = """이 이미지는 건축 평면도에서 잘라 낸 샘플입니다. 평행한 이중선으로 그린 벽만 찾으세요.
문짝, 문 스윙, 가구, 치수선, 문자, 계단 디딤, 해칭, 기둥 기호는 벽이 아닙니다.
벽이면 그 이중선(두 면이 함께 들어간 가늘고 긴 구간)마다 bbox 하나를 주세요. 방 전체나 샘플 전체를 한 박스로 묶지 마세요.
좌표는 이 샘플 기준입니다. 왼쪽 위가 (0, 0), 오른쪽 아래가 (1, 1)입니다. bbox는 [x0, y0, x1, y1]이고 값은 0 이상 1 이하입니다.
벽으로 보이는 이중선이 없으면 walls는 빈 배열입니다.
설명 없이 아래 JSON만 <result> 안에 넣으세요.
<result>
{"walls":[{"bbox":[0.10,0.20,0.14,0.80]}]}
</result>
"""

# 샘플 창에서 이중선을 찾을 때 쓰는 대역. common(30–420 mm) 밖 벽도 모을 수 있게 넓힌다.
_MEASURE_GAP_MIN_MM = 15.0
_MEASURE_GAP_MAX_MM = 800.0
_MEASURE_MIN_LENGTH_MM = 350.0
_MEASURE_MIN_OVERLAP_MM = 200.0
_BOX_PAD_MM = 80.0


def sampled_conditions_path(floor_dir: Path) -> Path:
    return floor_dir / SAMPLED_DIR / SAMPLED_FILE


def load_sampled_overrides(path: Path) -> list[dict]:
    """wall_samples/wall_conditions.json 의 conditions 배열.

    파일이 조건 객체 배열이어도 읽는다. 벽이 없으면 빈 배열이다.
    """
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, list):
        items = data
    elif isinstance(data, dict):
        items = data.get("conditions")
        if items is None:
            raise ValueError(f"{path} 에 conditions 배열이 없습니다")
    else:
        raise ValueError(f"{path} 는 객체이거나 조건 배열이어야 합니다")
    if not isinstance(items, list):
        raise ValueError(f"{path} 의 conditions 는 배열이어야 합니다")
    out: list[dict] = []
    for item in items:
        if not isinstance(item, dict):
            raise ValueError(f"{path} 의 conditions 항목은 객체여야 합니다")
        out.append(item)
    return out


def norm_bbox_to_mm(
    bbox: list[float],
    window: tuple[float, float, float, float],
) -> tuple[float, float, float, float]:
    """샘플 이미지 정규 좌표를 도면 mm 로 바꾼다.

    이미지 y 는 아래 방향이다. window 는 (xmin, ymin, xmax, ymax) 이고 y 는 위가 크다.
    """
    x0, y0, x1, y1 = bbox
    xmin, ymin, xmax, ymax = window
    nx0, nx1 = min(x0, x1), max(x0, x1)
    ny0, ny1 = min(y0, y1), max(y0, y1)
    mx0 = xmin + nx0 * (xmax - xmin)
    mx1 = xmin + nx1 * (xmax - xmin)
    my1 = ymax - ny0 * (ymax - ymin)
    my0 = ymax - ny1 * (ymax - ymin)
    return mx0, my0, mx1, my1


def _parse_bbox(raw: Any) -> list[float] | None:
    if isinstance(raw, dict):
        keys = ("x0", "y0", "x1", "y1")
        if all(key in raw for key in keys):
            raw = [raw[key] for key in keys]
        else:
            return None
    if not isinstance(raw, (list, tuple)) or len(raw) != 4:
        return None
    try:
        vals = [float(value) for value in raw]
    except (TypeError, ValueError):
        return None
    if any(math.isnan(value) or math.isinf(value) for value in vals):
        return None
    return vals


def _as_norm_bbox(raw: Any, width: int | None, height: int | None) -> list[float] | None:
    vals = _parse_bbox(raw)
    if vals is None:
        return None
    peak = max(abs(value) for value in vals)
    if peak > 1.5 and width and height and width > 0 and height > 0:
        vals = [vals[0] / width, vals[1] / height, vals[2] / width, vals[3] / height]
    elif peak > 1.5:
        return None
    x0, y0, x1, y1 = vals
    if x1 < x0:
        x0, x1 = x1, x0
    if y1 < y0:
        y0, y1 = y1, y0
    x0 = min(1.0, max(0.0, x0))
    y0 = min(1.0, max(0.0, y0))
    x1 = min(1.0, max(0.0, x1))
    y1 = min(1.0, max(0.0, y1))
    if (x1 - x0) < 0.002 or (y1 - y0) < 0.002:
        return None
    return [x0, y0, x1, y1]


def _sample_by_id(samples: list[dict]) -> dict[str, dict]:
    return {str(item.get("id")): item for item in samples if item.get("id")}


def parse_observations(data: Any, samples: list[dict]) -> dict[str, list[list[float]]]:
    """LLM 답 또는 observations.json 을 샘플 id → 정규 bbox 목록으로 푼다."""
    known = _sample_by_id(samples)
    if isinstance(data, dict) and "samples" not in data and "walls" not in data:
        inner = data.get("observations")
        if isinstance(inner, (dict, list)):
            data = inner
    groups: list[tuple[str | None, Any]] = []
    if isinstance(data, dict) and isinstance(data.get("samples"), list):
        for item in data["samples"]:
            if not isinstance(item, dict):
                continue
            groups.append((str(item.get("id") or "") or None, item.get("walls") or []))
    elif isinstance(data, dict) and isinstance(data.get("walls"), list):
        sample_id = str(data.get("id") or "") or None
        if sample_id is None and len(known) > 1:
            raise ValueError("샘플이 둘 이상이면 walls 를 samples[].id 로 나눠야 합니다")
        only = next(iter(known), None) if len(known) == 1 else None
        groups.append((sample_id or only, data["walls"]))
    elif isinstance(data, list):
        if len(known) > 1:
            raise ValueError("샘플이 둘 이상이면 walls 를 samples[].id 로 나눠야 합니다")
        groups.append((next(iter(known), None), data))
    else:
        raise ValueError("observations 는 samples 또는 walls 를 가진 객체여야 합니다")

    found: dict[str, list[list[float]]] = {sid: [] for sid in known}
    for sample_id, walls in groups:
        if sample_id and sample_id not in known:
            raise ValueError(f"알 수 없는 샘플 id: {sample_id}")
        targets = [sample_id] if sample_id else list(known)
        if not isinstance(walls, list):
            raise ValueError("walls 는 배열이어야 합니다")
        for wall in walls:
            raw = wall.get("bbox") if isinstance(wall, dict) else wall
            for sid in targets:
                sample = known[sid]
                size = sample.get("png_size") or {}
                norm = _as_norm_bbox(raw, size.get("width"), size.get("height"))
                if norm is None:
                    continue
                found[sid].append(norm)
    return found


def _center_weight(cx: float, cy: float, bounds: tuple[float, float, float, float]) -> float:
    xmin, ymin, xmax, ymax = bounds
    half_w = max((xmax - xmin) * 0.5, 1.0)
    half_h = max((ymax - ymin) * 0.5, 1.0)
    edge = max(abs(cx - (xmin + xmax) * 0.5) / half_w, abs(cy - (ymin + ymax) * 0.5) / half_h)
    return 1.0 - 0.7 * min(edge, 1.0)


def _overlap_1d(a0: float, a1: float, b0: float, b1: float) -> float:
    lo = max(min(a0, a1), min(b0, b1))
    hi = min(max(a0, a1), max(b0, b1))
    return max(0.0, hi - lo)


def _seg_hits_rect(seg: Seg, rect: tuple[float, float, float, float]) -> bool:
    x0, y0, x1, y1 = rect
    if seg.is_h:
        yy = (seg.y0 + seg.y1) * 0.5
        if not (y0 <= yy <= y1):
            return False
        return _overlap_1d(seg.x0, seg.x1, x0, x1) >= min(400.0, seg.length * 0.3)
    if seg.is_v:
        xx = (seg.x0 + seg.x1) * 0.5
        if not (x0 <= xx <= x1):
            return False
        return _overlap_1d(seg.y0, seg.y1, y0, y1) >= min(400.0, seg.length * 0.3)
    return False


def choose_sample_side_mm(bounds: tuple[float, float, float, float]) -> float:
    """한 변 약 12 m. 작은 도면은 짧은 변의 42% 까지 줄인다."""
    xmin, ymin, xmax, ymax = bounds
    short = min(xmax - xmin, ymax - ymin)
    return min(12000.0, max(6000.0, 0.42 * short))


def _clamp_window(
    cx: float,
    cy: float,
    side: float,
    bounds: tuple[float, float, float, float],
) -> tuple[float, float, float, float]:
    xmin, ymin, xmax, ymax = bounds
    half = side * 0.5
    x0, x1 = cx - half, cx + half
    y0, y1 = cy - half, cy + half
    if x1 - x0 > xmax - xmin:
        x0, x1 = xmin, xmax
    elif x0 < xmin:
        x1 += xmin - x0
        x0 = xmin
    elif x1 > xmax:
        x0 -= x1 - xmax
        x1 = xmax
    if y1 - y0 > ymax - ymin:
        y0, y1 = ymin, ymax
    elif y0 < ymin:
        y1 += ymin - y0
        y0 = ymin
    elif y1 > ymax:
        y0 -= y1 - ymax
        y1 = ymax
    return (max(xmin, x0), max(ymin, y0), min(xmax, x1), min(ymax, y1))


def _fallback_windows(
    bounds: tuple[float, float, float, float],
) -> list[tuple[float, float, float, float]]:
    """가운데를 왼쪽·오른쪽으로, 세로로 긴 도면은 아래·위로 둘로 나눈다."""
    x0, y0, x1, y1 = bounds
    width, height = x1 - x0, y1 - y0
    if width >= height:
        mid = (x0 + x1) * 0.5
        overlap = width * 0.08
        return [(x0, y0, mid + overlap, y1), (mid - overlap, y0, x1, y1)]
    mid = (y0 + y1) * 0.5
    overlap = height * 0.08
    return [(x0, y0, x1, mid + overlap), (x0, mid - overlap, x1, y1)]


def pick_sample_windows(
    segs: list[Seg],
    bounds: tuple[float, float, float, float],
    *,
    count: int = 2,
) -> list[dict]:
    """도면 가운데에서 벽선이 많은 샘플 창을 count 개 고른다."""
    if count < 1:
        return []
    xmin, ymin, xmax, ymax = bounds
    if xmax <= xmin or ymax <= ymin:
        raise ValueError("도면 bbox 가 비어 있습니다")
    side = choose_sample_side_mm(bounds)
    margin_x = (xmax - xmin) * 0.08
    margin_y = (ymax - ymin) * 0.08
    step = max(side * 0.5, 1000.0)
    candidates: list[tuple[float, tuple[float, float, float, float]]] = []
    y = ymin + side * 0.5
    if y > ymax - side * 0.5:
        y = (ymin + ymax) * 0.5
    while y <= ymax - side * 0.5 + step * 0.25:
        x = xmin + side * 0.5
        if x > xmax - side * 0.5:
            x = (xmin + xmax) * 0.5
        while x <= xmax - side * 0.5 + step * 0.25:
            if margin_x < (x - xmin) < (xmax - xmin - margin_x) and margin_y < (y - ymin) < (
                ymax - ymin - margin_y
            ):
                window = _clamp_window(x, y, side, bounds)
                hits = sum(1 for seg in segs if seg.length >= 400.0 and _seg_hits_rect(seg, window))
                score = hits * _center_weight(x, y, bounds)
                candidates.append((score, window))
            x += step
        y += step
    candidates.sort(key=lambda item: item[0], reverse=True)
    min_sep = side * 0.55
    chosen: list[tuple[float, float, float, float]] = []
    for score, window in candidates:
        if score <= 0 and chosen:
            break
        cx = (window[0] + window[2]) * 0.5
        cy = (window[1] + window[3]) * 0.5
        if all(math.hypot(cx - (w[0] + w[2]) * 0.5, cy - (w[1] + w[3]) * 0.5) >= min_sep for w in chosen):
            chosen.append(window)
        if len(chosen) >= count:
            break
    if len(chosen) < count:
        for window in _fallback_windows(bounds):
            cx = (window[0] + window[2]) * 0.5
            cy = (window[1] + window[3]) * 0.5
            if all(
                math.hypot(cx - (w[0] + w[2]) * 0.5, cy - (w[1] + w[3]) * 0.5) >= min_sep * 0.5
                for w in chosen
            ):
                chosen.append(window)
            if len(chosen) >= count:
                break
    windows = []
    for index, window in enumerate(chosen[:count], start=1):
        x0, y0, x1, y1 = window
        windows.append(
            {
                "id": f"sample_{index:02d}",
                "bbox_mm": {"xmin": x0, "ymin": y0, "xmax": x1, "ymax": y1},
            }
        )
    return windows


def _window_tuple(sample: dict) -> tuple[float, float, float, float]:
    box = sample["bbox_mm"]
    return (float(box["xmin"]), float(box["ymin"]), float(box["xmax"]), float(box["ymax"]))


def render_sample_png(
    entities: list[DXFEntity],
    window: tuple[float, float, float, float],
    png_path: Path,
    *,
    px_width: int = 1800,
) -> tuple[int, int]:
    """여백 없이 window 를 PNG 로 그린다. 왼쪽 위가 (xmin, ymax) 다."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.collections import LineCollection

    xmin, ymin, xmax, ymax = window
    span_x = max(xmax - xmin, 1.0)
    span_y = max(ymax - ymin, 1.0)
    dpi = 100
    fig_w = px_width / dpi
    fig_h = fig_w * (span_y / span_x)
    fig, ax = plt.subplots(figsize=(fig_w, fig_h), dpi=dpi)
    segs: list[list[tuple[float, float]]] = []
    for entity in entities:
        kind = entity.dxftype()
        try:
            if kind == "LINE":
                segs.append(
                    [
                        (float(entity.dxf.start.x), float(entity.dxf.start.y)),
                        (float(entity.dxf.end.x), float(entity.dxf.end.y)),
                    ]
                )
            elif kind == "LWPOLYLINE":
                pts = [(float(p[0]), float(p[1])) for p in entity.get_points("xy")]
                if len(pts) >= 2:
                    if entity.closed and pts[0] != pts[-1]:
                        pts = pts + [pts[0]]
                    segs.append(pts)
        except Exception:  # noqa: BLE001
            continue
    if segs:
        ax.add_collection(LineCollection(segs, colors="#222222", linewidths=0.8, clip_on=True))
    ax.autoscale(False)
    ax.set_xlim(xmin, xmax)
    ax.set_ylim(ymin, ymax)
    ax.margins(0)
    ax.axis("off")
    fig.subplots_adjust(left=0, right=1, bottom=0, top=1)
    png_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(png_path, dpi=dpi, pad_inches=0)
    plt.close(fig)
    from PIL import Image

    with Image.open(png_path) as image:
        return image.size


def _pair_overlap(a: Seg, b: Seg, *, along_x: bool) -> float:
    if along_x:
        return _overlap_1d(a.x0, a.x1, b.x0, b.x1)
    return _overlap_1d(a.y0, a.y1, b.y0, b.y1)


def _overlap_center_in_box(
    a: Seg,
    b: Seg,
    box: tuple[float, float, float, float],
    *,
    along_x: bool,
) -> bool:
    x0, y0, x1, y1 = box
    if along_x:
        lo = max(min(a.x0, a.x1), min(b.x0, b.x1))
        hi = min(max(a.x0, a.x1), max(b.x0, b.x1))
        cx = (lo + hi) * 0.5
        cy = ((a.y0 + a.y1) * 0.5 + (b.y0 + b.y1) * 0.5) * 0.5
    else:
        lo = max(min(a.y0, a.y1), min(b.y0, b.y1))
        hi = min(max(a.y0, a.y1), max(b.y0, b.y1))
        cy = (lo + hi) * 0.5
        cx = ((a.x0 + a.x1) * 0.5 + (b.x0 + b.x1) * 0.5) * 0.5
    return x0 <= cx <= x1 and y0 <= cy <= y1


def measure_pairs_in_box(
    segs: list[Seg],
    box: tuple[float, float, float, float],
    *,
    wall_keys: set[tuple[int, int]] | None = None,
    limit: int = 8,
) -> list[dict]:
    """박스 안 가장 가까운 평행 이중선의 간격·길이를 잰다."""
    x0, y0, x1, y1 = box
    pad = _BOX_PAD_MM
    expanded = (x0 - pad, y0 - pad, x1 + pad, y1 + pad)
    keys = wall_keys or set()
    found: list[dict] = []

    def collect(group: list[Seg], *, along_x: bool) -> None:
        def ortho(seg: Seg) -> float:
            if along_x:
                return (seg.y0 + seg.y1) * 0.5
            return (seg.x0 + seg.x1) * 0.5

        items = [seg for seg in group if seg.length >= _MEASURE_MIN_LENGTH_MM and _seg_hits_rect(seg, expanded)]
        items.sort(key=ortho)
        nearest: dict[tuple[int, int], tuple[float, Seg, float]] = {}
        for index, left in enumerate(items):
            origin = ortho(left)
            for right in items[index + 1 :]:
                gap = ortho(right) - origin
                if gap > _MEASURE_GAP_MAX_MM:
                    break
                if gap < _MEASURE_GAP_MIN_MM:
                    continue
                overlap = _pair_overlap(left, right, along_x=along_x)
                short = min(left.length, right.length)
                if overlap < max(_MEASURE_MIN_OVERLAP_MM, 0.45 * short):
                    continue
                if not _overlap_center_in_box(left, right, box, along_x=along_x):
                    continue
                prev = nearest.get(left.key)
                if prev is None or gap < prev[0]:
                    nearest[left.key] = (gap, right, overlap)
        seen: set[tuple[tuple[int, int], tuple[int, int]]] = set()
        for left_key, (gap, right, overlap) in nearest.items():
            pair_key = tuple(sorted((left_key, right.key)))
            if pair_key in seen:
                continue
            seen.add(pair_key)
            left = next(seg for seg in items if seg.key == left_key)
            short = min(left.length, right.length)
            long = max(left.length, right.length)
            found.append(
                {
                    "gap_mm": gap,
                    "shorter_length_mm": short,
                    "longer_length_mm": long,
                    "overlap_mm": overlap,
                    "overlap_ratio": overlap / short if short else 0.0,
                    "orientation": "H" if along_x else "V",
                    "covered_by_common": left.key in keys and right.key in keys,
                    "keys": [left.key, right.key],
                }
            )

    collect([seg for seg in segs if seg.is_h], along_x=True)
    collect([seg for seg in segs if seg.is_v], along_x=False)
    found.sort(key=lambda item: item["shorter_length_mm"], reverse=True)
    return found[:limit]


def _same_cluster(left: dict, right: dict) -> bool:
    gap_gap = abs(left["gap_mm"] - right["gap_mm"])
    short = min(left["shorter_length_mm"], right["shorter_length_mm"])
    length_gap = abs(left["shorter_length_mm"] - right["shorter_length_mm"])
    return gap_gap <= 15.0 and length_gap <= max(40.0, 0.12 * short)


def cluster_measurements(measurements: list[dict]) -> list[list[dict]]:
    """간격 15 mm · 길이 12% 안으로 가까운 측정만 한 조건으로 묶는다."""
    ordered = sorted(measurements, key=lambda item: (item["gap_mm"], item["shorter_length_mm"]))
    groups: list[list[dict]] = []
    for item in ordered:
        placed = False
        for group in groups:
            if all(_same_cluster(item, other) for other in group):
                group.append(item)
                placed = True
                break
        if not placed:
            groups.append([item])
    return groups


def _len_text(mm: float) -> str:
    if mm >= 1000:
        return f"{mm / 1000:.2f} m"
    return f"{int(round(mm))} mm"


def condition_from_cluster(cluster: list[dict], *, sample_ids: list[str]) -> dict:
    """한 군집을 common 에 더할 좁은 조건으로 만든다.

    candidate.max_length_mm 이 있어 이 길이 대역 밖은 이 조건의 후보가 아니다.
    """
    gaps = [item["gap_mm"] for item in cluster]
    shorts = [item["shorter_length_mm"] for item in cluster]
    overlaps = [item["overlap_ratio"] for item in cluster]
    overlap_mm = [item["overlap_mm"] for item in cluster]
    gap_pad = 5.0
    len_pad = max(10.0, min(shorts) * 0.015)
    gap_min = max(10, int(math.floor(min(gaps) - gap_pad)))
    gap_max = int(math.ceil(max(gaps) + gap_pad))
    len_min = max(200, int(math.floor(min(shorts) - len_pad)))
    len_max = int(math.ceil(max(shorts) + len_pad))
    raw_ratio = min(overlaps)
    ratio = 0.9 if raw_ratio >= 0.9 else round(max(0.7, raw_ratio - 0.05), 2)
    where = ", ".join(sample_ids) if sample_ids else "샘플"
    if len(set(int(round(gap)) for gap in gaps)) == 1 and len(set(int(round(value)) for value in shorts)) == 1:
        note = (
            f"{where} 에서 LLM이 벽으로 본 이중선. "
            f"간격 {int(round(gaps[0]))} mm, 짧은 쪽 {_len_text(shorts[0])}."
        )
    else:
        note = (
            f"{where} 에서 LLM이 벽으로 본 이중선. "
            f"간격 {int(round(min(gaps)))}–{int(round(max(gaps)))} mm, "
            f"짧은 쪽 {_len_text(min(shorts))}–{_len_text(max(shorts))}."
        )
    return {
        "note": note,
        "candidate": {
            "min_length_mm": len_min,
            "max_length_mm": len_max,
            "gap_mm": {"min": gap_min, "max": gap_max},
            "overlap_mm_min": max(200, int(min(overlap_mm) * 0.9)),
            "overlap_ratio_of_shorter": ratio,
        },
        "long_double_wall": {
            "enabled": True,
            "gap_mm": {"min": gap_min, "max": gap_max},
            "shorter_length_mm_gte": len_min,
            "overlap_ratio_of_shorter_gte": ratio,
            "long_neighbor_count_gte": 99,
            "same_face_shorter_length_mm_gte": len_min,
        },
    }


def _public_measurement(item: dict, sample_id: str) -> dict:
    return {
        "sample": sample_id,
        "gap_mm": round(item["gap_mm"], 1),
        "shorter_length_mm": round(item["shorter_length_mm"], 1),
        "longer_length_mm": round(item["longer_length_mm"], 1),
        "overlap_mm": round(item["overlap_mm"], 1),
        "overlap_ratio": round(item["overlap_ratio"], 3),
        "orientation": item["orientation"],
        "covered_by_common": bool(item["covered_by_common"]),
    }


def collect_conditions(
    segs: list[Seg],
    samples: list[dict],
    boxes_by_sample: dict[str, list[list[float]]],
    *,
    wall_keys: set[tuple[int, int]] | None = None,
) -> dict:
    """LLM bbox 안의 이중선을 재고, common 에 없는 것만 조건으로 만든다."""
    keys = wall_keys if wall_keys is not None else detect_wall_keys(segs, conditions=load_wall_conditions(None))
    measurements: list[dict] = []
    missed: list[tuple[dict, str]] = []
    for sample in samples:
        sample_id = str(sample["id"])
        window = _window_tuple(sample)
        for norm in boxes_by_sample.get(sample_id) or []:
            box = norm_bbox_to_mm(norm, window)
            for item in measure_pairs_in_box(segs, box, wall_keys=keys):
                measurements.append(_public_measurement(item, sample_id))
                if not item["covered_by_common"]:
                    item["sample"] = sample_id
                    missed.append(item)
    clusters = cluster_measurements(missed)
    conditions = []
    for cluster in clusters:
        ids: list[str] = []
        for item in cluster:
            sample_id = str(item.get("sample") or "")
            if sample_id and sample_id not in ids:
                ids.append(sample_id)
        conditions.append(condition_from_cluster(cluster, sample_ids=ids))
    return {"measurements": measurements, "conditions": conditions}


def content_bounds(
    segs: list[Seg],
    meta_bbox: dict | None,
) -> tuple[float, float, float, float]:
    if meta_bbox:
        return (
            float(meta_bbox["xmin"]),
            float(meta_bbox["ymin"]),
            float(meta_bbox["xmax"]),
            float(meta_bbox["ymax"]),
        )
    long = [seg for seg in segs if seg.length >= 400.0 and (seg.is_h or seg.is_v)] or segs
    if not long:
        raise ValueError("샘플을 자를 선이 없습니다")
    xs = [value for seg in long for value in (seg.x0, seg.x1)]
    ys = [value for seg in long for value in (seg.y0, seg.y1)]
    return (min(xs), min(ys), max(xs), max(ys))


def segments_of(entities: list[DXFEntity]) -> list[Seg]:
    return extract_segments(entities, angle_tol_deg=8.0)


def write_sample_document(
    path: Path,
    *,
    floor: str,
    drawing_id: str,
    samples: list[dict],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = {
        "floor": floor,
        "drawing_id": drawing_id,
        "prompt": WALL_SAMPLE_PROMPT,
        "samples": samples,
    }
    path.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")


def load_floor_entities(dxf_path: Path) -> list[DXFEntity]:
    doc = ezdxf.readfile(str(dxf_path))
    return list(doc.modelspace())
