"""MCP OAuth Authorization Server Provider.

Implements the MCP SDK's OAuthAuthorizationServerProvider to support
Dynamic Client Registration (RFC 7591) and OAuth 2.0 Authorization Code
flow with PKCE (RFC 7636) for Copilot Studio connectivity.

The MCP server acts as its own authorization server:
  - POST /register  → Dynamic Client Registration
  - GET  /authorize → Authorization (auto-approved for MCP clients)
  - POST /token     → Token exchange (code → access_token + refresh_token)
  - GET  /.well-known/oauth-authorization-server → OAuth metadata

Tokens are self-issued HS256 JWTs signed with a server-side secret.
"""

import os
import time
import secrets
import logging
from dataclasses import dataclass
from typing import Optional

import jwt

from mcp.server.auth.provider import (
    OAuthAuthorizationServerProvider,
    AuthorizationParams,
    AccessToken,
    TokenVerifier,
)
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken

logger = logging.getLogger("mcp-auth")

# ─── Token signing ───
# Persistent key from env var, or generate per-instance (tokens lost on restart)
SIGNING_KEY = os.environ.get("MCP_SIGNING_KEY", secrets.token_hex(32))
TOKEN_EXPIRY = 3600  # 1 hour
AUTH_CODE_EXPIRY = 600  # 10 minutes


# ─── Stored authorization code ───
@dataclass
class StoredAuthCode:
    """Authorization code with associated OAuth parameters."""

    code: str
    client_id: str
    redirect_uri: str
    redirect_uri_provided_explicitly: bool
    code_challenge: str
    scopes: list[str]
    state: Optional[str]
    created_at: float
    expires_at: float
    resource: Optional[str]


@dataclass
class StoredRefreshToken:
    """Refresh token with associated client and scope info."""

    token: str
    client_id: str
    scopes: list[str]
    created_at: float
    expires_at: Optional[float]


class McpAuthProvider(
    OAuthAuthorizationServerProvider[StoredAuthCode, StoredRefreshToken, AccessToken]
):
    """OAuth authorization server for MCP clients.

    Handles Dynamic Client Registration and token issuance.
    Uses auto-approve authorization — no interactive user consent
    is required because the MCP tool access is controlled at the
    client registration level.
    """

    def __init__(self):
        self._clients: dict[str, OAuthClientInformationFull] = {}
        self._auth_codes: dict[str, StoredAuthCode] = {}
        self._refresh_tokens: dict[str, StoredRefreshToken] = {}

    # ── Client management ──

    async def get_client(
        self, client_id: str
    ) -> OAuthClientInformationFull | None:
        return self._clients.get(client_id)

    async def register_client(
        self, client_info: OAuthClientInformationFull
    ) -> None:
        if client_info.client_id:
            self._clients[client_info.client_id] = client_info
            logger.info(
                "Registered client: %s (%s)",
                client_info.client_id,
                client_info.client_name or "unnamed",
            )

    # ── Authorization ──

    async def authorize(
        self,
        client: OAuthClientInformationFull,
        params: AuthorizationParams,
    ) -> str:
        """Auto-approve and return redirect URL with authorization code."""
        code = secrets.token_urlsafe(48)

        now = time.time()
        stored = StoredAuthCode(
            code=code,
            client_id=client.client_id,
            redirect_uri=str(params.redirect_uri),
            redirect_uri_provided_explicitly=params.redirect_uri_provided_explicitly,
            code_challenge=params.code_challenge,
            scopes=params.scopes or [],
            state=params.state,
            created_at=now,
            expires_at=now + AUTH_CODE_EXPIRY,
            resource=params.resource,
        )
        self._auth_codes[code] = stored

        # Build redirect back to the client with code + state
        redirect_url = str(params.redirect_uri)
        separator = "&" if "?" in redirect_url else "?"
        redirect_url += f"{separator}code={code}"
        if params.state:
            redirect_url += f"&state={params.state}"

        logger.info(
            "Auto-approved authorization for client %s (scopes=%s)",
            client.client_id,
            params.scopes,
        )
        return redirect_url

    # ── Authorization code ──

    async def load_authorization_code(
        self,
        client: OAuthClientInformationFull,
        authorization_code: str,
    ) -> StoredAuthCode | None:
        stored = self._auth_codes.get(authorization_code)
        if stored is None:
            return None
        if stored.client_id != client.client_id:
            logger.warning(
                "Auth code client mismatch: expected %s, got %s",
                stored.client_id,
                client.client_id,
            )
            return None
        if time.time() > stored.expires_at:
            self._auth_codes.pop(authorization_code, None)
            logger.warning("Auth code expired for client %s", client.client_id)
            return None
        return stored

    async def exchange_authorization_code(
        self,
        client: OAuthClientInformationFull,
        authorization_code: StoredAuthCode,
    ) -> OAuthToken:
        """Exchange authorization code for access + refresh tokens."""
        # Single-use: remove the code
        self._auth_codes.pop(authorization_code.code, None)

        # Issue self-signed JWT access token
        now = int(time.time())
        payload = {
            "sub": client.client_id,
            "client_id": client.client_id,
            "scopes": authorization_code.scopes,
            "iat": now,
            "exp": now + TOKEN_EXPIRY,
            "iss": "mcp-png2pdf",
        }
        access_token = jwt.encode(payload, SIGNING_KEY, algorithm="HS256")

        # Issue refresh token
        refresh_token = secrets.token_urlsafe(48)
        self._refresh_tokens[refresh_token] = StoredRefreshToken(
            token=refresh_token,
            client_id=client.client_id,
            scopes=authorization_code.scopes,
            created_at=now,
            expires_at=None,  # refresh tokens don't expire
        )

        logger.info("Issued tokens for client %s", client.client_id)

        return OAuthToken(
            access_token=access_token,
            token_type="Bearer",
            expires_in=TOKEN_EXPIRY,
            scope=" ".join(authorization_code.scopes),
            refresh_token=refresh_token,
        )

    # ── Refresh tokens ──

    async def load_refresh_token(
        self,
        client: OAuthClientInformationFull,
        refresh_token: str,
    ) -> StoredRefreshToken | None:
        stored = self._refresh_tokens.get(refresh_token)
        if stored is None or stored.client_id != client.client_id:
            return None
        return stored

    async def exchange_refresh_token(
        self,
        client: OAuthClientInformationFull,
        refresh_token: StoredRefreshToken,
        scopes: list[str],
    ) -> OAuthToken:
        """Exchange refresh token for new access + refresh tokens."""
        effective_scopes = refresh_token.scopes if refresh_token else scopes

        # Issue new access token
        now = int(time.time())
        payload = {
            "sub": client.client_id,
            "client_id": client.client_id,
            "scopes": effective_scopes,
            "iat": now,
            "exp": now + TOKEN_EXPIRY,
            "iss": "mcp-png2pdf",
        }
        access_token = jwt.encode(payload, SIGNING_KEY, algorithm="HS256")

        # Rotate refresh token
        new_refresh_token = secrets.token_urlsafe(48)
        self._refresh_tokens[new_refresh_token] = StoredRefreshToken(
            token=new_refresh_token,
            client_id=client.client_id,
            scopes=effective_scopes,
            created_at=now,
            expires_at=None,
        )
        self._refresh_tokens.pop(refresh_token.token, None)

        logger.info("Refreshed tokens for client %s", client.client_id)

        return OAuthToken(
            access_token=access_token,
            token_type="Bearer",
            expires_in=TOKEN_EXPIRY,
            scope=" ".join(effective_scopes),
            refresh_token=new_refresh_token,
        )

    # ── Access tokens (for revocation lookup) ──

    async def load_access_token(self, token: str) -> AccessToken | None:
        """Load and validate an access token."""
        try:
            payload = jwt.decode(
                token, SIGNING_KEY, algorithms=["HS256"], issuer="mcp-png2pdf"
            )
            return AccessToken(
                token=token,
                client_id=payload.get("client_id", ""),
                scopes=payload.get("scopes", []),
                expires_at=payload.get("exp"),
            )
        except jwt.InvalidTokenError:
            return None

    # ── Revocation ──

    async def revoke_token(self, token) -> None:
        """Revoke a token."""
        if isinstance(token, str):
            self._refresh_tokens.pop(token, None)
        logger.info("Token revoked")


class McpTokenVerifier(TokenVerifier):
    """Verifies self-issued JWT access tokens on MCP requests."""

    async def verify_token(self, token: str) -> AccessToken | None:
        try:
            payload = jwt.decode(
                token, SIGNING_KEY, algorithms=["HS256"], issuer="mcp-png2pdf"
            )
            return AccessToken(
                token=token,
                client_id=payload.get("client_id", ""),
                scopes=payload.get("scopes", []),
                expires_at=payload.get("exp"),
            )
        except jwt.ExpiredSignatureError:
            logger.warning("Access token expired")
            return None
        except jwt.InvalidTokenError as e:
            logger.warning("Invalid access token: %s", e)
            return None
