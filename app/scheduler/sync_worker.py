"""Standalone sync worker — runs build sync in its own process.

Launched by the scheduler via subprocess so it never blocks the web server.
"""
import asyncio
import logging
import sys

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)


async def run_sync():
    from sqlalchemy import select
    from app.database import get_db_session
    from app.models import Build
    from app.services.triage import TriageService

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
    for build_data in builds_data:
        build_num = build_data.get("number", "?")
        state = build_data.get("state", "")
        try:
            async with get_db_session() as session:
                triage = TriageService(session)
                if state in ("failed", "failing"):
                    await triage.sync_and_triage_build(build_data)
                    triaged += 1
                else:
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

    logger.info(f"Sync complete: synced {synced}, triaged {triaged}")


if __name__ == "__main__":
    asyncio.run(run_sync())
