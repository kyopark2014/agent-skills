#!/usr/bin/env python3
"""floor_wall_original vs floor_wall_validated 시각 diff.

- 회색: BASE (original)
- 어두운 빨강: 유지된 WALL
- 초록: promote (validated에만 있는 WALL)
- 파랑: demote (original에만 있던 WALL)

Usage:
  python render_wall_diff.py --artifacts $ART --floor 5F
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import ezdxf  # noqa: E402

from lib_llm_correct import WALL_LAYER, BASE_LAYER  # noqa: E402


def _seg_key(x0: float, y0: float, x1: float, y1: float, *, q: float = 50.0) -> tuple:
    """양방향 동일 키 (양자화)."""
    a = (round(x0 / q) * q, round(y0 / q) * q, round(x1 / q) * q, round(y1 / q) * q)
    b = (round(x1 / q) * q, round(y1 / q) * q, round(x0 / q) * q, round(y0 / q) * q)
    return a if a <= b else b


def _collect_wall_segs(msp) -> dict[tuple, list[tuple[float, float]]]:
    out: dict[tuple, list[tuple[float, float]]] = {}
    for e in msp:
        if e.dxf.layer != WALL_LAYER:
            continue
        t = e.dxftype()
        pts: list[tuple[float, float]] = []
        try:
            if t == "LINE":
                pts = [
                    (float(e.dxf.start.x), float(e.dxf.start.y)),
                    (float(e.dxf.end.x), float(e.dxf.end.y)),
                ]
            elif t == "LWPOLYLINE":
                pts = [(float(p[0]), float(p[1])) for p in e.get_points("xy")]
                if e.closed and pts and pts[0] != pts[-1]:
                    pts = pts + [pts[0]]
            else:
                continue
        except Exception:  # noqa: BLE001
            continue
        if len(pts) < 2:
            continue
        for i in range(len(pts) - 1):
            x0, y0 = pts[i]
            x1, y1 = pts[i + 1]
            if math.hypot(x1 - x0, y1 - y0) < 1.0:
                continue
            k = _seg_key(x0, y0, x1, y1)
            out[k] = [(x0, y0), (x1, y1)]
    return out


def _collect_base_segs(msp) -> list[list[tuple[float, float]]]:
    out: list[list[tuple[float, float]]] = []
    for e in msp:
        if e.dxf.layer != BASE_LAYER:
            continue
        t = e.dxftype()
        pts: list[tuple[float, float]] = []
        try:
            if t == "LINE":
                pts = [
                    (float(e.dxf.start.x), float(e.dxf.start.y)),
                    (float(e.dxf.end.x), float(e.dxf.end.y)),
                ]
            elif t == "LWPOLYLINE":
                pts = [(float(p[0]), float(p[1])) for p in e.get_points("xy")]
                if e.closed and pts and pts[0] != pts[-1]:
                    pts = pts + [pts[0]]
            else:
                continue
        except Exception:  # noqa: BLE001
            continue
        if len(pts) >= 2:
            out.append(pts)
    return out


def render_diff(
    *,
    original_dxf: Path,
    validated_dxf: Path,
    out_png: Path,
    bbox_mm: dict[str, float] | None,
    title: str,
    dpi: int = 200,
    px_width: int = 14000,
) -> dict:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.collections import LineCollection
    from matplotlib.lines import Line2D

    doc_o = ezdxf.readfile(str(original_dxf))
    doc_v = ezdxf.readfile(str(validated_dxf))
    walls_o = _collect_wall_segs(doc_o.modelspace())
    walls_v = _collect_wall_segs(doc_v.modelspace())
    base = _collect_base_segs(doc_o.modelspace())

    keys_o = set(walls_o)
    keys_v = set(walls_v)
    kept = keys_o & keys_v
    demoted = keys_o - keys_v
    promoted = keys_v - keys_o

    kept_segs = [walls_o[k] for k in kept]
    demote_segs = [walls_o[k] for k in demoted]
    promote_segs = [walls_v[k] for k in promoted]

    if bbox_mm:
        cx0 = float(bbox_mm["xmin"])
        cy0 = float(bbox_mm["ymin"])
        cx1 = float(bbox_mm["xmax"])
        cy1 = float(bbox_mm["ymax"])
        span_y0 = max(cy1 - cy0, 1.0)
        tick = max(span_y0 * 0.025, 1000.0)
        xmin = cx0 - max(tick * 0.8, 1500)
        ymin = cy0 - max(tick * 2.5, 4000)
        xmax = cx1 + max(tick * 2.0, 3000)
        ymax = cy1 + max(tick * 6.5, 9000)
        content_bbox = (cx0, cy0, cx1, cy1, tick)
        pad = 0.0
        tight = False
    else:
        xs: list[float] = []
        ys: list[float] = []
        for segs in (base, kept_segs, demote_segs, promote_segs):
            for s in segs:
                for p in s:
                    xs.append(p[0])
                    ys.append(p[1])
        if not xs:
            xs, ys = [0.0, 1.0], [0.0, 1.0]
        xmin, xmax = min(xs), max(xs)
        ymin, ymax = min(ys), max(ys)
        pad = max(xmax - xmin, ymax - ymin) * 0.02
        content_bbox = None
        tight = True

    span_x = max(xmax - xmin, 1.0)
    span_y = max(ymax - ymin, 1.0)
    fig_w = px_width / dpi
    fig_h = fig_w * (span_y / span_x)
    fig, ax = plt.subplots(figsize=(fig_w, fig_h), dpi=dpi)

    if base:
        ax.add_collection(
            LineCollection(base, colors="#cccccc", linewidths=0.25, antialiased=True, zorder=1)
        )
    if kept_segs:
        ax.add_collection(
            LineCollection(
                kept_segs, colors="#c0392b", linewidths=0.9, antialiased=True, zorder=2, alpha=0.55
            )
        )
    if demote_segs:
        ax.add_collection(
            LineCollection(
                demote_segs, colors="#2980b9", linewidths=1.6, antialiased=True, zorder=3
            )
        )
    if promote_segs:
        ax.add_collection(
            LineCollection(
                promote_segs, colors="#27ae60", linewidths=1.6, antialiased=True, zorder=4
            )
        )

    ax.set_xlim(xmin - pad, xmax + pad)
    ax.set_ylim(ymin - pad, ymax + pad)
    ax.set_aspect("equal", adjustable="box")
    ax.axis("off")
    ax.set_facecolor("white")
    fig.patch.set_facecolor("white")

    if title and content_bbox is not None:
        cx0, cy0, cx1, cy1, tick = content_bbox
        ax.text(
            (cx0 + cx1) / 2,
            cy1 + tick * 1.6 + tick * 2.0,
            title,
            ha="center",
            va="bottom",
            color="#2c3e50",
            fontsize=36,
            fontweight="bold",
            clip_on=False,
        )

    legend = [
        Line2D([0], [0], color="#c0392b", lw=2, alpha=0.55, label=f"kept WALL ({len(kept)})"),
        Line2D([0], [0], color="#27ae60", lw=2.5, label=f"promote (+{len(promoted)})"),
        Line2D([0], [0], color="#2980b9", lw=2.5, label=f"demote (−{len(demoted)})"),
        Line2D([0], [0], color="#cccccc", lw=1.5, label="BASE"),
    ]
    ax.legend(
        handles=legend,
        loc="lower right",
        fontsize=14,
        framealpha=0.92,
        fancybox=True,
    )

    if tight:
        fig.subplots_adjust(left=0.02, right=0.98, bottom=0.02, top=0.94 if title else 0.98)
        save_kw = {"bbox_inches": "tight", "pad_inches": 0.05}
    else:
        fig.subplots_adjust(left=0.0, right=1.0, bottom=0.0, top=1.0)
        save_kw = {"bbox_inches": None, "pad_inches": 0}

    out_png.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_png, dpi=dpi, facecolor="white", edgecolor="none", **save_kw)
    plt.close(fig)

    if content_bbox is not None:
        try:
            from PIL import Image

            Image.MAX_IMAGE_PIXELS = None
            im = Image.open(out_png).convert("RGB")
            pr, pb, pt = 200, 120, 160
            w, h = im.size
            canvas = Image.new("RGB", (w + pr, h + pb + pt), (255, 255, 255))
            canvas.paste(im, (0, pt))
            canvas.save(out_png, optimize=True)
        except Exception:  # noqa: BLE001
            pass

    stats = {
        "n_kept": len(kept),
        "n_promoted": len(promoted),
        "n_demoted": len(demoted),
        "png": str(out_png),
    }
    try:
        from PIL import Image

        Image.MAX_IMAGE_PIXELS = None
        stats["png_size"] = list(Image.open(out_png).size)
    except Exception:  # noqa: BLE001
        pass
    return stats


def main() -> int:
    p = argparse.ArgumentParser(description="original vs validated WALL diff PNG")
    p.add_argument("--artifacts", type=Path, required=True)
    p.add_argument("--floor", required=True)
    p.add_argument("--dpi", type=int, default=200)
    p.add_argument("--px-width", type=int, default=14000)
    args = p.parse_args()

    floor_dir = args.artifacts / "floors" / args.floor
    orig = floor_dir / "floor_wall_original.dxf"
    val = floor_dir / "floor_wall_validated.dxf"
    if not orig.is_file():
        raise SystemExit(f"missing {orig}")
    if not val.is_file():
        raise SystemExit(f"missing {val} — correct_walls_floor 먼저 실행")

    meta: dict = {}
    for name in ("floor_wall_validated_meta.json", "floor_wall_original_meta.json"):
        mp = floor_dir / name
        if mp.is_file():
            meta = json.loads(mp.read_text(encoding="utf-8"))
            break
    bbox = meta.get("bbox_mm")
    size_m = meta.get("size_m") or {}
    title = f"{args.floor} WALL DIFF  original → validated"
    if size_m.get("width") and size_m.get("height"):
        title += f"  {float(size_m['width']):.2f}×{float(size_m['height']):.2f} m"

    out = floor_dir / "diff_original_vs_validated.png"
    print(f"diff {orig.name} vs {val.name} → {out.name}", flush=True)
    stats = render_diff(
        original_dxf=orig,
        validated_dxf=val,
        out_png=out,
        bbox_mm=bbox,
        title=title,
        dpi=args.dpi,
        px_width=args.px_width,
    )
    print(
        f"  kept={stats['n_kept']} promote={stats['n_promoted']} "
        f"demote={stats['n_demoted']} size={stats.get('png_size')}"
    )
    print(f"→ {out}")

    # also drop a small json next to it
    side = floor_dir / "diff_original_vs_validated.json"
    side.write_text(json.dumps({"floor": args.floor, **stats}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
