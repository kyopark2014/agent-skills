"""MCP Apps host helpers: cache ui:// HTML and proxy tool calls for the web UI."""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("mcp_apps_host")

# tool_name -> app descriptor
_APP_BY_TOOL: dict[str, dict[str, Any]] = {}


def clear() -> None:
    _APP_BY_TOOL.clear()


def register_tool_app(
    tool_name: str,
    *,
    resource_uri: str,
    html: str,
    mcp_server: str | None = None,
    csp: dict[str, Any] | None = None,
    mime_type: str | None = None,
) -> None:
    if not tool_name or not resource_uri or not html:
        return
    _APP_BY_TOOL[tool_name] = {
        "resourceUri": resource_uri,
        "html": html,
        "mcpServer": mcp_server,
        "csp": csp or {},
        "mimeType": mime_type or "text/html;profile=mcp-app",
        "tool": tool_name,
    }
    logger.info(
        "Registered MCP App UI for tool=%s uri=%s html_len=%s",
        tool_name,
        resource_uri,
        len(html),
    )


def get_tool_app(tool_name: str | None) -> dict[str, Any] | None:
    if not tool_name:
        return None
    return _APP_BY_TOOL.get(tool_name)


def _fallback_weather_app(tool_name: str) -> dict[str, Any] | None:
    """Last-resort UI for weather tools when Apps cache was not warmed."""
    if tool_name not in {"get_weather", "get_metropolitan_weather"}:
        return None
    try:
        from application.mcp_server_weather import VIEW_URI, WEATHER_HTML
    except Exception as exc:
        logger.warning("weather UI fallback import failed: %s", exc)
        return None
    register_tool_app(
        tool_name,
        resource_uri=VIEW_URI,
        html=WEATHER_HTML,
        mcp_server="weather",
        csp={"resourceDomains": ["https://www.weather.go.kr"]},
    )
    return get_tool_app(tool_name)


def build_app_payload(tool_name: str | None, tool_result_text: str) -> dict[str, Any] | None:
    app = get_tool_app(tool_name) or (
        _fallback_weather_app(tool_name) if tool_name else None
    )
    if not app:
        return None
    return {
        **app,
        "toolResult": {
            "content": [{"type": "text", "text": tool_result_text}],
        },
    }


def extract_ui_resource_uri(tool_metadata: dict[str, Any] | None) -> str | None:
    """Read `_meta.ui.resourceUri` from LangChain MCP tool metadata."""
    if not isinstance(tool_metadata, dict):
        return None
    mcp = tool_metadata.get("mcp")
    if not isinstance(mcp, dict):
        return None
    tool_meta = mcp.get("tool")
    if not isinstance(tool_meta, dict):
        return None
    meta = tool_meta.get("_meta")
    if not isinstance(meta, dict):
        return None
    ui = meta.get("ui")
    if isinstance(ui, dict):
        uri = ui.get("resourceUri")
        if isinstance(uri, str) and uri.startswith("ui://"):
            return uri
    legacy = meta.get("ui/resourceUri")
    if isinstance(legacy, str) and legacy.startswith("ui://"):
        return legacy
    return None


def extract_mcp_server_name(tool_metadata: dict[str, Any] | None) -> str | None:
    if not isinstance(tool_metadata, dict):
        return None
    mcp = tool_metadata.get("mcp")
    if not isinstance(mcp, dict):
        return None
    server = mcp.get("server")
    if isinstance(server, dict):
        name = server.get("name")
        if isinstance(name, str) and name.strip():
            return name.strip()
    return None


def _resource_text_and_meta(contents: Any) -> tuple[str | None, dict[str, Any]]:
    items = contents if isinstance(contents, list) else [contents]
    for item in items:
        text = getattr(item, "text", None)
        if text is None and isinstance(item, dict):
            text = item.get("text")
        if not isinstance(text, str) or not text:
            continue
        meta = getattr(item, "meta", None)
        if meta is None and isinstance(item, dict):
            meta = item.get("meta")
        mime = getattr(item, "mimeType", None) or getattr(item, "mime_type", None)
        if mime is None and isinstance(item, dict):
            mime = item.get("mimeType") or item.get("mime_type")
        csp = None
        if isinstance(meta, dict):
            ui = meta.get("ui")
            if isinstance(ui, dict):
                csp = ui.get("csp")
        return text, {"csp": csp or {}, "mimeType": mime}
    return None, {}


async def cache_apps_from_adapter(
    adapter: Any,
    tools: list[Any],
    *,
    fallback_server_name: str | None = None,
) -> int:
    """Read ui:// resources for tools that declare MCP Apps metadata."""
    registered = 0
    client = getattr(adapter, "_client", None)
    if client is None or not hasattr(client, "read_resource"):
        logger.warning("MCP adapter has no resource client; skipping Apps cache")
        return 0

    for tool in tools:
        metadata = getattr(tool, "metadata", None) or {}
        uri = extract_ui_resource_uri(metadata)
        if not uri:
            continue
        try:
            contents = await client.read_resource(uri)
            html, extra = _resource_text_and_meta(contents)
            if not html:
                logger.warning("Empty UI resource for %s (%s)", tool.name, uri)
                continue
            register_tool_app(
                tool.name,
                resource_uri=uri,
                html=html,
                mcp_server=extract_mcp_server_name(metadata) or fallback_server_name,
                csp=extra.get("csp"),
                mime_type=extra.get("mimeType"),
            )
            registered += 1
        except Exception as exc:
            logger.error("Failed to cache MCP App UI for %s: %s", tool.name, exc)
    return registered


async def call_mcp_tool(
    mcp_server: str,
    tool_name: str,
    arguments: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Call one MCP tool via a short-lived adapter session (for iframe refresh)."""
    from langchain.mcp import MCPAdapter

    from application import mcp_config

    config = mcp_config.load_config(mcp_server)
    if not config or "mcpServers" not in config:
        raise ValueError(f"Unknown MCP server: {mcp_server}")

    servers = config["mcpServers"]
    # Prefer exact key, else first entry
    if mcp_server in servers:
        server_params = {mcp_server: servers[mcp_server]}
    else:
        # weather config key is "weather"
        first_key = next(iter(servers))
        server_params = {first_key: servers[first_key]}
        mcp_server = first_key

    async with MCPAdapter({"mcpServers": server_params}) as adapter:
        tools = await adapter.list_tools()
        match = next((t for t in tools if t.name == tool_name), None)
        if match is None:
            raise ValueError(f"Tool not found: {tool_name}")
        result = await match.ainvoke(arguments or {})
        text = normalize_tool_result_text(result)
        return {
            "content": [{"type": "text", "text": text}],
            "isError": False,
        }


def normalize_tool_result_text(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, dict):
        if content.get("type") == "text" and isinstance(content.get("text"), str):
            return content["text"]
        # Structured tool dict → JSON
        import json

        try:
            return json.dumps(content, ensure_ascii=False)
        except Exception:
            return str(content)
    if isinstance(content, list):
        texts: list[str] = []
        for block in content:
            if isinstance(block, str):
                texts.append(block)
            elif isinstance(block, dict) and block.get("type") == "text":
                texts.append(str(block.get("text", "")))
            elif hasattr(block, "text"):
                texts.append(str(getattr(block, "text") or ""))
            else:
                texts.append(str(block))
        return "\n".join(t for t in texts if t)
    return str(content)
