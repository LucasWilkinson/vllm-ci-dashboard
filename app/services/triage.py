import asyncio
import json
import logging
import os
import re
from datetime import datetime

from sqlalchemy import select, distinct, func, or_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import Build, Job, Failure, KnownFailure
from app.services.buildkite import BuildkiteService, TestEngineService
from app.services.claude import (
    analyze_failure_with_claude,
    deduplicate_known_failures,
    resume_claude_session,
)
from app.services.pattern_matcher import PatternMatcher
from app.services.triage_status import triage_status

logger = logging.getLogger(__name__)

# Patterns that indicate infrastructure failures (not test bugs).
# Checked against the last portion of the log.
_INFRA_EXIT_PATTERNS = [
    (re.compile(r"Exited with status -1.*agent.?lost", re.IGNORECASE),
     "Agent lost - machine became unreachable"),
    (re.compile(r"signal_reason.*agent.?lost", re.IGNORECASE),
     "Agent lost connection"),
    (re.compile(r"Exited with status -1.*process.?killed", re.IGNORECASE),
     "Process killed by system"),
    (re.compile(r"Exited with status 137\b", re.IGNORECASE),
     "Process killed (SIGKILL) - likely OOM"),
    (re.compile(r"Exited with status 143\b", re.IGNORECASE),
     "Process terminated (SIGTERM)"),
    (re.compile(r"Exited with status -1.*cancel", re.IGNORECASE),
     "Job canceled"),
]


def _detect_pre_execution_infra(job: "Job") -> str | None:
    """Detect pre-execution infrastructure failures from Buildkite structured data.

    Uses exit_status, signal, and signal_reason fields from the Buildkite API
    to identify jobs where the test code never ran (agent lost, pod provisioning
    failure, etc.).

    Returns an error message string if pre-execution infra is detected, None otherwise.
    """
    exit_status = job.exit_status
    signal_reason = job.signal_reason or ""

    # exit_status == -1: Agent was stopped or lost before the job completed.
    # This means the underlying infrastructure (Kubernetes pod, EC2 instance)
    # died before tests could run.
    if exit_status == -1:
        if "agent_lost" in signal_reason:
            return "Agent lost - infrastructure became unreachable (pod/node failure)"
        if "agent_stop" in signal_reason:
            return "Agent stopped - infrastructure terminated (pod eviction or scaling)"
        if "cancel" in signal_reason:
            return "Job cancelled before execution"
        # Generic -1 with unknown reason
        return f"Pre-execution failure (exit_status=-1, signal_reason={signal_reason or 'unknown'})"

    return None


def _detect_infra_exit(log_content: str | None) -> str | None:
    """Detect infrastructure exit patterns from log content (fallback).

    Used when structured exit data doesn't indicate pre-execution failure
    but the log content suggests infrastructure issues.

    Returns an error message string if an infra pattern is detected, None otherwise.
    Checks the last 3000 chars of the log where exit status messages appear.
    """
    if not log_content:
        return "Job failed with no log output (agent may have lost connection)"

    # Buildkite appends exit status at the end of the log
    tail = log_content[-3000:]
    for pattern, message in _INFRA_EXIT_PATTERNS:
        if pattern.search(tail):
            return message

    return None


def _clean_log_line(line: str) -> str:
    """Strip ANSI codes, Buildkite timestamps, and carriage returns from a log line."""
    line = line.rstrip('\r')
    line = re.sub(r'\x1b\[[0-9;]*m', '', line)
    line = re.sub(r'\x1b_[^\x07]*\x07', '', line)
    line = line.replace('\x07', '')
    line = re.sub(r'_bk;t=\d+\s*', '', line)
    line = re.sub(r'^\s*\[[0-9T\-:Z]+\]\s*', '', line)
    return line


def _parse_failing_test_value(value: str | None) -> str | list[str] | None:
    """Deserialize failing_test JSON list if stored as string."""
    if isinstance(value, str) and value.startswith("["):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return value


def _normalize_test_path(test_path: str | None) -> str | None:
    """Normalize test path for consistent storage and matching.

    - Strips leading 'tests/' prefix (Claude sometimes includes it, sometimes not)
    - Strips leading './' or '/'
    """
    if not test_path:
        return test_path
    # Handle JSON-encoded lists
    if test_path.startswith('['):
        try:
            tests = json.loads(test_path)
            normalized = [_normalize_test_path(t) for t in tests]
            return json.dumps(normalized)
        except json.JSONDecodeError:
            pass
    # Strip common prefixes
    path = test_path.strip()
    path = re.sub(r'^\./', '', path)
    path = re.sub(r'^tests/', '', path)
    return path


def _match_executions_to_jobs(
    failed_executions: list[dict], jobs: list[Job]
) -> dict[str, list[dict]]:
    """Match Test Engine failed executions to jobs by test file path.

    Test Engine test_name format: "tests/path/test_file.py test_func_name"
    Job command contains the test file paths it runs.

    Returns: {job.buildkite_job_id: [execution1, execution2, ...]}
    """
    result: dict[str, list[dict]] = {}

    for execution in failed_executions:
        test_name = execution.get("test_name", "")
        # Extract test file path — format is "tests/path/test_file.py test_func"
        # or sometimes "tests/path/test_file.py::test_func"
        parts = re.split(r'\s+|::', test_name, maxsplit=1)
        test_file = parts[0] if parts else ""

        if not test_file:
            continue

        # Match to job by checking if test file path appears in job.command
        for job in jobs:
            if not job.command:
                continue
            if test_file in job.command:
                result.setdefault(job.buildkite_job_id, []).append(execution)
                break

    return result


class TriageService:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.buildkite = BuildkiteService()
        self.pattern_matcher = PatternMatcher(session)

    async def sync_recent_builds(
        self,
        limit: int = 20,
        branch: str = "main",
        nightly_daily_only: bool = False,
    ) -> dict:
        """Unified sync entry point. Fetches builds from Buildkite, sorts oldest-first,
        processes sequentially (sync+triage if failed/failing, sync-only otherwise).
        Commits after each build.

        Returns dict with synced/triaged counts.
        """
        builds_data = await self.buildkite.list_recent_builds(
            limit=limit, branch=branch, nightly_daily_only=nightly_daily_only
        )
        # Sort oldest-first so KnownFailures are created before later builds auto-resolve them
        builds_data.sort(key=lambda b: b.get("number", 0))

        synced = 0
        triaged = 0

        for build_data in builds_data:
            try:
                state = build_data.get("state", "")
                if state in ("failed", "failing"):
                    await self.sync_and_triage_build(build_data)
                    triaged += 1
                else:
                    build = await self._get_or_create_build(build_data)
                    full_build = await self.buildkite.get_build(build_data["number"])
                    await self._sync_jobs(build, full_build.get("jobs", []))
                    # Auto-resolve KFs whose affected jobs passed in this build
                    await self._auto_resolve_known_failures(build)
                synced += 1
                await self.session.commit()
            except Exception as e:
                logger.error(f"Failed to sync build #{build_data.get('number')}: {e}")
                await self.session.rollback()
                continue

        return {
            "synced": synced,
            "triaged": triaged,
            "message": f"Synced {synced} builds, triaged {triaged} failed builds",
        }

    async def sync_and_triage_build(self, build_data: dict) -> Build:
        build = await self._get_or_create_build(build_data)

        if build.branch and build.branch not in ("main", "master"):
            logger.info(f"Skipping triage for non-main branch build #{build.buildkite_build_number} ({build.branch})")
            return build

        # Always sync jobs to pick up retries (new jobs with different UIDs)
        full_build = await self.buildkite.get_build(build.buildkite_build_number)
        await self._sync_jobs(build, full_build.get("jobs", []))

        # Query failed jobs with failure relationship loaded (excluding soft fails)
        stmt = (
            select(Job)
            .where(Job.build_id == build.id)
            .where(Job.state == "failed")
            .where(Job.soft_failed == False)
            .options(selectinload(Job.failures))
        )
        result = await self.session.execute(stmt)
        failed_jobs = result.scalars().all()

        # Only triage jobs that don't already have failures (new or retry jobs)
        jobs_to_triage = [j for j in failed_jobs if not j.failures]
        if not jobs_to_triage:
            build.triage_status = "completed"
            await self.session.flush()
            # Still run auto-resolve even when no new triage needed — a KF
            # may have been created after this build was originally triaged
            await self._auto_resolve_known_failures(build)
            return build

        await triage_status.start_triage(build.buildkite_build_number, len(jobs_to_triage))
        await triage_status.log(
            build.buildkite_build_number,
            f"Build #{build.buildkite_build_number}: {len(jobs_to_triage)} jobs to triage",
        )

        try:
            await self._batch_triage_jobs(jobs_to_triage, build)

            build.triage_status = "completed"
            await self.session.flush()
            await self._auto_resolve_known_failures(build)
            await triage_status.complete_triage(build.buildkite_build_number)
        except Exception as e:
            await triage_status.log(build.buildkite_build_number, f"Error: {e}", level="error")
            await triage_status.error_triage(build.buildkite_build_number, str(e))
            raise

        return build

    async def _auto_resolve_known_failures(self, build: Build):
        """Auto-resolve open KnownFailures whose affected jobs ran but didn't produce the failure.

        Only resolves if the current build is newer than the last build where the failure was seen,
        to avoid incorrect resolution when syncing builds out of order.
        """
        # Get all open KnownFailures not seen in this build
        stmt = (
            select(KnownFailure)
            .where(KnownFailure.status == "open")
            .where(KnownFailure.last_seen_build_id != build.id)
            .options(
                selectinload(KnownFailure.failures).selectinload(Failure.job),
                selectinload(KnownFailure.last_seen_build),
            )
        )
        result = await self.session.execute(stmt)
        candidates = result.scalars().all()

        if not candidates:
            return

        # Get jobs that PASSED (or soft-failed) in this build.
        passed_stmt = (
            select(distinct(Job.name))
            .where(Job.build_id == build.id)
            .where(Job.name.isnot(None))
            .where(or_(Job.state == "passed", Job.soft_failed == True))
        )
        passed_result = await self.session.execute(passed_stmt)
        passed_job_names = {row[0] for row in passed_result.all()}

        # Get ALL job names present in this build (any state) to distinguish
        # "job not present" from "job present but not passed".
        all_jobs_stmt = (
            select(distinct(Job.name))
            .where(Job.build_id == build.id)
            .where(Job.name.isnot(None))
        )
        all_jobs_result = await self.session.execute(all_jobs_stmt)
        all_build_job_names = {row[0] for row in all_jobs_result.all()}

        resolved_count = 0
        for kf in candidates:
            if kf.is_flaky:
                continue  # Flaky issues require manual resolution

            # Only resolve if current build is newer than where failure was last seen
            if kf.last_seen_build and build.buildkite_build_number <= kf.last_seen_build.buildkite_build_number:
                continue

            # Get the set of job names historically affected by this KnownFailure
            affected_job_names = set()
            for f in kf.failures:
                if f.job and f.job.name:
                    affected_job_names.add(f.job.name)

            if not affected_job_names:
                continue

            # Check which affected jobs are present in this build
            present_affected = affected_job_names & all_build_job_names
            if not present_affected:
                continue  # None of the affected jobs exist in this build

            # ALL present affected jobs must have PASSED. If any is still
            # running, failed, or blocked, we can't conclude the failure is gone.
            if present_affected.issubset(passed_job_names):
                kf.status = "resolved"
                kf.resolved_at = datetime.utcnow()
                kf.resolved_by = "auto"
                kf.resolved_in_build_id = build.id
                resolved_count += 1

        if resolved_count > 0:
            await self.session.flush()
            logger.info(f"Auto-resolved {resolved_count} known failure(s) after build #{build.buildkite_build_number}")
            await triage_status.log(
                build.buildkite_build_number,
                f"Auto-resolved {resolved_count} known failure(s) (affected jobs passed)",
            )

    async def _batch_triage_jobs(self, jobs: list[Job], build: Build) -> bool:
        """Triage all failed jobs in a build using parallel individual Claude calls.

        Uses Buildkite Test Engine for pytest failures when available,
        falling back to raw log analysis for non-pytest or uncovered jobs.

        Returns True if triage succeeded (even partially), False if it failed entirely.
        """
        build_num = build.buildkite_build_number
        logger.info(f"Triaging {len(jobs)} jobs for build #{build_num}")
        await triage_status.log(build_num, f"Starting triage for {len(jobs)} failed jobs")
        await triage_status.update_phase(build_num, "fetching_logs")

        # Step 1: Try to get Test Engine (test collector) data
        tc_matched: dict[str, list[dict]] = {}
        if build.buildkite_build_id:
            try:
                te_service = TestEngineService()
                run = await te_service.get_run_by_build_id(build.buildkite_build_id)
                if run:
                    run_id = run.get("id")
                    if run_id:
                        failed_execs = await te_service.get_failed_executions(run_id)
                        if failed_execs:
                            tc_matched = _match_executions_to_jobs(failed_execs, jobs)
                            logger.info(
                                f"Test Engine: {len(failed_execs)} failed executions "
                                f"matched to {len(tc_matched)} jobs"
                            )
                            await triage_status.log(
                                build_num,
                                f"Test Engine: {len(failed_execs)} failed executions matched to {len(tc_matched)} jobs",
                            )
            except Exception as e:
                logger.warning(f"Test Engine lookup failed, falling back to logs: {e}")
                await triage_status.log(build_num, f"Test Engine lookup failed: {e}", level="warn")

        # Step 2: Classify pre-execution infra failures from structured exit data.
        # Jobs where exit_status == -1 (agent lost/stopped) never ran test code,
        # so there's no point fetching logs or calling Claude.
        pre_exec_infra_jobs: list[tuple[Job, str]] = []
        remaining_jobs: list[Job] = []
        for job in jobs:
            infra_msg = _detect_pre_execution_infra(job)
            if infra_msg:
                pre_exec_infra_jobs.append((job, infra_msg))
            else:
                remaining_jobs.append(job)

        for job, infra_msg in pre_exec_infra_jobs:
            job_name = job.name or job.step_key or "unknown"
            await self._create_infra_failure(job, build, infra_msg)
            await triage_status.update_job(build_num, job_name)
            await triage_status.log(
                build_num,
                f"Pre-exec infra (exit_status={job.exit_status}): {job_name} - {infra_msg}",
                level="warn",
            )

        if pre_exec_infra_jobs:
            await triage_status.log(
                build_num,
                f"Classified {len(pre_exec_infra_jobs)} job(s) as pre-execution infra from Buildkite exit data",
            )

        if not remaining_jobs:
            # All jobs were pre-execution infra — nothing left to triage
            await self.session.flush()
            return True

        # Step 3: Fetch logs for remaining jobs (TC-covered jobs also need raw logs
        # so Claude can find full execution output beyond truncated TC tracebacks)
        jobs = remaining_jobs
        await triage_status.log(build_num, f"Fetching logs for {len(jobs)} jobs...")

        async def fetch_log(job: Job) -> tuple[Job, str | None]:
            log = await self.buildkite.get_job_log(job.buildkite_job_id, build_num)
            return job, log

        log_results = await asyncio.gather(
            *[fetch_log(j) for j in jobs], return_exceptions=True
        )

        # Build list of (job, log_content, tc_executions) tuples
        import tempfile
        import shutil
        log_tmp_dir = tempfile.mkdtemp(prefix=f"triage-{build_num}-")

        job_infos: list[tuple[Job, str, list[dict] | None]] = []
        tc_only_jobs: list[tuple[Job, list[dict]]] = []  # fallback: TC data but no log
        no_log_jobs: list[Job] = []  # jobs with no log and no TC data

        for result in log_results:
            if isinstance(result, Exception):
                logger.error(f"Failed to fetch log: {result}")
                continue
            job, log_content = result
            tc_execs = tc_matched.get(job.buildkite_job_id)
            if log_content:
                # Check for infra exits before sending to Claude
                infra_msg = _detect_infra_exit(log_content)
                if infra_msg:
                    # Still send to Claude — the log might have useful test data
                    # before the infra exit. But flag it so we can fall back.
                    job_infos.append((job, log_content, tc_execs))
                else:
                    job_infos.append((job, log_content, tc_execs))
            elif tc_execs:
                # Log fetch failed but we have TC data — use mechanical fallback
                tc_only_jobs.append((job, tc_execs))
            else:
                # No log, no TC data — likely an infra failure (agent lost, etc.)
                no_log_jobs.append(job)

        # Handle jobs with no log output — create infra failures directly
        for job in no_log_jobs:
            job_name = job.name or job.step_key or "unknown"
            infra_msg = _detect_infra_exit(None)  # returns generic "no log" message
            await self._create_infra_failure(job, build, infra_msg)
            await triage_status.log(
                build_num, f"Infra failure (no log): {job_name} - {infra_msg}", level="warn"
            )

        if not job_infos and not tc_only_jobs and not no_log_jobs:
            shutil.rmtree(log_tmp_dir, ignore_errors=True)
            return False

        try:
            return await self._parallel_triage_jobs(
                job_infos, tc_only_jobs, build, log_tmp_dir,
            )
        finally:
            shutil.rmtree(log_tmp_dir, ignore_errors=True)

    async def _parallel_triage_jobs(
        self,
        job_infos: list[tuple[Job, str, list[dict] | None]],
        tc_only_jobs: list[tuple[Job, list[dict]]],
        build: Build,
        log_tmp_dir: str,
    ) -> bool:
        """Triage all jobs via parallel Claude calls, with TC data as enrichment.

        Args:
            job_infos: List of (job, log_content, tc_executions_or_none) tuples.
                All go through Claude. TC data enriches the prompt when available.
            tc_only_jobs: Jobs where log fetch failed but TC data exists. These
                get mechanical triage as fallback.
            build: The build being triaged.
            log_tmp_dir: Temp directory for log files.
        """
        build_num = build.buildkite_build_number

        # Load KnownFailures relevant to this build
        kf_context, kf_by_id = await self._load_kf_context(build)

        created_failures: list[Failure] = []

        # --- Fallback: TC-only jobs where log fetch failed ---
        if tc_only_jobs:
            await triage_status.log(
                build_num,
                f"Processing {len(tc_only_jobs)} jobs with TC data only (log fetch failed)",
            )
            for job, executions in tc_only_jobs:
                job_name = job.name or job.step_key or "unknown"
                tc_failures = await self._triage_tc_job(job, executions, build, kf_context)
                created_failures.extend(tc_failures)
                await triage_status.update_job(build_num, job_name)
                await triage_status.log(
                    build_num,
                    f"TC fallback {job_name}: {len(tc_failures)} failure(s) from structured data",
                )

        # --- Process all jobs with logs via parallel Claude calls ---
        if job_infos:
            tc_count = sum(1 for _, _, tc in job_infos if tc)
            await triage_status.update_phase(build_num, "analyzing")
            await triage_status.log(
                build_num,
                f"Analyzing {len(job_infos)} jobs with Claude (max 5 concurrent, {tc_count} with TC enrichment)...",
            )

            sem = asyncio.Semaphore(5)

            async def triage_one_job(
                job: Job, log_content: str, tc_execs: list[dict] | None,
            ) -> tuple[Job, str, list[Failure]]:
                async with sem:
                    job_name = job.name or job.step_key or "unknown"
                    tc_label = " [+TC]" if tc_execs else ""
                    await triage_status.log(build_num, f"Analyzing {job_name}{tc_label}...")
                    await triage_status.update_job(build_num, job_name)

                    # Write log to temp file
                    log_file = os.path.join(log_tmp_dir, f"{job.buildkite_job_id}.log")
                    with open(log_file, "w") as f:
                        f.write(log_content)

                    analyses, session_id = await analyze_failure_with_claude(
                        log_file, kf_context, tc_executions=tc_execs,
                    )

                    # Process analyses into Failure records
                    job_failures: list[Failure] = []
                    for analysis in analyses:
                        failure = await self._process_individual_failure(
                            analysis, job, log_content, build,
                        )
                        if failure:
                            job_failures.append(failure)

                    await self.session.flush()

                    # Auto-assign unassigned failures that share the same
                    # test function (ignoring params) or same error message
                    self._assign_sibling_parameterized_failures(job_failures)
                    self._assign_same_error_failures(job_failures)

                    # Retry remaining unassigned via session resume
                    unassigned = [f for f in job_failures if f.known_failure_id is None]
                    if unassigned and session_id:
                        await self._retry_unassigned_failures(unassigned, session_id, build)
                        await self.session.flush()

                    # If Claude found nothing, check for infrastructure exit patterns
                    if not job_failures:
                        infra_msg = _detect_infra_exit(log_content)
                        if infra_msg:
                            failure = await self._create_infra_failure(
                                job, build, infra_msg, log_content
                            )
                            job_failures.append(failure)
                            await self.session.flush()
                            await triage_status.log(
                                build_num,
                                f"Infra exit detected for {job_name}: {infra_msg}",
                                level="warn",
                            )

                    n_failures = len(job_failures)
                    n_kf = sum(1 for f in job_failures if f.known_failure_id is not None)
                    await triage_status.log(
                        build_num,
                        f"Done {job_name}: {n_failures} failure(s), {n_kf} matched to KFs",
                    )
                    return job, job_name, job_failures

            results = await asyncio.gather(
                *[triage_one_job(job, log, tc) for job, log, tc in job_infos],
                return_exceptions=True,
            )

            for result in results:
                if isinstance(result, Exception):
                    logger.error(f"Parallel triage failed for a job: {result}")
                    await triage_status.log(build_num, f"Job triage error: {result}", level="error")
                    continue
                _, _, job_failures = result
                created_failures.extend(job_failures)

        await self.session.flush()

        # --- Deduplicate KnownFailures created in parallel ---
        # Parallel Claude calls may independently create KFs for the same root cause.
        # Merge duplicates by title similarity: keep the first, reassign the rest.
        await self._dedup_parallel_known_failures(created_failures, build_num)
        await self.session.flush()

        # Summary
        total = len(created_failures)
        assigned = sum(1 for f in created_failures if f.known_failure_id is not None)
        unassigned_count = total - assigned
        await triage_status.log(
            build_num,
            f"Build #{build_num} triage complete: {total} failures, {assigned} matched to KFs"
            + (f", {unassigned_count} unassigned" if unassigned_count else ""),
        )

        if unassigned_count:
            logger.warning(
                f"{unassigned_count} failure(s) could not be assigned to known failures "
                f"in build #{build_num}"
            )

        return total > 0 or len(job_infos) > 0 or len(tc_only_jobs) > 0

    async def _load_kf_context(self, build: Build) -> tuple[list[dict], dict[int, KnownFailure]]:
        """Load KnownFailures relevant to this build for context.

        Includes:
        - All open KnownFailures
        - Resolved KFs whose resolved_in_build is after this build
        - KFs that predate history (first_seen is the oldest build in DB, meaning
          the failure may have started even earlier and should be matched)

        Returns (kf_context list for Claude, kf_by_id lookup dict).
        """
        # Find the oldest build in the DB to detect predates-history KFs
        oldest_stmt = select(func.min(Build.buildkite_build_number)).where(Build.branch == "main")
        oldest_result = await self.session.execute(oldest_stmt)
        oldest_build_number = oldest_result.scalar()

        kf_stmt = (
            select(KnownFailure)
            .where(
                or_(
                    KnownFailure.status == "open",
                    # Include resolved KFs that were resolved after this build
                    KnownFailure.resolved_in_build_id.in_(
                        select(Build.id).where(
                            Build.buildkite_build_number > build.buildkite_build_number
                        )
                    ),
                    # Include predates-history KFs (first_seen is the oldest build)
                    KnownFailure.first_seen_build_id.in_(
                        select(Build.id).where(
                            Build.buildkite_build_number == oldest_build_number
                        )
                    ) if oldest_build_number is not None else False,
                )
            )
            .options(
                selectinload(KnownFailure.failures).selectinload(Failure.job),
            )
        )
        kf_result = await self.session.execute(kf_stmt)
        open_known_failures = kf_result.scalars().all()

        kf_by_id = {kf.id: kf for kf in open_known_failures}
        kf_context = []
        for kf in open_known_failures:
            affected_jobs_set: set[str] = set()
            affected_tests_set: set[str] = set()
            for f in (kf.failures or []):
                if f.job and f.job.name:
                    affected_jobs_set.add(f.job.name)
                if f.failing_test:
                    parsed = _parse_failing_test_value(f.failing_test)
                    if isinstance(parsed, list):
                        affected_tests_set.update(parsed)
                    elif parsed:
                        affected_tests_set.add(parsed)
            kf_context.append({
                "id": kf.id,
                "title": kf.title,
                "summary": kf.summary,
                "match_prompt": kf.match_prompt,
                "category": kf.category,
                "affected_jobs": sorted(affected_jobs_set),
                "affected_tests": sorted(affected_tests_set),
            })

        return kf_context, kf_by_id

    async def _dedup_parallel_known_failures(
        self, failures: list[Failure], build_num: int
    ):
        """Use Claude to identify and merge duplicate KnownFailures from parallel triage.

        When N parallel Claude calls each see the same new failure pattern, each
        independently creates a KnownFailure. This asks Claude to evaluate whether
        any newly-created KFs describe the same root cause and should be merged.
        """
        # Collect all KF IDs created during this triage (from the failures list)
        kf_ids = {f.known_failure_id for f in failures if f.known_failure_id is not None}
        if len(kf_ids) <= 1:
            return

        # Load them
        kf_stmt = select(KnownFailure).where(KnownFailure.id.in_(kf_ids))
        kf_result = await self.session.execute(kf_stmt)
        kfs = kf_result.scalars().all()

        # Only consider KFs first seen in THIS build (newly created, not pre-existing)
        build_stmt = select(Build.id).where(Build.buildkite_build_number == build_num)
        build_result = await self.session.execute(build_stmt)
        current_build_id = build_result.scalar_one_or_none()
        if not current_build_id:
            return
        new_kfs = [kf for kf in kfs if kf.first_seen_build_id == current_build_id]

        if len(new_kfs) <= 1:
            return

        # Ask Claude to identify duplicates
        await triage_status.log(
            build_num,
            f"Checking {len(new_kfs)} new known failures for duplicates...",
        )

        kf_descriptions = [
            {
                "id": kf.id,
                "title": kf.title,
                "summary": kf.summary,
                "match_prompt": kf.match_prompt,
                "category": kf.category,
            }
            for kf in new_kfs
        ]

        merge_groups = await deduplicate_known_failures(kf_descriptions)
        if not merge_groups:
            await triage_status.log(build_num, "No duplicates found")
            return

        # Build lookup
        kf_by_id = {kf.id: kf for kf in new_kfs}
        merged_count = 0

        for group in merge_groups:
            keep_id = group.get("keep_id")
            merge_ids = group.get("merge_ids", [])
            reason = group.get("reason", "")

            keeper = kf_by_id.get(keep_id)
            if not keeper:
                logger.warning(f"Dedup keep_id {keep_id} not found, skipping group")
                continue

            for dupe_id in merge_ids:
                dupe = kf_by_id.get(dupe_id)
                if not dupe:
                    logger.warning(f"Dedup merge_id {dupe_id} not found, skipping")
                    continue

                # Reassign all Failures pointing to dupe
                for f in failures:
                    if f.known_failure_id == dupe.id:
                        f.known_failure_id = keeper.id

                await self.session.delete(dupe)
                merged_count += 1
                logger.info(
                    f"Merged KF #{dupe.id} ({dupe.title!r}) into "
                    f"KF #{keeper.id} ({keeper.title!r}): {reason}"
                )

        if merged_count > 0:
            await triage_status.log(
                build_num,
                f"Merged {merged_count} duplicate known failure(s)",
            )

    async def _triage_tc_job(
        self,
        job: Job,
        executions: list[dict],
        build: Build,
        kf_context: list[dict],
    ) -> list[Failure]:
        """Create Failure records directly from Test Engine structured data (no Claude)."""
        failures: list[Failure] = []

        for ex in executions:
            test_name = ex.get("test_name", "unknown")
            # Parse test path: "tests/path/test_file.py test_func" or "tests/path/test_file.py::test_func"
            parts = re.split(r'\s+|::', test_name, maxsplit=1)
            test_file = parts[0] if parts else "unknown"
            test_func = parts[1] if len(parts) > 1 else ""

            # Normalize: strip "tests/" prefix
            test_file_norm = _normalize_test_path(test_file) or test_file
            failing_test = f"{test_file_norm}::{test_func}" if test_func else test_file_norm

            # Extract error info from traceback
            failure_reason = ex.get("failure_reason", "")
            error_type = "TestFailure"
            error_detail = ""
            # Try to extract error type from last line of traceback
            if failure_reason:
                lines = failure_reason.strip().split("\n")
                last_line = lines[-1].strip() if lines else ""
                if ":" in last_line:
                    error_type = last_line.split(":")[0].strip()
                    error_detail = last_line.split(":", 1)[1].strip()[:80]

            error_signature = f"{test_file_norm}::{test_func}:{error_type}:{error_detail}" if test_func else f"{test_file_norm}:{error_type}:{error_detail}"

            # Build log excerpt from TC data
            log_excerpt = self._build_tc_log_excerpt(executions, failing_test)

            # Try to match to existing KnownFailure by test name overlap
            known_failure_id = None
            effective_category = "test"
            for kf_info in kf_context:
                # Check if any affected test matches
                for at in kf_info.get("affected_tests", []):
                    if failing_test and (failing_test in at or at in failing_test):
                        known_failure_id = kf_info["id"]
                        effective_category = kf_info.get("category", "test")
                        break
                if known_failure_id:
                    break

            is_flaky = False
            if error_signature and effective_category != "infra":
                is_flaky = await self.pattern_matcher.is_signature_flaky(error_signature)

            if known_failure_id:
                # Update last_seen_build
                kf = await self.session.get(KnownFailure, known_failure_id)
                if kf:
                    if not kf.last_seen_build_id:
                        kf.last_seen_build_id = build.id
                    else:
                        last_build_stmt = select(Build).where(Build.id == kf.last_seen_build_id)
                        lb_result = await self.session.execute(last_build_stmt)
                        last_build = lb_result.scalar_one_or_none()
                        if last_build and build.buildkite_build_number > last_build.buildkite_build_number:
                            kf.last_seen_build_id = build.id
            else:
                # Create new KnownFailure
                title = f"{test_func or test_file_norm}: {error_type}" if error_type != "TestFailure" else f"Test failure in {test_file_norm}"
                kf = KnownFailure(
                    title=title[:200],
                    category=effective_category,
                    summary=f"{error_type}: {error_detail}"[:500] if error_detail else f"Test failure in {test_file_norm}",
                    match_prompt=f"Test {failing_test} failing with {error_type}",
                    status="open",
                    is_flaky=is_flaky,
                    first_seen_build_id=build.id,
                    last_seen_build_id=build.id,
                )
                self.session.add(kf)
                await self.session.flush()
                known_failure_id = kf.id

            failure = Failure(
                job_id=job.id,
                known_failure_id=known_failure_id,
                failure_category=effective_category,
                failure_type="test",
                failing_test=failing_test,
                error_signature=error_signature,
                error_message=f"{error_type}: {error_detail}" if error_detail else error_type,
                root_cause=failure_reason[-500:] if failure_reason else None,
                is_flaky=is_flaky,
                log_excerpt=log_excerpt,
            )
            self.session.add(failure)
            failures.append(failure)

            if error_signature:
                await self.pattern_matcher.record_signature(error_signature)

        await self.session.flush()
        return failures

    # Common infra failure patterns with canonical titles.
    # Maps (keyword_in_error, ...) -> (canonical_title, summary_template, match_prompt)
    _INFRA_PATTERNS = [
        {
            "keywords": ["agent lost", "agent stopped", "no log output", "pre-execution failure"],
            "title": "Agent lost / pod provisioning failure",
            "summary": "Infrastructure failure where the Kubernetes pod or agent died before or during job execution. No test code ran.",
            "match_prompt": "Agent lost or pod failure: exit_status=-1, no log output, agent became unreachable or was stopped/evicted.",
            "failure_type": "agent_lost",
        },
        {
            "keywords": ["cancelled", "canceled"],
            "title": "Job cancelled",
            "summary": "Job was cancelled before completing.",
            "match_prompt": "Job cancelled before execution completed.",
            "failure_type": "cancelled",
        },
    ]

    async def _create_infra_failure(
        self,
        job: Job,
        build: Build,
        error_message: str,
        log_content: str | None = None,
    ) -> Failure:
        """Create an infrastructure failure directly (no Claude needed).

        Used for agent-lost, OOM kills, and other infrastructure exits where
        there's no test failure for Claude to analyze.
        """
        # Build a log excerpt from the tail of the log (where exit info appears)
        log_excerpt = ""
        if log_content:
            lines = log_content.split("\n")
            tail_lines = lines[-30:] if len(lines) > 30 else lines
            cleaned = [_clean_log_line(l) for l in tail_lines]
            log_excerpt = "\n".join(l for l in cleaned if l.strip())

        # Determine the canonical pattern for this error
        error_lower = error_message.lower()
        matched_pattern = None
        for pattern in self._INFRA_PATTERNS:
            if any(kw in error_lower for kw in pattern["keywords"]):
                matched_pattern = pattern
                break

        # Try to match to an existing infra KnownFailure
        kf_stmt = (
            select(KnownFailure)
            .where(KnownFailure.category == "infra")
            .where(KnownFailure.status == "open")
        )
        kf_result = await self.session.execute(kf_stmt)
        existing_kfs = kf_result.scalars().all()

        known_failure_id = None
        for kf in existing_kfs:
            kf_title_lower = (kf.title or "").lower()
            kf_match_lower = (kf.match_prompt or "").lower()
            combined = kf_title_lower + " " + kf_match_lower

            if matched_pattern:
                # Match if the existing KF has any of the same keywords
                if any(kw in combined for kw in matched_pattern["keywords"]):
                    known_failure_id = kf.id
                    break
            else:
                # Fallback: keyword overlap between error message and KF
                error_words = set(error_lower.split())
                kf_words = set(combined.split())
                overlap = error_words & kf_words - {"in", "the", "a", "of", "for", "and", "or", "to", "with"}
                if len(overlap) >= 3:
                    known_failure_id = kf.id
                    break

        if known_failure_id:
            kf = await self.session.get(KnownFailure, known_failure_id)
            if kf:
                if not kf.last_seen_build_id:
                    kf.last_seen_build_id = build.id
                else:
                    last_build = await self.session.get(Build, kf.last_seen_build_id)
                    if last_build and build.buildkite_build_number > last_build.buildkite_build_number:
                        kf.last_seen_build_id = build.id

        if not known_failure_id:
            # Create a new infra KnownFailure with a canonical title (no job name)
            if matched_pattern:
                title = matched_pattern["title"]
                summary = matched_pattern["summary"]
                match_prompt = matched_pattern["match_prompt"]
            else:
                title = error_message[:200]
                summary = f"Infrastructure failure: {error_message}. Job exited abnormally without producing test results."
                match_prompt = f"Infrastructure exit: {error_message}"

            kf = KnownFailure(
                title=title,
                category="infra",
                summary=summary,
                match_prompt=match_prompt,
                status="open",
                is_flaky=False,
                first_seen_build_id=build.id,
                last_seen_build_id=build.id,
            )
            self.session.add(kf)
            await self.session.flush()
            known_failure_id = kf.id

        failure_type = matched_pattern["failure_type"] if matched_pattern else "infra"

        failure = Failure(
            job_id=job.id,
            known_failure_id=known_failure_id,
            failure_category="infra",
            failure_type=failure_type,
            error_message=error_message,
            log_excerpt=log_excerpt or error_message,
        )
        self.session.add(failure)
        return failure

    async def _process_individual_failure(
        self,
        analysis: dict,
        job: Job,
        log_content: str,
        build: Build,
    ) -> Failure | None:
        """Process a single failure from an individual Claude triage response."""
        # Handle failing_test — normalize paths before storage
        failing_test = analysis.get("failing_test")
        if isinstance(failing_test, list):
            failing_test = [_normalize_test_path(t) or t for t in failing_test]
            failing_test_str = json.dumps(failing_test)
            failing_test = failing_test[0] if failing_test else None
        else:
            failing_test = _normalize_test_path(failing_test)
            failing_test_str = failing_test

        error_message = analysis.get("error_message")
        log_line_start = analysis.get("log_line_start")
        log_line_end = analysis.get("log_line_end")

        # Build log excerpt from Claude's line range
        if log_content and log_line_start and log_line_end:
            log_excerpt = self._build_log_excerpt(
                log_content, int(log_line_start), int(log_line_end),
                job.web_url or "",
            )
        else:
            log_excerpt = error_message or ""

        # Resolve KnownFailure assignment
        error_signature = analysis.get("error_signature")
        new_kf_data = analysis.get("new_known_failure")
        effective_category = (new_kf_data.get("category") if new_kf_data else None) or analysis.get("category")

        known_failure_id = analysis.get("known_failure_id")

        if known_failure_id:
            kf = await self.session.get(KnownFailure, known_failure_id)
            if kf:
                if not effective_category:
                    effective_category = kf.category
                # Update last_seen_build if this build is newer
                if not kf.last_seen_build_id:
                    kf.last_seen_build_id = build.id
                else:
                    last_build_stmt = select(Build).where(Build.id == kf.last_seen_build_id)
                    lb_result = await self.session.execute(last_build_stmt)
                    last_build = lb_result.scalar_one_or_none()
                    if last_build and build.buildkite_build_number > last_build.buildkite_build_number:
                        kf.last_seen_build_id = build.id
                # Update first_seen_build if this build is older
                if not kf.first_seen_build_id:
                    kf.first_seen_build_id = build.id
                else:
                    first_build_stmt = select(Build).where(Build.id == kf.first_seen_build_id)
                    fb_result = await self.session.execute(first_build_stmt)
                    first_build = fb_result.scalar_one_or_none()
                    if first_build and build.buildkite_build_number < first_build.buildkite_build_number:
                        kf.first_seen_build_id = build.id
            else:
                logger.warning(f"Claude assigned KF ID {known_failure_id} not found")
                known_failure_id = None

        is_flaky = False
        if error_signature and effective_category != "infra":
            is_flaky = await self.pattern_matcher.is_signature_flaky(error_signature)

        if not known_failure_id and new_kf_data:
            title = new_kf_data.get("title", error_message[:200] if error_message else "Unknown failure")
            kf = KnownFailure(
                title=title,
                category=new_kf_data.get("category", effective_category),
                summary=new_kf_data.get("summary"),
                match_prompt=new_kf_data.get("match_prompt"),
                status="open",
                is_flaky=is_flaky,
                first_seen_build_id=build.id,
                last_seen_build_id=build.id,
            )
            self.session.add(kf)
            await self.session.flush()
            known_failure_id = kf.id

        failure = Failure(
            job_id=job.id,
            known_failure_id=known_failure_id,
            failure_category=effective_category,
            failure_type=analysis.get("failure_type"),
            failing_test=failing_test_str,
            error_signature=error_signature,
            error_message=error_message,
            root_cause=analysis.get("root_cause"),
            is_flaky=is_flaky,
            log_excerpt=log_excerpt,
        )
        self.session.add(failure)

        if error_signature:
            await self.pattern_matcher.record_signature(error_signature)

        return failure

    async def _retry_unassigned_failures(
        self,
        unassigned: list[Failure],
        session_id: str,
        build: Build,
    ):
        """Resume Claude session to get KF assignments for unassigned failures."""
        logger.info(f"Retrying {len(unassigned)} unassigned failure(s) via Claude resume")

        # Build retry prompt with unassigned failure details
        failure_details = []
        for f in unassigned:
            details = f"- error_signature: {f.error_signature or 'none'}"
            if f.error_message:
                details += f"\n  error_message: {f.error_message[:200]}"
            if f.failing_test:
                details += f"\n  failing_test: {f.failing_test}"
            failure_details.append(details)

        retry_prompt = (
            "Some failures in your response are missing known_failure assignments. "
            "Every failure MUST have either:\n"
            "- known_failure_id: referencing an existing ID from the KNOWN FAILURES list\n"
            "- new_known_failure: {title, summary, match_prompt, category}\n\n"
            "Unassigned failures:\n"
            + "\n".join(failure_details) + "\n\n"
            "Return ONLY a JSON array where each element has:\n"
            '{"error_signature": "...", "known_failure_id": <int or null>, '
            '"new_known_failure": {"title": "...", "summary": "...", "match_prompt": "...", '
            '"category": "..."} or null}\n'
        )

        result_text = await resume_claude_session(session_id, retry_prompt)
        if not result_text:
            logger.warning("Claude resume for unassigned failures returned no result")
            return

        # Parse response
        try:
            array_match = re.search(r'\[[\s\S]*\]', result_text)
            if not array_match:
                logger.warning("Claude resume response missing JSON array")
                return
            fixes = json.loads(array_match.group())
        except json.JSONDecodeError as e:
            logger.warning(f"Failed to parse Claude resume response: {e}")
            return

        # Build lookup by error_signature
        unassigned_by_sig = {}
        for f in unassigned:
            if f.error_signature:
                unassigned_by_sig[f.error_signature] = f

        for fix in fixes:
            sig = fix.get("error_signature")
            failure = unassigned_by_sig.get(sig) if sig else None
            if not failure:
                continue

            known_failure_id = fix.get("known_failure_id")
            new_kf_data = fix.get("new_known_failure")

            if known_failure_id:
                kf = await self.session.get(KnownFailure, known_failure_id)
                if kf:
                    failure.known_failure_id = kf.id
                    kf.last_seen_build_id = build.id
                    continue

            if new_kf_data:
                title = new_kf_data.get("title", "Unknown failure")
                kf = KnownFailure(
                    title=title,
                    category=new_kf_data.get("category", failure.failure_category),
                    summary=new_kf_data.get("summary"),
                    match_prompt=new_kf_data.get("match_prompt"),
                    status="open",
                    is_flaky=failure.is_flaky or False,
                    first_seen_build_id=build.id,
                    last_seen_build_id=build.id,
                )
                self.session.add(kf)
                await self.session.flush()
                failure.known_failure_id = kf.id

        assigned_count = sum(1 for f in unassigned if f.known_failure_id is not None)
        logger.info(f"Claude retry assigned {assigned_count}/{len(unassigned)} failures")

    @staticmethod
    def _get_test_function(failing_test: str | None) -> str | None:
        """Extract the test function name (without parameters) from a failing_test string.

        e.g. "test_file.py::test_func[param1-param2]" -> "test_file.py::test_func"
        """
        if not failing_test:
            return None
        # Strip parameterization brackets
        bracket_idx = failing_test.find('[')
        return failing_test[:bracket_idx] if bracket_idx > 0 else failing_test

    @staticmethod
    def _assign_same_error_failures(failures: list[Failure]):
        """Auto-assign unassigned failures to the same KF when they share the same error message.

        When 14 tests fail with the same OOM error, Claude may assign one but miss the rest.
        This groups unassigned failures by error_message and assigns them to the KF of any
        sibling failure with the same error.
        """
        # Build map: error_message -> known_failure_id (from assigned failures)
        error_to_kf: dict[str, int] = {}
        for f in failures:
            if f.known_failure_id is None or not f.error_message:
                continue
            # Use first line of error message as key
            key = f.error_message.split('\n')[0].strip()
            if key:
                error_to_kf[key] = f.known_failure_id

        if not error_to_kf:
            return

        assigned = 0
        for f in failures:
            if f.known_failure_id is not None or not f.error_message:
                continue
            key = f.error_message.split('\n')[0].strip()
            if key in error_to_kf:
                f.known_failure_id = error_to_kf[key]
                assigned += 1
                logger.info(
                    f"Auto-assigned same-error failure to KF#{f.known_failure_id}: {key[:80]}"
                )

        if assigned:
            logger.info(f"Auto-assigned {assigned} same-error failure(s)")

    @staticmethod
    def _assign_sibling_parameterized_failures(failures: list[Failure]):
        """Auto-assign unassigned failures to the same KF as an assigned sibling.

        When Claude identifies test_func[param_A] as KF#X but misses test_func[param_B],
        this assigns param_B to KF#X since they're the same test function.
        """
        # Build map: test_function -> known_failure_id (from assigned failures)
        func_to_kf: dict[str, int] = {}
        for f in failures:
            if f.known_failure_id is None:
                continue
            test = f.failing_test
            if isinstance(test, str) and test.startswith('['):
                try:
                    tests = json.loads(test)
                except json.JSONDecodeError:
                    tests = [test]
            elif isinstance(test, str):
                tests = [test]
            else:
                continue
            for t in tests:
                func = TriageService._get_test_function(t)
                if func:
                    func_to_kf[func] = f.known_failure_id

        if not func_to_kf:
            return

        assigned = 0
        for f in failures:
            if f.known_failure_id is not None:
                continue
            test = f.failing_test
            if isinstance(test, str) and test.startswith('['):
                try:
                    tests = json.loads(test)
                except json.JSONDecodeError:
                    tests = [test]
            elif isinstance(test, str):
                tests = [test]
            else:
                continue
            for t in tests:
                func = TriageService._get_test_function(t)
                if func and func in func_to_kf:
                    f.known_failure_id = func_to_kf[func]
                    assigned += 1
                    logger.info(
                        f"Auto-assigned sibling parameterized test {t} to KF#{f.known_failure_id}"
                    )
                    break

        if assigned:
            logger.info(f"Auto-assigned {assigned} sibling parameterized failure(s)")

    async def _get_or_create_build(self, build_data: dict) -> Build:
        stmt = (
            select(Build)
            .where(Build.buildkite_build_number == build_data["number"])
            .options(selectinload(Build.jobs))
        )
        result = await self.session.execute(stmt)
        build = result.scalar_one_or_none()

        if build:
            build.state = build_data.get("state", build.state)
            build.synced_at = datetime.utcnow()
            # Backfill buildkite_build_id if not set
            if not build.buildkite_build_id and build_data.get("id"):
                build.buildkite_build_id = build_data["id"]
        else:
            created_at = None
            if build_data.get("created_at"):
                try:
                    created_at = datetime.fromisoformat(
                        build_data["created_at"].replace("Z", "+00:00")
                    )
                except ValueError:
                    pass

            build = Build(
                buildkite_build_number=build_data["number"],
                buildkite_build_id=build_data.get("id"),
                build_type=build_data.get("build_type"),
                state=build_data.get("state"),
                commit_sha=build_data.get("commit"),
                branch=build_data.get("branch"),
                message=build_data.get("message", "")[:500] if build_data.get("message") else None,
                web_url=build_data.get("web_url"),
                created_at=created_at,
                synced_at=datetime.utcnow(),
            )
            self.session.add(build)
            await self.session.flush()

        return build

    async def _sync_jobs(self, build: Build, jobs_data: list[dict]):
        # Get existing jobs with failure relationship loaded
        stmt = select(Job).where(Job.build_id == build.id).options(selectinload(Job.failures))
        result = await self.session.execute(stmt)
        existing_jobs = result.scalars().all()
        existing_job_ids = {job.buildkite_job_id for job in existing_jobs}

        # Build lookup dict for existing jobs
        existing_jobs_by_id = {job.buildkite_job_id: job for job in existing_jobs}

        for job_data in jobs_data:
            job_id = job_data.get("id")
            if not job_id:
                continue

            if job_id in existing_job_ids:
                existing_job = existing_jobs_by_id[job_id]
                new_state = job_data.get("state", existing_job.state)
                existing_job.state = new_state
                existing_job.soft_failed = job_data.get("soft_failed", False)
                # Backfill command if not set
                if not existing_job.command and job_data.get("command"):
                    existing_job.command = job_data["command"]
                # Always update exit metadata (may change on retry)
                existing_job.exit_status = job_data.get("exit_status")
                existing_job.signal = job_data.get("signal")
                existing_job.signal_reason = job_data.get("signal_reason")
            else:
                job = Job(
                    build_id=build.id,
                    buildkite_job_id=job_id,
                    name=job_data.get("name"),
                    state=job_data.get("state"),
                    step_key=job_data.get("step_key"),
                    web_url=job_data.get("web_url"),
                    command=job_data.get("command"),
                    soft_failed=job_data.get("soft_failed", False),
                    exit_status=job_data.get("exit_status"),
                    signal=job_data.get("signal"),
                    signal_reason=job_data.get("signal_reason"),
                )
                self.session.add(job)

        await self.session.flush()

        # Detect retry outcomes by step_key grouping.
        # In Buildkite, a retry creates a NEW job with the same step_key.
        # If a step_key has both a failed job (with failures) and a passed job,
        # the failures are confirmed flaky.
        all_jobs_stmt = select(Job).where(Job.build_id == build.id).options(selectinload(Job.failures))
        all_jobs_result = await self.session.execute(all_jobs_stmt)
        all_jobs = all_jobs_result.scalars().all()

        jobs_by_step_key: dict[str, list[Job]] = {}
        for job in all_jobs:
            if job.step_key:
                jobs_by_step_key.setdefault(job.step_key, []).append(job)

        for step_key, step_jobs in jobs_by_step_key.items():
            has_passed = any(j.state == "passed" for j in step_jobs)
            if not has_passed:
                continue

            for job in step_jobs:
                if job.state != "failed" or not job.failures:
                    continue
                # This failed job has a sibling that passed — mark failures as retry_passed
                for failure in job.failures:
                    if not failure.retry_passed:
                        failure.retry_passed = True
                        if failure.error_signature:
                            await self.pattern_matcher.record_retry_success(
                                failure.error_signature
                            )
                        logger.info(f"Retry passed for {job.name} (step_key={step_key})")
                # Clear retry_pending since we've resolved the outcome
                job.retry_pending = False

        await self.session.flush()

    @staticmethod
    def _clean_verbose_assertion_lines(excerpt: str) -> str:
        """Clean up verbose pytest assertion output.

        Strips:
        - RequestOutput(...) and CompletionOutput(...) repr dumps
        - Long 'where N = len([RequestOutput(...' lines
        - Token ID arrays
        - Keeps the essential assertion error and values
        """
        cleaned_lines = []
        skip_continuation = False
        for line in excerpt.split('\n'):
            # Check if this line starts a verbose repr dump
            if re.search(r'RequestOutput\(request_id=', line) or re.search(r'CompletionOutput\(index=', line):
                skip_continuation = True
                continue
            if re.search(r'prompt_token_ids=\[', line):
                skip_continuation = True
                continue
            # Lines that are continuations of long repr strings
            if skip_continuation:
                # Check if the line ends the repr (closing bracket/paren) or starts a new meaningful line
                if re.match(r'^\d+\t', line) and not re.search(r'(token_ids|prompt=|encoder_prompt|outputs=\[)', line):
                    skip_continuation = False
                else:
                    continue
            # Truncate lines with len([RequestOutput(...)...]) to just len([...])
            line = re.sub(
                r'len\(\[RequestOutput\(.*?\][\)]*\)',
                'len([...outputs...])',
                line,
                flags=re.DOTALL,
            )
            # Truncate lines with long token ID arrays
            line = re.sub(r'prompt_token_ids=\[[^\]]{100,}\]', 'prompt_token_ids=[...]', line)
            # Truncate very long lines (>500 chars) at a reasonable point
            if len(line) > 500:
                line = line[:500] + '...'
            cleaned_lines.append(line)
        return '\n'.join(cleaned_lines)

    @staticmethod
    def _build_log_excerpt(log_content: str, line_start: int, line_end: int,
                           job_web_url: str = "", max_size: int = 8000) -> str:
        """Build a log excerpt from a line range (1-indexed, as returned by Claude).

        Cleans ANSI codes, filters noise, and adds a Buildkite deep link.
        """
        lines = log_content.split('\n')
        # Clamp to valid range (convert from 1-indexed to 0-indexed)
        start_idx = max(0, line_start - 1)
        end_idx = min(len(lines), line_end)

        noise_patterns = [
            r'Pulling fs layer', r'Waiting\s*$', r'Downloading\s*$',
            r'Extracting\s*$', r'Pull complete', r'Already exists',
        ]

        excerpt_lines = []
        for j in range(start_idx, end_idx):
            if any(re.search(p, lines[j]) for p in noise_patterns):
                continue
            excerpt_lines.append(f"{j+1}\t{_clean_log_line(lines[j])}")

        result = '\n'.join(excerpt_lines)
        result = TriageService._clean_verbose_assertion_lines(result)

        if job_web_url:
            if '#' in job_web_url:
                base, anchor = job_web_url.split('#', 1)
                bk_line_url = f"{base}?line={line_start}#{anchor}"
            else:
                bk_line_url = f"{job_web_url}?line={line_start}"
            result = f"[View in Buildkite at line {line_start}]({bk_line_url})\n\n{result}"

        return result[:max_size]

    @staticmethod
    def _build_tc_log_excerpt(
        executions: list[dict], failing_test: str | None, max_size: int = 8000
    ) -> str:
        """Build a log excerpt from Test Engine structured failure data.

        Finds the execution matching the failing_test and formats the traceback.
        """
        # Find the matching execution for this specific failure
        matched = None
        for ex in executions:
            test_name = ex.get("test_name", "")
            if failing_test and failing_test in test_name:
                matched = ex
                break
            # Also try matching by test function name only
            if failing_test:
                func_name = failing_test.split("::")[-1].split("[")[0] if "::" in failing_test else failing_test
                if func_name and func_name in test_name:
                    matched = ex
                    break

        if not matched:
            # Fall back to first execution
            matched = executions[0] if executions else None

        if not matched:
            return ""

        parts = [f"Test: {matched.get('test_name', 'unknown')}"]
        if matched.get("location"):
            parts.append(f"Location: {matched['location']}")
        if matched.get("failure_reason"):
            parts.append(f"\n{matched['failure_reason']}")

        excerpt = "\n".join(parts)
        return excerpt[:max_size]

    async def _triage_job(self, job: Job, build_number: int):
        """Triage a single job individually (used by retriage_job)."""
        logger.info(f"Triaging job {job.name} ({job.buildkite_job_id})")

        log_content = await self.buildkite.get_job_log(job.buildkite_job_id, build_number)
        if not log_content:
            logger.warning(f"No log content for job {job.buildkite_job_id}")
            return

        # Get the build for KnownFailure association
        build_stmt = select(Build).where(Build.buildkite_build_number == build_number)
        build_result = await self.session.execute(build_stmt)
        build = build_result.scalar_one_or_none()
        if not build:
            logger.warning(f"Build #{build_number} not found")
            return

        kf_context, _ = await self._load_kf_context(build)

        # Save log to temp file for Claude to analyze with tools
        import tempfile
        log_tmp = tempfile.NamedTemporaryFile(
            prefix=f"triage-{build_number}-", suffix=".log", mode="w", delete=False,
        )
        log_tmp.write(log_content)
        log_tmp.close()

        try:
            analyses, session_id = await analyze_failure_with_claude(log_tmp.name, kf_context)

            created_failures = []
            for analysis in analyses:
                failure = await self._process_individual_failure(
                    analysis, job, log_content, build,
                )
                if failure:
                    created_failures.append(failure)

            await self.session.flush()

            # Auto-assign unassigned failures that share the same
            # test function (ignoring params) or same error message
            self._assign_sibling_parameterized_failures(created_failures)
            self._assign_same_error_failures(created_failures)

            # Retry remaining unassigned failures via Claude resume
            unassigned = [f for f in created_failures if f.known_failure_id is None]
            if unassigned and session_id:
                await self._retry_unassigned_failures(unassigned, session_id, build)
                await self.session.flush()

            still_unassigned = [f for f in created_failures if f.known_failure_id is None]
            if still_unassigned:
                logger.warning(
                    f"{len(still_unassigned)} failure(s) in job {job.name} "
                    f"could not be assigned to known failures"
                )

            logger.info(f"Triaged job {job.name}: {len(analyses)} failure(s)")
        finally:
            os.unlink(log_tmp.name)

    async def retriage_job(self, job_id: int) -> Failure | None:
        stmt = select(Job).where(Job.id == job_id).options(
            selectinload(Job.build),
            selectinload(Job.failures),
        )
        result = await self.session.execute(stmt)
        job = result.scalar_one_or_none()

        if not job:
            return None

        # Delete existing failures for this job
        for failure in job.failures:
            await self.session.delete(failure)
        await self.session.flush()

        await self._triage_job(job, job.build.buildkite_build_number)

        # Reload failures
        await self.session.refresh(job, ["failures"])
        return job.failures[0] if job.failures else None
