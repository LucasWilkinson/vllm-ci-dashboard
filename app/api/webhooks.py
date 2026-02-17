import hashlib
import hmac
import logging
from fastapi import APIRouter, Request, HTTPException, BackgroundTasks

from app.config import settings
from app.database import get_db_session
from app.services.buildkite import BuildkiteService
from app.services.triage import TriageService

logger = logging.getLogger(__name__)
router = APIRouter()


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
            buildkite = BuildkiteService()
            triage = TriageService(db)

            builds_data = await buildkite.get_builds(branch="main", limit=5)
            synced = 0
            triaged = 0

            for build_data in builds_data:
                build, is_new = await triage.sync_build(build_data)
                if is_new:
                    synced += 1
                    triaged_count = await triage.triage_failed_jobs(build)
                    triaged += triaged_count

            await db.commit()
            logger.info(f"GitHub webhook: synced {synced} builds, triaged {triaged} jobs")
        except Exception as e:
            logger.error(f"GitHub webhook sync error: {e}")
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
    if event_type != "push":
        return {"status": "ignored", "event": event_type}

    import json
    data = json.loads(payload)
    ref = data.get("ref", "")

    if ref not in ("refs/heads/main", "refs/heads/master"):
        return {"status": "ignored", "reason": f"not main branch: {ref}"}

    commit_sha = data.get("after", "")[:7]
    logger.info(f"GitHub push to main: {commit_sha}")

    background_tasks.add_task(sync_latest_builds)

    return {
        "status": "accepted",
        "event": "push",
        "branch": "main",
        "commit": commit_sha,
    }
