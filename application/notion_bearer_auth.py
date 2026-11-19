"""httpx2 Auth that injects the current Notion (remote MCP) access token per request."""

from __future__ import annotations

import os

import httpx2


class NotionBearerAuth(httpx2.Auth):
    """Read the token from chat/env on every request so refreshed tokens apply without rebuild."""

    def auth_flow(self, request: httpx2.Request):
        token = ""
        try:
            import chat

            token = (getattr(chat, "notion_access_token", None) or "").strip()
        except Exception:
            token = ""
        if not token:
            token = (os.environ.get("NOTION_REMOTE_ACCESS_TOKEN") or "").strip()
        if token:
            request.headers["Authorization"] = f"Bearer {token}"
        yield request
