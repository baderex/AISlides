# Native production deployment

Build Angular with `npm ci` and `npm run build` in `frontend`. Serve `frontend/dist/studio/browser` with an HTTPS reverse proxy and route application paths to `index.html`. Proxy `/api/`, `/health/live` and `/health/ready` to the private ASGI listener. Disable response buffering and compression for event streams. Allow multipart request bodies large enough for `ASSET_LIMIT_BYTES` (12 MiB by default) plus multipart overhead, and set an upstream read timeout appropriate for synchronous exports and conversational requests.

## Runtime configuration

- Install the backend package and run `python -m playwright install --with-deps --only-shell chromium` on Linux workers. Install the browser for the worker service account or share its configured Playwright browser cache. Workers may also use an installed Chromium executable selected with `HTML_LAYOUT_BROWSER`. The browser measures slides locally.
- Set `APP_ENV=production`, `PUBLIC_ORIGIN=https://studio.example.com` and `ALLOW_SIGNUP=false`. Production requires an account session. Set `PUBLIC_ORIGIN` to the browser-visible HTTPS origin so the API accepts same-origin writes.
- `DATABASE_URL=postgresql+psycopg://studio:...@db.example.com/studio`. Enable TLS and use a least-privileged service role.
- `JOB_BACKEND=redis` and `REDIS_URL=rediss://...`. Redis dispatches queued job IDs. SQL keeps the authoritative job records, lease tokens and checkpoints. Workers recover queued jobs through SQL if a dispatch is missed.
- `STORAGE_BACKEND=s3`, `S3_BUCKET=...`, and optional `S3_ENDPOINT=...` for an S3-compatible service. Use AWS-standard credential sources scoped to the bucket. Keep the bucket private and enable versioning according to your retention requirements.
- Set `LLM_API_URL` and `LLM_MODEL_ID` in `backend/.env`. The application reads those two settings from dotenv files. Supply `LLM_API_KEY` through environment-based secrets. Set token and cost budgets with `AI_JOB_TOKEN_BUDGET` and `AI_JOB_COST_BUDGET`. Cost estimates use the operator-supplied `AI_INPUT_USD_PER_MILLION` and `AI_OUTPUT_USD_PER_MILLION`.
- To enable reviewed PowerPoint downloads, provide LibreOffice Impress and the deck fonts on the API host. Run the unprivileged API service with resource limits for document rendering. Set the proxy timeout longer than `POWERPOINT_REVIEW_TIMEOUT` (45 seconds by default) to allow time for conversion and inspection.

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

Each worker processes one checkpoint at a time. Scale worker processes to match database capacity and model quotas. Apply per-account and per-IP rate limits at the reverse proxy. The application records workflow and edit events and provides a process-local sign-in limit. Log provider failures and request metadata without logging credentials, prompts or upload contents.

Monitor `/health/live` and `/health/ready`, worker leases and failure events. Readiness checks SQL, storage and a recent worker heartbeat. It also pings Redis when enabled. The `ai_provider` field reports configuration status. Verify provider connectivity with an actual generation job. Back up the database and objects together with versioned artifacts. Test restoration into a private staging environment before upgrades. Generated export records and objects expire according to `ARTIFACT_RETENTION_DAYS`. Uploaded source assets are retained for saved decks.

Verify PostgreSQL, Redis and S3 against the chosen deployment environment before opting into that profile.
