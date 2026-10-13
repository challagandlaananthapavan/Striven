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

## Public Netlify frontend and API

The existing Streamlit application remains unchanged. The separate React/Vite frontend is in `frontend/`, and its FastAPI service is in `api/`. For public access from phones and computers on any network, host the API on Render with persistent disk storage and host the static frontend on Netlify. The `render.yaml` blueprint configures the API service, HTTPS health check, model-cache paths, and a persistent `/var/data` disk. `netlify.toml` configures the frontend build. The frontend build output is `frontend/dist/`.

### Deploy the API to Render

1. Push the project to GitHub, including `api/`, `src/`, `frontend/`, `render.yaml`, and `netlify.toml`. Keep `.env` and all local secrets out of Git.
2. In Render, select **New → Blueprint**, connect the repository, and apply its `render.yaml`.
3. Enter private values for `ADMIN_USERNAME`, `ADMIN_PASSWORD`, `EMPLOYEE_USERNAME`, `EMPLOYEE_PASSWORD`, and `GROQ_API_KEY` when Render requests them. Render generates `API_SECRET_KEY`. Use new, strong passwords and a newly generated Groq key; never put these values in the frontend or Netlify.
4. Wait for the service to deploy. Render assigns an HTTPS URL such as `https://strivenrag-api.onrender.com`. Confirm `https://<your-api-host>/api/health` returns `{"status":"ok"}`.

The blueprint runs one API worker and attaches a 20 GB persistent disk at `/var/data`. Accounts, activity logs, uploaded files, processed data, vector indexes, and downloaded model files use this mount. It selects a paid Render `standard` service because persistent disks are not available on free web services. Keep a backup of the persistent disk; deleting/replacing it loses the server's accounts, documents, and index.

### Connect and deploy the Netlify frontend

1. In **Netlify → Site configuration → Environment variables**, set `VITE_API_BASE_URL` to the Render HTTPS base URL, without a path (for example, `https://strivenrag-api.onrender.com`).
2. Trigger a new Netlify deploy so Vite embeds the public API URL into the frontend bundle.
3. Open `https://strivenrag.netlify.app/` on your phone or another computer and sign in. The API CORS allowlist in `render.yaml` includes this exact Netlify origin.
4. The Render persistent disk starts empty. Sign in using the bootstrap accounts configured in Render and upload/index documents through the frontend. Local `logs/accounts.sqlite3`, `data/`, and `vector_store/` are ignored by Git and are not transferred automatically.

The following commands are for a local production-build check. Netlify builds the frontend from the repository using `netlify.toml`. If deploying manually, publish the contents of `frontend/dist/`.

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
