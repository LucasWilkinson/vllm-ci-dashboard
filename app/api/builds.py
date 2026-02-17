from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, func, or_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.models import Build, Job, Failure, ErrorSignature
from app.models.github import FailureIssueLink
from app.schemas.build import (
    BuildResponse,
    BuildSummary,
    DashboardSummary,
    BuildWithFailures,
    FailedJobSummary,
    CurrentIssue,
    CurrentIssueGroup,
    FailingBuildInfo,
)
from app.services.buildkite import BuildkiteService
from app.services.triage import TriageService

router = APIRouter()


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
        .selectinload(Failure.issue_links)
        .selectinload(FailureIssueLink.github_issue)
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
        failed_job_summaries = []
        for job in build.jobs:
            if job.state == "failed" and job.failures and not job.soft_failed:
                # Each job can have multiple failures with different root causes
                for failure in job.failures:
                    linked_issue = None
                    if failure.issue_links:
                        link = failure.issue_links[0]
                        if link.github_issue:
                            linked_issue = link.github_issue

                    # Deserialize failing_test if stored as JSON
                    failing_test = failure.failing_test
                    if isinstance(failing_test, str) and failing_test.startswith("["):
                        import json
                        try:
                            failing_test = json.loads(failing_test)
                        except json.JSONDecodeError:
                            pass

                    failed_job_summaries.append(FailedJobSummary(
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
                        is_flaky=failure.is_flaky or False,
                        linked_issue_number=linked_issue.github_issue_number if linked_issue else None,
                        linked_issue_state=linked_issue.state if linked_issue else None,
                        linked_issue_url=linked_issue.github_issue_url if linked_issue else None,
                    ))

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
            failed_jobs=failed_job_summaries,
        ))

    return response


@router.get("/dashboard/summary", response_model=DashboardSummary)
async def get_dashboard_summary(db: AsyncSession = Depends(get_db)):
    today = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)

    total_stmt = select(func.count(Build.id))
    total_result = await db.execute(total_stmt)
    total_builds = total_result.scalar() or 0

    pending_stmt = select(func.count(Build.id)).where(Build.triage_status == "pending")
    pending_result = await db.execute(pending_stmt)
    pending_triages = pending_result.scalar() or 0

    completed_stmt = select(func.count(Build.id)).where(Build.triage_status == "completed")
    completed_result = await db.execute(completed_stmt)
    completed_triages = completed_result.scalar() or 0

    infra_stmt = (
        select(func.count(Failure.id))
        .join(Job, Failure.job_id == Job.id)
        .join(Build, Job.build_id == Build.id)
        .where(Build.created_at >= today)
        .where(Failure.failure_category == "infra")
    )
    infra_result = await db.execute(infra_stmt)
    infra_failures_today = infra_result.scalar() or 0

    test_stmt = (
        select(func.count(Failure.id))
        .join(Job, Failure.job_id == Job.id)
        .join(Build, Job.build_id == Build.id)
        .where(Build.created_at >= today)
        .where(Failure.failure_category == "test")
    )
    test_result = await db.execute(test_stmt)
    test_failures_today = test_result.scalar() or 0

    return DashboardSummary(
        total_builds=total_builds,
        pending_triages=pending_triages,
        completed_triages=completed_triages,
        infra_failures_today=infra_failures_today,
        test_failures_today=test_failures_today,
    )


@router.get("/current-issues", response_model=list[CurrentIssue])
async def get_current_issues(db: AsyncSession = Depends(get_db)):
    """Get test failures that are still broken (real-time).

    A failure is "current" if:
    - It's a test failure (not infra)
    - It failed on the latest nightly/daily build OR any main commit since then
    - It hasn't succeeded on any main commit since then
    - It hasn't been marked as resolved by PR

    This provides real-time visibility into broken tests, not just from the
    last nightly/daily but from any subsequent main branch commits as well.
    """
    # Find the latest nightly/daily build
    latest_nightly_stmt = (
        select(Build)
        .where(or_(
            Build.message.contains("Full CI run - nightly"),
            Build.message.contains("Full CI run - daily"),
        ))
        .order_by(Build.buildkite_build_number.desc())
        .limit(1)
    )
    result = await db.execute(latest_nightly_stmt)
    latest_nightly = result.scalar_one_or_none()

    if not latest_nightly:
        return []

    # Get all test failures from this build and newer
    # Note: resolved_by_pr is NOT filtered - we show it as a linked PR until it passes on main
    failures_stmt = (
        select(Failure)
        .join(Job, Failure.job_id == Job.id)
        .join(Build, Job.build_id == Build.id)
        .where(Build.buildkite_build_number >= latest_nightly.buildkite_build_number)
        .where(Failure.failure_category == "test")
        .where(Job.soft_failed == False)
        .options(
            selectinload(Failure.job).selectinload(Job.build),
            selectinload(Failure.issue_links).selectinload(FailureIssueLink.github_issue),
        )
        .order_by(Build.buildkite_build_number.desc())
    )
    result = await db.execute(failures_stmt)
    failures = result.scalars().all()

    # Group failures by failing_test (not error_signature) to handle non-deterministic tests
    # This groups failures that are the same test but with different output values
    def normalize_test_key(key: str) -> str:
        """Strip pytest parameterization brackets from test names.

        e.g. 'test_foo[param1-param2]' -> 'test_foo'
        """
        import re
        # Handle JSON list format first
        if key.startswith("["):
            import json
            try:
                parsed = json.loads(key)
                key = parsed[0] if parsed else key
            except json.JSONDecodeError:
                pass
        # Strip parameterization suffix like [Qwen/Qwen3-8B] or [param1-param2]
        return re.sub(r'\[.*\]$', '', key)

    by_test: dict[str, list[Failure]] = {}
    for f in failures:
        # Use failing_test as the grouping key
        test_key = f.failing_test or f.error_signature or "unknown"
        test_key = normalize_test_key(test_key)
        if test_key not in by_test:
            by_test[test_key] = []
        by_test[test_key].append(f)

    # For each signature, check if it has passed since
    # A test "passes" if a job with the same name passed on a later build
    current_issues = []

    for test_key, sig_failures in by_test.items():
        # Get the first and last failure for this signature
        first_failure = min(sig_failures, key=lambda f: f.job.build.buildkite_build_number)
        last_failure = max(sig_failures, key=lambda f: f.job.build.buildkite_build_number)
        first_build_num = first_failure.job.build.buildkite_build_number
        last_build_num = last_failure.job.build.buildkite_build_number

        # Check if any job with same name passed on a build after this failure
        job_name = first_failure.job.name
        passed_stmt = (
            select(Job)
            .join(Build, Job.build_id == Build.id)
            .where(Build.buildkite_build_number > last_build_num)
            .where(Build.branch == "main")
            .where(Job.name == job_name)
            .where(Job.state == "passed")
            .limit(1)
        )
        result = await db.execute(passed_stmt)
        passed_job = result.scalar_one_or_none()

        if passed_job:
            # This issue has been fixed
            continue

        # Get linked issue if any
        linked_issue = None
        for f in sig_failures:
            if f.issue_links:
                link = f.issue_links[0]
                if link.github_issue:
                    linked_issue = link.github_issue
                    break

        # Deserialize failing_test if needed
        failing_test = first_failure.failing_test
        if isinstance(failing_test, str) and failing_test.startswith("["):
            import json
            try:
                failing_test = json.loads(failing_test)
            except json.JSONDecodeError:
                pass

        # Look up flaky info from ErrorSignature
        is_flaky = False
        flaky_rate = None
        retry_success_count = None
        signature_occurrence_count = None
        if first_failure.error_signature:
            sig_stmt = select(ErrorSignature).where(
                ErrorSignature.signature_hash == first_failure.error_signature
            )
            sig_result = await db.execute(sig_stmt)
            error_sig = sig_result.scalar_one_or_none()
            if error_sig:
                is_flaky = error_sig.is_flaky
                retry_success_count = error_sig.retry_success_count
                signature_occurrence_count = error_sig.occurrence_count
                if error_sig.occurrence_count > 0:
                    flaky_rate = error_sig.retry_success_count / error_sig.occurrence_count

        # Collect all failing builds info, sorted by build number (newest first)
        failing_builds = sorted(
            [
                FailingBuildInfo(
                    build_number=f.job.build.buildkite_build_number,
                    commit_sha=f.job.build.commit_sha,
                    build_url=f.job.build.web_url,
                    job_url=f.job.web_url,
                )
                for f in sig_failures
            ],
            key=lambda b: b.build_number,
            reverse=True,
        )

        current_issues.append(CurrentIssue(
            failure_id=first_failure.id,
            job_id=first_failure.job.id,
            job_name=job_name or "unknown",
            job_url=first_failure.job.web_url,
            failing_test=failing_test,
            failure_type=first_failure.failure_type,
            error_message=first_failure.error_message,
            error_signature=first_failure.error_signature,
            log_excerpt=first_failure.log_excerpt,
            first_seen_build=first_build_num,
            last_seen_build=last_build_num,
            occurrence_count=len(sig_failures),
            is_flaky=is_flaky,
            flaky_rate=flaky_rate,
            retry_success_count=retry_success_count,
            signature_occurrence_count=signature_occurrence_count,
            linked_issue_number=linked_issue.github_issue_number if linked_issue else None,
            linked_issue_url=linked_issue.github_issue_url if linked_issue else None,
            failing_builds=failing_builds,
        ))

    # Sort by occurrence count (most frequent first)
    current_issues.sort(key=lambda x: x.occurrence_count, reverse=True)

    return current_issues


@router.get("/current-issues-grouped", response_model=list[CurrentIssueGroup])
async def get_current_issues_grouped(db: AsyncSession = Depends(get_db)):
    """Get current issues grouped by root cause error.

    Groups issues that have the same error message together, making it easier
    to see how many tests are affected by the same underlying problem.
    """
    current_issues = await get_current_issues(db)

    def get_error_key(error_msg: str | None) -> str:
        """Create a grouping key from error message."""
        if not error_msg:
            return "unknown"
        # Normalize the error message for grouping
        # Strip variable parts like memory addresses, line numbers, specific values
        import re
        key = error_msg.strip()
        # Truncate to first 100 chars for grouping
        key = key[:100]
        return key

    # Group by error message
    groups: dict[str, list[CurrentIssue]] = {}
    for issue in current_issues:
        key = get_error_key(issue.error_message)
        if key not in groups:
            groups[key] = []
        groups[key].append(issue)

    # Build response
    result = []
    for error_key, issues in groups.items():
        # Find any linked issue in the group
        linked_issue_number = None
        linked_issue_url = None
        for issue in issues:
            if issue.linked_issue_number:
                linked_issue_number = issue.linked_issue_number
                linked_issue_url = issue.linked_issue_url
                break

        result.append(CurrentIssueGroup(
            error_key=error_key,
            error_message=issues[0].error_message,
            failure_type=issues[0].failure_type,
            linked_issue_number=linked_issue_number,
            linked_issue_url=linked_issue_url,
            total_affected_tests=len(issues),
            first_seen_build=min(i.first_seen_build for i in issues),
            last_seen_build=max(i.last_seen_build for i in issues),
            issues=issues,
        ))

    # Sort by number of affected tests (most affected first)
    result.sort(key=lambda x: x.total_affected_tests, reverse=True)

    return result


@router.get("/{build_number}", response_model=BuildResponse)
async def get_build(build_number: int, db: AsyncSession = Depends(get_db)):
    stmt = (
        select(Build)
        .where(Build.buildkite_build_number == build_number)
        .options(
            selectinload(Build.jobs)
            .selectinload(Job.failures)
            .selectinload(Failure.issue_links)
            .selectinload(FailureIssueLink.github_issue)
        )
    )
    result = await db.execute(stmt)
    build = result.scalar_one_or_none()

    if not build:
        raise HTTPException(status_code=404, detail="Build not found")

    return build


@router.post("/sync")
async def sync_builds(
    limit: int = Query(20, le=100),
    branch: str = Query("main", description="Branch to sync (default: main for nightly/daily)"),
    db: AsyncSession = Depends(get_db),
):
    import asyncio

    buildkite = BuildkiteService()
    triage = TriageService(db)

    builds_data = await buildkite.list_recent_builds(limit=limit, branch=branch)

    async def sync_single(build_data: dict) -> tuple[bool, bool]:
        """Returns (synced, triaged)"""
        state = build_data.get("state", "")
        if state in ("failed", "failing"):
            await triage.sync_and_triage_build(build_data)
            return (True, True)
        else:
            build = await triage._get_or_create_build(build_data)
            full_build = await buildkite.get_build(build_data["number"])
            await triage._sync_jobs(build, full_build.get("jobs", []))
            return (True, False)

    # Process builds in parallel batches of 5
    batch_size = 5
    synced = 0
    triaged = 0

    for i in range(0, len(builds_data), batch_size):
        batch = builds_data[i:i + batch_size]
        results = await asyncio.gather(*[sync_single(b) for b in batch], return_exceptions=True)
        for result in results:
            if isinstance(result, Exception):
                continue
            s, t = result
            if s:
                synced += 1
            if t:
                triaged += 1

    await db.commit()

    return {
        "synced": synced,
        "triaged": triaged,
        "message": f"Synced {synced} builds, triaged {triaged} failed builds",
    }


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
