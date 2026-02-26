import hashlib
import hmac
import json
import logging
from datetime import datetime
from fastapi import APIRouter, Request, HTTPException, BackgroundTasks

from app.config import settings
from app.database import get_db_session
from app.services.triage import TriageService

logger = logging.getLogger(__name__)
router = APIRouter()


def verify_buildkite_token(token: str | None, expected: str) -> bool:
    """Verify Buildkite webhook token."""
    if not token or not expected:
        return False
    return hmac.compare_digest(token, expected)


def verify_github_signature(payload: bytes, signature: str | None, secret: str) -> bool:
    """Verify GitHub webhook signature."""
    if not signature or not secret:
        return False
    expected = "sha256=" + hmac.new(
        secret.encode(), payload, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, signature)


async def sync_latest_builds():
    """Background task to sync latest builds after a push to main."""
    async with get_db_session() as db:
        try:
            triage = TriageService(db)
            result = await triage.sync_recent_builds(limit=5)
            logger.info(f"GitHub webhook: {result['message']}")
        except Exception as e:
            logger.error(f"GitHub webhook sync error: {e}")
            await db.rollback()


async def handle_issue_closed(issue_number: int):
    """Background task to resolve KFs linked to a closed GitHub issue."""
    from sqlalchemy import select
    from app.models import GitHubIssue, KnownFailure, log_kf_event

    async with get_db_session() as db:
        try:
            stmt = select(GitHubIssue).where(
                GitHubIssue.github_issue_number == issue_number
            )
            result = await db.execute(stmt)
            issue = result.scalar_one_or_none()
            if not issue:
                return

            issue.state = "closed"

            # Resolve linked open KFs
            kf_stmt = (
                select(KnownFailure)
                .where(KnownFailure.github_issue_id == issue.id)
                .where(KnownFailure.status == "open")
            )
            kf_result = await db.execute(kf_stmt)
            linked_kfs = kf_result.scalars().all()
            for kf in linked_kfs:
                kf.status = "resolved"
                kf.resolved_at = datetime.utcnow()
                kf.resolved_by = "issue_closed"
                log_kf_event(
                    db, kf.id, "issue_closed",
                    github_issue_number=issue_number,
                )
                logger.info(
                    f"Webhook: resolved KF#{kf.id} '{kf.title}' — "
                    f"issue #{issue_number} closed"
                )

            await db.commit()
        except Exception as e:
            logger.error(f"Failed to handle issue closed #{issue_number}: {e}")
            await db.rollback()


async def sync_build_from_webhook(build_number: int):
    """Background task to sync a specific build when Buildkite notifies us."""
    async with get_db_session() as db:
        try:
            triage = TriageService(db)
            build_data = await triage.buildkite.get_build(build_number)
            build = await triage.sync_and_triage_build(build_data)
            await db.commit()
            logger.info(f"Buildkite webhook: synced build #{build_number}, status={build.triage_status}")
        except Exception as e:
            logger.error(f"Buildkite webhook sync error for build #{build_number}: {e}")
            await db.rollback()


@router.post("/github")
async def github_webhook(request: Request, background_tasks: BackgroundTasks):
    """Handle GitHub webhook events.

    Triggers a build sync when there's a push to the main branch.
    """
    payload = await request.body()
    signature = request.headers.get("X-Hub-Signature-256")

    webhook_secret = getattr(settings, 'github_webhook_secret', None)
    if webhook_secret:
        if not verify_github_signature(payload, signature, webhook_secret):
            raise HTTPException(status_code=401, detail="Invalid signature")

    event_type = request.headers.get("X-GitHub-Event")
    data = json.loads(payload)

    if event_type == "issues":
        action = data.get("action")
        issue = data.get("issue", {})
        issue_number = issue.get("number")

        if action == "closed" and issue_number:
            logger.info(f"GitHub issue #{issue_number} closed via webhook")
            background_tasks.add_task(handle_issue_closed, issue_number)
            return {"status": "accepted", "event": "issues", "action": "closed", "issue": issue_number}

        return {"status": "ignored", "event": "issues", "action": action}

    if event_type == "push":
        ref = data.get("ref", "")
        if ref not in ("refs/heads/main", "refs/heads/master"):
            return {"status": "ignored", "reason": f"not main branch: {ref}"}

        commit_sha = data.get("after", "")[:7]
        logger.info(f"GitHub push to main: {commit_sha}")
        background_tasks.add_task(sync_latest_builds)
        return {"status": "accepted", "event": "push", "branch": "main", "commit": commit_sha}

    return {"status": "ignored", "event": event_type}


@router.post("/buildkite")
async def buildkite_webhook(request: Request, background_tasks: BackgroundTasks):
    """Handle Buildkite webhook events.

    Listens for job.finished and build.finished events on main branch builds.
    When a job finishes (including retries and manually unblocked jobs),
    re-syncs the build to update known failure first/last seen tracking.
    """
    # Verify token if configured
    webhook_token = settings.buildkite_webhook_token
    if webhook_token:
        token = request.headers.get("X-Buildkite-Token")
        if not verify_buildkite_token(token, webhook_token):
            raise HTTPException(status_code=401, detail="Invalid token")

    event_type = request.headers.get("X-Buildkite-Event")
    payload = await request.body()
    data = json.loads(payload)

    # Only handle job.finished and build.finished
    if event_type not in ("job.finished", "build.finished"):
        return {"status": "ignored", "event": event_type}

    # Extract build info
    build_data = data.get("build", {})
    build_number = build_data.get("number")
    branch = build_data.get("branch", "")

    if not build_number:
        return {"status": "ignored", "reason": "no build number"}

    # Only process main branch builds
    if branch not in ("main", "master"):
        return {"status": "ignored", "reason": f"not main branch: {branch}"}

    if event_type == "job.finished":
        job_data = data.get("job", {})
        job_name = job_data.get("name", "unknown")
        job_state = job_data.get("state", "unknown")
        logger.info(
            f"Buildkite job.finished: build #{build_number}, "
            f"job={job_name}, state={job_state}"
        )
    else:
        build_state = build_data.get("state", "unknown")
        logger.info(f"Buildkite build.finished: #{build_number}, state={build_state}")

    # Sync the build in background
    background_tasks.add_task(sync_build_from_webhook, build_number)

    return {
        "status": "accepted",
        "event": event_type,
        "build_number": build_number,
        "branch": branch,
    }
