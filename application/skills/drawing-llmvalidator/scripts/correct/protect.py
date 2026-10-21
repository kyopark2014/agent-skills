"""Domain API: protect.

Implementations currently live in ``correct._rules``; import from here
so call sites and future file moves stay stable.
"""

from __future__ import annotations

from correct._rules import (  # noqa: F401
    protect_corridor_wall_entities,
    demote_in_bboxes,
    review_demote_keep_ids,
    door_side_wall_ids,
)
