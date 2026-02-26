"""Seed database by syncing real builds from Buildkite.

Syncs the last N builds on main and the last N nightly/daily builds.
"""
import asyncio
import argparse

from app.database import async_session_maker, engine, Base
from app.services.triage import TriageService


async def seed(main_limit: int = 10, nightly_limit: int = 10):
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)

    print(f"Syncing last {nightly_limit} nightly/daily builds...")
    async with async_session_maker() as session:
        triage = TriageService(session)
        result = await triage.sync_recent_builds(
            limit=nightly_limit, branch="main", nightly_daily_only=True
        )
        await session.commit()
        print(f"  Synced {result.get('synced', 0)}, triaged {result.get('triaged', 0)}")

    print(f"Syncing last {main_limit} main branch builds...")
    async with async_session_maker() as session:
        triage = TriageService(session)
        result = await triage.sync_recent_builds(
            limit=main_limit, branch="main", nightly_daily_only=False
        )
        await session.commit()
        print(f"  Synced {result.get('synced', 0)}, triaged {result.get('triaged', 0)}")

    print("Done!")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Seed database from Buildkite")
    parser.add_argument("--main", type=int, default=10, help="Number of main branch builds")
    parser.add_argument("--nightly", type=int, default=10, help="Number of nightly/daily builds")
    args = parser.parse_args()
    asyncio.run(seed(main_limit=args.main, nightly_limit=args.nightly))
