# PNG → PDF Converter

An Azure-hosted tool that converts PNG images to PDFs with matching page dimensions.

| Component | Technology | Azure Service |
|-----------|------------|---------------|
| **API** | Python Azure Function (v2, HTTP trigger) | Azure Functions (Consumption) |
| **Web UI** | Flask + HTML/CSS/JS + MSAL.js | Azure Container Apps |
| **MCP Server** | Python MCP SDK + Streamable HTTP | Azure Container Apps |
| **Auth** | Entra ID (single-tenant, PKCE) | App Registrations + Easy Auth |
| **IaC** | Bicep | Azure Developer CLI (azd) |

---

## Project Structure

```
├── .azure/plan.md              # Deployment plan (source of truth)
├── azure.yaml                  # azd project configuration
├── hooks/
│   └── postprovision.ps1       # Updates Container App env vars post-deploy
├── infra/
│   ├── main.bicep              # Subscription-scoped entry point
│   ├── main.parameters.json    # Parameter values
│   └── modules/
│       ├── acr-credentials.bicep
│       ├── app-insights.bicep
│       ├── app-registration.bicep
│       ├── container-app.bicep
│       ├── container-registry.bicep
│       ├── function-app.bicep
│       ├── log-analytics.bicep
│       └── mcp-container-app.bicep
├── src/
│   ├── function-app/           # Azure Function (PNG→PDF API)
│   │   ├── function_app.py
│   │   ├── png_to_pdf.py
│   │   ├── host.json
│   │   ├── local.settings.json
│   │   └── requirements.txt
│   ├── mcp-server/             # MCP Server (Container Apps)
│   │   ├── server.py
│   │   ├── auth.py
│   │   ├── Dockerfile
│   │   └── requirements.txt
│   └── web/                    # Flask UI (Container Apps)
│       ├── app.py
│       ├── Dockerfile
│       ├── requirements.txt
│       └── templates/
│           └── index.html
├── png2pdf.py                  # Original CLI script
└── requirements.txt            # Original CLI requirements
```

---

## Prerequisites

- [Azure CLI](https://learn.microsoft.com/cli/azure/install-azure-cli)
- [Azure Developer CLI (azd)](https://learn.microsoft.com/azure/developer/azure-developer-cli/install-azd)
- [Azure Functions Core Tools v4](https://learn.microsoft.com/azure/azure-functions/functions-run-local)
- [Python 3.13+](https://python.org)
- [Docker](https://docker.com) (for building the web container)
- An Azure subscription with permissions to create resources and app registrations

---

## Local Development

### 1. Azure Function (API)

```powershell
cd src/function-app
python -m venv .venv
.venv/Scripts/Activate.ps1          # Windows
pip install -r requirements.txt
func start
```

The function runs at `http://localhost:7071`. Test with:

```powershell
curl -X POST http://localhost:7071/api/convert `
     -F "file=@path/to/image.png" `
     -o output.pdf
```

### 2. Flask Web UI

```powershell
cd src/web
python -m venv .venv
.venv/Scripts/Activate.ps1
pip install -r requirements.txt

# Set env vars for local dev (auth values can be empty for testing without auth)
$env:FUNCTION_URL = "http://localhost:7071"
$env:AZURE_CLIENT_ID = ""
$env:AZURE_TENANT_ID = ""
$env:API_SCOPE = ""

flask run --port 5000
```

Open `http://localhost:5000` in your browser.

> **Note**: MSAL.js login won't work locally without valid Entra ID app registration values. For local testing without auth, you can bypass the login screen by temporarily modifying the HTML.

### 3. MCP Server

```powershell
cd src/mcp-server
python -m venv .venv
.venv/Scripts/Activate.ps1
pip install -r requirements.txt

# Set env vars for local dev
$env:FUNCTION_URL = "http://localhost:7071"
$env:API_IDENTIFIER_URI = "api://png2pdf-api"
$env:AZURE_TENANT_ID = "<your-tenant-id>"
$env:MCP_CLIENT_ID = "<mcp-app-client-id>"
$env:MCP_IDENTIFIER_URI = "api://png2pdf-mcp"

python server.py
```

The MCP server runs at `http://localhost:8080`. Test with the [MCP Inspector](https://github.com/modelcontextprotocol/inspector) or curl:

```powershell
# List available tools
curl -X POST http://localhost:8080/mcp `
     -H "Content-Type: application/json" `
     -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'
```

> **Note**: Auth is validated via Entra ID JWT. For local testing without auth, you can temporarily comment out the `AuthMiddleware` in `server.py`.

---

## Deploy to Azure

### First-time setup

```powershell
azd auth login
azd init                # Only if not already initialized
azd up                  # Provisions infrastructure + deploys code
```

`azd up` will:
1. Prompt for environment name and Azure location
2. Provision all infrastructure via Bicep (resource group, Function App, Container Apps, ACR, App Insights, Entra ID app registrations)
3. Build and push the Docker image to ACR
4. Deploy the Function App code
5. Run the `postprovision` hook to update Container App env vars with final auth + API values

### Subsequent deploys

```powershell
azd deploy              # Deploys code changes only
azd deploy api          # Deploy Function App only
azd deploy web          # Deploy web UI only
azd deploy mcp          # Deploy MCP server only
```

### Tear down

```powershell
azd down                # Deletes all Azure resources
```

---

## Architecture

```
┌─────────────────┐     HTTPS + Bearer      ┌──────────────────┐
│  Container Apps  │ ─────────────────────▶  │  Azure Function  │
│  (Flask Web UI)  │     POST /api/convert   │  (PNG → PDF)     │
└────────┬─────────┘                         └──────────────────┘
         │                                           ▲
    MSAL.js login                                    │ MI token
         ▼                                           │
┌─────────────────┐                         ┌────────┴─────────┐
│   Entra ID      │◀────────────────────────│  Container Apps  │
│  (single tenant)│    OAuth 2.0 + MI       │  (MCP Server)    │
└─────────────────┘                         └────────▲─────────┘
         ▲                                           │
         │              OAuth 2.0 JWT                │
         └───────────────────────────────── ┌────────┴─────────┐
                                            │  Copilot Studio  │
                                            │  (Agent)         │
                                            └──────────────────┘
```

### Web UI Flow
1. User opens the Container Apps URL → redirected to Entra ID login
2. After sign-in, the SPA acquires an access token scoped to the Function's API
3. User uploads a PNG → SPA sends it to the Function with the Bearer token
4. Function validates the token via Easy Auth, converts the PNG, returns the PDF
5. Browser triggers a download of the PDF

### MCP Server Flow
1. Copilot Studio agent authenticates via OAuth 2.0 to the MCP server
2. User sends a PNG to the agent → agent calls `convert_png_to_pdf` MCP tool
3. MCP server acquires a managed identity token for the Function App
4. MCP server proxies the request to `/api/convert` and returns the PDF as base64
5. Agent delivers the PDF to the user

---

## MCP Server Security

The MCP server uses a **layered authentication model**:

| Layer | Flow | Purpose |
|-------|------|---------|
| **1 — App Auth** | Copilot Studio → MCP Server | OAuth 2.0 JWT validated against Entra ID JWKS. Audience: `api://png2pdf-mcp` |
| **2 — User Identity** | (optional) | Delegated tokens carry user claims (`oid`, `name`, `email`) for audit trails |
| **3 — Managed Identity** | MCP Server → Function App | System-assigned MI acquires token for `api://png2pdf-api/.default` |

The `{env}-mcp` app registration defines:
- **OAuth2 scope** `Convert.ReadWrite` — for delegated (user) access
- **App role** `MCP.Invoke` — for client credentials (app-only) access

All tenant users are permitted; no group restriction is enforced.

---

## Copilot Studio Agent Setup

1. Create a new agent in [Copilot Studio](https://copilotstudio.microsoft.com)
2. Add an **MCP Server** action pointing to `https://<mcp-container-app-fqdn>/mcp`
3. Configure **OAuth 2.0 authentication**:
   - Authority: `https://login.microsoftonline.com/<tenant-id>`
   - Client ID: your agent's app registration client ID
   - Scope: `api://png2pdf-mcp/.default`
   - Grant type: Client Credentials (or delegated for user-level auditing)
4. Grant the agent's app the `MCP.Invoke` role on the `{env}-mcp` app registration
5. Test end-to-end: send a PNG image in the agent chat → receive a PDF download
