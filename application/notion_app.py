"""MCP Apps wiring for the remote Notion MCP.

Notion's hosted MCP ships an MCP App (``ui://notion/page``), but it embeds
``app.notion.com`` in an iframe that Notion only allows from claude.ai / chatgpt
sandboxes (``frame-ancestors``), and no tool's ``_meta.ui`` points at it. So agent-skills
renders Notion tool results with its own viewer.

The viewer's HTML lives with the web app (``application/mcp_app_views``) and is served
through ``GET /api/mcp-apps/resource``; here we only tell the host which tools it
belongs to. Keep this URI in sync with ``application/mcp_app_resources.py``.
"""
from __future__ import annotations

try:
    from application import mcp_apps_host
except ImportError:
    import mcp_apps_host  # type: ignore

SERVER_NAME = "notion-remote"
VIEW_URI = "ui://agent-skills/notion-viewer.html"

# Tools whose results the viewer can render (all read-only).
APP_TOOLS = (
    "notion-search",
    "notion-fetch",
    "notion-list-recent-pages",
    "notion-list-favorite-pages",
    "notion-list-private-pages",
    "notion-list-shared-pages",
)


def register_notion_apps() -> int:
    """Attach the viewer to the Notion read tools; returns how many were registered."""
    for tool_name in APP_TOOLS:
        mcp_apps_host.register_tool_app(
            tool_name,
            resource_uri=VIEW_URI,
            mcp_server=SERVER_NAME,
        )
    return len(APP_TOOLS)
