from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.database import init_db
from app.api import builds, triages, issues, jobs, webhooks
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


@app.get("/api/health")
async def health_check():
    return {"status": "healthy"}


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


try:
    app.mount("/", StaticFiles(directory="static", html=True), name="static")
except RuntimeError:
    pass
