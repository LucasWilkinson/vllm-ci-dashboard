from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.database import init_db
from app.api import builds, triages, issues, jobs, webhooks, known_failures
from app.scheduler.cron import start_scheduler, shutdown_scheduler
from app.services.triage_status import triage_status


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    start_scheduler()
    yield
    shutdown_scheduler()


app = FastAPI(
    title="CI Triage Bot",
    description="CI Triage Bot for vLLM Nightly/Daily Builds",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(builds.router, prefix="/api/builds", tags=["builds"])
app.include_router(triages.router, prefix="/api/triages", tags=["triages"])
app.include_router(issues.router, prefix="/api/issues", tags=["issues"])
app.include_router(jobs.router, prefix="/api/jobs", tags=["jobs"])
app.include_router(webhooks.router, prefix="/api/webhooks", tags=["webhooks"])
app.include_router(known_failures.router, prefix="/api/known-failures", tags=["known-failures"])


@app.get("/api/health")
async def health_check():
    import asyncio
    import httpx
    from app.config import settings

    checks: dict[str, str] = {}

    # Check Buildkite token
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                "https://api.buildkite.com/v2/user",
                headers={"Authorization": f"Bearer {settings.buildkite_api_token}"},
            )
            checks["buildkite"] = "ok" if resp.status_code == 200 else f"error:{resp.status_code}"
    except Exception as e:
        checks["buildkite"] = f"error:{e}"

    # Check GitHub CLI auth (active account)
    try:
        proc = await asyncio.create_subprocess_exec(
            "gh", "auth", "token",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=10)
        # gh auth token returns the active account's token (exit 0) or fails
        checks["github"] = "ok" if proc.returncode == 0 and stdout.strip() else "error:not authenticated"
    except Exception as e:
        checks["github"] = f"error:{e}"

    all_ok = all(v == "ok" for v in checks.values())
    return {"status": "healthy" if all_ok else "degraded", "checks": checks}


@app.websocket("/ws/triage-status")
async def websocket_triage_status(websocket: WebSocket):
    """WebSocket endpoint for real-time triage status updates."""
    await websocket.accept()
    await triage_status.add_client(websocket)
    try:
        while True:
            # Keep connection alive, ignore incoming messages
            await websocket.receive_text()
    except WebSocketDisconnect:
        triage_status.remove_client(websocket)


STATIC_DIR = Path("static")

try:
    app.mount("/assets", StaticFiles(directory=str(STATIC_DIR / "assets")), name="static-assets")
except RuntimeError:
    pass


@app.get("/{full_path:path}")
async def spa_fallback(request: Request, full_path: str):
    """Serve index.html for SPA client-side routes."""
    # Try to serve the exact static file first
    file_path = STATIC_DIR / full_path
    if file_path.is_file():
        return FileResponse(file_path)
    # Fall back to index.html for SPA routing
    index_path = STATIC_DIR / "index.html"
    if index_path.is_file():
        return FileResponse(index_path)
    return FileResponse(index_path)
