import asyncio
import logging
import sys

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

logger = logging.getLogger(__name__)
scheduler = AsyncIOScheduler()

_sync_proc: asyncio.subprocess.Process | None = None


async def _monitor_sync(proc: asyncio.subprocess.Process):
    """Monitor sync subprocess in background, log output when done."""
    global _sync_proc
    try:
        stdout, _ = await proc.communicate()
        if stdout:
            for line in stdout.decode(errors="replace").strip().split("\n"):
                if line.strip():
                    logger.info(f"[sync] {line}")
        if proc.returncode != 0:
            logger.error(f"Sync worker exited with code {proc.returncode}")
    finally:
        _sync_proc = None


async def sync_builds_job():
    """Launch sync as a subprocess so it never blocks the server event loop."""
    global _sync_proc

    # Skip if a sync is already running
    if _sync_proc is not None and _sync_proc.returncode is None:
        logger.info("Sync already in progress, skipping")
        return

    _sync_proc = await asyncio.create_subprocess_exec(
        sys.executable, "-m", "app.scheduler.sync_worker",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    # Fire and forget — monitor in background
    asyncio.create_task(_monitor_sync(_sync_proc))


async def sync_github_issues_job():
    from app.database import get_db_session
    from app.services.github import GitHubService

    async with get_db_session() as session:
        github_service = GitHubService(session)
        await github_service.sync_issue_states()
        await session.commit()


def start_scheduler():
    scheduler.add_job(
        sync_builds_job,
        IntervalTrigger(minutes=15),
        id="sync_builds",
        replace_existing=True,
    )
    scheduler.add_job(
        sync_github_issues_job,
        IntervalTrigger(minutes=15),
        id="sync_github_issues",
        replace_existing=True,
    )
    scheduler.start()


def shutdown_scheduler():
    global _sync_proc
    if _sync_proc and _sync_proc.returncode is None:
        _sync_proc.terminate()
    scheduler.shutdown(wait=False)
