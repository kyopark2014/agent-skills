"""Per-task cancel flags for cooperative agent interruption.

Stop in the UI calls POST /api/tasks/{id}/cancel, which sets a flag checked by
the LangGraph astream loop, should_continue, call_model, and the tool node.
The same thread_id checkpoint is kept so the next user turn continues history.

bash registers its process group under that thread id. Cancel and the bash
timeout both signal the group, so a stopped command does not keep running.
"""

from __future__ import annotations

import os
import signal
import threading
import time

_lock = threading.Lock()
# task_id / runtime_session_id -> cancelled_at monotonic time
_cancelled: dict[str, float] = {}
# thread_id -> process group ids started by bash
_groups: dict[str, set[int]] = {}
_CANCEL_TTL_SECONDS = 3600


def _signal_pgroup(pgid: int, sig: int) -> None:
    if pgid <= 1:
        return
    try:
        os.killpg(pgid, sig)
    except (ProcessLookupError, PermissionError, OSError):
        pass


def track_pgroup(key: str | None, pgid: int) -> None:
    if not key or pgid <= 1:
        return
    with _lock:
        _groups.setdefault(key, set()).add(pgid)


def untrack_pgroup(key: str | None, pgid: int) -> None:
    if not key or pgid <= 1:
        return
    with _lock:
        group = _groups.get(key)
        if not group:
            return
        group.discard(pgid)
        if not group:
            _groups.pop(key, None)


def kill_pgroups(key: str | None) -> None:
    """Stop every bash process group registered for this thread."""
    if not key:
        return
    with _lock:
        pgids = list(_groups.get(key, ()))
    for pgid in pgids:
        _signal_pgroup(pgid, signal.SIGTERM)
    for pgid in pgids:
        _signal_pgroup(pgid, signal.SIGKILL)


def request_cancel(task_id: str) -> None:
    if not task_id:
        return
    with _lock:
        _cancelled[task_id] = time.monotonic()
    kill_pgroups(task_id)


def is_cancelled(task_id: str | None) -> bool:
    if not task_id:
        return False
    with _lock:
        ts = _cancelled.get(task_id)
        if ts is None:
            return False
        if time.monotonic() - ts > _CANCEL_TTL_SECONDS:
            _cancelled.pop(task_id, None)
            return False
        return True


def clear(task_id: str | None) -> None:
    if not task_id:
        return
    with _lock:
        _cancelled.pop(task_id, None)


def consume_cancelled(task_id: str | None) -> bool:
    """Return True if cancelled, clearing the flag (one-shot for late-persist skip)."""
    if not task_id:
        return False
    with _lock:
        ts = _cancelled.pop(task_id, None)
        if ts is None:
            return False
        return time.monotonic() - ts <= _CANCEL_TTL_SECONDS
