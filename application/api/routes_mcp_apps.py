"""API endpoints that let MCP Apps iframes call tools through the host."""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from application.api.routes_auth import require_user_id
from application import mcp_apps_host

logger = logging.getLogger("routes_mcp_apps")

router = APIRouter(prefix="/api/mcp-apps", tags=["mcp-apps"])


class CallToolRequest(BaseModel):
    mcpServer: str = Field(..., min_length=1)
    tool: str = Field(..., min_length=1)
    arguments: dict[str, Any] = Field(default_factory=dict)


@router.post("/call-tool")
async def call_tool(body: CallToolRequest, request: Request) -> dict[str, Any]:
    """Proxy `tools/call` from an MCP App iframe to the underlying MCP server."""
    require_user_id(request)
    try:
        return await mcp_apps_host.call_mcp_tool(
            body.mcpServer,
            body.tool,
            body.arguments,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("MCP App tool call failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc
