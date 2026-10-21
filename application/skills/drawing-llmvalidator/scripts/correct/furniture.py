"""Domain API: furniture.

Implementations currently live in ``correct._rules``; import from here
so call sites and future file moves stay stable.
"""

from __future__ import annotations

from correct._rules import (  # noqa: F401
    demote_closed_furniture_boxes,
    demote_meeting_room_interiors,
    demote_bed_ward_interiors,
    demote_hospital_beds,
    demote_kitchen_equipment,
    demote_conveyor_belt,
    demote_coffee_machine_row,
    demote_dining_hall_furniture,
    demote_cafeteria_counter_lines,
    demote_serving_counter_walls,
    demote_fitness_equipment,
    demote_landscape_walls,
    demote_parallel_grid_axis_walls,
    filter_promote_away_from_grid_axes,
    find_fitness_labels,
)
