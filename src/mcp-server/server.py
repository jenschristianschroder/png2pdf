"""MCP Server for PNG to PDF conversion.

Exposes the convert_png_to_pdf tool via Streamable HTTP transport.
Proxies conversion requests to the existing Azure Function App.

Implements MCP Authorization Specification (2025-06-18):
  - GET /.well-known/oauth-protected-resource  (RFC 9728)
  - Bearer token validation against Entra ID   (Layers 1+2)
  - Managed identity for backend calls         (Layer 3)
"""

import os
import sys
import base64
import json
import logging
import time

import httpx
from azure.identity.aio import DefaultAzureCredential
from mcp.server.fastmcp import FastMCP
from starlette.applications import Starlette
from starlette.routing import Mount, Route
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware

from auth import validate_token, AuthError

# ─── Configuration ───
FUNCTION_URL = os.environ.get("FUNCTION_URL", "http://localhost:7071")
API_IDENTIFIER_URI = os.environ.get("API_IDENTIFIER_URI", "api://png2pdf-api")
AZURE_TENANT_ID = os.environ.get("AZURE_TENANT_ID", "")
MCP_CLIENT_ID = os.environ.get("MCP_CLIENT_ID", "")
MCP_IDENTIFIER_URI = os.environ.get("MCP_IDENTIFIER_URI", "api://png2pdf-mcp")
PORT = int(os.environ.get("PORT", "8080"))
MAX_INPUT_SIZE = 10 * 1024 * 1024  # 10 MB

# ─── Logging ───
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger("mcp-server")

# ─── Azure credential for calling Function App (Layer 3 — managed identity) ───
credential = DefaultAzureCredential()

# ─── MCP Server ───
mcp = FastMCP(
    "PNG to PDF Converter",
    description="Converts PNG images to PDF documents with matching page dimensions.",
)


# ─── Authentication Middleware ───
# Paths that must be served WITHOUT authentication (MCP spec + health probes)
PUBLIC_PATHS = {
    "/.well-known/oauth-protected-resource",
    "/health",
}


class AuthMiddleware(BaseHTTPMiddleware):
    """Validates Bearer tokens on MCP endpoints (Layer 1 + 2).

    Public endpoints (well-known metadata, health) are exempt from auth
    per the MCP Authorization Specification (2025-06-18).
    """

    async def dispatch(self, request: Request, call_next):
        # Skip auth for public paths and CORS preflight
        if request.url.path in PUBLIC_PATHS or request.method == "OPTIONS":
            return await call_next(request)

        try:
            auth_header = request.headers.get("Authorization")
            auth_context = await validate_token(auth_header)
            request.state.auth_context = auth_context
        except AuthError as e:
            logger.warning("Auth failed: %s", e.message)
            # Return WWW-Authenticate header per RFC 6750 §3
            resource_metadata_url = str(request.base_url).rstrip("/") + "/.well-known/oauth-protected-resource"
            return JSONResponse(
                {"error": e.message},
                status_code=e.status_code,
                headers={
                    "WWW-Authenticate": f'Bearer resource_metadata="{resource_metadata_url}"',
                },
            )

        return await call_next(request)


# ─── MCP Tool ───


@mcp.tool()
async def convert_png_to_pdf(png_base64: str, filename: str = "image") -> str:
    """Convert a PNG image to a PDF document with matching page dimensions.

    The PNG image is sent as a base64-encoded string and the resulting PDF
    is returned as a base64-encoded string.

    Args:
        png_base64: Base64-encoded PNG image data.
        filename: Optional original filename (without extension). Defaults to "image".

    Returns:
        JSON string with pdf_base64 (base64-encoded PDF), filename, and size_bytes.
    """
    start_time = time.time()

    # Validate input
    if not png_base64:
        return json.dumps({"error": "png_base64 is required"})

    try:
        png_bytes = base64.b64decode(png_base64)
    except Exception:
        return json.dumps({"error": "Invalid base64 encoding"})

    if len(png_bytes) == 0:
        return json.dumps({"error": "Empty PNG data"})

    if len(png_bytes) > MAX_INPUT_SIZE:
        return json.dumps({"error": "PNG exceeds 10 MB limit"})

    try:
        # Acquire managed identity token for Function App (Layer 3)
        scope = f"{API_IDENTIFIER_URI}/.default"
        token = await credential.get_token(scope)

        # Build multipart form data matching the Function App's expected format
        output_filename = filename.removesuffix(".png").removesuffix(".PNG") + ".pdf"
        files = {"file": (f"{filename}.png", png_bytes, "image/png")}

        # Call Function App
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(
                f"{FUNCTION_URL}/api/convert",
                files=files,
                headers={"Authorization": f"Bearer {token.token}"},
            )

        duration_ms = int((time.time() - start_time) * 1000)

        if response.status_code != 200:
            error_detail = response.text
            logger.error(
                "Function App returned %d: %s (duration=%dms)",
                response.status_code,
                error_detail,
                duration_ms,
            )
            return json.dumps(
                {
                    "error": f"Conversion failed (HTTP {response.status_code}): {error_detail}"
                }
            )

        # Encode PDF as base64 for MCP transport
        pdf_bytes = response.content
        pdf_base64 = base64.b64encode(pdf_bytes).decode("utf-8")

        logger.info(
            "Conversion successful: input=%d bytes, output=%d bytes, duration=%dms",
            len(png_bytes),
            len(pdf_bytes),
            duration_ms,
        )

        return json.dumps(
            {
                "pdf_base64": pdf_base64,
                "filename": output_filename,
                "size_bytes": len(pdf_bytes),
            }
        )

    except Exception as e:
        logger.exception("Conversion error")
        return json.dumps({"error": f"Conversion failed: {str(e)}"})


# ─── OAuth Protected Resource Metadata (RFC 9728) ───
# Per MCP Authorization Spec (2025-06-18), this tells MCP clients
# which authorization server to use and what scopes are available.


async def oauth_protected_resource(request: Request) -> JSONResponse:
    """Serve RFC 9728 Protected Resource Metadata.

    This endpoint is the entry point for MCP auth discovery:
    1. MCP client (Copilot Studio) fetches this to learn where to get tokens
    2. It discovers Entra ID as the authorization server
    3. It fetches Entra ID's openid-configuration for authorize/token endpoints
    4. It authenticates and sends Bearer tokens on subsequent MCP requests
    """
    server_url = str(request.base_url).rstrip("/")

    return JSONResponse(
        {
            "resource": server_url,
            "authorization_servers": [
                f"https://login.microsoftonline.com/{AZURE_TENANT_ID}/v2.0"
            ],
            "scopes_supported": [
                f"{MCP_IDENTIFIER_URI}/Convert.ReadWrite",
            ],
            "bearer_methods_supported": ["header"],
            "resource_documentation": f"{server_url}/health",
        },
        headers={
            "Cache-Control": "public, max-age=3600",
        },
    )


# ─── Health endpoint ───


async def health(request: Request) -> JSONResponse:
    """Health check for Container Apps probes."""
    return JSONResponse({"status": "healthy", "service": "png2pdf-mcp"})


# ─── App assembly ───


def create_app() -> Starlette:
    """Create the Starlette app with MCP routes, auth middleware, and discovery endpoints."""
    mcp_app = mcp.streamable_http_app()

    app = Starlette(
        routes=[
            Route("/.well-known/oauth-protected-resource", oauth_protected_resource, methods=["GET"]),
            Route("/health", health, methods=["GET"]),
            Mount("/", app=mcp_app),
        ],
        middleware=[
            Middleware(AuthMiddleware),
        ],
    )
    return app


app = create_app()

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=PORT)
