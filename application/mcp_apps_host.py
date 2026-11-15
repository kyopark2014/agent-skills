"""MCP Apps host helpers: cache ui:// HTML and proxy tool calls for the web UI."""
from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from typing import Any

logger = logging.getLogger("mcp_apps_host")

# tool_name -> app descriptor
_APP_BY_TOOL: dict[str, dict[str, Any]] = {}

# Servers whose ui:// resources the web host reads via GET /api/mcp-apps/resource
# (HTML is not inlined into every tool result).
HOST_FETCHED_SERVERS = frozenset({"weather", "notion-remote", "use-vault", "excalidraw"})

NOTION_IFRAME_TOOLS = frozenset(
    {
        "notion-search",
        "notion-fetch",
        "notion-list-recent-pages",
        "notion-list-favorite-pages",
        "notion-list-private-pages",
        "notion-list-shared-pages",
    }
)

# use-vault (ob-note): read tools + in-app Save (vault_write). Other mutations stay agent-only.
VAULT_IFRAME_TOOLS = frozenset(
    {
        "vault_tree",
        "vault_list",
        "vault_read",
        "vault_search",
        "vault_graph",
        "vault_backlinks",
        "vault_asset",
        "vault_render",
        "vault_write",
    }
)

EXCALIDRAW_IFRAME_TOOLS = frozenset(
    {
        "export_to_excalidraw",
        "save_checkpoint",
        "read_checkpoint",
    }
)
EXCALIDRAW_MCP_URL = "https://mcp.excalidraw.com/mcp"

_REMOTE_OAUTH_MCPS: dict[str, tuple[str, frozenset[str]]] = {
    "notion-remote": ("notion_auth", NOTION_IFRAME_TOOLS),
}
# Servers reached with local AWS credentials / ECS task role (SigV4).
_IAM_MCPS: dict[str, frozenset[str]] = {"use-vault": VAULT_IFRAME_TOOLS}
_PUBLIC_HTTP_MCPS: dict[str, tuple[str, frozenset[str]]] = {
    "excalidraw": (EXCALIDRAW_MCP_URL, EXCALIDRAW_IFRAME_TOOLS),
}
_ALLOWED_TOOLS: dict[str, frozenset[str]] = {
    **{k: v[1] for k, v in _REMOTE_OAUTH_MCPS.items()},
    **_IAM_MCPS,
    **{k: v[1] for k, v in _PUBLIC_HTTP_MCPS.items()},
}


def resolve_vault_actor_id(user_id: str | None) -> str:
    """Account id for use-vault ``actor_id`` (ob-note vault owner).

    Prefer ``use_vault_actor_id`` when the local login id (e.g. ``admin``) is not
    the same as the Google email that owns the vault on the ob-note server.
    """
    try:
        from application import utils

        cfg = utils.load_config() or {}
    except Exception:
        cfg = {}
    override = (
        (cfg.get("use_vault_actor_id") if isinstance(cfg, dict) else None)
        or os.environ.get("use_vault_actor_id")
        or os.environ.get("USE_VAULT_ACTOR_ID")
        or ""
    ).strip()
    if override:
        return override
    return (user_id or "").strip() or "default"


def clear() -> None:
    _APP_BY_TOOL.clear()


def register_tool_app(
    tool_name: str,
    *,
    resource_uri: str,
    html: str | None = None,
    mcp_server: str | None = None,
    csp: dict[str, Any] | None = None,
    mime_type: str | None = None,
) -> None:
    if not tool_name or not resource_uri:
        return
    if not html and (mcp_server or "") not in HOST_FETCHED_SERVERS:
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
        len(html or ""),
    )


def get_tool_app(tool_name: str | None) -> dict[str, Any] | None:
    if not tool_name:
        return None
    return _APP_BY_TOOL.get(tool_name)


def _fallback_weather_app(tool_name: str) -> dict[str, Any] | None:
    """Last-resort UI for weather tools when Apps cache was not warmed."""
    if tool_name not in {
        "get_weather",
        "get_short_term_forecast",
        "get_metropolitan_weather",
    }:
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


def build_app_payload(
    tool_name: str | None,
    tool_result_text: str,
    structured_content: Any = None,
    tool_input: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    app = get_tool_app(tool_name) or (
        _fallback_weather_app(tool_name) if tool_name else None
    )
    if not app:
        return None
    tool_result: dict[str, Any] = {
        "content": [{"type": "text", "text": tool_result_text}],
    }
    # MCP Apps UIs (e.g. use-vault) read ``structuredContent`` (kind/list/files).
    if structured_content is not None:
        tool_result["structuredContent"] = structured_content
    payload: dict[str, Any] = {**app, "toolResult": tool_result}
    if payload.get("mcpServer") in HOST_FETCHED_SERVERS:
        # The web host reads the ui:// resource itself (and applies its _meta.ui.csp).
        payload.pop("html", None)
        payload.pop("csp", None)
    if isinstance(tool_input, dict):
        payload["toolInput"] = tool_input
    return payload


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


def is_app_only_tool(tool_metadata: dict[str, Any] | None) -> bool:
    """True when a tool is meant for the MCP App iframe only."""
    if not isinstance(tool_metadata, dict):
        return False
    mcp = tool_metadata.get("mcp")
    tool_meta = mcp.get("tool") if isinstance(mcp, dict) else None
    meta = tool_meta.get("_meta") if isinstance(tool_meta, dict) else None
    ui = meta.get("ui") if isinstance(meta, dict) else None
    visibility = ui.get("visibility") if isinstance(ui, dict) else None
    return isinstance(visibility, list) and bool(visibility) and "model" not in visibility


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
        if fallback_server_name in HOST_FETCHED_SERVERS:
            register_tool_app(
                tool.name,
                resource_uri=uri,
                mcp_server=fallback_server_name,
            )
            registered += 1
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
                mcp_server=fallback_server_name or extract_mcp_server_name(metadata),
                csp=extra.get("csp"),
                mime_type=extra.get("mimeType"),
            )
            registered += 1
        except Exception as exc:
            logger.error("Failed to cache MCP App UI for %s: %s", tool.name, exc)
    return registered


def normalize_server(name: str | None) -> str:
    return (name or "").strip().lower()


def _use_vault_endpoint() -> tuple[str, str]:
    """``(streamable-http URL, region)`` for the use-vault AgentCore Runtime."""
    from application import mcp_config

    cfg = mcp_config.config if isinstance(getattr(mcp_config, "config", None), dict) else {}
    try:
        from application import utils

        loaded = utils.load_config() or {}
        if isinstance(loaded, dict):
            cfg = {**cfg, **loaded}
    except Exception:
        pass
    region = (
        cfg.get("use_vault_mcp_region")
        or cfg.get("region")
        or os.environ.get("AWS_REGION")
        or "us-west-2"
    )
    url = (mcp_config.get_use_vault_mcp_url() or "").strip()
    if not url:
        raise ValueError(
            "use-vault MCP runtime was not found (set use_vault_mcp_url / use_vault_mcp_runtime_arn)"
        )
    return url, str(region)


@asynccontextmanager
async def remote_session(server: str, user_id: str | None):
    """Open an MCP ClientSession to a hosted MCP for iframe resource/tool proxying."""
    import asyncio
    import importlib

    if (
        server not in _REMOTE_OAUTH_MCPS
        and server not in _IAM_MCPS
        and server not in _PUBLIC_HTTP_MCPS
    ):
        raise ValueError(f"No remote MCP session for server: {server}")
    if not user_id:
        raise ValueError("Sign in required")

    import mcp
    from mcp.client.streamable_http import create_mcp_http_client, streamable_http_client

    if server in _PUBLIC_HTTP_MCPS:
        url, _allowed = _PUBLIC_HTTP_MCPS[server]
        http = create_mcp_http_client(timeout=None)
        async with http:
            async with streamable_http_client(url, http_client=http) as streams:
                async with mcp.ClientSession(streams[0], streams[1]) as session:
                    await session.initialize()
                    yield session
        return

    if server in _IAM_MCPS:
        from application import agentcore_sigv4_auth

        url, region = await asyncio.to_thread(_use_vault_endpoint)
        http = create_mcp_http_client(
            auth=agentcore_sigv4_auth.AgentCoreSigV4Auth(region),
            timeout=None,
        )
        async with http:
            async with streamable_http_client(url, http_client=http) as streams:
                async with mcp.ClientSession(streams[0], streams[1]) as session:
                    await session.initialize()
                    yield session
        return

    module_name, _allowed = _REMOTE_OAUTH_MCPS[server]
    try:
        auth = importlib.import_module(f"application.{module_name}")
    except ImportError:
        auth = importlib.import_module(module_name)

    token = await asyncio.to_thread(auth.get_access_token, user_id)
    if not token:
        raise ValueError(f"{server} is not connected. Connect it from the MCP menu.")

    http = create_mcp_http_client(headers={"Authorization": f"Bearer {token}"})
    async with http:
        async with streamable_http_client(auth.PROVIDER.mcp_url, http_client=http) as streams:
            async with mcp.ClientSession(streams[0], streams[1]) as session:
                await session.initialize()
                yield session


async def _call_remote_tool(
    server: str,
    user_id: str | None,
    tool_name: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    allowed = _ALLOWED_TOOLS[server]
    if tool_name not in allowed:
        raise ValueError(f"{server} tool not allowed from MCP Apps: {tool_name}")
    if not user_id:
        raise ValueError("Sign in required")
    if server == "use-vault":
        # Iframe is untrusted: always act as the signed-in / configured vault owner.
        arguments = {**arguments, "actor_id": resolve_vault_actor_id(user_id)}
    async with remote_session(server, user_id) as session:
        result = await session.call_tool(tool_name, arguments)
    # Keep structuredContent / image blocks / _meta for MCP Apps UIs.
    return result.model_dump(by_alias=True, exclude_none=True, mode="json")


async def call_mcp_tool(
    mcp_server: str,
    tool_name: str,
    arguments: dict[str, Any] | None = None,
    user_id: str | None = None,
) -> dict[str, Any]:
    """Call one MCP tool (iframe refresh / in-app actions)."""
    from langchain.mcp import MCPAdapter

    from application import mcp_config

    arguments = arguments or {}
    server = normalize_server(mcp_server)
    name = (tool_name or "").strip()

    if server in _ALLOWED_TOOLS:
        return await _call_remote_tool(server, user_id, name, arguments)

    config = mcp_config.load_config(mcp_server)
    if not config or "mcpServers" not in config:
        raise ValueError(f"Unknown MCP server: {mcp_server}")

    servers = config["mcpServers"]
    if mcp_server in servers:
        server_params = {mcp_server: servers[mcp_server]}
    else:
        first_key = next(iter(servers))
        server_params = {first_key: servers[first_key]}
        mcp_server = first_key

    # Convert streamable_http auth_type into MCPAdapter auth (e.g. SigV4).
    try:
        from application import langgraph_agent

        converted = langgraph_agent.load_multiple_mcp_server_parameters(
            {"mcpServers": server_params}
        )
        if converted:
            server_params = converted
    except Exception as exc:
        logger.warning("MCP params conversion skipped: %s", exc)

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
