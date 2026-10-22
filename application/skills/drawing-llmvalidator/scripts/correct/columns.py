"""Domain API: columns.

Implementations currently live in ``correct._rules``; import from here
so call sites and future file moves stay stable.
"""

from __future__ import annotations

from correct._rules import (  # noqa: F401
    find_hbeam_column_entities,
    promote_hbeam_columns,
    promote_hbeam_sleeves,
    promote_wall_square_columns,
    promote_closed_square_columns,
    promote_concentric_double_square_columns,
    promote_round_columns,
    find_round_column_rings,
    trim_walls_through_round_columns,
    promote_h_symbol_columns,
    promote_column_adjacent_wall_pairs,
    demote_pictogram_columns,
    promote_end_wall_columns,
)
