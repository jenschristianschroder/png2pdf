# PNG → PDF Converter

An Azure-hosted tool that converts PNG images to PDFs with matching page dimensions.

| Component | Technology | Azure Service |
|-----------|------------|---------------|
| **API** | Python Azure Function (v2, HTTP trigger) | Azure Functions (Consumption) |
| **Web UI** | Flask + HTML/CSS/JS + MSAL.js | Azure Container Apps |
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
│       └── log-analytics.bicep
├── src/
│   ├── function-app/           # Azure Function (PNG→PDF API)
│   │   ├── function_app.py
│   │   ├── png_to_pdf.py
│   │   ├── host.json
│   │   ├── local.settings.json
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
```

### Tear down

```powershell
azd down                # Deletes all Azure resources
```

---

## Architecture

```
┌─────────────────┐     HTTPS + Bearer token     ┌──────────────────┐
│                  │ ──────────────────────────▶  │                  │
│  Container Apps  │     POST /api/convert        │  Azure Function  │
│  (Flask Web UI)  │  ◀──────────────────────────  │  (PNG → PDF)     │
│                  │     application/pdf           │                  │
└────────┬─────────┘                              └──────────────────┘
         │                                                │
    MSAL.js login                                   Easy Auth
         │                                          (validates JWT)
         ▼                                                │
┌─────────────────┐                                       │
│   Entra ID      │◀──────────────────────────────────────┘
│  (single tenant)│
└─────────────────┘
```

1. User opens the Container Apps URL → redirected to Entra ID login
2. After sign-in, the SPA acquires an access token scoped to the Function's API
3. User uploads a PNG → SPA sends it to the Function with the Bearer token
4. Function validates the token via Easy Auth, converts the PNG, returns the PDF
5. Browser triggers a download of the PDF
