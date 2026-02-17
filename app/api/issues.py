from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import Failure, GitHubIssue, FailureIssueLink
from app.schemas.github import (
    GitHubIssueCreate,
    GitHubIssueResponse,
    FailureIssueLinkResponse,
    LinkIssueRequest,
)
from app.services.github import GitHubService
from app.services.pattern_matcher import PatternMatcher

router = APIRouter()


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


@router.post("/failures/{failure_id}/create", response_model=GitHubIssueResponse)
async def create_issue_for_failure(
    failure_id: int,
    issue_data: GitHubIssueCreate,
    db: AsyncSession = Depends(get_db),
):
    stmt = select(Failure).where(Failure.id == failure_id)
    result = await db.execute(stmt)
    failure = result.scalar_one_or_none()

    if not failure:
        raise HTTPException(status_code=404, detail="Failure not found")

    github = GitHubService(db)
    issue_result = await github.create_issue(
        title=issue_data.title,
        body=issue_data.body,
        labels=issue_data.labels,
    )

    stmt = select(GitHubIssue).where(
        GitHubIssue.github_issue_number == issue_result["github_issue_number"]
    )
    result = await db.execute(stmt)
    issue = result.scalar_one()

    link = FailureIssueLink(
        failure_id=failure_id,
        github_issue_id=issue.id,
        link_type="created",
    )
    db.add(link)

    pattern_matcher = PatternMatcher(db)
    if failure.error_signature:
        await pattern_matcher.record_signature(failure.error_signature, issue.id)

    await db.commit()
    return issue


@router.post("/failures/{failure_id}/link", response_model=FailureIssueLinkResponse)
async def link_issue_to_failure(
    failure_id: int,
    link_request: LinkIssueRequest,
    db: AsyncSession = Depends(get_db),
):
    stmt = select(Failure).where(Failure.id == failure_id)
    result = await db.execute(stmt)
    failure = result.scalar_one_or_none()

    if not failure:
        raise HTTPException(status_code=404, detail="Failure not found")

    github = GitHubService(db)
    issue = await github.get_or_create_issue(link_request.github_issue_number)

    existing_link_stmt = select(FailureIssueLink).where(
        FailureIssueLink.failure_id == failure_id,
        FailureIssueLink.github_issue_id == issue.id,
    )
    existing_result = await db.execute(existing_link_stmt)
    if existing_result.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="Link already exists")

    link = FailureIssueLink(
        failure_id=failure_id,
        github_issue_id=issue.id,
        link_type=link_request.link_type,
    )
    db.add(link)

    pattern_matcher = PatternMatcher(db)
    if failure.error_signature:
        await pattern_matcher.record_signature(failure.error_signature, issue.id)

    await db.commit()

    await db.refresh(link)
    link.github_issue = issue

    return link


@router.delete("/failures/{failure_id}/unlink/{issue_number}")
async def unlink_issue_from_failure(
    failure_id: int,
    issue_number: int,
    db: AsyncSession = Depends(get_db),
):
    """Remove the link between a failure and a GitHub issue."""
    stmt = select(FailureIssueLink).join(GitHubIssue).where(
        FailureIssueLink.failure_id == failure_id,
        GitHubIssue.github_issue_number == issue_number,
    )
    result = await db.execute(stmt)
    link = result.scalar_one_or_none()

    if not link:
        raise HTTPException(status_code=404, detail="Link not found")

    await db.delete(link)
    await db.commit()

    return {"message": f"Unlinked issue #{issue_number} from failure"}


@router.post("/sync")
async def sync_github_issues(db: AsyncSession = Depends(get_db)):
    github = GitHubService(db)
    await github.sync_issue_states()
    await db.commit()
    return {"message": "Issue states synced"}
