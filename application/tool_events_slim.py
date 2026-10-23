# Copyright 2026 Amazon.com, Inc. or its affiliates
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Shrink tool_events payloads returned to the browser.

Drawing runs store large bash/skills blobs. Shipping them verbatim on every
GET /messages (and /run draft) makes task entry slow over CloudFront.
"""

from __future__ import annotations

import json
from typing import Any

# Per-field cap for string/serialized tool payload fields.
TOOL_EVENT_FIELD_MAX_CHARS = 2_000
# Safety cap so a single turn cannot dominate the response.
TOOL_EVENTS_MAX_COUNT = 300


def _truncate_text(text: str, max_chars: int) -> str:
    if max_chars <= 0 or len(text) <= max_chars:
        return text
    omitted = len(text) - max_chars
    suffix = f"\n...[truncated {omitted} chars]"
    keep = max(0, max_chars - len(suffix))
    return text[:keep] + suffix


def _slim_value(value: Any, max_chars: int = TOOL_EVENT_FIELD_MAX_CHARS) -> Any:
    if isinstance(value, str):
        return _truncate_text(value, max_chars)
    if isinstance(value, (dict, list)):
        try:
            raw = json.dumps(value, ensure_ascii=False, default=str)
        except TypeError:
            raw = str(value)
        if len(raw) <= max_chars:
            return value
        return _truncate_text(raw, max_chars)
    return value


def slim_tool_events(events: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """Return a UI-safe copy of tool_events (truncated fields, recent N)."""
    if not events:
        return []
    selected = list(events)
    if len(selected) > TOOL_EVENTS_MAX_COUNT:
        selected = selected[-TOOL_EVENTS_MAX_COUNT:]
    out: list[dict[str, Any]] = []
    for event in selected:
        if not isinstance(event, dict):
            continue
        slim = dict(event)
        if "data" in slim:
            slim["data"] = _slim_value(slim.get("data"))
        if "content" in slim:
            slim["content"] = _slim_value(slim.get("content"))
        if "input" in slim:
            slim["input"] = _slim_value(slim.get("input"))
        out.append(slim)
    return out


def slim_messages_for_api(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Copy messages with slimmed tool_events for HTTP responses."""
    out: list[dict[str, Any]] = []
    for message in messages:
        row = dict(message)
        row["tool_events"] = slim_tool_events(row.get("tool_events") or [])
        out.append(row)
    return out
