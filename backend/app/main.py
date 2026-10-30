from contextlib import asynccontextmanager
from collections.abc import AsyncIterator
from urllib.parse import urlsplit

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response

from app.api import agent_runs, chat, chats, files, health, models, plans, runs, workspaces
from app.core.config import get_settings
from app.core.database import init_db
from app.core.errors import AppError, app_error_handler
from app.services.python_sandbox import cleanup_stale_workspaces


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    init_db()
    cleanup_stale_workspaces()
    yield


settings = get_settings()
app = FastAPI(title=settings.app_name, lifespan=lifespan)

app.add_exception_handler(AppError, app_error_handler)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def protect_local_api(request: Request, call_next):
    # CORS controls reading responses, but does not stop a cross-origin form from
    # initiating a command. Reject untrusted browser requests before dispatch.
    allowed_origins = set(settings.cors_origin_list)
    allowed_hosts = {"localhost", "127.0.0.1", "::1"}
    allowed_hosts.update(urlsplit(origin).hostname for origin in allowed_origins)
    hostname = request.url.hostname
    if request.client and request.client.host == "testclient":
        allowed_hosts.add("testserver")
    if hostname not in allowed_hosts:
        return JSONResponse(status_code=403, content={"code": "UNTRUSTED_HOST", "message": "Untrusted local API host."})
    origin = request.headers.get("origin")
    same_origin = f"{request.url.scheme}://{request.url.netloc}"
    if origin is not None and origin not in allowed_origins and origin != same_origin:
        return JSONResponse(status_code=403, content={"code": "UNTRUSTED_ORIGIN", "message": "This website cannot access the local agent."})
    if not origin and request.headers.get("sec-fetch-site") == "cross-site":
        return JSONResponse(status_code=403, content={"code": "UNTRUSTED_ORIGIN", "message": "Cross-site local API requests are not allowed."})
    return await call_next(request)

app.include_router(health.router)
app.include_router(models.router)
app.include_router(chats.router)
app.include_router(files.router)
app.include_router(chat.router)
app.include_router(plans.router)
app.include_router(runs.router)
app.include_router(agent_runs.router)
app.include_router(workspaces.router)


@app.get("/", include_in_schema=False)
async def root() -> dict[str, str]:
    return {
        "service": settings.app_name,
        "status": "ok",
        "health": "/api/health",
        "docs": "/docs",
    }


@app.get("/favicon.ico", include_in_schema=False)
async def favicon() -> Response:
    return Response(status_code=204)
