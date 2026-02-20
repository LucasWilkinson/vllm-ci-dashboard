from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

scheduler = AsyncIOScheduler()


async def sync_builds_job():
    from app.services.triage import TriageService
    from app.database import async_session_maker

    async with async_session_maker() as session:
        triage_service = TriageService(session)
        await triage_service.sync_recent_builds(limit=10)


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
        IntervalTrigger(hours=1),
        id="sync_builds",
        replace_existing=True,
    )
    scheduler.add_job(
        sync_github_issues_job,
        IntervalTrigger(hours=6),
        id="sync_github_issues",
        replace_existing=True,
    )
    scheduler.start()


def shutdown_scheduler():
    scheduler.shutdown(wait=False)
