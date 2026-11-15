"""API endpoints that let MCP Apps iframes call tools through the host."""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from application.api.routes_auth import require_user_id
from application import mcp_app_resources, mcp_apps_host

logger = logging.getLogger("routes_mcp_apps")

router = APIRouter(prefix="/api/mcp-apps", tags=["mcp-apps"])


def _root_cause(exc: BaseException) -> str:
    """The real error behind anyio/MCP task-group wrappers."""
    while isinstance(exc, BaseExceptionGroup) and exc.exceptions:
        exc = exc.exceptions[0]
    return str(exc) or exc.__class__.__name__


class CallToolRequest(BaseModel):
    mcpServer: str = Field(..., min_length=1)
    tool: str = Field(..., min_length=1)
    arguments: dict[str, Any] = Field(default_factory=dict)


@router.post("/call-tool")
async def call_tool(body: CallToolRequest, request: Request) -> dict[str, Any]:
    """Proxy `tools/call` from an MCP App iframe to the underlying MCP server."""
    user_id = require_user_id(request)
    try:
        return await mcp_apps_host.call_mcp_tool(
            body.mcpServer,
            body.tool,
            body.arguments,
            user_id=user_id,
        )
    except ValueError as exc:
        logger.warning(
            "mcp-apps call rejected: server=%r tool=%r reason=%s",
            body.mcpServer,
            body.tool,
            exc,
        )
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("MCP App tool call failed")
        raise HTTPException(status_code=500, detail=_root_cause(exc)) from exc


@router.get("/resource")
async def read_resource(server: str, uri: str, request: Request) -> dict[str, Any]:
    """Read a ``ui://`` resource the way an MCP Apps host does (cached server-side)."""
    user_id = require_user_id(request)
    try:
        return await mcp_app_resources.read(server, uri, user_id)
    except ValueError as exc:
        logger.warning(
            "mcp-apps resource rejected: server=%r uri=%r reason=%s", server, uri, exc
        )
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.warning(
            "MCP App resource read failed: server=%r uri=%r: %s",
            server,
            uri,
            _root_cause(exc),
        )
        raise HTTPException(status_code=502, detail=_root_cause(exc)) from exc
