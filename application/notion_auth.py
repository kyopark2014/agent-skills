"""Per-user Notion (remote MCP) OAuth tokens.

Notion's hosted MCP (``https://mcp.notion.com/mcp``) uses OAuth 2.0 authorization
code + PKCE with dynamic client registration. The flow itself lives in
``mcp_oauth``; this module only supplies the Notion provider settings and keeps the
public function API used by the routes, runtime_mode and the MCP Apps host.
"""

from __future__ import annotations

try:
    from application.mcp_oauth import McpOAuth, OAuthProvider
except ImportError:  # flat layout
    from mcp_oauth import McpOAuth, OAuthProvider  # type: ignore

NOTION_MCP_URL = "https://mcp.notion.com/mcp"
NOTION_ISSUER = "https://mcp.notion.com"

PROVIDER = OAuthProvider(
    name="notion",
    mcp_url=NOTION_MCP_URL,
    issuer=NOTION_ISSUER,
    metadata_url=f"{NOTION_ISSUER}/.well-known/oauth-authorization-server",
    client_name="agent-skills",
    token_file="notion_oauth.json",
    state_dir="_notion_oauth",
    read_scope="default",
    selected_names=frozenset({"notion-remote", "notion remote", "notion_remote"}),
)
oauth = McpOAuth(PROVIDER)

token_path = oauth.token_path
start = oauth.start
finish = oauth.finish
get_access_token = oauth.get_access_token
status_for_user = oauth.status_for_user
clear_token = oauth.clear_token
mcp_selected = oauth.mcp_selected
is_token_fresh = oauth.is_token_fresh
