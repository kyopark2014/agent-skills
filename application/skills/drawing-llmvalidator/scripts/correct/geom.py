"""Domain API: geom.

Implementations currently live in ``correct._rules``; import from here
so call sites and future file moves stay stable.
"""

from __future__ import annotations

from correct._rules import (  # noqa: F401
    find_promote_segments,
    find_demote_wall_entities,
    promote_corridor_walls,
    promote_corridor_door_flanks,
    promote_collinear_room_walls,
    promote_butt_partitions,
    promote_room_corner_returns,
    promote_wall_mate_faces,
    promote_room_row_dividers,
    promote_in_bboxes,
    promote_slanted_double_walls,
    promote_curved_corner_walls,
    promote_crossed_x_walls,
    promote_x_cover_squares,
    promote_triple_diagonal_walls,
    promote_capped_triple_walls,
    promote_diagonal_hatch_walls,
    promote_wall_end_returns,
    promote_mullion_diagonal_walls,
    promote_collinear_wall_continuations,
    promote_diagonal_wall_turns,
    promote_service_core_walls,
    promote_sanitary_fixtures,
    promote_vision_bathrooms,
    promote_vision_outer_walls,
    promote_vision_office_walls,
    promote_stair_label_walls,
    promote_nonarea_enclosures,
    promote_bathroom_label_walls,
    promote_office_label_walls,
    promote_short_connected_doubles,
    promote_projection_junctions,
    promote_shifted_corner_faces,
    promote_thick_wall_mates,
    mark_doors_and_columns,
)
