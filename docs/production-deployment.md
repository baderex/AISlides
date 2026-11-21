# Native production deployment

Build Angular with `npm ci` and `npm run build` in the frontend directory. Serve `dist/studio/browser` with an HTTPS reverse proxy and route application paths to `index.html`. Proxy `/api/` and health endpoints to the private ASGI listener. Disable response buffering and compression for event streams. Allow multipart bodies large enough for the chosen upload limit and set an upstream read timeout appropriate for synchronous exports and conversational requests.

## Runtime configuration

- `APP_ENV=production`, `PUBLIC_ORIGIN=https://studio.example.com`, `ALLOW_SIGNUP=false`.
- `DATABASE_URL=postgresql+psycopg://studio:...@db.example.com/studio`. Enable TLS and use a least-privileged service role.
- `JOB_BACKEND=redis` and `REDIS_URL=rediss://...`. Redis dispatches queued job IDs. SQL keeps the authoritative job records, lease tokens and checkpoints. Workers recover queued jobs through SQL if a dispatch is missed.
- `STORAGE_BACKEND=s3`, `S3_BUCKET=...`, and optional `S3_ENDPOINT=...` for an S3-compatible service. Use AWS-standard credential sources scoped to the bucket. Keep the bucket private and enable versioning according to your retention requirements.
- Configure provider credentials through environment-based secrets. Set token and cost budgets with `AI_JOB_TOKEN_BUDGET` and `AI_JOB_COST_BUDGET`. Cost estimates use the operator-supplied `AI_INPUT_USD_PER_MILLION` and `AI_OUTPUT_USD_PER_MILLION`.
- To enable reviewed PowerPoint downloads, provide LibreOffice Impress and the deck fonts on the API host. Run the unprivileged API service with resource limits for document rendering. Set the proxy timeout longer than `POWERPOINT_REVIEW_TIMEOUT` (45 seconds by default) to allow time for conversion and inspection.

From the backend working directory, run `alembic upgrade head`, `python -m app.cli seed`, then `python -m app.cli create-user user@example.com` to provision an account. Secure session cookies require HTTPS.

## Supervision

Run the two commands below in separate systemd services, with an absolute Python path, an unprivileged user, an `EnvironmentFile`, `WorkingDirectory` pointing to the backend, `Restart=on-failure`, and an appropriate `MemoryMax` and `CPUQuota`.

```sh
.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 2
.venv/bin/python -m app.worker
```

Each worker processes one checkpoint at a time. Scale worker processes to match database capacity and model quotas. Apply per-account and per-IP rate limits at the reverse proxy. The application records workflow and edit events and provides a process-local sign-in limit. Log provider failures and request metadata without logging credentials, prompts or upload contents.

Monitor `/health/live` and `/health/ready`, worker leases and failure events. Back up the database and objects together with versioned artifacts. Test restoration into a private staging environment before upgrades. Generated export records and objects expire according to `ARTIFACT_RETENTION_DAYS`. Uploaded source assets are retained for saved decks.

Verify PostgreSQL, Redis and S3 against the chosen deployment environment before opting into that profile.
