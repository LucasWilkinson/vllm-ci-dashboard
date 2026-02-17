import logging
from datetime import datetime
from difflib import SequenceMatcher

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import ErrorSignature, GitHubIssue, Failure, Job, Build
from app.models.github import FailureIssueLink

logger = logging.getLogger(__name__)


class PatternMatcher:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def find_similar_failures(
        self, failure: Failure, min_issue_created_at: datetime | None = None, limit: int = 5
    ) -> list[dict]:
        suggestions = []

        if failure.error_signature:
            exact_matches = await self._find_by_signature(failure.error_signature, min_issue_created_at)
            for match in exact_matches:
                if match["github_issue"] and match["github_issue"] not in [
                    s["github_issue"] for s in suggestions
                ]:
                    suggestions.append({
                        **match,
                        "similarity_score": 1.0,
                        "match_reason": "Exact signature match",
                    })

        if failure.error_message and len(suggestions) < limit:
            fuzzy_matches = await self._find_by_error_message(
                failure.error_message, min_issue_created_at, limit - len(suggestions)
            )
            for match in fuzzy_matches:
                if match["github_issue"] and match["github_issue"] not in [
                    s["github_issue"] for s in suggestions
                ]:
                    suggestions.append(match)

        return suggestions[:limit]

    async def _find_by_signature(self, signature: str, min_issue_created_at: datetime | None = None) -> list[dict]:
        stmt = (
            select(ErrorSignature)
            .where(ErrorSignature.signature_hash == signature)
            .where(ErrorSignature.associated_issue_id.isnot(None))
            .options(selectinload(ErrorSignature.associated_issue))
        )
        result = await self.session.execute(stmt)
        error_sigs = result.scalars().all()

        matches = []
        for error_sig in error_sigs:
            if error_sig.associated_issue:
                # Filter by issue creation date if specified
                if min_issue_created_at and error_sig.associated_issue.created_at:
                    if error_sig.associated_issue.created_at < min_issue_created_at:
                        continue
                matches.append({
                    "github_issue": error_sig.associated_issue,
                    "occurrence_count": error_sig.occurrence_count,
                })
        return matches

    async def _find_by_error_message(
        self, error_message: str, min_issue_created_at: datetime | None = None, limit: int = 5
    ) -> list[dict]:
        stmt = (
            select(Failure)
            .where(Failure.error_message.isnot(None))
            .options(selectinload(Failure.issue_links).selectinload(FailureIssueLink.github_issue))
            .limit(100)
        )
        result = await self.session.execute(stmt)
        historical_failures = result.scalars().all()

        scored_matches = []
        for hist_failure in historical_failures:
            if not hist_failure.error_message:
                continue

            similarity = SequenceMatcher(
                None, error_message.lower(), hist_failure.error_message.lower()
            ).ratio()

            if similarity > 0.6:
                for link in hist_failure.issue_links:
                    # Filter by issue creation date if specified
                    if min_issue_created_at and link.github_issue and link.github_issue.created_at:
                        if link.github_issue.created_at < min_issue_created_at:
                            continue
                    scored_matches.append({
                        "github_issue": link.github_issue,
                        "similarity_score": similarity,
                        "match_reason": f"Similar error message ({similarity:.0%} match)",
                    })

        scored_matches.sort(key=lambda x: x["similarity_score"], reverse=True)
        return scored_matches[:limit]

    async def record_signature(
        self, signature: str, github_issue_id: int | None = None
    ):
        stmt = select(ErrorSignature).where(ErrorSignature.signature_hash == signature)
        result = await self.session.execute(stmt)
        error_sig = result.scalar_one_or_none()

        if error_sig:
            error_sig.occurrence_count += 1
            if github_issue_id:
                error_sig.associated_issue_id = github_issue_id
        else:
            error_sig = ErrorSignature(
                signature_hash=signature,
                occurrence_count=1,
                associated_issue_id=github_issue_id,
            )
            self.session.add(error_sig)

        await self.session.flush()
        return error_sig

    async def record_retry_success(self, signature: str):
        """Record that a failure with this signature succeeded after retry."""
        stmt = select(ErrorSignature).where(ErrorSignature.signature_hash == signature)
        result = await self.session.execute(stmt)
        error_sig = result.scalar_one_or_none()

        if error_sig:
            error_sig.retry_success_count += 1
            await self.session.flush()
            logger.info(f"Recorded retry success for {signature}: {error_sig.retry_success_count}/{error_sig.occurrence_count}")

    async def is_signature_flaky(self, signature: str) -> bool:
        """Check if a signature is considered flaky based on retry success history."""
        stmt = select(ErrorSignature).where(ErrorSignature.signature_hash == signature)
        result = await self.session.execute(stmt)
        error_sig = result.scalar_one_or_none()

        if not error_sig:
            return False

        return error_sig.is_flaky

    async def _find_from_previous_builds(
        self, failure: Failure, build: Build, limit: int = 5
    ) -> list[dict]:
        """Find issues linked to similar failures in previous builds of the same type."""
        if not build.build_type:
            return []

        # Get previous builds of the same type, ordered by build number descending
        stmt = (
            select(Build)
            .where(Build.build_type == build.build_type)
            .where(Build.buildkite_build_number < build.buildkite_build_number)
            .order_by(Build.buildkite_build_number.desc())
            .limit(10)  # Look at last 10 builds of same type
            .options(
                selectinload(Build.jobs)
                .selectinload(Job.failures)
                .selectinload(Failure.issue_links)
                .selectinload(FailureIssueLink.github_issue)
            )
        )
        result = await self.session.execute(stmt)
        previous_builds = result.scalars().all()

        suggestions = []
        job_name = failure.job.name if failure.job else None

        for prev_build in previous_builds:
            for job in prev_build.jobs:
                if job.state != "failed" or not job.failures:
                    continue

                # Check each failure in the job
                for prev_failure in job.failures:
                    # Check if same job failed before
                    same_job = job.name == job_name if job_name else False

                    # Check if similar error signature
                    same_signature = (
                        failure.error_signature and
                        prev_failure.error_signature == failure.error_signature
                    )

                    # Check if similar error message
                    similar_message = False
                    if failure.error_message and prev_failure.error_message:
                        similarity = SequenceMatcher(
                            None,
                            failure.error_message.lower(),
                            prev_failure.error_message.lower()
                        ).ratio()
                        similar_message = similarity > 0.7

                    if same_job or same_signature or similar_message:
                        # This failure had a similar issue - get its linked issues
                        for link in prev_failure.issue_links:
                            if link.github_issue:
                                issue = link.github_issue
                                if issue not in [s["github_issue"] for s in suggestions]:
                                    reason = []
                                    if same_job:
                                        reason.append(f"same job failed in build #{prev_build.buildkite_build_number}")
                                    if same_signature:
                                        reason.append("same error signature")
                                    if similar_message:
                                        reason.append("similar error message")

                                    suggestions.append({
                                        "github_issue": issue,
                                        "similarity_score": 0.95 if same_signature else 0.85,
                                        "match_reason": f"Previous {build.build_type}: " + ", ".join(reason),
                                    })

            if len(suggestions) >= limit:
                break

        return suggestions[:limit]

    async def get_suggestions_for_failure(self, failure_id: int) -> list[dict]:
        # Load failure with job and build to get build's created_at
        stmt = (
            select(Failure)
            .where(Failure.id == failure_id)
            .options(selectinload(Failure.job).selectinload(Job.build))
        )
        result = await self.session.execute(stmt)
        failure = result.scalar_one_or_none()

        if not failure:
            return []

        suggestions = []
        seen_issues = set()

        # First, check previous builds of the same type for linked issues
        if failure.job and failure.job.build:
            prev_build_matches = await self._find_from_previous_builds(
                failure, failure.job.build
            )
            for match in prev_build_matches:
                issue = match["github_issue"]
                if issue.github_issue_number not in seen_issues:
                    seen_issues.add(issue.github_issue_number)
                    suggestions.append({
                        "github_issue_number": issue.github_issue_number,
                        "title": issue.title,
                        "state": issue.state,
                        "github_issue_url": issue.github_issue_url,
                        "similarity_score": match["similarity_score"],
                        "match_reason": match["match_reason"],
                    })

        # Then look for signature/message matches
        min_issue_created_at = None
        if failure.job and failure.job.build and failure.job.build.created_at:
            min_issue_created_at = failure.job.build.created_at

        similar = await self.find_similar_failures(failure, min_issue_created_at)

        for match in similar:
            issue = match["github_issue"]
            if issue.github_issue_number not in seen_issues:
                seen_issues.add(issue.github_issue_number)
                suggestions.append({
                    "github_issue_number": issue.github_issue_number,
                    "title": issue.title,
                    "state": issue.state,
                    "github_issue_url": issue.github_issue_url,
                    "similarity_score": match["similarity_score"],
                    "match_reason": match["match_reason"],
                })

        return suggestions[:5]
