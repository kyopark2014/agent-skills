"""Per-user OAuth 2.0 (authorization code + PKCE, dynamic client registration) for hosted MCP servers.

Used by Notion and Canva. Each provider is described by an :class:`OAuthProvider`;
:class:`McpOAuth` implements the flow server-side so the browser only opens the
authorize URL in a popup:

  start()   -> discovery, DCR (cached per redirect_uri), PKCE, returns authorize URL
  finish()  -> validates single-use state, exchanges the code, stores tokens
  get_access_token() -> returns a fresh token, refreshing with the refresh token

Tokens are stored under ``{SESSION_STORAGE_DIR}/{user}/{provider.token_file}``.
AgentCore Runtime receives the access token via the invoke payload; it cannot read
this app-local file.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

logger = logging.getLogger("mcp_oauth")

EXPIRY_SKEW_SECONDS = 120
PENDING_TTL_SECONDS = 600
HTTP_TIMEOUT = 20


@dataclass(frozen=True)
class OAuthProvider:
    name: str  # "notion" / "canva"
    mcp_url: str
    issuer: str
    metadata_url: str
    client_name: str
    token_file: str
    state_dir: str
    read_scope: str
    # Optional second tier requested on demand (generate/edit). ``write_marker`` is a
    # scope that only appears in the write tier; it is how status reports ``write``.
    write_scope: str | None = None
    write_marker: str | None = None
    selected_names: frozenset[str] = frozenset()
    # Some servers (Canva) only accept loopback redirect URIs for dynamically registered
    # clients. For those, a hosted app cannot receive the redirect itself: the browser
    # lands on ``manual_redirect_uri`` (nothing listens there) and the user pastes that
    # address-bar URL back so the server can finish the exchange (see ``parse_redirect``).
    loopback_only: bool = False
    manual_redirect_uri: str | None = None


def _utils():
    try:
        from application import utils
    except ImportError:
        import utils  # type: ignore
    return utils


def _now() -> float:
    return time.time()


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _form(**fields: str) -> bytes:
    return urllib.parse.urlencode(fields).encode("utf-8")


def _http_json(
    url: str,
    *,
    data: bytes | None = None,
    headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    req = urllib.request.Request(
        url, data=data, headers=headers or {}, method="POST" if data else "GET"
    )
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
            body = resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:300]
        raise RuntimeError(f"{url} -> HTTP {e.code}: {detail}") from e
    return json.loads(body) if body else {}


def _write_json(path: str, data: dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def _read_json(path: str) -> dict[str, Any] | None:
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
        return raw if isinstance(raw, dict) else None
    except (OSError, json.JSONDecodeError) as e:
        logger.warning("Failed to read %s: %s", path, e)
        return None


def _normalize_issuer(value: str) -> str:
    # Some servers return the issuer with and some without a trailing slash.
    return (value or "").rstrip("/")


class McpOAuth:
    def __init__(self, provider: OAuthProvider):
        self.p = provider
        self._metadata_cache: dict[str, Any] | None = None

    # ------------------------------------------------------------------ storage
    def token_path(self, user_id: str) -> str:
        utils = _utils()
        segment = utils.sanitize_user_path_segment(user_id) or "default"
        return os.path.join(utils.SESSION_STORAGE_DIR, segment, self.p.token_file)

    def _app_dir(self) -> str:
        path = os.path.join(_utils().SESSION_STORAGE_DIR, self.p.state_dir)
        os.makedirs(path, exist_ok=True)
        return path

    def _read(self, user_id: str) -> dict[str, Any] | None:
        rec = _read_json(self.token_path(user_id))
        if rec and (rec.get("access_token") or "").strip():
            return rec
        return None

    def _write(self, user_id: str, data: dict[str, Any]) -> None:
        if not _utils().sanitize_user_path_segment(user_id):
            raise ValueError(f"Invalid user_id for {self.p.name} token path")
        _write_json(self.token_path(user_id), data)

    def clear_token(self, user_id: str) -> None:
        path = self.token_path(user_id)
        try:
            if os.path.isfile(path):
                os.remove(path)
                logger.info("Cleared %s token for %s", self.p.name, user_id)
        except OSError as e:
            logger.warning("Failed to clear %s token for %s: %s", self.p.name, user_id, e)

    # ----------------------------------------------------------------- metadata
    def _metadata(self) -> dict[str, Any]:
        if self._metadata_cache is None:
            self._metadata_cache = _http_json(self.p.metadata_url)
        return self._metadata_cache

    def _client_cache_path(self, redirect_uri: str) -> str:
        digest = hashlib.sha256(redirect_uri.encode("utf-8")).hexdigest()[:16]
        return os.path.join(self._app_dir(), f"client_{digest}.json")

    def _get_client_id(self, redirect_uri: str) -> str:
        cached = _read_json(self._client_cache_path(redirect_uri))
        if cached and cached.get("client_id") and cached.get("redirect_uri") == redirect_uri:
            return str(cached["client_id"])
        reg = _http_json(
            self._metadata()["registration_endpoint"],
            data=json.dumps(
                {
                    "client_name": self.p.client_name,
                    "redirect_uris": [redirect_uri],
                    "grant_types": ["authorization_code", "refresh_token"],
                    "response_types": ["code"],
                    "token_endpoint_auth_method": "none",
                }
            ).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        client_id = str(reg["client_id"])
        _write_json(
            self._client_cache_path(redirect_uri),
            {"client_id": client_id, "redirect_uri": redirect_uri},
        )
        logger.info("Registered %s OAuth client for %s", self.p.name, redirect_uri)
        return client_id

    # ---------------------------------------------------------------- authorize
    def _pending_path(self, state: str) -> str:
        safe = "".join(c for c in state if c.isalnum() or c in "-_")
        return os.path.join(self._app_dir(), f"pending_{safe}.json")

    def start(self, user_id: str, redirect_uri: str, *, write: bool = False) -> str:
        """Begin the flow; returns the URL the browser must open."""
        scope = self.p.read_scope
        if write:
            if not self.p.write_scope:
                raise ValueError(f"{self.p.name} has no write scope tier")
            scope = self.p.write_scope
        meta = self._metadata()
        client_id = self._get_client_id(redirect_uri)
        verifier = _b64url(secrets.token_bytes(48))
        challenge = _b64url(hashlib.sha256(verifier.encode("ascii")).digest())
        state = _b64url(secrets.token_bytes(24))
        _write_json(
            self._pending_path(state),
            {
                "user_id": user_id,
                "verifier": verifier,
                "redirect_uri": redirect_uri,
                "client_id": client_id,
                "scope": scope,
                "created_at": _now(),
            },
        )
        query = urllib.parse.urlencode(
            {
                "response_type": "code",
                "client_id": client_id,
                "redirect_uri": redirect_uri,
                "state": state,
                "code_challenge": challenge,
                "code_challenge_method": "S256",
                "resource": self.p.mcp_url,
                "scope": scope,
            }
        )
        return f"{meta['authorization_endpoint']}?{query}"

    def parse_redirect(self, pasted_url: str) -> tuple[str, str, str | None]:
        """Extract (state, code, iss) from the URL a user pasted after approving.

        Only the provider's manual loopback redirect is accepted, so an arbitrary URL
        cannot be fed into the exchange.
        """
        manual = self.p.manual_redirect_uri
        if not manual:
            raise ValueError(f"{self.p.name} does not use a manual redirect")
        parsed = urllib.parse.urlparse((pasted_url or "").strip())
        expected = urllib.parse.urlparse(manual)
        if (parsed.scheme, parsed.hostname, parsed.port, parsed.path) != (
            expected.scheme,
            expected.hostname,
            expected.port,
            expected.path,
        ):
            raise ValueError("붙여넣은 주소가 승인 후 이동한 주소가 아닙니다.")
        q = {k: v[0] for k, v in urllib.parse.parse_qs(parsed.query).items()}
        if q.get("error"):
            raise ValueError(f"{self.p.name} denied the request: {q['error']}")
        if not q.get("code") or not q.get("state"):
            raise ValueError("주소에 code/state가 없습니다. 주소 전체를 복사했는지 확인하세요.")
        return q["state"], q["code"], q.get("iss")

    def finish(
        self,
        state: str,
        code: str,
        iss: str | None = None,
        *,
        expected_user_id: str | None = None,
    ) -> str:
        """Complete the flow; returns the user_id the token was stored for.

        ``expected_user_id`` (manual flow) rejects a state started by another user.
        """
        if not state or not code:
            raise ValueError("state and code are required")
        pending_file = self._pending_path(state)
        pending = _read_json(pending_file)
        if not pending:
            raise ValueError("Unknown or expired OAuth state")
        if expected_user_id is not None and str(pending.get("user_id")) != str(expected_user_id):
            raise ValueError("Unknown or expired OAuth state")
        try:
            os.remove(pending_file)  # single use
        except OSError:
            pass
        age = _now() - float(pending.get("created_at", 0))
        if age > PENDING_TTL_SECONDS:
            raise ValueError("OAuth state expired")
        if iss and _normalize_issuer(iss) != _normalize_issuer(self.p.issuer):
            raise ValueError(f"Unexpected issuer in authorization response: {iss}")

        meta = self._metadata()
        try:
            token = _http_json(
                meta["token_endpoint"],
                data=_form(
                    grant_type="authorization_code",
                    code=code,
                    redirect_uri=pending["redirect_uri"],
                    client_id=pending["client_id"],
                    code_verifier=pending["verifier"],
                    resource=self.p.mcp_url,
                ),
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
        except Exception:
            # Enough context to tell client/redirect/PKCE/timing problems apart
            # (never log the code, verifier or tokens).
            logger.error(
                "%s token exchange failed: client_id=%s redirect_uri=%s "
                "code_len=%d code_parts=%d verifier_len=%d age_since_authorize=%.1fs "
                "metadata_issuer=%s",
                self.p.name,
                pending.get("client_id"),
                pending.get("redirect_uri"),
                len(code),
                code.count(":") + 1,
                len(pending.get("verifier") or ""),
                age,
                meta.get("issuer"),
            )
            raise
        user_id = str(pending["user_id"])
        self._save_token_response(
            user_id, token, client_id=pending["client_id"], requested_scope=pending.get("scope")
        )
        return user_id

    def _save_token_response(
        self,
        user_id: str,
        token: dict[str, Any],
        *,
        client_id: str,
        requested_scope: str | None = None,
        previous: dict[str, Any] | None = None,
    ) -> None:
        access = (token.get("access_token") or "").strip()
        if not access:
            raise ValueError("Token response has no access_token")
        ttl = int(token.get("expires_in") or 3600)
        refresh = (
            token.get("refresh_token") or (previous or {}).get("refresh_token") or ""
        ).strip()
        scope = (
            token.get("scope")
            or (previous or {}).get("scope")
            or requested_scope
            or self.p.read_scope
        )
        self._write(
            user_id,
            {
                "access_token": access,
                "refresh_token": refresh,
                "client_id": client_id,
                "expires_at": _now() + ttl,
                "expires_in": ttl,
                "scope": scope,
                "token_type": token.get("token_type") or "Bearer",
                "updated_at": datetime.now(timezone.utc).isoformat(),
            },
        )
        logger.info(
            "Saved %s token for %s (expires_in=%ss, refresh=%s)",
            self.p.name,
            user_id,
            ttl,
            bool(refresh),
        )

    # ---------------------------------------------------------------- read side
    @staticmethod
    def is_token_fresh(record: dict[str, Any] | None, *, skew: int = EXPIRY_SKEW_SECONDS) -> bool:
        if not record or not (record.get("access_token") or "").strip():
            return False
        try:
            exp = float(record.get("expires_at"))
        except (TypeError, ValueError):
            return False
        return exp > (_now() + max(0, skew))

    def _refresh(self, user_id: str, record: dict[str, Any]) -> dict[str, Any] | None:
        refresh = (record.get("refresh_token") or "").strip()
        client_id = (record.get("client_id") or "").strip()
        if not refresh or not client_id:
            return None
        try:
            token = _http_json(
                self._metadata()["token_endpoint"],
                data=_form(
                    grant_type="refresh_token",
                    refresh_token=refresh,
                    client_id=client_id,
                    resource=self.p.mcp_url,
                ),
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            self._save_token_response(user_id, token, client_id=client_id, previous=record)
            return self._read(user_id)
        except Exception as e:
            logger.warning("%s token refresh failed for %s: %s", self.p.name, user_id, e)
            return None

    def get_access_token(
        self, user_id: str | None, *, allow_expired: bool = False
    ) -> Optional[str]:
        """Return a usable access token, refreshing it when it is near expiry."""
        if not user_id or not str(user_id).strip():
            return None
        uid = str(user_id).strip()
        record = self._read(uid)
        if not record:
            return None
        if not self.is_token_fresh(record):
            refreshed = self._refresh(uid, record)
            if refreshed:
                record = refreshed
            elif not allow_expired:
                return None
        return (record.get("access_token") or "").strip() or None

    def status_for_user(self, user_id: str | None) -> dict[str, Any]:
        record = self._read(str(user_id).strip()) if user_id and str(user_id).strip() else None
        if not record:
            return {"connected": False, "fresh": False, "expires_at": None, "write": False}
        try:
            exp = float(record.get("expires_at"))
        except (TypeError, ValueError):
            exp = None
        # A refreshable token counts as fresh: it is renewed transparently on use.
        fresh = self.is_token_fresh(record) or bool((record.get("refresh_token") or "").strip())
        scopes = str(record.get("scope") or "").split()
        marker = self.p.write_marker
        return {
            "connected": True,
            "fresh": fresh,
            "expires_at": exp,
            "updated_at": record.get("updated_at"),
            "write": bool(marker and marker in scopes),
        }

    def mcp_selected(self, mcp_servers: list | None) -> bool:
        if not mcp_servers:
            return False
        names = {str(s).strip().lower() for s in mcp_servers if str(s).strip()}
        return bool(names & self.p.selected_names)
