# Shipmnts Bulk Action Platform

A PostgreSQL-backed bulk action service for CRM contacts. The API accepts work quickly, persists it as bounded batches, and lets multiple workers select and process batches concurrently. The dashboard shows accounts, account limits, worker count, queue depth, and action progress.

## Architecture

![Bulk action platform architecture](bulk_action_architecture.png)

The diagram above shows the current system flow. PostgreSQL stores the source records and durable batch queue; worker processes claim separate batches and persist their outcomes. The service does not use Redis in the current implementation.

## Requirements

- Windows 10/11 with PowerShell
- Python 3.11+
- PostgreSQL installed and running locally
- A PostgreSQL role that can create databases (`CREATEDB`) for automatic first-run database creation. Alternatively, create the database manually before setup.

The project does not need Docker for local development. PostgreSQL itself must be installed separately; it owns its server data directory and database files.

## First-time setup (PowerShell)

Open PowerShell in the project folder (the folder containing this README):

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
```

Edit `.env` and set `DATABASE_URL` to your local PostgreSQL username, password, and database. For example:

```dotenv
DATABASE_URL=postgresql+psycopg://postgres:YOUR_PASSWORD@localhost:5432/bulk_actions
```

URL-encode special characters in the password (for example, `@` becomes `%40`). Keep `.env` private; it is ignored by Git.

Create the database and application tables:

```powershell
.\scripts\setup_database.ps1
```

The setup command is safe to run more than once. API startup also runs the same initialization automatically, so the setup script is optional. The database is created if it does not exist and the configured role has `CREATEDB`; otherwise, create `bulk_actions` in pgAdmin or with `createdb`, then rerun setup. Schema tables are created and updated by the application. No application data is seeded automatically.

## Run the application

Start the API and dashboard from the project folder:

```powershell
.\.venv\Scripts\uvicorn.exe app.main:app --host 127.0.0.1 --port 8000
```

Open:

- Dashboard: <http://127.0.0.1:8000/>
- API documentation: <http://127.0.0.1:8000/docs>
- Health check: <http://127.0.0.1:8000/health>

Stop the API with `Ctrl+C`. Restart after changing `.env`.

## Create accounts and seed contacts

Create an account using the dashboard or API (`POST /accounts`):

```powershell
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/accounts `
  -ContentType 'application/json' `
  -Body '{"id":"A001","name":"Demo Account"}'
```

Seed contacts using the CLI (creates the account if it does not already exist):

```powershell
.\.venv\Scripts\python.exe -m app.seed --account-id A001 --count 10000
```

Or add contacts to an existing account through `POST /accounts/{accountId}/contacts`. The seed endpoint/command remains available; the service does not insert sample contacts on startup. Database tables can be inspected with pgAdmin or `psql` (`accounts`, `contacts`, `bulk_actions`, `bulk_action_batches`, `bulk_action_logs`, and `account_rate_windows`).

## Run and scale workers

For a simple one-worker run, open a second PowerShell window in this folder:

```powershell
.\.venv\Scripts\python.exe -m app.worker
```

To scale from the dashboard, set these values in `.env`, then restart the API:

```dotenv
ENABLE_LOCAL_WORKER_CONTROL=true
MAX_LOCAL_WORKERS=8
```

When using dashboard worker scaling, do not also start `app.worker` in a terminal. Set the desired worker count in the dashboard's Worker Pool panel. To run multiple independent worker processes instead, leave local worker control disabled and launch `app.worker` in separate terminals (each process claims work from PostgreSQL). PostgreSQL uses row locks and `SKIP LOCKED` so workers claim separate batches; progress and worker assignments are persisted and visible in the dashboard and API.

## Submit a bulk action

Create an action from the dashboard, or use the API:

```powershell
$body = @{
  accountId = "A001"
  entity = "contact"
  action = "update"
  filter = @{ status = "active" }
  updates = @{ status = "inactive" }
} | ConvertTo-Json -Depth 5
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/bulk-actions `
  -ContentType 'application/json' -Body $body
```

The response contains an action ID. Inspect its progress, batches, queue, and logs in `/docs` or via:

```text
GET /bulk-actions/{actionId}
GET /bulk-actions/{actionId}/stats
GET /bulk-actions/{actionId}/batches
GET /bulk-actions/{actionId}/queue
GET /bulk-actions/{actionId}/logs
```

Account processing is rate-limited to 10,000 entity events per UTC minute, shared across workers. Duplicate contacts are identified by normalized email during selection and logged as skipped. Actions can be scheduled for a future timestamp. Retries and worker crash recovery are handled by persisted batch state and leases.

## Clean test data

There is no automatic data reset on application startup. To clear all application rows while retaining the schema and APIs, connect to the configured database and truncate the application tables in dependency order:

```sql
TRUNCATE TABLE bulk_action_logs, bulk_action_batches, bulk_actions, contacts, account_rate_windows, accounts RESTART IDENTITY;
```

This is destructive and removes all accounts, contacts, actions, and logs. Do not run it against data you need.

## Repository notes

- `.env` and virtual environments are local and excluded from Git.
- `architecture.md`, `diagram.md`, `flow.md`, `database-implementation.md`, `prd.md`, and `progress.md` document design and assignment context.
- `postman_collection.json` contains API examples.
- `load_test.py` is an optional throughput utility; use it only when you intentionally want to generate load.
- Docker files are retained for users who prefer Docker, but Docker is not required for the Windows setup above.
