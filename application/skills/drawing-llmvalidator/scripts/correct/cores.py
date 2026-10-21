"""Domain API: cores.

Implementations currently live in ``correct._rules``; import from here
so call sites and future file moves stay stable.
"""

from __future__ import annotations

from correct._rules import (  # noqa: F401
    find_stair_cores,
    promote_stair_enclosure_walls,
    protect_stair_enclosure_entities,
    find_elevator_shafts,
    find_elevator_banks,
    promote_elevator_enclosure_walls,
    demote_elevator_door_back_faces,
    filter_promote_away_from_elevator_doors,
    protect_elevator_enclosure_entities,
    find_open_hall_labels,
    collect_open_hall_regions,
    demote_open_hall_center_walls,
    filter_promote_away_from_open_halls,
    promote_stage_enclosure_walls,
)
