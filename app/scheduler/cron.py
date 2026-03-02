import asyncio
import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

logger = logging.getLogger(__name__)
scheduler = AsyncIOScheduler()

# Lock to prevent overlapping syncs
_sync_lock = asyncio.Lock()


async def _do_sync_builds():
    """Run build sync on the main event loop, yielding between builds.

    Uses a fresh DB session per build and limits triage to 3 failed builds
    per cycle to avoid blocking the event loop for too long.
    """
    from sqlalchemy import select
    from app.database import get_db_session
    from app.models import Build
    from app.services.triage import TriageService

    if _sync_lock.locked():
        logger.info("Sync already in progress, skipping scheduled run")
        return

    async with _sync_lock:
        # Fetch build list
        async with get_db_session() as session:
            triage = TriageService(session)
            builds_data = await triage.buildkite.list_recent_builds(
                limit=20, branch="main", nightly_daily_only=False
            )

            stmt = select(Build.buildkite_build_number).where(
                Build.state.in_(["running", "scheduled"])
            )
            result = await session.execute(stmt)
            stuck_numbers = {row[0] for row in result.all()}
            fetched_numbers = {b["number"] for b in builds_data}
            for build_num in stuck_numbers - fetched_numbers:
                try:
                    build_data = await triage.buildkite.get_build(build_num)
                    if build_data:
                        builds_data.append(build_data)
                except Exception as e:
                    logger.warning(f"Failed to re-fetch stuck build #{build_num}: {e}")

        builds_data.sort(key=lambda b: b.get("number", 0))

        synced = 0
        triaged = 0
        max_triage_per_cycle = 3  # Limit Claude-heavy triage to avoid long blocking

        for build_data in builds_data:
            build_num = build_data.get("number", "?")
            state = build_data.get("state", "")
            try:
                # Skip triage if we've hit the per-cycle limit
                if state in ("failed", "failing") and triaged >= max_triage_per_cycle:
                    logger.info(f"Skipping triage for build #{build_num} (hit per-cycle limit)")
                    # Still sync the build metadata/jobs, just don't triage
                    async with get_db_session() as session:
                        triage = TriageService(session)
                        build = await triage._get_or_create_build(build_data)
                        full_build = await triage.buildkite.get_build(build_data["number"])
                        await triage._sync_jobs(build, full_build.get("jobs", []))
                        synced += 1
                        await session.commit()
                elif state in ("failed", "failing"):
                    async with get_db_session() as session:
                        triage = TriageService(session)
                        await triage.sync_and_triage_build(build_data)
                        triaged += 1
                        synced += 1
                        await session.commit()
                else:
                    async with get_db_session() as session:
                        triage = TriageService(session)
                        build = await triage._get_or_create_build(build_data)
                        full_build = await triage.buildkite.get_build(build_data["number"])
                        await triage._sync_jobs(build, full_build.get("jobs", []))
                        await triage._auto_resolve_known_failures(build)
                        synced += 1
                        await session.commit()
                logger.info(f"Synced build #{build_num} ({synced}/{len(builds_data)})")
            except Exception as e:
                logger.error(f"Failed to sync build #{build_num}: {e}")
                continue
            # Yield to event loop between builds so HTTP requests can be served
            await asyncio.sleep(0.1)

        logger.info(f"Scheduled sync complete: synced {synced}, triaged {triaged}")


async def sync_builds_job():
    """Scheduler entry point — fires the sync as a background task."""
    asyncio.create_task(_do_sync_builds())


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
    scheduler.shutdown(wait=False)
