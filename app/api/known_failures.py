import asyncio
import json
import logging
from datetime import datetime, timedelta

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db, get_db_session
from app.models import Build, Job, Failure, KnownFailure, KnownFailureEvent, log_kf_event
from app.schemas.known_failure import (
    KnownFailureResponse,
    KnownFailureUpdate,
    KnownFailureInstance,
    FailuresByBuild,
    BuildRef,
    BuildInHistory,
    BuildHistoryEntry,
    KnownFailureHistory,
    ResolveRequest,
    ReassignFailureRequest,
    MergeKnownFailuresRequest,
    SplitFailuresRequest,
    LinkIssueToKnownFailureRequest,
)
from app.services.github import GitHubService

logger = logging.getLogger(__name__)

router = APIRouter()

# In-memory tracking of active commit triages:
# {known_failure_id: {commit_sha: status_str}}
# status: "running" | "no_builds" | "synced" | "triaged" | "error:message"
_active_commit_triages: dict[int, dict[str, str]] = {}


def _parse_failing_test(value: str | None) -> str | list[str] | None:
    """Deserialize failing_test JSON list if stored as string."""
    if isinstance(value, str) and value.startswith("["):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return value


async def _build_known_failure_response(
    kf: KnownFailure, db: AsyncSession, include_failures_by_build: bool = False,
    commit_dates_cache: dict[str, datetime] | None = None,
) -> KnownFailureResponse:
    """Build a KnownFailureResponse from a KnownFailure model instance.

    Args:
        commit_dates_cache: Pre-fetched commit dates to avoid per-KF GitHub API calls.
            If None, dates are fetched on demand.
    """
    ref_commit_dates: dict[str, datetime] = commit_dates_cache or {}
    if commit_dates_cache is None:
        # Fetch on demand (single KF detail view)
        ref_builds = [b for b in [kf.first_seen_build, kf.last_seen_build, kf.resolved_in_build] if b]
        ref_shas = {b.commit_sha for b in ref_builds if b.commit_sha}
        if ref_shas:
            try:
                gh = GitHubService(db)
                date_strs = await gh.get_commit_dates(ref_shas)
                for sha, date_str in date_strs.items():
                    ref_commit_dates[sha] = datetime.fromisoformat(
                        date_str.replace("Z", "+00:00")
                    )
            except Exception:
                pass

    # Get first/last seen build refs
    first_seen = None
    if kf.first_seen_build:
        first_seen = BuildRef(
            build_number=kf.first_seen_build.buildkite_build_number,
            commit_sha=kf.first_seen_build.commit_sha,
            committed_at=ref_commit_dates.get(kf.first_seen_build.commit_sha or ""),
            created_at=kf.first_seen_build.created_at,
            message=kf.first_seen_build.message,
        )

    last_seen = None
    if kf.last_seen_build:
        last_seen = BuildRef(
            build_number=kf.last_seen_build.buildkite_build_number,
            commit_sha=kf.last_seen_build.commit_sha,
            committed_at=ref_commit_dates.get(kf.last_seen_build.commit_sha or ""),
            created_at=kf.last_seen_build.created_at,
            message=kf.last_seen_build.message,
        )

    resolved_in_build = None
    if kf.resolved_in_build:
        resolved_in_build = BuildRef(
            build_number=kf.resolved_in_build.buildkite_build_number,
            commit_sha=kf.resolved_in_build.commit_sha,
            committed_at=ref_commit_dates.get(kf.resolved_in_build.commit_sha or ""),
            created_at=kf.resolved_in_build.created_at,
            message=kf.resolved_in_build.message,
        )

    # Count failures and collect affected job names
    failure_count = len(kf.failures) if kf.failures else 0
    affected_jobs_set: set[str] = set()
    if kf.failures:
        for f in kf.failures:
            if f.job:
                affected_jobs_set.add(f.job.name or f.job.step_key or "unknown")
    affected_jobs = sorted(affected_jobs_set)

    # GitHub issue
    github_issue = None
    if kf.github_issue:
        from app.schemas.github import GitHubIssueResponse
        github_issue = GitHubIssueResponse.model_validate(kf.github_issue)

    failures_by_build: list[FailuresByBuild] = []
    if include_failures_by_build and kf.failures:
        # Group failures by build
        build_groups: dict[int, list[Failure]] = {}
        for f in kf.failures:
            build_id = f.job.build_id
            if build_id not in build_groups:
                build_groups[build_id] = []
            build_groups[build_id].append(f)

        # Fetch GitHub commit dates for all commit SHAs
        commit_shas = set()
        for failures in build_groups.values():
            build = failures[0].job.build
            if build.commit_sha:
                commit_shas.add(build.commit_sha)

        gh_commit_dates: dict[str, datetime] = {}
        if commit_shas:
            gh = GitHubService(db)
            try:
                date_strs = await gh.get_commit_dates(commit_shas)
                for sha, date_str in date_strs.items():
                    gh_commit_dates[sha] = datetime.fromisoformat(
                        date_str.replace("Z", "+00:00")
                    )
            except Exception:
                pass  # GitHub fetch is best-effort

        for build_id, failures in build_groups.items():
            build = failures[0].job.build
            # Count commits behind for this build
            build_commits_behind = 0
            stmt = (
                select(func.count(Build.id))
                .where(Build.buildkite_build_number > build.buildkite_build_number)
                .where(Build.branch == "main")
            )
            result = await db.execute(stmt)
            build_commits_behind = result.scalar() or 0

            instances = [
                KnownFailureInstance(
                    failure_id=f.id,
                    job_id=f.job.id,
                    job_name=f.job.name or f.job.step_key or "unknown",
                    job_url=f.job.web_url,
                    failing_test=_parse_failing_test(f.failing_test),
                    error_message=f.error_message,
                    log_excerpt=f.log_excerpt,
                )
                for f in failures
            ]

            failures_by_build.append(FailuresByBuild(
                build_number=build.buildkite_build_number,
                build_url=build.web_url,
                commit_sha=build.commit_sha,
                committed_at=gh_commit_dates.get(build.commit_sha or ""),
                created_at=build.created_at,
                commits_behind=build_commits_behind,
                failures=instances,
            ))

        # Sort by build number descending (newest first)
        failures_by_build.sort(key=lambda x: x.build_number, reverse=True)

    return KnownFailureResponse(
        id=kf.id,
        title=kf.title,
        summary=kf.summary,
        match_prompt=kf.match_prompt,
        category=kf.category,
        status=kf.status,
        is_flaky=kf.is_flaky,
        github_issue=github_issue,
        resolved_by_pr=kf.resolved_by_pr,
        resolved_by=kf.resolved_by,
        resolved_in_build=resolved_in_build,
        first_seen_build=first_seen,
        last_seen_build=last_seen,
        failure_count=failure_count,
        affected_jobs=affected_jobs,
        failures_by_build=failures_by_build,
    )


def _load_known_failure_options():
    """Common selectinload options for KnownFailure queries."""
    return [
        selectinload(KnownFailure.github_issue),
        selectinload(KnownFailure.first_seen_build),
        selectinload(KnownFailure.last_seen_build),
        selectinload(KnownFailure.resolved_in_build),
        selectinload(KnownFailure.failures)
        .selectinload(Failure.job)
        .selectinload(Job.build),
    ]


@router.get("", response_model=list[KnownFailureResponse])
async def list_known_failures(
    status: str = "open",
    category: str = "test",
    resolved_since_hours: int | None = Query(None, description="Only show failures resolved within this many hours"),
    is_flaky: bool | None = Query(None, description="Filter by flaky status"),
    db: AsyncSession = Depends(get_db),
):
    """List known failures, defaulting to open test failures (current issues)."""
    stmt = (
        select(KnownFailure)
        .options(*_load_known_failure_options())
        .join(Build, KnownFailure.first_seen_build_id == Build.id)
        .where(Build.branch == "main")
    )
    if status != "all":
        stmt = stmt.where(KnownFailure.status == status)
    if category != "all":
        stmt = stmt.where(KnownFailure.category == category)
    if resolved_since_hours is not None:
        cutoff = datetime.utcnow() - timedelta(hours=resolved_since_hours)
        stmt = stmt.where(KnownFailure.resolved_at >= cutoff)
    if is_flaky is not None:
        stmt = stmt.where(KnownFailure.is_flaky == is_flaky)
    stmt = stmt.order_by(KnownFailure.created_at.desc())

    result = await db.execute(stmt)
    known_failures = result.scalars().all()

    # Batch-fetch all commit dates upfront (one GitHub API call per unique SHA)
    all_shas: set[str] = set()
    for kf in known_failures:
        for b in [kf.first_seen_build, kf.last_seen_build, kf.resolved_in_build]:
            if b and b.commit_sha:
                all_shas.add(b.commit_sha)

    commit_dates_cache: dict[str, datetime] = {}
    if all_shas:
        try:
            gh = GitHubService(db)
            date_strs = await gh.get_commit_dates(all_shas)
            for sha, date_str in date_strs.items():
                commit_dates_cache[sha] = datetime.fromisoformat(
                    date_str.replace("Z", "+00:00")
                )
        except Exception:
            pass

    return [
        await _build_known_failure_response(kf, db, include_failures_by_build=False, commit_dates_cache=commit_dates_cache)
        for kf in known_failures
    ]


@router.get("/{known_failure_id}", response_model=KnownFailureResponse)
async def get_known_failure(
    known_failure_id: int,
    db: AsyncSession = Depends(get_db),
):
    """Get a known failure with failures grouped by build."""
    stmt = (
        select(KnownFailure)
        .where(KnownFailure.id == known_failure_id)
        .options(*_load_known_failure_options())
    )
    result = await db.execute(stmt)
    kf = result.scalar_one_or_none()

    if not kf:
        raise HTTPException(status_code=404, detail="Known failure not found")

    return await _build_known_failure_response(kf, db, include_failures_by_build=True)


@router.get("/{known_failure_id}/history", response_model=KnownFailureHistory)
async def get_known_failure_history(
    known_failure_id: int,
    db: AsyncSession = Depends(get_db),
):
    """Get build-by-build history for a known failure since first seen."""
    # 1. Load KnownFailure with its failures to extract affected job names
    stmt = (
        select(KnownFailure)
        .where(KnownFailure.id == known_failure_id)
        .options(
            selectinload(KnownFailure.first_seen_build),
            selectinload(KnownFailure.resolved_in_build),
            selectinload(KnownFailure.failures)
            .selectinload(Failure.job),
        )
    )
    result = await db.execute(stmt)
    kf = result.scalar_one_or_none()

    if not kf:
        raise HTTPException(status_code=404, detail="Known failure not found")

    # Extract distinct affected job names and test names
    affected_jobs_set: set[str] = set()
    affected_tests_set: set[str] = set()
    if kf.failures:
        for f in kf.failures:
            if f.job:
                affected_jobs_set.add(f.job.name or f.job.step_key or "unknown")
            if f.failing_test:
                parsed = _parse_failing_test(f.failing_test)
                if isinstance(parsed, list):
                    affected_tests_set.update(parsed)
                elif parsed:
                    affected_tests_set.add(parsed)
    affected_jobs = sorted(affected_jobs_set)
    affected_tests = sorted(affected_tests_set)

    if not kf.first_seen_build:
        return KnownFailureHistory(
            known_failure_id=kf.id,
            title=kf.title,
            affected_jobs=affected_jobs,
            affected_tests=affected_tests,
            predates_history=False,
            entries=[],
        )

    first_seen_build_number = kf.first_seen_build.buildkite_build_number

    # 2. Check if first_seen_build is the oldest build in DB
    oldest_stmt = select(func.min(Build.buildkite_build_number)).where(Build.branch == "main")
    oldest_result = await db.execute(oldest_stmt)
    oldest_build_number = oldest_result.scalar()
    predates_history = oldest_build_number is not None and first_seen_build_number <= oldest_build_number

    # 3. Fetch ALL main-branch builds, ordered by build_number DESC
    #    We fetch everything (not just >= first_seen) so we can show context:
    #    the pass before the first failure, and any not-run entries in between.
    #    Trimming happens after status computation (step 6c).
    builds_stmt = (
        select(Build)
        .where(Build.branch == "main")
        .where(Build.state != "canceled")
        .order_by(Build.buildkite_build_number.desc())
    )
    builds_result = await db.execute(builds_stmt)
    all_builds = builds_result.scalars().all()

    if not all_builds:
        return KnownFailureHistory(
            known_failure_id=kf.id,
            title=kf.title,
            affected_jobs=affected_jobs,
            affected_tests=affected_tests,
            predates_history=predates_history,
            entries=[],
        )

    build_ids = [b.id for b in all_builds]

    # 4. Batch-load all jobs matching affected job names in those builds
    if affected_jobs:
        jobs_stmt = (
            select(Job)
            .where(Job.build_id.in_(build_ids))
            .where(Job.name.in_(affected_jobs))
        )
        jobs_result = await db.execute(jobs_stmt)
        all_jobs = jobs_result.scalars().all()
    else:
        all_jobs = []

    # Group jobs by build_id
    jobs_by_build: dict[int, list[Job]] = {}
    job_ids: list[int] = []
    for j in all_jobs:
        jobs_by_build.setdefault(j.build_id, []).append(j)
        job_ids.append(j.id)

    # 5. Batch-load all failures in matching jobs
    all_failures_by_job: dict[int, list[Failure]] = {}
    kf_failures_by_build: dict[int, list[Failure]] = {}
    if job_ids:
        failures_stmt = (
            select(Failure)
            .where(Failure.job_id.in_(job_ids))
        )
        failures_result = await db.execute(failures_stmt)
        all_failures = failures_result.scalars().all()

        for f in all_failures:
            all_failures_by_job.setdefault(f.job_id, []).append(f)
            if f.known_failure_id == kf.id:
                for j in all_jobs:
                    if j.id == f.job_id:
                        kf_failures_by_build.setdefault(j.build_id, []).append(f)
                        break

    # Status priority for aggregation (higher = worse)
    _STATUS_PRIORITY = {
        "not_run": 0,
        "pass": 1,
        "other_fail": 2,
        "job_fail": 3,
        "infra_fail": 4,
        "diff_fail": 5,
        "flaky_pass": 6,
        "fail": 7,
    }

    def _build_status(build: Build) -> tuple[str, list[KnownFailureInstance]]:
        """Determine status for a single build."""
        build_jobs = jobs_by_build.get(build.id, [])
        kf_failures = kf_failures_by_build.get(build.id, [])

        if kf_failures:
            all_retry_passed = all(f.retry_passed for f in kf_failures)
            instances = [
                KnownFailureInstance(
                    failure_id=f.id,
                    job_id=f.job_id,
                    job_name=next(
                        (j.name or j.step_key or "unknown" for j in build_jobs if j.id == f.job_id),
                        "unknown",
                    ),
                    job_url=next(
                        (j.web_url for j in build_jobs if j.id == f.job_id),
                        None,
                    ),
                    failing_test=_parse_failing_test(f.failing_test),
                    error_message=f.error_message,
                    log_excerpt=None,
                )
                for f in kf_failures
            ]
            return ("flaky_pass" if all_retry_passed else "fail", instances)
        elif not build_jobs:
            return ("not_run", [])
        else:
            # Filter to only jobs that have actually finished running.
            # Jobs in "blocked", "waiting", "running", "scheduled" states haven't completed.
            _terminal_states = {"passed", "failed", "broken", "timed_out"}
            finished_jobs = [j for j in build_jobs if j.state in _terminal_states or j.soft_failed]

            if not finished_jobs:
                # No matching jobs have finished — treat as not run
                return ("not_run", [])

            # Soft-failed jobs are allowed to fail; treat them as passed
            meaningful_jobs = [j for j in finished_jobs if not j.soft_failed]
            if not meaningful_jobs:
                return ("pass", [])

            all_passed = all(j.state == "passed" for j in meaningful_jobs)
            if all_passed:
                return ("pass", [])
            else:
                job_failures = []
                for j in meaningful_jobs:
                    job_failures.extend(all_failures_by_job.get(j.id, []))
                if not job_failures:
                    # Jobs failed but no failure records — untriaged
                    return ("job_fail", [])
                all_infra = all(
                    f.failure_category == "infra" for f in job_failures
                )
                if all_infra:
                    return ("infra_fail", [])
                # Check if any failures involve the same test(s) as this KF
                # but with a different root cause (different KnownFailure)
                for jf in job_failures:
                    if jf.failing_test:
                        parsed = _parse_failing_test(jf.failing_test)
                        tests = parsed if isinstance(parsed, list) else [parsed]
                        if affected_tests_set & set(tests):
                            return ("diff_fail", [])
                return ("other_fail", [])

    # 6. Compute per-build status, then group by commit
    # builds are already ordered newest-first (by build_number DESC)
    from collections import OrderedDict
    commit_groups: OrderedDict[str | None, list[Build]] = OrderedDict()
    for build in all_builds:
        commit_groups.setdefault(build.commit_sha, []).append(build)

    entries: list[BuildHistoryEntry] = []
    for commit_sha, builds_for_commit in commit_groups.items():
        # Use the earliest build's metadata for the commit entry
        # (first build is typically the normal one, nightly/daily comes later)
        representative = builds_for_commit[-1]  # oldest build for this commit

        build_entries: list[BuildInHistory] = []
        all_failures: list[KnownFailureInstance] = []
        worst_status = "not_run"

        for build in builds_for_commit:
            status, instances = _build_status(build)
            build_entries.append(BuildInHistory(
                build_number=build.buildkite_build_number,
                build_url=build.web_url,
                build_type=build.build_type,
                status=status,
            ))
            all_failures.extend(instances)
            if _STATUS_PRIORITY.get(status, 0) > _STATUS_PRIORITY.get(worst_status, 0):
                worst_status = status

        entries.append(BuildHistoryEntry(
            commit_sha=commit_sha,
            created_at=representative.created_at,
            message=representative.message,
            status=worst_status,
            builds=build_entries,
            failures=all_failures,
        ))

    # 6b. Fill in missing commits from GitHub and populate committed_at
    # Our DB only has builds that were synced. Many main-branch commits may not
    # have been captured. Fetch the commit list from GitHub and insert "not_run"
    # entries for any commits we don't have.
    existing_shas = {e.commit_sha for e in entries if e.commit_sha}
    if entries:
        # Determine time range from our entries
        oldest_entry = entries[-1]  # entries are newest-first
        newest_entry = entries[0]
        since_dt = oldest_entry.created_at
        until_dt = newest_entry.created_at
        if since_dt and until_dt:
            gh = GitHubService(db)
            try:
                gh_commits = await gh.list_main_commits(
                    since=since_dt.isoformat() + "Z" if since_dt else None,
                    until=until_dt.isoformat() + "Z" if until_dt else None,
                    per_page=100,
                )
                # Build SHA → date lookup for committed_at
                gh_commit_dates: dict[str, datetime] = {}
                for commit in gh_commits:
                    if commit.get("date"):
                        gh_commit_dates[commit["sha"]] = datetime.fromisoformat(
                            commit["date"].replace("Z", "+00:00")
                        )

                # Populate committed_at on existing entries
                for entry in entries:
                    if entry.commit_sha and entry.commit_sha in gh_commit_dates:
                        entry.committed_at = gh_commit_dates[entry.commit_sha]

                # Insert missing commits as "not_run" entries
                new_entries = []
                for commit in gh_commits:
                    sha = commit["sha"]
                    if sha not in existing_shas:
                        committed_at = gh_commit_dates.get(sha)
                        new_entries.append(BuildHistoryEntry(
                            commit_sha=sha,
                            committed_at=committed_at,
                            created_at=None,
                            message=commit.get("message"),
                            status="not_run",
                            triaged=False,
                            builds=[],
                            failures=[],
                        ))
                if new_entries:
                    entries.extend(new_entries)

                # Re-sort newest-first by committed_at (fall back to created_at)
                # Strip tzinfo for consistent comparison
                def _sort_key(e: BuildHistoryEntry) -> datetime:
                    dt = e.committed_at or e.created_at
                    if dt is None:
                        return datetime.min
                    if dt.tzinfo is not None:
                        dt = dt.replace(tzinfo=None)
                    return dt

                entries.sort(key=_sort_key, reverse=True)
            except Exception as e:
                logger.warning(f"Failed to fill GitHub commits for KF history: {e}")

    # 6c. For resolved KFs, ensure the resolved_in_build appears in the history
    if kf.status == "resolved" and kf.resolved_in_build:
        rib = kf.resolved_in_build
        rib_sha = rib.commit_sha
        # Check if resolved_in_build's commit is already in entries
        rib_in_entries = any(e.commit_sha == rib_sha for e in entries)
        if not rib_in_entries:
            # Insert the resolved_in_build as a "pass" entry at the right position
            rib_entry = BuildHistoryEntry(
                commit_sha=rib_sha,
                created_at=rib.created_at,
                message=rib.message,
                status="pass",
                builds=[BuildInHistory(
                    build_number=rib.buildkite_build_number,
                    build_url=rib.web_url,
                    build_type=rib.build_type,
                    status="pass",
                )],
                failures=[],
            )
            # Insert at the right chronological position (entries are newest-first)
            inserted = False
            for i, entry in enumerate(entries):
                if entry.created_at and rib.created_at and entry.created_at < rib.created_at:
                    entries.insert(i, rib_entry)
                    inserted = True
                    break
            if not inserted:
                entries.insert(0, rib_entry)

    # 7. Trim to failure window with one pass of context on each side.
    #    Entries are newest-first. Find the failure window (first fail → last fail),
    #    keep one pass above the first failure and one pass below the last failure.
    #    Only count actual failures of THIS known failure (fail, flaky_pass) — not
    #    other_fail/infra_fail/job_fail which are unrelated to this specific issue.
    _FAIL_STATUSES = {"fail", "flaky_pass"}
    no_prior_runs = False
    first_fail_idx = None
    last_fail_idx = None
    for i, entry in enumerate(entries):
        if entry.status in _FAIL_STATUSES:
            if first_fail_idx is None:
                first_fail_idx = i
            last_fail_idx = i

    if first_fail_idx is not None:
        # Top trim: find the first pass above (before, i.e. newer than) the first failure.
        # If found, clip to that pass. If not, keep everything up to the present
        # (the failure is still active, so show all recent not_run commits).
        top = 0
        for i in range(first_fail_idx - 1, -1, -1):
            if entries[i].status == "pass":
                top = i
                break

        # Bottom trim: find the first pass below (after) the last failure.
        # Keep that pass, trim everything older.
        bottom = len(entries)
        found_bottom_pass = False
        for i in range(last_fail_idx + 1, len(entries)):
            if entries[i].status == "pass":
                bottom = i + 1  # Include the pass
                found_bottom_pass = True
                break

        # Detect no_prior_runs: no pass found below failures, and the oldest
        # entries are all "not_run" (test never ran before it started failing)
        if not found_bottom_pass and not predates_history:
            trailing = entries[last_fail_idx + 1:]
            if trailing and all(e.status == "not_run" for e in trailing):
                no_prior_runs = True

        entries = entries[top:bottom]

    return KnownFailureHistory(
        known_failure_id=kf.id,
        title=kf.title,
        affected_jobs=affected_jobs,
        affected_tests=affected_tests,
        predates_history=predates_history,
        no_prior_runs=no_prior_runs,
        is_flaky=kf.is_flaky,
        entries=entries,
    )


@router.patch("/{known_failure_id}", response_model=KnownFailureResponse)
async def update_known_failure(
    known_failure_id: int,
    update: KnownFailureUpdate,
    db: AsyncSession = Depends(get_db),
):
    """Update a known failure's title, category, or flaky status."""
    stmt = (
        select(KnownFailure)
        .where(KnownFailure.id == known_failure_id)
        .options(*_load_known_failure_options())
    )
    result = await db.execute(stmt)
    kf = result.scalar_one_or_none()

    if not kf:
        raise HTTPException(status_code=404, detail="Known failure not found")

    if update.title is not None:
        kf.title = update.title
    if update.summary is not None:
        kf.summary = update.summary
    if update.match_prompt is not None:
        kf.match_prompt = update.match_prompt
    if update.category is not None:
        kf.category = update.category
    if update.is_flaky is not None:
        kf.is_flaky = update.is_flaky

    await db.commit()
    await db.refresh(kf)

    return await _build_known_failure_response(kf, db)


@router.post("/{known_failure_id}/resolve")
async def resolve_known_failure(
    known_failure_id: int,
    request: ResolveRequest,
    db: AsyncSession = Depends(get_db),
):
    """Mark a known failure as resolved."""
    stmt = select(KnownFailure).where(KnownFailure.id == known_failure_id)
    result = await db.execute(stmt)
    kf = result.scalar_one_or_none()

    if not kf:
        raise HTTPException(status_code=404, detail="Known failure not found")

    kf.status = "resolved"
    kf.resolved_at = datetime.utcnow()
    kf.resolved_by = "manual"
    if request.resolved_by_pr is not None:
        kf.resolved_by_pr = request.resolved_by_pr

    # Set resolved_in_build to latest main branch build
    latest_build_stmt = (
        select(Build)
        .where(Build.branch == "main")
        .order_by(Build.buildkite_build_number.desc())
        .limit(1)
    )
    latest_result = await db.execute(latest_build_stmt)
    latest_build = latest_result.scalar_one_or_none()
    if latest_build:
        kf.resolved_in_build_id = latest_build.id

    log_kf_event(
        db, known_failure_id, "manual_resolve",
        resolved_by_pr=request.resolved_by_pr,
    )
    await db.commit()
    return {"message": "Known failure resolved", "id": kf.id}


@router.post("/{known_failure_id}/reopen")
async def reopen_known_failure(
    known_failure_id: int,
    db: AsyncSession = Depends(get_db),
):
    """Reopen a resolved known failure."""
    stmt = select(KnownFailure).where(KnownFailure.id == known_failure_id)
    result = await db.execute(stmt)
    kf = result.scalar_one_or_none()

    if not kf:
        raise HTTPException(status_code=404, detail="Known failure not found")

    kf.status = "open"
    kf.resolved_at = None
    kf.resolved_by_pr = None
    kf.resolved_by = None
    kf.resolved_in_build_id = None

    log_kf_event(db, known_failure_id, "manual_reopen")
    await db.commit()
    return {"message": "Known failure reopened", "id": kf.id}


@router.post("/{known_failure_id}/link-issue")
async def link_issue_to_known_failure(
    known_failure_id: int,
    request: LinkIssueToKnownFailureRequest,
    db: AsyncSession = Depends(get_db),
):
    """Link a GitHub issue to a known failure."""
    stmt = select(KnownFailure).where(KnownFailure.id == known_failure_id)
    result = await db.execute(stmt)
    kf = result.scalar_one_or_none()

    if not kf:
        raise HTTPException(status_code=404, detail="Known failure not found")

    github = GitHubService(db)
    issue = await github.get_or_create_issue(request.github_issue_number)
    kf.github_issue_id = issue.id

    await db.commit()
    return {
        "message": f"Linked issue #{request.github_issue_number} to known failure",
        "id": kf.id,
    }


@router.delete("/{known_failure_id}/unlink-issue")
async def unlink_issue_from_known_failure(
    known_failure_id: int,
    db: AsyncSession = Depends(get_db),
):
    """Remove the GitHub issue link from a known failure."""
    stmt = select(KnownFailure).where(KnownFailure.id == known_failure_id)
    result = await db.execute(stmt)
    kf = result.scalar_one_or_none()

    if not kf:
        raise HTTPException(status_code=404, detail="Known failure not found")

    kf.github_issue_id = None

    await db.commit()
    return {"message": "Unlinked issue from known failure", "id": kf.id}


@router.post("/reassign")
async def reassign_failure(
    request: ReassignFailureRequest,
    db: AsyncSession = Depends(get_db),
):
    """Move a failure instance to a different or new KnownFailure."""
    # Get the failure
    stmt = (
        select(Failure)
        .where(Failure.id == request.failure_id)
        .options(selectinload(Failure.job).selectinload(Job.build))
    )
    result = await db.execute(stmt)
    failure = result.scalar_one_or_none()

    if not failure:
        raise HTTPException(status_code=404, detail="Failure not found")

    old_kf_id = failure.known_failure_id

    if request.target_known_failure_id is not None:
        # Move to existing KnownFailure
        stmt = select(KnownFailure).where(KnownFailure.id == request.target_known_failure_id)
        result = await db.execute(stmt)
        target_kf = result.scalar_one_or_none()

        if not target_kf:
            raise HTTPException(status_code=404, detail="Target known failure not found")

        failure.known_failure_id = target_kf.id

        # Update last_seen_build if this failure's build is newer
        if failure.job and failure.job.build:
            if not target_kf.last_seen_build_id:
                target_kf.last_seen_build_id = failure.job.build.id
            else:
                last_build_stmt = select(Build).where(Build.id == target_kf.last_seen_build_id)
                last_build_result = await db.execute(last_build_stmt)
                last_build = last_build_result.scalar_one_or_none()
                if last_build and failure.job.build.buildkite_build_number > last_build.buildkite_build_number:
                    target_kf.last_seen_build_id = failure.job.build.id
    else:
        # Create new KnownFailure
        if not request.new_title:
            raise HTTPException(status_code=400, detail="new_title required when creating a new known failure")

        new_kf = KnownFailure(
            title=request.new_title,
            category=failure.failure_category,
            status="open",
            is_flaky=failure.is_flaky or False,
            first_seen_build_id=failure.job.build.id if failure.job else None,
            last_seen_build_id=failure.job.build.id if failure.job else None,
        )
        db.add(new_kf)
        await db.flush()
        failure.known_failure_id = new_kf.id

    log_kf_event(
        db, failure.known_failure_id, "manual_reassign",
        failure_id=request.failure_id,
        from_kf_id=old_kf_id,
        to_kf_id=failure.known_failure_id,
    )

    # Check if old KnownFailure is now empty
    if old_kf_id:
        remaining_stmt = select(func.count(Failure.id)).where(
            Failure.known_failure_id == old_kf_id,
            Failure.id != request.failure_id,
        )
        remaining_result = await db.execute(remaining_stmt)
        remaining_count = remaining_result.scalar() or 0

        if remaining_count == 0:
            # Delete the now-empty KnownFailure
            old_kf_stmt = select(KnownFailure).where(KnownFailure.id == old_kf_id)
            old_kf_result = await db.execute(old_kf_stmt)
            old_kf = old_kf_result.scalar_one_or_none()
            if old_kf:
                await db.delete(old_kf)

    await db.commit()
    return {
        "message": "Failure reassigned",
        "failure_id": request.failure_id,
        "known_failure_id": failure.known_failure_id,
    }


@router.post("/merge")
async def merge_known_failures(
    request: MergeKnownFailuresRequest,
    db: AsyncSession = Depends(get_db),
):
    """Merge source KnownFailure into target. All failures move to target, source is deleted."""
    if request.source_id == request.target_id:
        raise HTTPException(status_code=400, detail="Cannot merge a known failure into itself")

    source_stmt = (
        select(KnownFailure)
        .where(KnownFailure.id == request.source_id)
        .options(
            selectinload(KnownFailure.failures),
            selectinload(KnownFailure.first_seen_build),
            selectinload(KnownFailure.last_seen_build),
        )
    )
    target_stmt = (
        select(KnownFailure)
        .where(KnownFailure.id == request.target_id)
        .options(
            selectinload(KnownFailure.first_seen_build),
            selectinload(KnownFailure.last_seen_build),
        )
    )

    source_result = await db.execute(source_stmt)
    source = source_result.scalar_one_or_none()
    target_result = await db.execute(target_stmt)
    target = target_result.scalar_one_or_none()

    if not source:
        raise HTTPException(status_code=404, detail="Source known failure not found")
    if not target:
        raise HTTPException(status_code=404, detail="Target known failure not found")

    # Move all failures from source to target.
    # Flush immediately so the FK changes persist before deleting the source,
    # otherwise SQLAlchemy's cascade will NULL the FKs on delete.
    moved_count = len(source.failures)
    for failure in source.failures:
        failure.known_failure_id = target.id
    await db.flush()

    log_kf_event(
        db, target.id, "manual_merge",
        source_id=source.id,
        source_title=source.title,
        failures_moved=moved_count,
    )

    # Update first/last seen builds
    if source.first_seen_build and target.first_seen_build:
        if source.first_seen_build.buildkite_build_number < target.first_seen_build.buildkite_build_number:
            target.first_seen_build_id = source.first_seen_build_id
    elif source.first_seen_build and not target.first_seen_build:
        target.first_seen_build_id = source.first_seen_build_id

    if source.last_seen_build and target.last_seen_build:
        if source.last_seen_build.buildkite_build_number > target.last_seen_build.buildkite_build_number:
            target.last_seen_build_id = source.last_seen_build_id
    elif source.last_seen_build and not target.last_seen_build:
        target.last_seen_build_id = source.last_seen_build_id

    # Copy GitHub issue if target doesn't have one
    if not target.github_issue_id and source.github_issue_id:
        target.github_issue_id = source.github_issue_id

    # Delete source
    await db.delete(source)
    await db.commit()

    return {
        "message": f"Merged known failure #{request.source_id} into #{request.target_id}",
        "target_id": request.target_id,
    }


@router.post("/split")
async def split_failures(
    request: SplitFailuresRequest,
    db: AsyncSession = Depends(get_db),
):
    """Split selected failures into a new KnownFailure."""
    if not request.failure_ids:
        raise HTTPException(status_code=400, detail="At least one failure_id required")

    # Load all specified failures
    stmt = (
        select(Failure)
        .where(Failure.id.in_(request.failure_ids))
        .options(selectinload(Failure.job).selectinload(Job.build))
    )
    result = await db.execute(stmt)
    failures = result.scalars().all()

    if len(failures) != len(request.failure_ids):
        raise HTTPException(status_code=404, detail="Some failures not found")

    # All failures must be from the same KnownFailure
    kf_ids = set(f.known_failure_id for f in failures)
    if len(kf_ids) != 1:
        raise HTTPException(status_code=400, detail="All failures must belong to the same known failure")

    old_kf_id = kf_ids.pop()

    # Find first/last seen builds among selected failures
    builds = [f.job.build for f in failures if f.job and f.job.build]
    first_build = min(builds, key=lambda b: b.buildkite_build_number) if builds else None
    last_build = max(builds, key=lambda b: b.buildkite_build_number) if builds else None

    # Auto-generate summary from error messages and failing tests
    error_messages = set()
    failing_tests = set()
    for f in failures:
        if f.error_message:
            error_messages.add(f.error_message[:200])
        if f.failing_test:
            parsed = _parse_failing_test(f.failing_test)
            tests = parsed if isinstance(parsed, list) else [parsed]
            for t in tests:
                failing_tests.add(t)
    summary_parts = []
    if failing_tests:
        summary_parts.append("Tests: " + ", ".join(sorted(failing_tests)[:5]))
    if error_messages:
        # Use the shortest/most common error message as the summary
        msg = min(error_messages, key=len)
        summary_parts.append(msg)
    auto_summary = ". ".join(summary_parts)[:500] if summary_parts else None

    # Auto-generate match_prompt from tests and error messages
    match_parts = []
    if failing_tests:
        tests_str = ', '.join(sorted(failing_tests)[:5])
        match_parts.append(f"Match when {tests_str} fail")
    if error_messages:
        # Use the most informative error message (longest, not shortest)
        msg = max(error_messages, key=len)
        # Extract the key error type and detail
        match_parts.append(f"with: {msg[:300]}")
    auto_match_prompt = " ".join(match_parts)[:500] if match_parts else None

    new_kf = KnownFailure(
        title=request.new_title,
        summary=auto_summary,
        match_prompt=auto_match_prompt,
        category=failures[0].failure_category,
        status="open",
        is_flaky=any(getattr(f, "is_flaky", False) for f in failures),
        first_seen_build_id=first_build.id if first_build else None,
        last_seen_build_id=last_build.id if last_build else None,
    )
    db.add(new_kf)
    await db.flush()

    # Move failures to new KF
    for f in failures:
        f.known_failure_id = new_kf.id

    log_kf_event(
        db, new_kf.id, "manual_split",
        source_kf_id=old_kf_id,
        failure_ids=request.failure_ids,
        new_title=request.new_title,
    )

    # Check if old KF is now empty
    if old_kf_id:
        remaining_stmt = select(func.count(Failure.id)).where(
            Failure.known_failure_id == old_kf_id,
            ~Failure.id.in_(request.failure_ids),
        )
        remaining_result = await db.execute(remaining_stmt)
        remaining = remaining_result.scalar() or 0

        if remaining == 0:
            old_kf = await db.get(KnownFailure, old_kf_id)
            if old_kf:
                await db.delete(old_kf)

    await db.commit()
    return {
        "message": f"Split {len(failures)} failures into new known failure",
        "new_id": new_kf.id,
    }


@router.post("/{known_failure_id}/load-earlier-history")
async def load_earlier_history(
    known_failure_id: int,
    db: AsyncSession = Depends(get_db),
):
    """Fetch older commits from GitHub and check Buildkite for builds on those commits.

    For KFs that predate recorded history, this extends the history backwards
    by finding builds on commits before the first_seen_build.
    """
    stmt = (
        select(KnownFailure)
        .where(KnownFailure.id == known_failure_id)
        .options(selectinload(KnownFailure.first_seen_build))
    )
    result = await db.execute(stmt)
    kf = result.scalar_one_or_none()
    if not kf:
        raise HTTPException(status_code=404, detail="Known failure not found")
    if not kf.first_seen_build:
        raise HTTPException(status_code=400, detail="No first_seen_build set")

    first_build = kf.first_seen_build

    # Get commits from GitHub before the first_seen_build
    gh = GitHubService(db)
    until_dt = first_build.created_at
    if not until_dt:
        raise HTTPException(status_code=400, detail="First seen build has no created_at")

    # Go back ~30 days
    since_dt = until_dt - timedelta(days=30)
    try:
        gh_commits = await gh.list_main_commits(
            since=since_dt.isoformat() + "Z",
            until=until_dt.isoformat() + "Z",
            per_page=100,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch GitHub commits: {e}")

    if not gh_commits:
        return {"message": "No earlier commits found", "builds_found": 0}

    # Check Buildkite for builds on these commits
    from app.services.buildkite import BuildkiteService
    bk = BuildkiteService()
    builds_found = 0

    for commit in gh_commits:
        sha = commit["sha"]
        # Check if we already have a build for this commit
        existing_stmt = select(Build).where(Build.commit_sha == sha)
        existing_result = await db.execute(existing_stmt)
        if existing_result.scalar_one_or_none():
            continue

        try:
            bk_builds = await bk.list_builds_by_commit(sha)
        except Exception:
            continue

        for bk_build in bk_builds:
            build_number = bk_build.get("number")
            if not build_number:
                continue

            # Check if this build is already in our DB
            existing_build_stmt = select(Build).where(
                Build.buildkite_build_number == build_number
            )
            existing_build_result = await db.execute(existing_build_stmt)
            if existing_build_result.scalar_one_or_none():
                continue

            # Sync this build (but don't triage yet)
            from app.services.triage import TriageService
            triage = TriageService(db)
            try:
                full_build = await bk.get_build(build_number)
                build = await triage._get_or_create_build(full_build)
                await triage._sync_jobs(build, full_build.get("jobs", []))
                builds_found += 1

                # If this build is older than first_seen_build and has the affected jobs passing,
                # we may be able to narrow down first_seen
                if build.buildkite_build_number < first_build.buildkite_build_number:
                    # Check if any affected jobs failed in this build
                    has_failure = any(
                        j.state == "failed" and not j.soft_failed
                        for j in build.jobs
                    )
                    if has_failure:
                        # This older build also failed — update first_seen_build
                        kf.first_seen_build_id = build.id
                        first_build = build

            except Exception as e:
                logger.warning(f"Failed to sync build #{build_number}: {e}")
                continue

    await db.commit()

    return {
        "message": f"Found {builds_found} earlier builds",
        "builds_found": builds_found,
    }


def _set_triage_status(known_failure_id: int, commit_sha: str, status: str):
    """Update the status of an active commit triage."""
    if known_failure_id in _active_commit_triages:
        _active_commit_triages[known_failure_id][commit_sha] = status


def _clear_triage(known_failure_id: int, commit_sha: str):
    """Remove a commit from active triages after a delay so frontend can read the result."""
    if known_failure_id in _active_commit_triages:
        _active_commit_triages[known_failure_id].pop(commit_sha, None)
        if not _active_commit_triages[known_failure_id]:
            del _active_commit_triages[known_failure_id]


async def _triage_commit_background(commit_sha: str, known_failure_id: int):
    """Background task to sync and triage builds for a specific commit."""
    try:
        await _triage_commit_background_inner(commit_sha, known_failure_id)
    except Exception as e:
        logger.error(f"Triage background task failed for {commit_sha}: {e}")
        _set_triage_status(known_failure_id, commit_sha, f"error:{e}")
    # Keep the result visible for 30s so the frontend can poll and read it
    await asyncio.sleep(30)
    _clear_triage(known_failure_id, commit_sha)


async def _triage_commit_background_inner(commit_sha: str, known_failure_id: int):
    """Inner implementation of commit triage background task."""
    async with get_db_session() as session:
        from app.services.buildkite import BuildkiteService
        from app.services.triage import TriageService

        bk = BuildkiteService()
        triage = TriageService(session)

        try:
            bk_builds = await bk.list_builds_by_commit(commit_sha)
        except Exception as e:
            logger.error(f"Failed to look up builds for commit {commit_sha}: {e}")
            _set_triage_status(known_failure_id, commit_sha, f"error:Buildkite lookup failed: {e}")
            return

        if not bk_builds:
            logger.info(f"No Buildkite builds found for commit {commit_sha}")
            _set_triage_status(known_failure_id, commit_sha, "no_builds")
            return

        triaged_any = False
        for bk_build in bk_builds:
            build_number = bk_build.get("number")
            if not build_number:
                continue
            try:
                full_build = await bk.get_build(build_number)
                state = full_build.get("state", "")
                if state in ("failed", "failing"):
                    await triage.sync_and_triage_build(full_build)
                    triaged_any = True
                else:
                    build = await triage._get_or_create_build(full_build)
                    await triage._sync_jobs(build, full_build.get("jobs", []))
                await session.commit()
            except Exception as e:
                logger.error(f"Failed to sync/triage build #{build_number}: {e}")
                await session.rollback()

        # After triaging, check if first_seen_build should be updated
        kf_stmt = (
            select(KnownFailure)
            .where(KnownFailure.id == known_failure_id)
            .options(
                selectinload(KnownFailure.first_seen_build),
                selectinload(KnownFailure.failures).selectinload(Failure.job).selectinload(Job.build),
            )
        )
        kf_result = await session.execute(kf_stmt)
        kf = kf_result.scalar_one_or_none()
        if kf and kf.failures:
            earliest_build = None
            for f in kf.failures:
                if f.job and f.job.build:
                    b = f.job.build
                    if earliest_build is None or b.buildkite_build_number < earliest_build.buildkite_build_number:
                        earliest_build = b
            if earliest_build and kf.first_seen_build:
                if earliest_build.buildkite_build_number < kf.first_seen_build.buildkite_build_number:
                    kf.first_seen_build_id = earliest_build.id
                    await session.commit()

        _set_triage_status(
            known_failure_id, commit_sha,
            "triaged" if triaged_any else "synced",
        )


@router.post("/{known_failure_id}/triage-commit")
async def triage_commit(
    known_failure_id: int,
    commit_sha: str = Query(..., description="Commit SHA to triage"),
    background_tasks: BackgroundTasks = BackgroundTasks(),
    db: AsyncSession = Depends(get_db),
):
    """Trigger triage for a specific commit SHA, associating results with this KF's context."""
    stmt = select(KnownFailure).where(KnownFailure.id == known_failure_id)
    result = await db.execute(stmt)
    kf = result.scalar_one_or_none()
    if not kf:
        raise HTTPException(status_code=404, detail="Known failure not found")

    # Track this commit as being actively triaged
    if known_failure_id not in _active_commit_triages:
        _active_commit_triages[known_failure_id] = {}
    _active_commit_triages[known_failure_id][commit_sha] = "running"

    background_tasks.add_task(_triage_commit_background, commit_sha, known_failure_id)
    return {"message": f"Triage started for commit {commit_sha[:8]}"}


@router.get("/{known_failure_id}/active-triages")
async def get_active_triages(known_failure_id: int):
    """Return commit SHAs being triaged for this KF with their status."""
    active = _active_commit_triages.get(known_failure_id, {})
    return {
        "commits": list(active.keys()),
        "statuses": dict(active),
    }


@router.get("/{known_failure_id}/events")
async def get_known_failure_events(
    known_failure_id: int,
    limit: int = Query(100, le=500),
    db: AsyncSession = Depends(get_db),
):
    """Get audit log events for a known failure, newest first."""
    stmt = (
        select(KnownFailureEvent)
        .where(KnownFailureEvent.known_failure_id == known_failure_id)
        .order_by(KnownFailureEvent.created_at.desc())
        .limit(limit)
    )
    result = await db.execute(stmt)
    events = result.scalars().all()

    return [
        {
            "id": e.id,
            "known_failure_id": e.known_failure_id,
            "event_type": e.event_type,
            "created_at": e.created_at.isoformat() if e.created_at else None,
            "details": json.loads(e.details) if e.details else None,
        }
        for e in events
    ]
