from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import Failure, GitHubIssue, KnownFailure
from app.schemas.github import GitHubIssueResponse
from app.services.github import GitHubService

router = APIRouter()


class CreateIssueRequest(BaseModel):
    title: str
    body: str
    labels: list[str] = []


@router.post("/failures/{failure_id}/create", response_model=GitHubIssueResponse)
async def create_issue_for_failure(
    failure_id: int,
    request: CreateIssueRequest,
    db: AsyncSession = Depends(get_db),
):
    """Create a GitHub issue and link it to the failure's KnownFailure if one exists."""
    failure = await db.get(Failure, failure_id)
    if not failure:
        raise HTTPException(status_code=404, detail="Failure not found")

    github = GitHubService(db)
    issue_data = await github.create_issue(request.title, request.body, request.labels)
    await db.flush()

    # Auto-link to KnownFailure if the failure has one
    if failure.known_failure_id:
        kf = await db.get(KnownFailure, failure.known_failure_id)
        if kf and not kf.github_issue_id:
            stmt = select(GitHubIssue).where(
                GitHubIssue.github_issue_number == issue_data["github_issue_number"]
            )
            result = await db.execute(stmt)
            gh_issue = result.scalar_one_or_none()
            if gh_issue:
                kf.github_issue_id = gh_issue.id

    await db.commit()
    return issue_data


@router.get("", response_model=list[GitHubIssueResponse])
async def list_issues(
    state: str | None = None,
    limit: int = 50,
    db: AsyncSession = Depends(get_db),
):
    stmt = select(GitHubIssue)
    if state:
        stmt = stmt.where(GitHubIssue.state == state)
    stmt = stmt.order_by(GitHubIssue.github_issue_number.desc()).limit(limit)

    result = await db.execute(stmt)
    return result.scalars().all()


@router.get("/{issue_number}", response_model=GitHubIssueResponse)
async def get_issue(issue_number: int, db: AsyncSession = Depends(get_db)):
    stmt = select(GitHubIssue).where(GitHubIssue.github_issue_number == issue_number)
    result = await db.execute(stmt)
    issue = result.scalar_one_or_none()

    if not issue:
        raise HTTPException(status_code=404, detail="Issue not found in database")

    return issue


@router.post("/sync")
async def sync_github_issues(db: AsyncSession = Depends(get_db)):
    github = GitHubService(db)
    await github.sync_issue_states()
    await db.commit()
    return {"message": "Issue states synced"}
