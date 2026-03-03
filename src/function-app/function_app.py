"""Azure Function App — PNG-to-PDF HTTP trigger (Python v2 programming model)."""

import json
import logging
import os
import uuid

import azure.functions as func
import jwt
from azure.identity import DefaultAzureCredential
from azure.storage.blob import BlobServiceClient, ContentSettings

from png_to_pdf import png_bytes_to_pdf_bytes

app = func.FunctionApp(http_auth_level=func.AuthLevel.ANONYMOUS)

# Cache for JWKS keys
_jwks_client = None
_TENANT_ID = os.environ.get("AZURE_TENANT_ID", "")
# Accept both the identifier URI and the client ID as valid audiences
_API_CLIENT_ID = os.environ.get("API_CLIENT_ID", "")
_VALID_AUDIENCES = [aud for aud in ["api://png2pdf-api", _API_CLIENT_ID] if aud]

# Blob storage config
_STORAGE_ACCOUNT_NAME = os.environ.get("STORAGE_ACCOUNT_NAME", "")
_CONTAINER_NAME = "pdfs"
_blob_service_client = None


def _get_blob_service_client() -> BlobServiceClient:
    """Get a cached BlobServiceClient using managed identity."""
    global _blob_service_client
    if _blob_service_client is None:
        account_url = f"https://{_STORAGE_ACCOUNT_NAME}.blob.core.windows.net"
        credential = DefaultAzureCredential()
        _blob_service_client = BlobServiceClient(account_url, credential=credential)
    return _blob_service_client


def _get_jwks_client():
    """Get a cached PyJWKClient for Azure AD token validation."""
    global _jwks_client
    if _jwks_client is None:
        oidc_url = f"https://login.microsoftonline.com/{_TENANT_ID}/discovery/v2.0/keys"
        _jwks_client = jwt.PyJWKClient(oidc_url, cache_jwk_set=True, lifespan=3600)
    return _jwks_client


def _validate_bearer_token(auth_header: str) -> bool:
    """Validate a Bearer token from a managed identity."""
    if not auth_header or not auth_header.startswith("Bearer "):
        return False
    token = auth_header[7:]
    try:
        signing_key = _get_jwks_client().get_signing_key_from_jwt(token)
        decoded = jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256"],
            audience=_VALID_AUDIENCES,
            options={"verify_iss": False},  # MI tokens use v1 issuer
        )
        logging.info("Token validated for appid=%s oid=%s", decoded.get("appid", decoded.get("azp")), decoded.get("oid"))
        return True
    except Exception as exc:
        logging.warning("Token validation failed: %s", exc)
        return False


@app.route(route="convert", methods=["POST", "OPTIONS"])
def convert(req: func.HttpRequest) -> func.HttpResponse:
    """Accept a PNG file upload and return the generated PDF.

    Expects the PNG as the raw request body (Content-Type: image/png)
    or as a multipart/form-data field named "file".
    """
    # Handle CORS preflight
    if req.method == "OPTIONS":
        return func.HttpResponse(status_code=204)

    # --- verify authentication via Bearer token ---
    auth_header = req.headers.get("Authorization", "")
    if not _validate_bearer_token(auth_header):
        return func.HttpResponse(
            '{"error": "Authentication required"}',
            status_code=401,
            mimetype="application/json",
        )

    # --- extract PNG bytes -------------------------------------------------
    content_type = (req.headers.get("Content-Type") or "").lower()

    if "multipart/form-data" in content_type:
        file = req.files.get("file")
        if file is None:
            return func.HttpResponse(
                '{"error": "No file field in multipart upload"}',
                status_code=400,
                mimetype="application/json",
            )
        png_data = file.stream.read()
        original_name = file.filename or "image.png"
    else:
        png_data = req.get_body()
        original_name = req.headers.get("X-Filename", "image.png")

    if not png_data:
        return func.HttpResponse(
            '{"error": "Empty request body"}',
            status_code=400,
            mimetype="application/json",
        )

    # --- convert -----------------------------------------------------------
    try:
        pdf_bytes = png_bytes_to_pdf_bytes(png_data)
    except Exception as exc:
        return func.HttpResponse(
            f'{{"error": "Conversion failed: {exc}"}}',
            status_code=500,
            mimetype="application/json",
        )

    # --- upload to blob storage --------------------------------------------
    pdf_name = original_name.rsplit(".", 1)[0] + ".pdf"
    blob_name = f"{uuid.uuid4()}.pdf"

    try:
        blob_client = _get_blob_service_client().get_blob_client(
            container=_CONTAINER_NAME, blob=blob_name
        )
        blob_client.upload_blob(
            pdf_bytes,
            content_settings=ContentSettings(content_type="application/pdf"),
            overwrite=True,
        )
        logging.info("Uploaded PDF to blob: %s (%d bytes)", blob_name, len(pdf_bytes))
    except Exception as exc:
        logging.exception("Failed to upload PDF to blob storage")
        return func.HttpResponse(
            f'{{"error": "Failed to store PDF: {exc}"}}',
            status_code=500,
            mimetype="application/json",
        )

    # --- respond with blob metadata ----------------------------------------
    return func.HttpResponse(
        json.dumps({
            "blob_name": blob_name,
            "filename": pdf_name,
            "size_bytes": len(pdf_bytes),
        }),
        status_code=200,
        mimetype="application/json",
    )
