from datetime import datetime

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

scheduler = AsyncIOScheduler()


async def sync_builds_job():
    import logging
    from app.services.triage import TriageService
    from app.database import async_session_maker

    logger = logging.getLogger(__name__)
    try:
        async with async_session_maker() as session:
            triage_service = TriageService(session)
            result = await triage_service.sync_recent_builds(
                limit=20, nightly_daily_only=False
            )
            await session.commit()
            logger.info(f"Scheduled sync complete: {result['message']}")
    except Exception as e:
        logger.error(f"Scheduled sync failed: {e}")


async def sync_github_issues_job():
    from app.services.github import GitHubService
    from app.database import async_session_maker

    async with async_session_maker() as session:
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
    scheduler.shutdown(wait=False)
