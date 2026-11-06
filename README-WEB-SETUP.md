# ByteVCS Web Application Setup

This adds a working React dashboard for the existing ByteVCS Python engine. The dashboard reads real repository information from the FastAPI backend and can stage files, unstage files, create commits, create branches, checkout branches, merge branches, and view diffs.

## 1. Requirements

- Python 3.10+ (3.11 or 3.12 recommended)
- Node.js 20.19+ or 22.12+
- npm

## 2. Set up Python and the API (Windows PowerShell)

Open PowerShell in the **ByteVCS project root** (the folder containing `pyproject.toml`):

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e .
python -m pip install -r api\requirements.txt
Copy-Item .env.example .env
```

If PowerShell blocks activation, use this for the current terminal only:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\.venv\Scripts\Activate.ps1
```

The included `web-demo` repository is selected by default. To point the API at another existing ByteVCS repository, edit `BYTEVCS_REPOSITORY` in `.env`. Use an absolute path, for example:

```dotenv
BYTEVCS_REPOSITORY=C:\Users\YourName\ByteVCS\web-demo
```

Start the backend from the project root:

```powershell
python -m uvicorn api.main:app --reload
```

Check `http://127.0.0.1:8000/api/health` and open `http://127.0.0.1:8000/docs` for interactive API documentation.

## 3. Start the website

Open a **second** PowerShell terminal:

```powershell
cd frontend
npm install
Copy-Item .env.example .env
npm run dev
```

Open the local URL Vite prints (normally `http://localhost:5173`). Keep the API terminal running too.

## 4. Optional Supabase PostgreSQL connection

The dashboard and local version-control engine work without Supabase. Supabase is optional and is used here for hosted application metadata, not for storing the ByteVCS object database or syncing repository contents.

1. Create a Supabase project.
2. In the Supabase dashboard, open **Project Settings → Database → Connection string**.
3. Copy the PostgreSQL URI. Prefer the session pooler URI for application connections if the direct database host is not reachable from your network.
4. Put it in the root `.env` as `DATABASE_URL`. The driver prefix may be `postgresql://` or `postgres://`; the backend normalizes it for psycopg 3.
5. Restart Uvicorn. The API attempts to create the `users` and `repositories` metadata tables. Check `http://127.0.0.1:8000/api/database` for connection status.

Example shape only — replace every placeholder with values from your own Supabase project:

```dotenv
DATABASE_URL=postgresql://postgres.PROJECT_REF:YOUR_DATABASE_PASSWORD@YOUR_POOLER_HOST:5432/postgres?sslmode=require
```

Never commit `.env` or share your database password. The frontend must never receive the database connection string or a Supabase service-role key.

## 5. What is connected, and what is not

### Implemented in this project

- Existing ByteVCS Python engine remains the source of truth for local repository operations.
- FastAPI routes read live repository status, files, branches, commits and diffs.
- Dashboard actions call the backend to stage/unstage files, commit, create/check out branches and merge.
- Uvicorn serves the FastAPI app.
- Optional PostgreSQL connection check and metadata-table initialization when `DATABASE_URL` is configured.

### Still future work

- Supabase metadata tables are initialized, but account registration, login, ownership permissions and multi-user repository management are not wired into the dashboard.
- Actual network `push`, `pull` and `clone` are not implemented. The existing `remote add/list/remove` commands only store remote configuration.
- Repository objects and snapshots remain in the local `.bytevcs` directory. Supabase PostgreSQL is not currently used as an object store.
- The API points to one configured local repository and is intended for localhost demonstration. Do not expose it publicly without authentication, authorization, per-user repository isolation, request limits and secure deployment configuration.

## 6. Test the existing engine

From the project root with the virtual environment active:

```powershell
python -m pytest -q
```

The source archive supplied for this integration passed all 9 existing tests.
