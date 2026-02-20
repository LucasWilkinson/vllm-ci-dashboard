"""Real-time triage status tracking with WebSocket support."""
import asyncio
import json as json_mod
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


LOG_BUFFER_MAX = 500


@dataclass
class TriageProgress:
    """Progress of a single build triage."""
    build_number: int
    total_jobs: int = 0
    completed_jobs: int = 0
    current_job: Optional[str] = None
    started_at: datetime = field(default_factory=datetime.utcnow)
    status: str = "pending"  # pending, running, completed, error
    phase: str = "fetching_logs"  # fetching_logs, analyzing, processing


class TriageStatusManager:
    """Manages triage status and broadcasts updates to WebSocket clients."""

    def __init__(self):
        self._active_triages: dict[int, TriageProgress] = {}
        self._clients: set = set()
        self._lock = asyncio.Lock()
        self._log_lines: deque[dict] = deque(maxlen=LOG_BUFFER_MAX)

    async def add_client(self, websocket):
        """Register a new WebSocket client."""
        self._clients.add(websocket)
        # Send current status immediately
        await self._send_to_client(websocket, self._get_status_message())
        # Send log history so new clients can see recent activity
        if self._log_lines:
            await self._send_to_client(websocket, {
                "type": "triage_log",
                "lines": list(self._log_lines),
            })

    def remove_client(self, websocket):
        """Unregister a WebSocket client."""
        self._clients.discard(websocket)

    async def log(self, build_number: int, message: str, level: str = "info"):
        """Add a log line and broadcast to clients."""
        line = {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "build_number": build_number,
            "message": message,
            "level": level,
        }
        self._log_lines.append(line)
        await self._broadcast_message({
            "type": "triage_log",
            "lines": [line],
        })

    async def start_triage(self, build_number: int, total_jobs: int):
        """Start tracking a new triage."""
        async with self._lock:
            self._active_triages[build_number] = TriageProgress(
                build_number=build_number,
                total_jobs=total_jobs,
                status="running"
            )
        await self._broadcast()

    async def update_phase(self, build_number: int, phase: str):
        """Update the triage phase (fetching_logs, analyzing, processing)."""
        async with self._lock:
            if build_number in self._active_triages:
                self._active_triages[build_number].phase = phase
        await self._broadcast()

    async def update_job(self, build_number: int, job_name: str):
        """Update progress for a job being triaged."""
        async with self._lock:
            if build_number in self._active_triages:
                triage = self._active_triages[build_number]
                triage.current_job = job_name
                triage.completed_jobs += 1
                triage.phase = "analyzing"
        await self._broadcast()

    async def complete_triage(self, build_number: int):
        """Mark a triage as complete."""
        async with self._lock:
            if build_number in self._active_triages:
                self._active_triages[build_number].status = "completed"
                self._active_triages[build_number].current_job = None
        await self._broadcast()
        # Remove after a short delay so clients see the completion
        await asyncio.sleep(2)
        async with self._lock:
            self._active_triages.pop(build_number, None)
        await self._broadcast()

    async def error_triage(self, build_number: int, error: str):
        """Mark a triage as errored."""
        async with self._lock:
            if build_number in self._active_triages:
                self._active_triages[build_number].status = "error"
                self._active_triages[build_number].current_job = error
        await self._broadcast()

    def _get_status_message(self) -> dict:
        """Get current status as a message dict."""
        return {
            "type": "triage_status",
            "active_triages": [
                {
                    "build_number": t.build_number,
                    "total_jobs": t.total_jobs,
                    "completed_jobs": t.completed_jobs,
                    "current_job": t.current_job,
                    "status": t.status,
                    "phase": t.phase,
                }
                for t in self._active_triages.values()
            ]
        }

    async def _broadcast(self):
        """Broadcast current status to all connected clients."""
        if not self._clients:
            return
        message = self._get_status_message()
        await self._broadcast_message(message)

    async def _broadcast_message(self, message: dict):
        """Broadcast an arbitrary message to all connected clients."""
        if not self._clients:
            return
        disconnected = set()
        for client in self._clients:
            try:
                await self._send_to_client(client, message)
            except Exception:
                disconnected.add(client)
        for client in disconnected:
            self._clients.discard(client)

    async def _send_to_client(self, websocket, message: dict):
        """Send a message to a single client."""
        await websocket.send_text(json_mod.dumps(message))


# Global singleton
triage_status = TriageStatusManager()
