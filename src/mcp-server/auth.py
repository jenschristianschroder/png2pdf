"""JWT validation for MCP server - layered authentication.

Layer 1: Application auth (always enforced) - validates Bearer token from Entra ID.
Layer 2: User identity extraction (when available) - extracts user claims for audit.

Follows MCP Authorization Specification (2025-06-18) for resource server behaviour.
The issuer uses the v2.0 endpoint to match the authorization_servers in
the /.well-known/oauth-protected-resource metadata.
"""

import os
import time
import logging
from dataclasses import dataclass, field
from typing import Optional

import jwt
import httpx

logger = logging.getLogger(__name__)

TENANT_ID = os.environ.get("AZURE_TENANT_ID", "")
MCP_CLIENT_ID = os.environ.get("MCP_CLIENT_ID", "")
MCP_IDENTIFIER_URI = os.environ.get("MCP_IDENTIFIER_URI", "api://png2pdf-mcp")

JWKS_URL = f"https://login.microsoftonline.com/{TENANT_ID}/discovery/v2.0/keys"
# v2.0 issuer format — must match the authorization server declared in
# /.well-known/oauth-protected-resource metadata
ISSUER_V2 = f"https://login.microsoftonline.com/{TENANT_ID}/v2.0"
# v1.0 issuer format — some tokens may still use this depending on token version
ISSUER_V1 = f"https://sts.windows.net/{TENANT_ID}/"
VALID_ISSUERS = [ISSUER_V2, ISSUER_V1]
VALID_AUDIENCES = [a for a in [MCP_CLIENT_ID, MCP_IDENTIFIER_URI] if a]

# Cache for JWKS keys
_jwks_cache: dict = {}
_jwks_cache_time: float = 0
JWKS_CACHE_TTL = 3600  # 1 hour


@dataclass
class AuthContext:
    """Authentication context extracted from a validated JWT."""

    app_id: str
    user_id: Optional[str] = None
    user_name: Optional[str] = None
    user_email: Optional[str] = None
    scopes: list[str] = field(default_factory=list)
    roles: list[str] = field(default_factory=list)
    claims: dict = field(default_factory=dict)

    @property
    def is_user_token(self) -> bool:
        return self.user_id is not None


class AuthError(Exception):
    """Raised when authentication fails."""

    def __init__(self, message: str, status_code: int = 401):
        self.message = message
        self.status_code = status_code
        super().__init__(message)


async def _get_jwks() -> dict:
    """Fetch and cache JWKS keys from Entra ID."""
    global _jwks_cache, _jwks_cache_time

    if _jwks_cache and (time.time() - _jwks_cache_time) < JWKS_CACHE_TTL:
        return _jwks_cache

    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.get(JWKS_URL)
        response.raise_for_status()
        _jwks_cache = response.json()
        _jwks_cache_time = time.time()
        logger.info("JWKS keys refreshed (%d keys)", len(_jwks_cache.get("keys", [])))
        return _jwks_cache


def _build_signing_keys(jwks: dict) -> dict:
    """Build a dictionary of signing keys keyed by kid."""
    signing_keys = {}
    for key_data in jwks.get("keys", []):
        try:
            jwk = jwt.PyJWK(key_data)
            if jwk.key_id:
                signing_keys[jwk.key_id] = jwk
        except Exception:
            continue
    return signing_keys


async def validate_token(authorization_header: Optional[str]) -> AuthContext:
    """Validate a Bearer token and return an AuthContext.

    Args:
        authorization_header: The full Authorization header value
            (e.g., "Bearer <token>").

    Returns:
        AuthContext with app and optional user identity.

    Raises:
        AuthError: If the token is missing, invalid, or unauthorized.
    """
    if not authorization_header:
        raise AuthError("Missing Authorization header")

    parts = authorization_header.split(" ")
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise AuthError(
            "Invalid Authorization header format. Expected 'Bearer <token>'"
        )

    token = parts[1]

    try:
        # Get signing keys
        jwks = await _get_jwks()
        signing_keys = _build_signing_keys(jwks)

        # Decode header to get kid
        unverified_header = jwt.get_unverified_header(token)
        kid = unverified_header.get("kid")
        if not kid or kid not in signing_keys:
            raise AuthError("Token signing key not found")

        signing_key = signing_keys[kid]

        # Try validation — accept both v1.0 and v2.0 issuers
        decoded = None
        last_error = None
        for issuer in VALID_ISSUERS:
            try:
                decoded = jwt.decode(
                    token,
                    signing_key.key,
                    algorithms=["RS256"],
                    audience=VALID_AUDIENCES,
                    issuer=issuer,
                    options={"require": ["exp", "iss", "aud"]},
                )
                break
            except jwt.InvalidIssuerError as e:
                last_error = e
                continue

        if decoded is None:
            raise AuthError("Token issuer is invalid") from last_error

        # Layer 1: Extract app identity (always present)
        app_id = (
            decoded.get("azp")
            or decoded.get("appid")
            or decoded.get("sub", "unknown")
        )

        # Layer 2: Extract user identity (present in delegated tokens)
        user_id = decoded.get("oid")
        user_name = decoded.get("name")
        user_email = decoded.get("preferred_username")

        # Extract scopes and roles for audit logging
        scopes = decoded.get("scp", "").split() if decoded.get("scp") else []
        roles = decoded.get("roles", [])

        context = AuthContext(
            app_id=app_id,
            user_id=user_id,
            user_name=user_name,
            user_email=user_email,
            scopes=scopes,
            roles=roles,
            claims=decoded,
        )

        if context.is_user_token:
            logger.info(
                "Authenticated user request: user=%s (%s), app=%s, scopes=%s",
                user_name,
                user_email,
                app_id,
                scopes,
            )
        else:
            logger.info(
                "Authenticated app-only request: app=%s, roles=%s",
                app_id,
                roles,
            )

        return context

    except jwt.ExpiredSignatureError:
        raise AuthError("Token has expired")
    except jwt.InvalidAudienceError:
        raise AuthError("Token audience is invalid")
    except jwt.InvalidIssuerError:
        raise AuthError("Token issuer is invalid")
    except jwt.DecodeError as e:
        raise AuthError(f"Token decode error: {e}")
    except AuthError:
        raise
    except Exception as e:
        logger.exception("Unexpected error during token validation")
        raise AuthError(f"Authentication failed: {e}")
