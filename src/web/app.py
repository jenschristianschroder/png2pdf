"""Flask web application - proxy UI for the PNG-to-PDF converter.

Proxies /api/convert requests to the Azure Function App using the Container App's
system-assigned managed identity for authentication.
"""

import os

import requests as http_requests
from azure.identity import DefaultAzureCredential
from flask import Flask, render_template, request, Response

app = Flask(__name__)

FUNCTION_URL = os.environ.get("FUNCTION_URL", "http://localhost:7071")
API_IDENTIFIER_URI = os.environ.get("API_IDENTIFIER_URI", "")

# Managed identity credential (works automatically in Azure Container Apps)
_credential = None


def _get_credential():
    global _credential
    if _credential is None:
        _credential = DefaultAzureCredential()
    return _credential


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

    # Forward the multipart upload to the Function App
    headers = {}
    if auth_header:
        headers["Authorization"] = auth_header

    # Stream the file through
    try:
        files = {}
        if "file" in request.files:
            f = request.files["file"]
            files["file"] = (f.filename, f.stream, f.content_type)

        resp = http_requests.post(
            f"{FUNCTION_URL}/api/convert",
            files=files if files else None,
            data=request.get_data() if not files else None,
            headers=headers,
            timeout=60,
        )
    except http_requests.RequestException as exc:
        return Response(
            f'{{"error": "Function App request failed: {exc}"}}',
            status=502,
            mimetype="application/json",
        )

    # Pass through the Function App response
    excluded_headers = {"transfer-encoding", "content-encoding", "connection"}
    response_headers = {
        k: v for k, v in resp.headers.items() if k.lower() not in excluded_headers
    }
    return Response(resp.content, status=resp.status_code, headers=response_headers)


@app.route("/health")
def health():
    """Health-check endpoint for Container Apps ingress probe."""
    return "OK", 200
