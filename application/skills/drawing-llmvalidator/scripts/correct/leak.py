"""Domain API: leak.

Implementations currently live in ``correct._rules``; import from here
so call sites and future file moves stay stable.
"""

from __future__ import annotations

from correct._rules import (  # noqa: F401
    close_leaking_wall_ends,
)
