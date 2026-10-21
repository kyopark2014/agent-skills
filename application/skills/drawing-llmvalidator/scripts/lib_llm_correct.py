#!/usr/bin/env python3
"""Vision-guided wall corrections: floor_wall_original → floor_wall_validated.

Implementation lives in the ``correct`` package:

- ``correct.orchestrator`` — ``apply_corrections`` (order / priority only)
- ``correct.primitives`` / ``correct.labels`` / ``correct.render`` — shared API
- ``correct.geom`` / ``doors`` / ``columns`` / ``cores`` / ``furniture`` /
  ``windows`` / ``protect`` / ``leak`` — domain import surfaces
- ``correct._rules`` — heuristic implementations (migrating into domain files)

This module remains the stable import path for scripts and tests.
"""

from __future__ import annotations

from correct import _rules as _rules

# ``import *`` skips leading-underscore names; tests import many helpers directly.
globals().update(
    {name: value for name, value in vars(_rules).items() if not name.startswith("__")}
)

from correct.orchestrator import apply_corrections  # noqa: E402,F401
from correct.render import load_review, render_wall_dxf_png  # noqa: E402,F401
