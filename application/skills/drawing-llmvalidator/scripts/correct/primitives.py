"""Shared primitives API for wall correction.

Implementations currently live in ``correct._rules``; this module is the
stable import path so callers can depend on ``correct.primitives`` while
bodies migrate file-by-file.
"""

from __future__ import annotations

from correct._rules import (  # noqa: F401
    WALL_LAYER,
    BASE_LAYER,
    DOOR_LAYER,
    WINDOW_LAYER,
    COLUMN_LAYER,
    WALL_COLOR,
    BASE_COLOR,
    DOOR_COLOR,
    WINDOW_COLOR,
    COLUMN_COLOR,
    AxisSeg,
    normalize_bbox,
    normalize_bbox_list,
    iter_axis_segs,
    _entities,
    _dxftype,
    _paint_layer,
    _fold_class_layers,
    _line_xy,
    _poly_xy,
    _arc_geom,
    _ensure_scan_hooks,
    _remember_entity,
)

__all__ = [
    "WALL_LAYER",
    "BASE_LAYER",
    "DOOR_LAYER",
    "WINDOW_LAYER",
    "COLUMN_LAYER",
    "WALL_COLOR",
    "BASE_COLOR",
    "DOOR_COLOR",
    "WINDOW_COLOR",
    "COLUMN_COLOR",
    "AxisSeg",
    "normalize_bbox",
    "normalize_bbox_list",
    "iter_axis_segs",
]
