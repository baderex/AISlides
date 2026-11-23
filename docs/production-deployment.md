# Native production deployment

Build Angular with `npm ci` and `npm run build` in `frontend`. Serve `frontend/dist/studio/browser` with an HTTPS reverse proxy and route application paths to `index.html`. Proxy `/api/`, `/health/live` and `/health/ready` to the private ASGI listener. Disable response buffering and compression for event streams. Allow multipart request bodies large enough for `ASSET_LIMIT_BYTES` (12 MiB by default) plus multipart overhead, and set an upstream read timeout long enough for queued file conversions, preview requests and conversational edits.

## Runtime configuration

- Install the backend package and run `python -m playwright install --with-deps --only-shell chromium` on Linux workers. Install the browser for the worker service account or share its configured Playwright browser cache. Workers may also use an installed Chromium executable selected with `HTML_LAYOUT_BROWSER`. The browser measures slides locally.
- Set `APP_ENV=production` and `PUBLIC_ORIGIN=https://studio.example.com`. Production requires an account session and disables public registration by default. Set `ALLOW_SIGNUP=true` to enable registration explicitly. Set `PUBLIC_ORIGIN` to the browser-visible HTTPS origin so the API accepts same-origin writes.
- `DATABASE_URL=postgresql+psycopg://studio:...@db.example.com/studio`. Enable TLS and use a least-privileged service role.
- `JOB_BACKEND=redis` and `REDIS_URL=rediss://...`. Redis dispatches queued job IDs. SQL keeps the authoritative job records, lease tokens and checkpoints. Workers recover queued jobs through SQL if a dispatch is missed.
- `STORAGE_BACKEND=s3`, `S3_BUCKET=...`, and optional `S3_ENDPOINT=...` for an S3-compatible service. Use AWS-standard credential sources scoped to the bucket. Keep the bucket private and enable versioning according to your retention requirements.
- Set `LLM_API_URL` and `LLM_MODEL_ID` in `backend/.env`. The application reads those two settings from dotenv files. Supply `LLM_API_KEY` through environment-based secrets. Set token and cost budgets with `AI_JOB_TOKEN_BUDGET` and `AI_JOB_COST_BUDGET`. Set `AI_INPUT_USD_PER_MILLION`, `AI_OUTPUT_USD_PER_MILLION`, `IMAGE_COST_USD` and `RESEARCH_COST_USD` to match your provider contracts. The application defaults are budgeting assumptions of $2, $8, $0.10 and $0.01 respectively.
- `ACCOUNT_DAILY_BUDGET_USD` defaults to $25 per account and `PROVIDER_DAILY_BUDGET_USD` to $100 across accounts. Each model attempt reserves a conservative input estimate and its maximum output before dispatch. Reported usage releases unused funds. Unknown charges, including interrupted requests, retain their reservation through the end of the UTC day. Image generation and research reserve the configured per-call price. Providers control final billing.
- `ACCOUNT_AI_REQUESTS_DAILY` (100), `ACCOUNT_AI_REQUESTS_PER_MINUTE` (12) and `ACCOUNT_AI_CONCURRENCY` (4) apply to generation and conversational edits together. Waiting generation jobs occupy a slot until answered and completed or cancelled. A retry of a failed or cancelled generation consumes another request. An explicit retry after failure starts a fresh `AI_JOB_TOKEN_BUDGET` allowance. Automatic retries and cancellation resumes share the current allowance. Total usage, the job cost cap and daily spend limits span all attempts. Counters and admission checks are shared across processes in SQL.
- To enable reviewed PowerPoint downloads, install LibreOffice Impress and the deck fonts on the workers. When API and worker hosts differ, set `POWERPOINT_RENDERER` to the worker executable path in the shared configuration. Run workers with resource limits for document rendering. Set the proxy timeout longer than `POWERPOINT_REVIEW_TIMEOUT` (45 seconds by default) to allow time for conversion and inspection.

From `backend`, prepare the Python environment and run the initial database and account setup with the same configuration as the API and worker.

```sh
python -m venv .venv
.venv/bin/python -m pip install -e .
.venv/bin/python -m alembic upgrade head
.venv/bin/python -m app.cli seed
.venv/bin/python -m app.cli create-user user@example.com
```

## Supervision

Run the two commands below in separate systemd services. Use an absolute Python path, an unprivileged user, the same runtime settings for both services, `WorkingDirectory` pointing to `backend`, `Restart=on-failure`, and appropriate `MemoryMax` and `CPUQuota` limits.

```sh
.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 2
.venv/bin/python -m app.worker
```

Each worker processes one checkpoint or file job at a time, alternating queues when both have work. Heartbeats renew the active claim using its ownership token. The API applies a SQL-backed sign-in and registration limit of 10 attempts per client IP per minute. Configure the proxy and ASGI server to forward and trust client addresses appropriately.

The editor queues every export. Direct API requests also queue decks at or above `EXPORT_QUEUE_SLIDE_THRESHOLD` (8 slides by default), branded PDF exports, and requests with `background=true`. A queued export returns HTTP 202 and a status URL in `Location`. Poll until the state is `complete` and follow `result.download`. Failed jobs report a message in `result.detail`. Smaller direct exports return the file immediately. Document extraction, uncached slide previews, template artwork and PowerPoint review also use the worker. `ARTIFACT_QUEUE_CAPACITY` defaults to 32 queued and running jobs. Each file attempt has a wall-clock limit of `ARTIFACT_TIMEOUT_SECONDS` (180 seconds), with one retry for transient failures.

PostgreSQL delivers committed event notifications through LISTEN/NOTIFY. SQLite and listener recovery use a shared cursor check per API process. Opening or reconnecting an editor replays the newest `EVENT_REPLAY_LIMIT` events (100 by default). The worker expires events after `EVENT_RETENTION_DAYS` (30 by default). Log provider failures and request metadata without logging credentials, prompts or upload contents.

Monitor `/health/live` and `/health/ready`, worker leases and failure events. Readiness checks SQL, storage and a recent worker heartbeat. It also pings Redis when enabled. The `ai_provider` field reports configuration status. Verify provider connectivity with an actual generation job. Back up the database and objects together with versioned artifacts. Test restoration into a private staging environment before upgrades. Generated export records and objects expire according to `ARTIFACT_RETENTION_DAYS`. The default `ACCOUNT_ASSET_LIMIT_BYTES` is 512 MiB per account. Current decks, historical versions, job checkpoints and reusable skills hold asset references. Deletion releases the last reference and schedules removal of the bytes. Unattached uploads have a grace period of `ORPHAN_GRACE_HOURS` (24 by default). Daily reconciliation checks the managed `framecraft-objects/` prefix for interrupted writes. Pre-upgrade files remain readable at their original keys. For versioned S3 buckets, configure lifecycle expiration for noncurrent object versions according to your retention policy.

Verify PostgreSQL, Redis and S3 against the chosen deployment environment before opting into that profile.
