"""Flask web application - proxy UI for the PNG-to-PDF converter.

Proxies /api/convert requests to the Azure Function App using the Container App's
system-assigned managed identity for authentication.
Serves /api/download/<blob_name> as a proxy to blob storage (MI-based access).
"""

import logging
import os
import re
import time

import requests as http_requests
from azure.identity import DefaultAzureCredential
from azure.storage.blob import BlobServiceClient
from flask import Flask, render_template, request, Response, jsonify

logger = logging.getLogger(__name__)

app = Flask(__name__)

FUNCTION_URL = os.environ.get("FUNCTION_URL", "http://localhost:7071")
API_IDENTIFIER_URI = os.environ.get("API_IDENTIFIER_URI", "")
STORAGE_ACCOUNT_NAME = os.environ.get("STORAGE_ACCOUNT_NAME", "")
CONTAINER_NAME = "pdfs"

# Managed identity credential (works automatically in Azure Container Apps)
_credential = None
_blob_service_client = None

# Validate blob names to prevent path traversal (UUID + .pdf)
_BLOB_NAME_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\.pdf$")


def _get_credential():
    global _credential
    if _credential is None:
        _credential = DefaultAzureCredential()
    return _credential


def _get_blob_service_client() -> BlobServiceClient:
    global _blob_service_client
    if _blob_service_client is None:
        account_url = f"https://{STORAGE_ACCOUNT_NAME}.blob.core.windows.net"
        _blob_service_client = BlobServiceClient(account_url, credential=_get_credential())
    return _blob_service_client


@app.route("/")
def index():
    """Serve the single-page upload UI."""
    return render_template("index.html")


@app.route("/api/convert", methods=["POST"])
def proxy_convert():
    """Proxy the convert request to the Azure Function with a managed identity token."""
    # Acquire token for the Function App's API
    if API_IDENTIFIER_URI:
        try:
            token = _get_credential().get_token(f"{API_IDENTIFIER_URI}/.default")
            auth_header = f"Bearer {token.token}"
        except Exception as exc:
            return Response(
                f'{{"error": "Failed to acquire managed identity token: {exc}"}}',
                status=500,
                mimetype="application/json",
            )
    else:
        auth_header = None

    # Forward the multipart upload to the Function App (retry on 503 cold starts)
    headers = {}
    if auth_header:
        headers["Authorization"] = auth_header

    max_retries = 3
    retry_delay = 2  # seconds

    for attempt in range(max_retries + 1):
        try:
            files = {}
            if "file" in request.files:
                f = request.files["file"]
                f.stream.seek(0)
                files["file"] = (f.filename, f.stream, f.content_type)

            resp = http_requests.post(
                f"{FUNCTION_URL}/api/convert",
                files=files if files else None,
                data=request.get_data() if not files else None,
                headers=headers,
                timeout=60,
            )

            if resp.status_code != 503 or attempt == max_retries:
                break

            logger.warning(
                "Function App returned 503 (attempt %d/%d), retrying in %ds...",
                attempt + 1, max_retries + 1, retry_delay,
            )
            time.sleep(retry_delay)
            retry_delay *= 2

        except http_requests.RequestException as exc:
            if attempt == max_retries:
                return Response(
                    f'{{"error": "Function App request failed: {exc}"}}',
                    status=502,
                    mimetype="application/json",
                )
            logger.warning(
                "Function App request error (attempt %d/%d): %s",
                attempt + 1, max_retries + 1, exc,
            )
            time.sleep(retry_delay)
            retry_delay *= 2

    # Pass through the Function App response (now JSON with blob_name)
    if resp.status_code != 200:
        return Response(resp.content, status=resp.status_code, mimetype="application/json")

    try:
        data = resp.json()
    except Exception:
        return Response(
            '{"error": "Invalid response from Function App"}',
            status=502,
            mimetype="application/json",
        )

    # Build proxy download URL
    blob_name = data.get("blob_name", "")
    return jsonify({
        "download_url": f"/api/download/{blob_name}",
        "filename": data.get("filename", "output.pdf"),
        "size_bytes": data.get("size_bytes", 0),
    })


@app.route("/api/download/<blob_name>")
def download_pdf(blob_name):
    """Proxy download a PDF from blob storage using managed identity."""
    if not _BLOB_NAME_RE.match(blob_name):
        return Response('{"error": "Invalid blob name"}', status=400, mimetype="application/json")

    try:
        blob_client = _get_blob_service_client().get_blob_client(
            container=CONTAINER_NAME, blob=blob_name
        )
        download_stream = blob_client.download_blob()
        pdf_bytes = download_stream.readall()
        content_type = download_stream.properties.content_settings.content_type or "application/pdf"

        return Response(
            pdf_bytes,
            status=200,
            mimetype=content_type,
            headers={
                "Content-Disposition": f'attachment; filename="{blob_name}"',
            },
        )
    except Exception as exc:
        error_msg = str(exc)
        if "BlobNotFound" in error_msg or "404" in error_msg:
            return Response('{"error": "PDF not found"}', status=404, mimetype="application/json")
        return Response(
            f'{{"error": "Failed to download PDF: {error_msg}"}}',
            status=500,
            mimetype="application/json",
        )


@app.route("/health")
def health():
    """Health-check endpoint for Container Apps ingress probe."""
    return "OK", 200
