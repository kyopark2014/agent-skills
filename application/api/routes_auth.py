"""Session auth — local User ID plus optional Google OAuth."""

from __future__ import annotations

import base64
import json
import logging
import os
import urllib.error
import urllib.parse
import urllib.request

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field

try:
    from application import utils
except ImportError:
    import utils  # type: ignore

logger = logging.getLogger("routes_auth")

router = APIRouter(prefix="/api/session", tags=["session"])

SESSION_COOKIE = "agent_user_id"
TOKENINFO_URL = "https://oauth2.googleapis.com/tokeninfo"
# agentic-work and peers store opaque HMAC cookies under the same name.
_SIGNED_COOKIE_PREFIX = "v1."
_MAX_PLAIN_USER_ID_LEN = 128


class SessionRequest(BaseModel):
    credential: str | None = Field(
        default=None, description="Google ID Token (JWT)"
    )
    access_token: str | None = Field(
        default=None, description="Google OAuth access token"
    )
    user_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=128,
        description="Local-only user id when auth bypass is enabled",
    )


class SessionResponse(BaseModel):
    user_id: str
    name: str | None = None
    picture: str | None = None
    llm_gateway_ready: bool = False
    knowledge_graph_enabled: bool = True
    graph_pattern: str = "pattern1"


class SessionSettingsPatch(BaseModel):
    knowledge_graph_enabled: bool | None = None
    graph_pattern: str | None = None


def _google_client_id() -> str:
    cfg = utils.load_config()
    return (cfg.get("google_client_id") or "").strip()


def _env_bypass_flag() -> bool:
    return os.environ.get("ALLOW_LOCAL_AUTH_BYPASS", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def is_loopback_request(request: Request) -> bool:
    """True only for direct localhost access. Ignores X-Forwarded-Host."""
    host = (request.headers.get("host") or "").split("%")[0]
    hostname = host.split(":")[0].strip().lower().strip("[]")
    return hostname in {"localhost", "127.0.0.1", "::1"}


def local_auth_bypass_enabled(request: Request) -> bool:
    """Allow plain User ID login when Google is not required.

    True when:
    - ALLOW_LOCAL_AUTH_BYPASS is set,
    - request Host is loopback, or
    - google_client_id is not configured (agent-skills default).
    """
    if _env_bypass_flag() or is_loopback_request(request):
        return True
    return not bool(_google_client_id())


def _llm_gateway_ready() -> bool:
    cfg = utils.load_config()
    url = (cfg.get("llm_gateway_url") or "").strip()
    key = (cfg.get("llm_gateway_key") or "").strip()
    return bool(url and key)


def verify_google_token(token: str, client_id: str) -> dict:
    url = f"{TOKENINFO_URL}?id_token={urllib.parse.quote(token)}"
    req = urllib.request.Request(url)
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            idinfo = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        raise ValueError(f"Token verification failed ({e.code}): {body}") from e
    except Exception as e:
        raise ValueError(f"Token verification request failed: {e}") from e

    if idinfo.get("aud") != client_id:
        raise ValueError(f"Invalid audience: {idinfo.get('aud')}")
    email = (idinfo.get("email") or "").strip()
    if not email:
        raise ValueError("Google token missing email")
    return idinfo


def verify_google_access_token(token: str, client_id: str) -> dict:
    info_url = f"{TOKENINFO_URL}?access_token={urllib.parse.quote(token)}"
    req = urllib.request.Request(info_url)
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            idinfo = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        raise ValueError(f"Token verification failed ({e.code}): {body}") from e
    except Exception as e:
        raise ValueError(f"Token verification request failed: {e}") from e

    aud = idinfo.get("aud") or idinfo.get("azp")
    if aud != client_id:
        raise ValueError(f"Invalid audience: {aud}")
    email = (idinfo.get("email") or "").strip()
    if not email:
        raise ValueError("Google token missing email")
    return idinfo


def _set_user_cookie(response: Response, user_id: str) -> None:
    response.set_cookie(
        key=SESSION_COOKIE,
        value=user_id,
        httponly=True,
        samesite="lax",
        max_age=60 * 60 * 24 * 365,
    )


def _uid_from_signed_cookie(raw: str) -> str | None:
    """Extract uid from opaque ``v1.<payload_b64>.<sig>`` cookies (e.g. agentic-work).

    agent-skills itself stores plain user_id values. When the browser still has a
    signed peer-app cookie under the same name, decode the payload so artifacts
    land under ``{uid}/`` instead of the full token string.
    """
    parts = raw.split(".")
    if len(parts) != 3 or parts[0] != "v1":
        return None
    try:
        padding = "=" * (-len(parts[1]) % 4)
        payload = json.loads(base64.urlsafe_b64decode(parts[1] + padding))
    except Exception:
        logger.warning("Ignoring undecodable signed session cookie")
        return None
    uid = (payload.get("uid") or "").strip()
    if not uid or len(uid) > _MAX_PLAIN_USER_ID_LEN:
        return None
    return uid


def resolve_cookie_user_id(raw: str | None) -> str | None:
    """Normalize cookie value to a plain user_id (never return a signed token)."""
    value = (raw or "").strip()
    if not value:
        return None
    if value.startswith(_SIGNED_COOKIE_PREFIX):
        return _uid_from_signed_cookie(value)
    if len(value) > _MAX_PLAIN_USER_ID_LEN:
        logger.warning("Ignoring oversized session cookie (%d chars)", len(value))
        return None
    return value


def get_optional_user_id(request: Request) -> str | None:
    return resolve_cookie_user_id(request.cookies.get(SESSION_COOKIE))


def _kick_graph_job(user_id: str, *, force: bool = False) -> None:
    """Fire-and-forget background graph extract.

    When force=False, respects fingerprint/cooldown/running lock.
    When force=True (e.g. Knowledge Graph toggled on), bypass those skips.
    """
    if not utils.is_knowledge_graph_enabled(user_id):
        logger.info("Knowledge Graph disabled for %s — skip extract", user_id)
        return
    try:
        from application.graph_jobs import ensure_graph_job

        ensure_graph_job(user_id, force=force)
    except Exception:
        logger.exception("Failed to schedule graph job for %s", user_id)


def _session_response(
    user_id: str,
    *,
    name: str | None = None,
    picture: str | None = None,
    llm_gateway_ready: bool | None = None,
) -> SessionResponse:
    settings = utils.load_user_settings(user_id)
    return SessionResponse(
        user_id=user_id,
        name=name,
        picture=picture,
        llm_gateway_ready=(
            _llm_gateway_ready() if llm_gateway_ready is None else llm_gateway_ready
        ),
        knowledge_graph_enabled=bool(
            settings.get("knowledge_graph_enabled", True)
        ),
        graph_pattern=utils.normalize_graph_pattern(
            settings.get("graph_pattern", utils.DEFAULT_GRAPH_PATTERN)
        ),
    )


@router.post("", response_model=SessionResponse)
def set_session(body: SessionRequest, request: Request, response: Response) -> SessionResponse:
    credential = (body.credential or "").strip()
    access_token = (body.access_token or "").strip()
    local_user_id = (body.user_id or "").strip()
    gateway_ready = _llm_gateway_ready()

    if credential or access_token:
        client_id = _google_client_id()
        if not client_id:
            raise HTTPException(
                status_code=500, detail="google_client_id is not configured"
            )
        try:
            if credential:
                idinfo = verify_google_token(credential, client_id)
            else:
                idinfo = verify_google_access_token(access_token, client_id)
        except ValueError as e:
            logger.warning("Google login rejected: %s", e)
            raise HTTPException(status_code=401, detail="Invalid Google credential") from e

        user_id = idinfo["email"].strip()
        _set_user_cookie(response, user_id)
        utils.ensure_user_artifacts_dir(user_id)
        utils.ensure_user_skills_dir(user_id)
        utils.ensure_user_skills_list(user_id)
        try:
            utils.ensure_user_graph_dir(user_id)
        except Exception:
            logger.exception("Failed to ensure graph dir for %s", user_id)
        try:
            utils.ensure_user_documents_dir(user_id)
        except Exception:
            logger.exception("Failed to ensure documents dir for %s", user_id)
        _kick_graph_job(user_id)
        logger.info("Google login success: %s (llm_gateway_ready=%s)", user_id, gateway_ready)
        return _session_response(
            user_id,
            name=(idinfo.get("name") or None),
            picture=(idinfo.get("picture") or None),
            llm_gateway_ready=gateway_ready,
        )

    if local_user_id:
        if not local_auth_bypass_enabled(request):
            raise HTTPException(
                status_code=403,
                detail="Local auth bypass is disabled",
            )
        _set_user_cookie(response, local_user_id)
        utils.ensure_user_artifacts_dir(local_user_id)
        utils.ensure_user_skills_dir(local_user_id)
        utils.ensure_user_skills_list(local_user_id)
        try:
            utils.ensure_user_graph_dir(local_user_id)
        except Exception:
            logger.exception("Failed to ensure graph dir for %s", local_user_id)
        try:
            utils.ensure_user_documents_dir(local_user_id)
        except Exception:
            logger.exception("Failed to ensure documents dir for %s", local_user_id)
        _kick_graph_job(local_user_id)
        logger.info(
            "Local auth bypass login: %s (llm_gateway_ready=%s)",
            local_user_id,
            gateway_ready,
        )
        return _session_response(local_user_id, llm_gateway_ready=gateway_ready)

    raise HTTPException(
        status_code=400, detail="credential, access_token, or user_id is required"
    )


@router.get("", response_model=SessionResponse | None)
def get_session(request: Request, response: Response) -> SessionResponse | None:
    raw_cookie = (request.cookies.get(SESSION_COOKIE) or "").strip()
    user_id = resolve_cookie_user_id(raw_cookie)
    if not user_id:
        return None
    # Rewrite opaque peer-app cookies to plain user_id for this app.
    if raw_cookie.startswith(_SIGNED_COOKIE_PREFIX) and raw_cookie != user_id:
        _set_user_cookie(response, user_id)
        logger.info("Normalized signed session cookie to user_id=%s", user_id)
    # Ensure workspace survives process restarts for an existing cookie session
    utils.ensure_user_artifacts_dir(user_id)
    utils.ensure_user_skills_dir(user_id)
    utils.ensure_user_skills_list(user_id)
    try:
        utils.ensure_user_graph_dir(user_id)
    except Exception:
        logger.exception("Failed to ensure graph dir for %s", user_id)
    try:
        utils.ensure_user_documents_dir(user_id)
    except Exception:
        logger.exception("Failed to ensure documents dir for %s", user_id)
    # Session restore (e.g. after server restart): start extract when fingerprint
    # is missing/stale. ensure_graph_job no-ops when source is unchanged.
    _kick_graph_job(user_id)
    return _session_response(user_id)


@router.patch("/settings", response_model=SessionResponse)
def patch_session_settings(
    body: SessionSettingsPatch, request: Request
) -> SessionResponse:
    """Update per-user feature settings (e.g. Knowledge Graph toggle)."""
    user_id = require_user_id(request)
    updates: dict[str, object] = {}
    if body.knowledge_graph_enabled is not None:
        updates["knowledge_graph_enabled"] = body.knowledge_graph_enabled
    if body.graph_pattern is not None:
        updates["graph_pattern"] = utils.normalize_graph_pattern(body.graph_pattern)
    prev_pattern = utils.get_graph_pattern(user_id)
    if updates:
        utils.save_user_settings(user_id, **updates)
    # Turning Knowledge Graph on force-runs extract (bypass unchanged/cooldown skips).
    if updates.get("knowledge_graph_enabled") is True:
        _kick_graph_job(user_id, force=True)
    new_pattern = updates.get("graph_pattern")
    if (
        isinstance(new_pattern, str)
        and new_pattern != prev_pattern
        and utils.is_knowledge_graph_enabled(user_id)
    ):
        try:
            from application.graph_jobs import republish_graph_html

            republish_graph_html(user_id, pattern=new_pattern)
        except Exception:
            logger.exception("Failed to republish graph HTML for %s", user_id)
    return _session_response(user_id)


@router.delete("", status_code=204, response_model=None)
def clear_session(response: Response) -> None:
    response.delete_cookie(key=SESSION_COOKIE, samesite="lax")


class McpOAuthStatusResponse(BaseModel):
    connected: bool = False
    fresh: bool = False
    expires_at: float | None = None
    updated_at: str | None = None
    write: bool = False


class McpOAuthAuthorizeResponse(BaseModel):
    url: str
    # True when the provider only accepts loopback redirects and this deployment is not
    # loopback: the user must paste the post-approval address-bar URL to /exchange.
    manual: bool = False


class McpOAuthExchangeRequest(BaseModel):
    url: str = Field(..., min_length=1, max_length=4096)


# Hosted MCP servers connected through per-user OAuth (see mcp_oauth.py).
_OAUTH_MCP_MODULES = {"notion": "notion_auth"}


def _oauth_module(name: str):
    import importlib

    module = _OAUTH_MCP_MODULES[name]
    try:
        return importlib.import_module(f"application.{module}")
    except ImportError:
        return importlib.import_module(module)


def _cookie_secure(request: Request) -> bool:
    forwarded = (request.headers.get("x-forwarded-proto") or "").split(",")[0].strip()
    if forwarded:
        return forwarded.lower() == "https"
    return request.url.scheme == "https"


def _oauth_redirect_uri(request: Request, name: str) -> str:
    """Public callback URL (must be identical for authorize + token exchange)."""
    path = f"/api/session/{name}/callback"
    if not is_loopback_request(request):
        sharing = (utils.load_config().get("sharing_url") or "").strip().rstrip("/")
        if sharing.startswith("https://"):
            return f"{sharing}{path}"
    secure = _cookie_secure(request)
    host = (
        request.headers.get("x-forwarded-host")
        or request.headers.get("host")
        or request.url.netloc
    )
    scheme = "https" if secure else "http"
    return f"{scheme}://{host}{path}"


def _register_oauth_mcp_routes(name: str) -> None:
    label = name.capitalize()

    def get_status(request: Request) -> McpOAuthStatusResponse:
        user_id = require_user_id(request)
        return McpOAuthStatusResponse(**_oauth_module(name).status_for_user(user_id))

    def authorize(request: Request, write: bool = False) -> McpOAuthAuthorizeResponse:
        """Return the OAuth URL; the browser opens it in a popup."""
        user_id = require_user_id(request)
        provider = _oauth_module(name).PROVIDER
        manual = bool(provider.loopback_only and not is_loopback_request(request))
        redirect_uri = (
            provider.manual_redirect_uri if manual else _oauth_redirect_uri(request, name)
        )
        try:
            url = _oauth_module(name).start(user_id, redirect_uri, write=write)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        except Exception as e:
            logger.exception("%s authorize failed", label)
            raise HTTPException(
                status_code=502, detail=f"{label} OAuth start failed: {e}"
            ) from e
        return McpOAuthAuthorizeResponse(url=url, manual=manual)

    def exchange(
        body: McpOAuthExchangeRequest, request: Request
    ) -> McpOAuthStatusResponse:
        """Finish a manual flow from the URL the user pasted after approving."""
        user_id = require_user_id(request)
        auth = _oauth_module(name)
        try:
            state, code, iss = auth.oauth.parse_redirect(body.url)
            auth.oauth.finish(state, code, iss, expected_user_id=user_id)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        except Exception as e:
            logger.warning("%s manual exchange failed: %s", label, e)
            raise HTTPException(
                status_code=502, detail=f"{label} 연결에 실패했습니다. 다시 시도하세요."
            ) from e
        return McpOAuthStatusResponse(**auth.status_for_user(user_id))

    def callback(
        code: str | None = None,
        state: str | None = None,
        iss: str | None = None,
        error: str | None = None,
    ) -> Response:
        """OAuth redirect target. Identity comes from ``state`` (single-use, server-side)."""
        ok = False
        message = error or f"{label} connection failed"
        if not error and code and state:
            try:
                _oauth_module(name).finish(state, code, iss)
                ok = True
                message = "connected"
            except Exception as e:
                logger.warning("%s OAuth callback failed: %s", label, e)
                message = f"{label} 연결에 실패했습니다. 다시 시도하세요."
        payload = json.dumps({"type": f"{name}-oauth", "ok": ok, "message": message})
        title = f"{label} 연결됨" if ok else f"{label} 연결 실패"
        html = (
            "<!doctype html><meta charset=utf-8><title>%s</title>"
            "<body style=\"font-family:system-ui;padding:2rem\"><p>%s. 이 창은 닫아도 됩니다.</p>"
            "<script>try{window.opener&&window.opener.postMessage(%s,window.location.origin)}"
            "catch(e){}window.close()</script></body>"
        ) % (title, title, payload.replace("<", "\\u003c"))
        return Response(
            content=html, media_type="text/html", status_code=200 if ok else 400
        )

    def disconnect(request: Request) -> None:
        user_id = require_user_id(request)
        _oauth_module(name).clear_token(user_id)

    base = f"/{name}"
    router.add_api_route(
        base, get_status, methods=["GET"], response_model=McpOAuthStatusResponse
    )
    router.add_api_route(
        f"{base}/authorize",
        authorize,
        methods=["GET"],
        response_model=McpOAuthAuthorizeResponse,
    )
    router.add_api_route(
        f"{base}/exchange",
        exchange,
        methods=["POST"],
        response_model=McpOAuthStatusResponse,
    )
    router.add_api_route(f"{base}/callback", callback, methods=["GET"])
    router.add_api_route(base, disconnect, methods=["DELETE"], status_code=204)


for _oauth_name in _OAUTH_MCP_MODULES:
    _register_oauth_mcp_routes(_oauth_name)


def require_user_id(request: Request) -> str:
    user_id = get_optional_user_id(request)
    if not user_id:
        raise HTTPException(status_code=401, detail="User session required")
    return user_id
