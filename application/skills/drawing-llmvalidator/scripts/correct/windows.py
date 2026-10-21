"""Domain API: windows.

Implementations currently live in ``correct._rules``; import from here
so call sites and future file moves stay stable.
"""

from __future__ import annotations

from correct._rules import (  # noqa: F401
    separate_observation_windows,
    separate_diagonal_observation_windows,
    promote_capped_windows,
    promote_double_sash_windows,
    promote_line_sash_windows,
    promote_strip_windows,
    promote_diagonal_strip_windows,
    promote_mullion_strip_windows,
    promote_bay_window_outlines,
)
