# RAG Document Assistant

A Streamlit chat interface for the local hybrid-search RAG pipeline. It supports PDF, image, Excel, and CSV documents, and displays the passages and page/sheet/row locations used to answer questions.

The sign-in screen displays the company logo from `Image.png` in the project root. Keep that file alongside `app.py`.

## Run locally

From this directory, install the dependencies into the working project environment and create a local environment file:

```powershell
& 'D:\striven_rag_project\RAG_CORE_BASELINE_v1.0\.venv\Scripts\python.exe' -m pip install -r requirements.txt
```

If `.env` does not exist, copy `.env.example` to `.env`. If it already exists, keep it and add or update the settings below so you don't overwrite your Groq key. Keep `.env` private; it is excluded from version control.

Configure unique private sign-in credentials in the same `.env` file:

```dotenv
ADMIN_USERNAME=your_admin_username
ADMIN_PASSWORD=your_unique_long_admin_password
EMPLOYEE_USERNAME=your_employee_username
EMPLOYEE_PASSWORD=your_unique_long_employee_password
```

Replace every example value before running the app. The app rejects the example placeholders and does not define default accounts. On first start, the configured admin and employee accounts are stored with salted PBKDF2 password hashes in the ignored `logs/accounts.sqlite3` database. Subsequent employee accounts are created by an admin from **Workspace → Employee details**; employees have read-only access to the approved document index. New employee accounts require passwords of at least 12 characters. Users can change their own password from the sidebar.

The initial passwords configured for this local setup are short. Change them from the sidebar before exposing the app to anyone else. Do not commit or share `.env`. For an app exposed beyond a trusted local network, put it behind an established authentication provider and HTTPS.

Bootstrap credentials are added only when their usernames are absent. Once initialized, the account database is authoritative: changing a password in `.env` does not reset the saved password. Use **Change my password** after signing in. If a configured bootstrap username already belongs to an account with a different role, startup stops and reports the conflict instead of silently granting the wrong access.

Start the app using the same Python environment where the dependencies were installed:

```powershell
& 'D:\striven_rag_project\RAG_CORE_BASELINE_v1.0\.venv\Scripts\python.exe' -m streamlit run app.py
```

After signing in, admins land on the **Workspace** page, where buttons open **Ask**, **Upload documents**, **Delete document**, **Employee details**, or **Activity log** as a separate view. Use **Back to workspace** to close the current view and return to the workspace buttons. Admins can rebuild the index from the upload view. Employees only see the question-and-answer workspace and can search the approved index. Admin sign-ins, failed sign-ins, file/account changes, and indexing outcomes are recorded in the ignored `logs/streamlit_audit.log` file. Passwords, API keys, and question text are not logged.

The app uses the existing `vector_store` when it is available. Uploaded documents are saved under `data/raw`; re-indexing uses the CPU-based embedding and OCR pipeline and may take some time.

## Public Netlify frontend with a computer-hosted API

The existing Streamlit application remains unchanged. The separate React/Vite frontend is in `frontend/`, and its FastAPI backend is in `api/`. Netlify hosts the static frontend; the backend runs on your own Windows computer and Cloudflare Tunnel provides a public HTTPS route to it. The computer must stay powered on, online, and running both the API and tunnel whenever anyone uses the site. This approach keeps the account database, uploaded documents, indexes, and logs on that computer. `netlify.toml` configures the frontend build; the output is `frontend/dist/`.

### Start the API on your Windows computer

Keep the private project-root `.env` on the computer running the API. It must have valid `API_SECRET_KEY` (at least 32 characters), `ADMIN_USERNAME`, `ADMIN_PASSWORD`, `EMPLOYEE_USERNAME`, `EMPLOYEE_PASSWORD`, and `GROQ_API_KEY` values. Do not copy `.env` to GitHub or Netlify. Use fresh credentials and a rotated Groq key if any previous values were shared.

Open PowerShell in the project directory and run:

```powershell
.\scripts\start-local-api.ps1
```

Leave that PowerShell window open. The script runs Uvicorn bound to `127.0.0.1:8000` (not directly exposed to the local network) and allows the Netlify site origin for browser requests. Confirm the API is running by opening `http://127.0.0.1:8000/api/health`; it should return `{"status":"ok"}`.

### Install and start Cloudflare Tunnel

Download and install the official Windows `cloudflared` MSI from [Cloudflare's download page](https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/downloads/). Open a new PowerShell window after installation so the updated PATH is loaded. In that second window, run:

```powershell
.\scripts\start-cloudflare-tunnel.ps1
```

Keep the tunnel window open. Copy the HTTPS `trycloudflare.com` URL printed by Cloudflare. Anyone who has that URL can reach the API, so share it only with people you intend to access the app. The app still requires login. The tunnel startup script uses an isolated temporary configuration so an existing named tunnel configuration on the computer cannot send API requests to a different local port.

Quick Tunnels are for testing/development, not production: they have no uptime guarantee, their hostname changes when restarted, and they stop working when the tunnel process exits. For a stable always-on address, use a named Cloudflare Tunnel with a domain configured in your Cloudflare account.

### Point Netlify at the tunnel

1. In **Netlify → Site configuration → Environment variables**, set `VITE_API_BASE_URL` to the complete HTTPS `trycloudflare.com` URL, with no trailing `/api` path. This variable must be set in Netlify; putting it in your local project `.env` does not configure the hosted website.
2. Trigger a new production deploy. Vite embeds this setting at build time.
3. Check `https://<your-tunnel-host>/api/health`; it should return `{"status":"ok"}`. Then open [strivenrag.netlify.app](https://strivenrag.netlify.app/) and sign in with the accounts configured on the API computer.
4. If the Quick Tunnel URL changes after a restart, replace the Netlify variable and deploy the frontend again.

The local account database, documents, vector index, and logs stay on the API computer; they are not uploaded to Netlify or GitHub. Back up the project `logs/`, `data/`, and `vector_store/` folders. The API computer must remain awake and connected to the internet, with both PowerShell processes running, for remote users to access the application.

Netlify builds the frontend from the repository using `netlify.toml`. To build it locally and publish manually, run:

```powershell
Set-Location 'D:\striven_rag_project\RAG_CORE_BASELINE_v1.0\RAG_CORE_BASELINE_v1.0\frontend'
npm install
npm run build
```

Do not configure `API_SECRET_KEY`, `GROQ_API_KEY`, or account passwords as Netlify `VITE_*` variables; Vite embeds frontend variables in publicly downloadable browser code. The frontend uses the API's short-lived signed bearer tokens and requires the backend to be reachable over HTTPS. `VITE_API_BASE_URL` has no localhost fallback, so an unconfigured deployment reports a clear setup error instead of trying to contact each visitor's own computer.

In the frontend, the Activity log parses the shared audit log and refreshes while the view is open. The Employee details view lists inactive accounts as well as active ones. Selecting **Restore** fills in that account's username; enter a new temporary password and submit to reactivate the login. Active duplicate usernames remain unavailable, and configured environment accounts cannot be disabled or re-created from the employee form.

## Supported uploads

- PDF
- PNG, JPEG, WebP, BMP, and TIFF images
- Excel (`.xlsx`, `.xlsm`, `.xls`)
- CSV

The generated answers use the configured Groq model and the local FAISS/BM25 retrieval pipeline. Source passages and their document locations are shown below each answer. PaddleOCR is installed on Python versions below 3.14; Python 3.14 does not currently have a compatible PaddlePaddle wheel, so OCR-based document processing requires a supported Python version.
