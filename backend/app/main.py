import logging
from urllib.parse import urlsplit

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.api import api
from app.config import settings
from app.db import Session
from app.presentations import Conflict
from app.providers import ProviderError, provider_configured
from app.queue import ready
from app.storage import Storage

log = logging.getLogger(__name__)
app = FastAPI(title="Framecraft Studio", version="0.1.0")
app.include_router(api)


@app.middleware("http")
async def request_boundary(request: Request, call_next):
    if request.method in {"POST", "PATCH", "PUT", "DELETE"}:
        origin = request.headers.get("origin")
        allowed = settings().public_origin
        if origin:
            # The public origin is required for production proxy deployments.
            same_origin = allowed and origin == allowed.rstrip("/")
            if not allowed and settings().app_env != "production":
                parsed = urlsplit(origin)
                same_origin = parsed.netloc == request.headers.get("host") or (
                    parsed.hostname in {"localhost", "127.0.0.1"} and parsed.port in {4200, 8000})
            if not same_origin:
                return JSONResponse({"detail": "Request origin is not trusted."}, 403)
        length = request.headers.get("content-length")
        if length and length.isdecimal():
            if int(length) > settings().asset_limit_bytes + 65536:
                return JSONResponse({"detail": "Request exceeds the configured size limit."}, 413)
        elif request.url.path == "/api/v1/assets":
            return JSONResponse({"detail": "File uploads require a content length."}, 411)
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "same-origin"
    response.headers["X-Frame-Options"] = "DENY"
    return response


@app.exception_handler(Conflict)
async def conflict_error(request, error):
    return JSONResponse({"detail": str(error)}, status_code=409)


@app.exception_handler(ProviderError)
async def provider_error(request, error):
    return JSONResponse({"detail": str(error)}, status_code=503)


@app.exception_handler(ValueError)
async def invalid_value(request, error):
    return JSONResponse({"detail": str(error)}, status_code=422)


@app.get("/health/live")
def live():
    return {"status": "ok"}


@app.get("/health/ready")
def readiness():
    services = {}
    for name in ("database", "storage", "worker"):
        try:
            if name == "storage":
                Storage().check()
                services[name] = "ok"
            else:
                with Session() as db:
                    services[name] = ("ok" if ready(db) else "unavailable") if name == "worker" else (
                        "ok" if db.scalar(text("SELECT 1")) == 1 else "unavailable")
        except Exception:
            log.warning("unavailable service=%s", name, exc_info=True)
            services[name] = "unavailable"
    ok = all(status == "ok" for status in services.values())
    ai = "configured" if provider_configured(settings().ai_provider) else "missing credentials"
    return JSONResponse({"status": "ok" if ok else "unavailable", **services, "ai_provider": ai}, 200 if ok else 503)
