import logging
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import Build, Job, Failure
from app.services.buildkite import BuildkiteService
from app.services.claude import analyze_failure_with_claude
from app.services.pattern_matcher import PatternMatcher
from app.services.triage_status import triage_status

logger = logging.getLogger(__name__)


class TriageService:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.buildkite = BuildkiteService()
        self.pattern_matcher = PatternMatcher(session)

    async def sync_and_triage_build(self, build_data: dict) -> Build:
        build = await self._get_or_create_build(build_data)

        if build.triage_status == "completed":
            return build

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

        # Count jobs that need triaging
        jobs_to_triage = [j for j in failed_jobs if not j.failures]
        if jobs_to_triage:
            await triage_status.start_triage(build.buildkite_build_number, len(jobs_to_triage))

        try:
            for job in jobs_to_triage:
                await triage_status.update_job(build.buildkite_build_number, job.name or "unknown")
                await self._triage_job(job, build.buildkite_build_number)

            build.triage_status = "completed"
            await self.session.flush()
            await triage_status.complete_triage(build.buildkite_build_number)
        except Exception as e:
            await triage_status.error_triage(build.buildkite_build_number, str(e))
            raise

        return build

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
                old_state = existing_job.state
                new_state = job_data.get("state", existing_job.state)

                # Detect retry success: job was failed, now passed
                if old_state == "failed" and new_state == "passed":
                    for failure in existing_job.failures:
                        if failure.error_signature:
                            await self.pattern_matcher.record_retry_success(
                                failure.error_signature
                            )
                    if existing_job.failures:
                        logger.info(f"Job {existing_job.name} succeeded after retry")

                existing_job.state = new_state
                existing_job.soft_failed = job_data.get("soft_failed", False)
            else:
                job = Job(
                    build_id=build.id,
                    buildkite_job_id=job_id,
                    name=job_data.get("name"),
                    state=job_data.get("state"),
                    step_key=job_data.get("step_key"),
                    web_url=job_data.get("web_url"),
                    soft_failed=job_data.get("soft_failed", False),
                )
                self.session.add(job)

        await self.session.flush()

    def _extract_smart_log_excerpt(self, log_content: str, job_web_url: str = "", max_size: int = 8000) -> str:
        """Extract the most relevant portions of a log, expanding backwards from failure.

        Returns log excerpt with line numbers for Buildkite linking.
        """
        import re
        lines = log_content.split('\n')

        # Noise patterns to skip (docker pull output, etc.)
        noise_patterns = [
            r'Pulling fs layer',
            r'Waiting\s*$',
            r'Downloading\s*$',
            r'Extracting\s*$',
            r'Pull complete',
            r'Already exists',
            r'\[[\dA-Z]+\[2K',  # ANSI cursor movement
        ]

        def is_noise(line: str) -> bool:
            return any(re.search(p, line) for p in noise_patterns)

        def clean_line(line: str) -> str:
            """Remove buildkite timing markers and timestamps from log lines."""
            # Remove _bk;t=1234567890 timing markers
            line = re.sub(r'_bk;t=\d+\s*', '', line)
            # Remove buildkite timestamps like [2026-02-12T23:12:25Z]
            line = re.sub(r'^\s*\[[0-9T\-:Z]+\]\s*', '', line)
            return line

        # Patterns for actual failures (in order of informativeness)
        failure_patterns = [
            (r'AssertionError', 'assertion'),
            (r'FAILED\s+tests/', 'pytest_failed'),
            (r'Error:.*accuracy', 'accuracy'),
            (r'ValueError:', 'value_error'),
            (r'RuntimeError:', 'runtime_error'),
            (r'KeyError:', 'key_error'),
            (r'TypeError:', 'type_error'),
            (r'ModuleNotFoundError:', 'import_error'),
            (r'ImportError:', 'import_error'),
            (r'HTTPError:', 'http_error'),
            (r'\d{3} Client Error', 'http_error'),
            (r'CUDA.*error', 'cuda_error'),
            (r'OutOfMemoryError', 'oom'),
        ]

        # Find failure location - scan backwards from end to find most relevant failure
        failure_idx = None
        failure_type = None

        for i in range(len(lines) - 1, -1, -1):
            line = lines[i]
            if is_noise(line):
                continue
            for pattern, ftype in failure_patterns:
                if re.search(pattern, line, re.IGNORECASE):
                    failure_idx = i
                    failure_type = ftype
                    break
            if failure_idx is not None:
                break

        if failure_idx is None:
            # Fallback: find any "Error" or "FAILED"
            for i in range(len(lines) - 1, -1, -1):
                if 'Error' in lines[i] or 'FAILED' in lines[i]:
                    if not is_noise(lines[i]):
                        failure_idx = i
                        break

        if failure_idx is None:
            failure_idx = len(lines) - 1

        # Expand backwards from failure, filtering noise
        context_lines = 50
        start_idx = max(0, failure_idx - context_lines)

        # Build excerpt expanding backwards from failure (don't include lines after)
        excerpt_lines = []
        for i in range(start_idx, failure_idx + 1):
            line = lines[i]
            if not is_noise(line):
                # Clean the line (remove timing markers, etc.)
                cleaned_line = clean_line(line)
                # Add line number prefix for Buildkite linking
                line_num = i + 1
                excerpt_lines.append(f"{line_num}\t{cleaned_line}")

        result = '\n'.join(excerpt_lines)

        # Add Buildkite link hint at the top if we have the URL
        if job_web_url and failure_idx is not None:
            # Insert ?line= before the # anchor if present
            if '#' in job_web_url:
                base, anchor = job_web_url.split('#', 1)
                bk_line_url = f"{base}?line={failure_idx + 1}#{anchor}"
            else:
                bk_line_url = f"{job_web_url}?line={failure_idx + 1}"
            result = f"[View in Buildkite at line {failure_idx + 1}]({bk_line_url})\n\n{result}"

        # Truncate if still too long (from the start, keeping the failure)
        if len(result) > max_size:
            result = '... (earlier lines truncated)\n' + result[-max_size:]

        return result

    def _extract_failure_specific_log(self, log_content: str, failing_test: str | None,
                                       error_message: str | None, job_web_url: str = "",
                                       max_size: int = 8000) -> str:
        """Extract log excerpt specific to a particular failure.

        For wrapper errors like 'Engine core initialization failed', searches for the
        actual root cause error earlier in the log.
        """
        import re
        lines = log_content.split('\n')

        # Wrapper errors that indicate we should look for the real cause
        wrapper_indicators = [
            'Engine core initialization failed',
            'Server exited unexpectedly',
            'See root cause above',
        ]

        # Root cause errors that should trigger special log extraction
        root_cause_indicators = [
            'No available memory',
            'Free memory',
            'OutOfMemoryError',
            'CUDA out of memory',
            'MemoryError',
            'Cannot allocate memory',
            'NCCL error',
            'not supported in:',  # ROCm unsupported feature
            'currently not supported',
        ]

        is_wrapper_error = error_message and any(w in error_message for w in wrapper_indicators)
        is_root_cause_error = error_message and any(w in error_message for w in root_cause_indicators)

        def clean_line(line: str) -> str:
            line = re.sub(r'_bk;t=\d+\s*', '', line)
            line = re.sub(r'^\s*\[[0-9T\-:Z]+\]\s*', '', line)
            return line

        def is_noise(line: str) -> bool:
            noise_patterns = [
                r'Pulling fs layer', r'Waiting\s*$', r'Downloading\s*$',
                r'Extracting\s*$', r'Pull complete', r'Already exists',
                r'DeprecationWarning:', r'FutureWarning:', r'warnings summary',
            ]
            return any(re.search(p, line) for p in noise_patterns)

        def format_excerpt(start_idx: int, end_idx: int, focus_line: int) -> str:
            excerpt_lines = []
            for j in range(start_idx, end_idx):
                if j < 0 or j >= len(lines):
                    continue
                if not is_noise(lines[j]):
                    cleaned = clean_line(lines[j])
                    excerpt_lines.append(f"{j+1}\t{cleaned}")
            result = '\n'.join(excerpt_lines)
            if job_web_url:
                if '#' in job_web_url:
                    base, anchor = job_web_url.split('#', 1)
                    bk_line_url = f"{base}?line={focus_line+1}#{anchor}"
                else:
                    bk_line_url = f"{job_web_url}?line={focus_line+1}"
                result = f"[View in Buildkite at line {focus_line+1}]({bk_line_url})\n\n{result}"
            return result[:max_size]

        # If it's a wrapper error OR a root cause error, find the relevant log section
        if is_wrapper_error or is_root_cause_error:
            root_cause_patterns = [
                r'ValueError:\s*(?:Free memory|No available memory)',
                r'ValueError:.*not supported in:',  # ROCm unsupported feature
                r'ValueError:.*currently not supported',
                r'OutOfMemoryError',
                r'CUDA out of memory',
                r'MemoryError',
                r'OSError:.*Cannot allocate memory',
                r'NCCL error',
                r'RuntimeError:.*CUDA',
            ]

            for i, line in enumerate(lines):
                for pattern in root_cause_patterns:
                    if re.search(pattern, line, re.IGNORECASE):
                        return format_excerpt(max(0, i - 10), min(len(lines), i + 30), i)

        # For assertion/test errors, find the actual pytest traceback (not the summary)
        if failing_test:
            test_name = failing_test.split('::')[-1].split('[')[0] if '::' in failing_test else failing_test
            test_file = failing_test.split('::')[0].split('/')[-1] if '::' in failing_test else ''

            # First, look for the actual pytest traceback with 'E ' prefix (assertion details)
            # These lines show the actual assertion failure with values
            for i in range(len(lines) - 1, -1, -1):
                line = lines[i]
                # Look for pytest 'E' lines showing assertion details
                if re.search(r'^\s*E\s+assert', line) or re.search(r'^\s*E\s+AssertionError', line):
                    # Found assertion line - expand around it to get full context
                    return format_excerpt(max(0, i - 20), min(len(lines), i + 15), i)

            # Second, look for traceback showing '>  assert' or the actual code line
            for i in range(len(lines) - 1, -1, -1):
                line = lines[i]
                if re.search(r'>\s*assert\s', line):
                    return format_excerpt(max(0, i - 15), min(len(lines), i + 20), i)

            # Third, look for the test function output (before warnings summary)
            for i in range(len(lines) - 1, -1, -1):
                line = lines[i]
                if (test_name in line or test_file in line):
                    # Check if this is the traceback section (not summary)
                    if 'FAILED' not in line and ('Error' in line or 'assert' in line.lower()):
                        return format_excerpt(max(0, i - 10), min(len(lines), i + 25), i)

            # Finally, find FAILED summary but look BEFORE the warnings summary for the traceback
            for i in range(len(lines) - 1, -1, -1):
                line = lines[i]
                if (test_name in line or test_file in line) and 'FAILED' in line:
                    # This is the summary line - find the traceback BEFORE warnings summary
                    # Search backwards from this point to find 'warnings summary' and go before it
                    for j in range(i - 1, max(0, i - 200), -1):
                        if 'warnings summary' in lines[j] or '=====' in lines[j]:
                            # Found warnings section - look for traceback before it
                            for k in range(j - 1, max(0, j - 100), -1):
                                if test_name in lines[k] or 'AssertionError' in lines[k]:
                                    return format_excerpt(max(0, k - 15), min(len(lines), k + 20), k)
                            break
                    # Fallback to showing around the FAILED line
                    return format_excerpt(max(0, i - 5), min(len(lines), i + 10), i)

        # Fallback to generic extraction
        return self._extract_smart_log_excerpt(log_content, job_web_url, max_size)

    def _find_root_cause_error(self, log_content: str, error_message: str | None) -> str | None:
        """For wrapper errors, find the actual root cause in the log."""
        import re

        wrapper_indicators = [
            'Engine core initialization failed',
            'Server exited unexpectedly',
            'See root cause above',
        ]

        if not error_message or not any(w in error_message for w in wrapper_indicators):
            return None  # Not a wrapper error

        lines = log_content.split('\n')

        # Patterns for actual root causes (in priority order)
        root_cause_patterns = [
            r'ValueError:\s*(Free memory|No available memory)[^.]+',
            r'OutOfMemoryError[^.]+',
            r'CUDA out of memory[^.]+',
            r'OSError:.*Cannot allocate memory',
            r'MemoryError[^.]+',
            r'NCCL error[^.]+',
            r'RuntimeError:.*CUDA[^.]+',
            r'ConnectionError[^.]+',
            r'TimeoutError[^.]+',
        ]

        for line in lines:
            for pattern in root_cause_patterns:
                match = re.search(pattern, line, re.IGNORECASE)
                if match:
                    return match.group(0).strip()[:300]

        return None

    async def _triage_job(self, job: Job, build_number: int):
        logger.info(f"Triaging job {job.name} ({job.buildkite_job_id})")

        log_content = await self.buildkite.get_job_log(job.buildkite_job_id, build_number)
        if not log_content:
            logger.warning(f"No log content for job {job.buildkite_job_id}")
            return

        # analyze_failure_with_claude now returns an array of analyses
        analyses = await analyze_failure_with_claude(log_content)

        # Create a Failure record for each distinct failure analysis
        for analysis in analyses:
            # Handle failing_test - serialize list to JSON string if needed
            failing_test = analysis.get("failing_test")
            failing_test_str = failing_test
            if isinstance(failing_test, list):
                import json
                failing_test_str = json.dumps(failing_test)
                # Use first test for log extraction
                failing_test = failing_test[0] if failing_test else None

            # Get the error message from Claude
            original_error_message = analysis.get("error_message")
            error_message = original_error_message

            # For wrapper errors, find the actual root cause
            root_cause_error = self._find_root_cause_error(log_content, error_message)
            if root_cause_error:
                error_message = root_cause_error

            # Extract failure-specific log excerpt
            # Pass original error_message so wrapper error detection works
            log_excerpt = self._extract_failure_specific_log(
                log_content, failing_test, original_error_message, job.web_url or ""
            )

            # Determine is_flaky from historical retry success, not Claude
            error_signature = analysis.get("error_signature")
            is_flaky = False
            if error_signature:
                is_flaky = await self.pattern_matcher.is_signature_flaky(error_signature)

            failure = Failure(
                job_id=job.id,
                failure_category=analysis.get("category"),
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

        await self.session.flush()
        logger.info(f"Triaged job {job.name}: {len(analyses)} failure(s)")

    async def get_build_with_details(self, build_number: int) -> Build | None:
        stmt = (
            select(Build)
            .where(Build.buildkite_build_number == build_number)
            .options(
                selectinload(Build.jobs).selectinload(Job.failure)
            )
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def retriage_job(self, job_id: int) -> Failure | None:
        stmt = select(Job).where(Job.id == job_id).options(selectinload(Job.build))
        result = await self.session.execute(stmt)
        job = result.scalar_one_or_none()

        if not job:
            return None

        # Delete existing failures for this job
        for failure in job.failures:
            await self.session.delete(failure)
        await self.session.flush()

        await self._triage_job(job, job.build.buildkite_build_number)
        return job.failures[0] if job.failures else None
