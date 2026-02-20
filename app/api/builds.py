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

    timeline = []
    for commit_sha, commit_builds in commits.items():
        if len(timeline) >= limit:
            break

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

        # Use the earliest created_at and first message from this commit's builds
        earliest_build = min(commit_builds, key=lambda b: b.created_at or datetime.max)

        timeline.append(CommitTimelineEntry(
            commit_sha=commit_sha,
            message=earliest_build.message,
            created_at=earliest_build.created_at,
            status=worst_state,
            builds=build_entries,
            failed_jobs=all_failed_jobs,
        ))

    # Apply offset after grouping
    return timeline[offset:]


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


async def _sync_builds_background(limit: int, branch: str, nightly_daily_only: bool):
    """Background task for syncing builds."""
    async with get_db_session() as session:
        triage = TriageService(session)
        result = await triage.sync_recent_builds(
            limit=limit, branch=branch, nightly_daily_only=nightly_daily_only
        )
        logger.info(f"Background sync complete: {result['message']}")


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
