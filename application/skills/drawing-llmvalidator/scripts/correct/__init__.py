"""Wall correction package: domain modules + single orchestrator."""

from __future__ import annotations

from correct.orchestrator import apply_corrections
from correct.render import load_review, render_wall_dxf_png
from correct.primitives import (
    WALL_LAYER,
    BASE_LAYER,
    DOOR_LAYER,
    WINDOW_LAYER,
    COLUMN_LAYER,
    normalize_bbox,
    normalize_bbox_list,
    iter_axis_segs,
    AxisSeg,
)

__all__ = [
    "apply_corrections",
    "load_review",
    "render_wall_dxf_png",
    "WALL_LAYER",
    "BASE_LAYER",
    "DOOR_LAYER",
    "WINDOW_LAYER",
    "COLUMN_LAYER",
    "normalize_bbox",
    "normalize_bbox_list",
    "iter_axis_segs",
    "AxisSeg",
]
