"""MCP Server for PNG to PDF conversion.

Exposes the convert_png_to_pdf tool via Streamable HTTP transport.
Proxies conversion requests to the existing Azure Function App.

Uses the MCP SDK's built-in OAuth framework for authentication:
  - POST /register  → Dynamic Client Registration (RFC 7591)
  - GET  /authorize → Authorization Code flow with PKCE (RFC 7636)
  - POST /token     → Token exchange
  - GET  /.well-known/oauth-protected-resource   (RFC 9728)
  - GET  /.well-known/oauth-authorization-server  (RFC 8414)
  - Managed identity for backend calls (Layer 3)
"""

import os
import sys
import base64
import json
import logging
import re
import time
from contextlib import asynccontextmanager

import httpx
from azure.identity.aio import DefaultAzureCredential
from azure.storage.blob.aio import BlobServiceClient
from mcp.server.fastmcp import FastMCP
from starlette.applications import Starlette
from starlette.routing import Mount, Route
from starlette.requests import Request
from starlette.responses import JSONResponse, Response as StarletteResponse

from auth_provider import McpAuthProvider

# ─── Configuration ───
FUNCTION_URL = os.environ.get("FUNCTION_URL", "http://localhost:7071")
API_IDENTIFIER_URI = os.environ.get("API_IDENTIFIER_URI", "api://png2pdf-api")
PORT = int(os.environ.get("PORT", "8080"))
MCP_SERVER_URL = os.environ.get("MCP_SERVER_URL", f"http://localhost:{PORT}")
STORAGE_ACCOUNT_NAME = os.environ.get("STORAGE_ACCOUNT_NAME", "")
CONTAINER_NAME = "pdfs"
MAX_INPUT_SIZE = 10 * 1024 * 1024  # 10 MB

# Validate blob names (UUID + .pdf)
_BLOB_NAME_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\.pdf$")

# Derive allowed hosts from the server URL for DNS rebinding protection
from urllib.parse import urlparse

_parsed = urlparse(MCP_SERVER_URL)
_allowed_hosts = [_parsed.hostname]
if _parsed.port:
    _allowed_hosts.append(f"{_parsed.hostname}:{_parsed.port}")
# Also allow localhost for health probes inside the container
_allowed_hosts.extend([f"localhost:{PORT}", f"127.0.0.1:{PORT}"])

# ─── Logging ───
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger("mcp-server")

# ─── Azure credential for calling Function App (Layer 3 — managed identity) ───
credential = DefaultAzureCredential()

# ─── Async blob service client for PDF downloads ───
_blob_service_client = None


async def _get_blob_service_client() -> BlobServiceClient:
    global _blob_service_client
    if _blob_service_client is None:
        account_url = f"https://{STORAGE_ACCOUNT_NAME}.blob.core.windows.net"
        _blob_service_client = BlobServiceClient(account_url, credential=credential)
    return _blob_service_client

# ─── Auth provider ───
auth_provider = McpAuthProvider()

# ─── MCP Server with built-in OAuth ───
# The SDK automatically creates these routes:
#   /.well-known/oauth-protected-resource    (RFC 9728)
#   /.well-known/oauth-authorization-server  (RFC 8414)
#   /register   (Dynamic Client Registration)
#   /authorize  (Authorization Code + PKCE)
#   /token      (Token exchange)
#   /revoke     (Token revocation)
#   /           (Streamable HTTP — protected by bearer auth)
mcp = FastMCP(
    "PNG to PDF Converter",
    instructions="Converts PNG images to PDF documents with matching page dimensions.",
    auth_server_provider=auth_provider,
    streamable_http_path="/",
    auth={
        "issuer_url": MCP_SERVER_URL,
        "resource_server_url": MCP_SERVER_URL,
        "client_registration_options": {
            "enabled": True,
            "valid_scopes": ["convert"],
            "default_scopes": ["convert"],
        },
        "revocation_options": {"enabled": True},
        "required_scopes": ["convert"],
    },
    transport_security={
        "enable_dns_rebinding_protection": True,
        "allowed_hosts": _allowed_hosts,
    },
)


# ─── MCP Tool ───


@mcp.tool()
async def convert_png_to_pdf(png_base64: str, filename: str = "image") -> str:
    """Convert a PNG image to a PDF document with matching page dimensions.

    The PNG image is sent as a base64-encoded string. The resulting PDF is
    stored in Azure Blob Storage and a download URL is returned.

    Args:
        png_base64: Base64-encoded PNG image data.
        filename: Optional original filename (without extension). Defaults to "image".

    Returns:
        JSON string with download_url (URL to download the PDF via proxy),
        filename, and size_bytes.
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

        # Parse JSON response from Function App (blob_name, filename, size_bytes)
        try:
            data = response.json()
        except Exception:
            return json.dumps({"error": "Invalid response from Function App"})

        blob_name = data.get("blob_name", "")
        output_filename = data.get("filename", filename + ".pdf")
        size_bytes = data.get("size_bytes", 0)

        # Build download URL through the MCP server proxy
        download_url = f"{MCP_SERVER_URL}/download/{blob_name}"

        logger.info(
            "Conversion successful: input=%d bytes, output=%d bytes, blob=%s, duration=%dms",
            len(png_bytes),
            size_bytes,
            blob_name,
            duration_ms,
        )

        return json.dumps(
            {
                "download_url": download_url,
                "filename": output_filename,
                "size_bytes": size_bytes,
            }
        )

    except Exception as e:
        logger.exception("Conversion error")
        return json.dumps({"error": f"Conversion failed: {str(e)}"})


# ─── Download proxy endpoint ───


async def download_pdf(request: Request) -> StarletteResponse:
    """Proxy download a PDF from blob storage using managed identity.

    No authentication required — the blob name is a UUID which acts as an
    unguessable capability URL. The blob auto-expires after 24 hours.
    """
    blob_name = request.path_params["blob_name"]

    if not _BLOB_NAME_RE.match(blob_name):
        return JSONResponse({"error": "Invalid blob name"}, status_code=400)

    try:
        blob_service = await _get_blob_service_client()
        blob_client = blob_service.get_blob_client(
            container=CONTAINER_NAME, blob=blob_name
        )
        download_stream = await blob_client.download_blob()
        pdf_bytes = await download_stream.readall()

        return StarletteResponse(
            content=pdf_bytes,
            status_code=200,
            media_type="application/pdf",
            headers={
                "Content-Disposition": f'attachment; filename="{blob_name}"',
            },
        )
    except Exception as exc:
        error_msg = str(exc)
        if "BlobNotFound" in error_msg or "404" in error_msg:
            return JSONResponse({"error": "PDF not found"}, status_code=404)
        logger.exception("Failed to download blob %s", blob_name)
        return JSONResponse(
            {"error": f"Failed to download PDF: {error_msg}"}, status_code=500
        )


# ─── Health endpoint ───


async def health(request: Request) -> JSONResponse:
    """Health check for Container Apps probes."""
    return JSONResponse({"status": "healthy", "service": "png2pdf-mcp"})


# ─── App assembly ───


def create_app() -> Starlette:
    """Create the Starlette app with MCP + OAuth routes and health endpoint."""
    mcp_app = mcp.streamable_http_app()

    @asynccontextmanager
    async def lifespan(app):
        """Propagate MCP session manager lifecycle to parent app."""
        async with mcp.session_manager.run():
            yield

    app = Starlette(
        routes=[
            Route("/health", health, methods=["GET"]),
            Route("/download/{blob_name}", download_pdf, methods=["GET"]),
            Mount("/", app=mcp_app),
        ],
        lifespan=lifespan,
    )
    return app


app = create_app()

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=PORT)
