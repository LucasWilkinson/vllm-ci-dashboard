import asyncio
import json
import logging
from collections import OrderedDict
from datetime import datetime

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from sqlalchemy import select, or_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db, get_db_session
from app.models import Build, Job, Failure, KnownFailure
from app.schemas.build import (
    BuildResponse,
    BuildInTimeline,
    CommitTimelineEntry,
    BuildWithFailures,
    FailedJobSummary,
)
from app.services.buildkite import BuildkiteService
from app.services.github import GitHubService
from app.services.triage import TriageService

logger = logging.getLogger(__name__)
router = APIRouter()

STATE_SEVERITY = {"failed": 4, "failing": 3, "running": 2, "scheduled": 1, "passed": 0}


def _build_failed_job_summaries(build: Build) -> list[FailedJobSummary]:
    """Build FailedJobSummary list from a Build model with loaded relationships."""
    summaries = []
    for job in build.jobs:
        if job.state == "failed" and job.failures and not job.soft_failed:
            for failure in job.failures:
                kf = failure.known_failure
                linked_issue = kf.github_issue if kf else None
                retry_passed = failure.retry_passed or False
                historically_flaky = (kf.is_flaky if kf else None) or False
                if retry_passed:
                    flaky_status = "flaky"
                elif historically_flaky:
                    flaky_status = "likely_flaky"
                else:
                    flaky_status = None

                failing_test = failure.failing_test
                if isinstance(failing_test, str) and failing_test.startswith("["):
                    try:
                        failing_test = json.loads(failing_test)
                    except json.JSONDecodeError:
                        pass

                summaries.append(FailedJobSummary(
                    job_id=job.id,
                    failure_id=failure.id,
                    job_name=job.name or job.step_key or "unknown",
                    step_key=job.step_key,
                    job_url=job.web_url,
                    failure_category=failure.failure_category,
                    failure_type=failure.failure_type,
                    failing_test=failing_test,
                    error_signature=failure.error_signature,
                    error_message=failure.error_message,
                    log_excerpt=failure.log_excerpt,
                    flaky_status=flaky_status,
                    known_failure_id=kf.id if kf else None,
                    known_failure_title=kf.title if kf else None,
                    linked_issue_number=linked_issue.github_issue_number if linked_issue else None,
                    linked_issue_state=linked_issue.state if linked_issue else None,
                    linked_issue_url=linked_issue.github_issue_url if linked_issue else None,
                ))
    return summaries


@router.get("", response_model=list[BuildWithFailures])
async def list_builds(
    build_type: str | None = Query(None, description="Filter by build type"),
    triage_status: str | None = Query(None, description="Filter by triage status"),
    state: str | None = Query(None, description="Filter by build state"),
    branch: str | None = Query(None, description="Filter by branch (e.g., 'main' for nightly/daily)"),
    nightly_daily: bool = Query(False, description="Filter to only nightly/daily builds (Full CI run)"),
    limit: int = Query(50, le=200),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
):
    stmt = select(Build).options(
        selectinload(Build.jobs)
        .selectinload(Job.failures)
        .selectinload(Failure.known_failure)
        .selectinload(KnownFailure.github_issue)
    )

    if build_type:
        stmt = stmt.where(Build.build_type == build_type)
    if triage_status:
        stmt = stmt.where(Build.triage_status == triage_status)
    if state:
        stmt = stmt.where(Build.state == state)
    if branch:
        stmt = stmt.where(Build.branch == branch)
    if nightly_daily:
        stmt = stmt.where(or_(
            Build.message.ilike("%Full CI run - daily%"),
            Build.message.ilike("%Full CI run - nightly%"),
        ))

    stmt = stmt.order_by(Build.buildkite_build_number.desc()).offset(offset).limit(limit)
    result = await db.execute(stmt)
    builds = result.scalars().all()

    response = []
    for build in builds:
        response.append(BuildWithFailures(
            id=build.id,
            buildkite_build_number=build.buildkite_build_number,
            build_type=build.build_type,
            state=build.state,
            commit_sha=build.commit_sha,
            branch=build.branch,
            message=build.message,
            web_url=build.web_url,
            triage_status=build.triage_status,
            created_at=build.created_at,
            total_jobs=len(build.jobs),
            failed_jobs=_build_failed_job_summaries(build),
        ))

    return response


@router.get("/timeline", response_model=list[CommitTimelineEntry])
async def get_timeline(
    branch: str | None = Query(None, description="Filter by branch"),
    nightly_daily: bool = Query(False, description="Filter to only nightly/daily builds"),
    limit: int = Query(50, le=200),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
):
    """Get builds grouped by commit SHA for timeline display."""
    stmt = select(Build).options(
        selectinload(Build.jobs)
        .selectinload(Job.failures)
        .selectinload(Failure.known_failure)
        .selectinload(KnownFailure.github_issue)
    )

    if branch:
        stmt = stmt.where(Build.branch == branch)
    if nightly_daily:
        stmt = stmt.where(or_(
            Build.message.ilike("%Full CI run - daily%"),
            Build.message.ilike("%Full CI run - nightly%"),
        ))

    # Exclude canceled builds — they don't produce meaningful results
    stmt = stmt.where(Build.state != "canceled")

    # Fetch more than limit to account for grouping (multiple builds per commit)
    stmt = stmt.order_by(Build.buildkite_build_number.desc()).limit(limit * 3)
    result = await db.execute(stmt)
    builds = result.scalars().all()

    # Group by commit_sha, preserving order (newest first)
    commits: OrderedDict[str | None, list[Build]] = OrderedDict()
    for build in builds:
        key = build.commit_sha
        if key not in commits:
            commits[key] = []
        commits[key].append(build)

    # Fetch GitHub commits to get commit dates for all entries
    since_str = None
    if builds:
        oldest = min(builds, key=lambda b: b.created_at or datetime.max)
        if oldest.created_at:
            since_str = oldest.created_at.isoformat() + "Z"

    github_service = GitHubService(db)
    gh_commits = await github_service.list_main_commits(
        since=since_str, per_page=limit * 3,
    )

    # Build a lookup: commit SHA → {date, message} from GitHub
    gh_commit_info: dict[str, dict] = {}
    for ghc in gh_commits:
        gh_commit_info[ghc["sha"]] = ghc

    # Build timeline entries from DB builds
    build_timeline: dict[str | None, CommitTimelineEntry] = {}
    for commit_sha, commit_builds in commits.items():
        # Aggregate worst state across builds for this commit
        worst_severity = -1
        worst_state = "passed"
        all_failed_jobs: list[FailedJobSummary] = []
        build_entries: list[BuildInTimeline] = []

        for build in commit_builds:
            failed_summaries = _build_failed_job_summaries(build)
            all_failed_jobs.extend(failed_summaries)

            severity = STATE_SEVERITY.get(build.state or "", 0)
            if severity > worst_severity:
                worst_severity = severity
                worst_state = build.state or "passed"

            passed = sum(1 for j in build.jobs if j.state == "passed" or j.soft_failed)
            failed_unique = sum(1 for j in build.jobs if j.state == "failed" and not j.soft_failed)
            not_run = len(build.jobs) - passed - failed_unique

            build_entries.append(BuildInTimeline(
                build_number=build.buildkite_build_number,
                build_type=build.build_type,
                state=build.state,
                web_url=build.web_url,
                triage_status=build.triage_status,
                total_jobs=len(build.jobs),
                failed_job_count=len(failed_summaries),
                passed_job_count=passed,
                not_run_job_count=not_run,
            ))

        earliest_build = min(commit_builds, key=lambda b: b.created_at or datetime.max)

        # Get committed_at from GitHub data if available
        gh_info = gh_commit_info.get(commit_sha or "") if commit_sha else None
        committed_at = None
        if gh_info and gh_info.get("date"):
            committed_at = datetime.fromisoformat(gh_info["date"].replace("Z", "+00:00"))

        entry = CommitTimelineEntry(
            commit_sha=commit_sha,
            message=earliest_build.message,
            committed_at=committed_at,
            created_at=earliest_build.created_at,
            status=worst_state,
            builds=build_entries,
            failed_jobs=all_failed_jobs,
        )
        build_timeline[commit_sha] = entry

    # For the main (non-nightly_daily) view, fill in missing commits from GitHub
    if not nightly_daily:
        for ghc in gh_commits:
            sha = ghc["sha"]
            if sha not in build_timeline:
                committed_at = None
                if ghc.get("date"):
                    committed_at = datetime.fromisoformat(ghc["date"].replace("Z", "+00:00"))
                build_timeline[sha] = CommitTimelineEntry(
                    commit_sha=sha,
                    message=ghc["message"],
                    committed_at=committed_at,
                    created_at=None,
                    status="not_triaged",
                    builds=[],
                    failed_jobs=[],
                )

    # Sort by committed_at (newest first), fall back to created_at
    # Strip timezone info for consistent comparison (DB dates are naive, GitHub dates are aware)
    def _sort_key(e: CommitTimelineEntry) -> datetime:
        dt = e.committed_at or e.created_at
        if dt is None:
            return datetime.min
        if dt.tzinfo is not None:
            dt = dt.replace(tzinfo=None)
        return dt

    timeline = sorted(
        build_timeline.values(),
        key=_sort_key,
        reverse=True,
    )
    return timeline[offset:offset + limit]


@router.get("/{build_number}", response_model=BuildResponse)
async def get_build(build_number: int, db: AsyncSession = Depends(get_db)):
    stmt = (
        select(Build)
        .where(Build.buildkite_build_number == build_number)
        .options(
            selectinload(Build.jobs)
            .selectinload(Job.failures)
        )
    )
    result = await db.execute(stmt)
    build = result.scalar_one_or_none()

    if not build:
        raise HTTPException(status_code=404, detail="Build not found")

    return build


_sync_lock = asyncio.Lock()


async def _sync_builds_background(limit: int, branch: str, nightly_daily_only: bool):
    """Background task for syncing builds.

    Uses a fresh DB session per build to avoid holding long transactions
    during Claude triage (which can take minutes per build).
    """
    if _sync_lock.locked():
        logger.warning("Sync already in progress, skipping")
        return
    async with _sync_lock:
        # First, fetch the build list using a short-lived session
        async with get_db_session() as session:
            triage = TriageService(session)
            builds_data = await triage.buildkite.list_recent_builds(
                limit=limit, branch=branch, nightly_daily_only=nightly_daily_only
            )

            # Also re-sync builds stuck in running/scheduled state
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
            try:
                # Fresh session per build — no long-held transactions
                async with get_db_session() as session:
                    triage = TriageService(session)
                    state = build_data.get("state", "")
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

        logger.info(f"Background sync complete: synced {synced}, triaged {triaged}")


@router.post("/sync")
async def sync_builds(
    background_tasks: BackgroundTasks,
    limit: int = Query(20, le=200),
    branch: str = Query("main", description="Branch to sync (default: main for nightly/daily)"),
    nightly_daily_only: bool = Query(False, description="Only sync nightly/daily builds"),
    background: bool = Query(False, description="Run sync in background (returns immediately)"),
    db: AsyncSession = Depends(get_db),
):
    if background:
        background_tasks.add_task(_sync_builds_background, limit, branch, nightly_daily_only)
        return {
            "synced": 0,
            "triaged": 0,
            "message": f"Background sync started for {limit} builds",
        }

    triage = TriageService(db)
    return await triage.sync_recent_builds(
        limit=limit, branch=branch, nightly_daily_only=nightly_daily_only
    )


@router.post("/{build_number}/sync")
async def sync_single_build(
    build_number: int,
    db: AsyncSession = Depends(get_db),
):
    """Sync a specific build by its Buildkite build number."""
    buildkite = BuildkiteService()
    triage = TriageService(db)

    build_data = await buildkite.get_build(build_number)
    if not build_data:
        raise HTTPException(status_code=404, detail="Build not found in Buildkite")

    state = build_data.get("state", "")
    triaged = False

    if state in ("failed", "failing"):
        await triage.sync_and_triage_build(build_data)
        triaged = True
    else:
        build = await triage._get_or_create_build(build_data)
        await triage._sync_jobs(build, build_data.get("jobs", []))

    await db.commit()

    return {
        "synced": True,
        "triaged": triaged,
        "message": f"Synced build #{build_number}" + (" and triaged" if triaged else ""),
    }
