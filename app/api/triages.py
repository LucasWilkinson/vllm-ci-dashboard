from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.models import Failure
from app.schemas.failure import FailureResponse, FailureSuggestion
from app.services.pattern_matcher import PatternMatcher
from app.services.triage import TriageService

router = APIRouter()


class FailureUpdate(BaseModel):
    failure_category: str | None = None
    failure_type: str | None = None
    is_flaky: bool | None = None


class ResolvedByPRRequest(BaseModel):
    pr_number: int


@router.get("/failures/{failure_id}", response_model=FailureResponse)
async def get_failure(failure_id: int, db: AsyncSession = Depends(get_db)):
    stmt = (
        select(Failure)
        .where(Failure.id == failure_id)
        .options(selectinload(Failure.issue_links))
    )
    result = await db.execute(stmt)
    failure = result.scalar_one_or_none()

    if not failure:
        raise HTTPException(status_code=404, detail="Failure not found")

    return failure


@router.get("/failures/{failure_id}/suggestions", response_model=list[FailureSuggestion])
async def get_failure_suggestions(failure_id: int, db: AsyncSession = Depends(get_db)):
    pattern_matcher = PatternMatcher(db)
    suggestions = await pattern_matcher.get_suggestions_for_failure(failure_id)
    return suggestions


@router.patch("/failures/{failure_id}", response_model=FailureResponse)
async def update_failure(
    failure_id: int,
    update: FailureUpdate,
    db: AsyncSession = Depends(get_db),
):
    """Update failure category, type, or flaky status."""
    stmt = select(Failure).where(Failure.id == failure_id)
    result = await db.execute(stmt)
    failure = result.scalar_one_or_none()

    if not failure:
        raise HTTPException(status_code=404, detail="Failure not found")

    if update.failure_category is not None:
        failure.failure_category = update.failure_category
    if update.failure_type is not None:
        failure.failure_type = update.failure_type
    if update.is_flaky is not None:
        failure.is_flaky = update.is_flaky

    await db.commit()
    await db.refresh(failure)

    return failure


@router.post("/failures/{failure_id}/retriage", response_model=FailureResponse)
async def retriage_failure(failure_id: int, db: AsyncSession = Depends(get_db)):
    stmt = select(Failure).where(Failure.id == failure_id)
    result = await db.execute(stmt)
    failure = result.scalar_one_or_none()

    if not failure:
        raise HTTPException(status_code=404, detail="Failure not found")

    triage = TriageService(db)
    new_failure = await triage.retriage_job(failure.job_id)
    await db.commit()

    if not new_failure:
        raise HTTPException(status_code=500, detail="Failed to retriage")

    return new_failure


@router.post("/failures/{failure_id}/resolved-by-pr")
async def mark_resolved_by_pr(
    failure_id: int,
    request: ResolvedByPRRequest,
    db: AsyncSession = Depends(get_db),
):
    """Mark a failure as resolved by a PR."""
    stmt = select(Failure).where(Failure.id == failure_id)
    result = await db.execute(stmt)
    failure = result.scalar_one_or_none()

    if not failure:
        raise HTTPException(status_code=404, detail="Failure not found")

    failure.resolved_by_pr = request.pr_number
    await db.commit()

    return {
        "message": f"Marked failure as resolved by PR #{request.pr_number}",
        "failure_id": failure_id,
        "resolved_by_pr": request.pr_number,
    }


@router.delete("/failures/{failure_id}/resolved-by-pr")
async def unmark_resolved_by_pr(
    failure_id: int,
    db: AsyncSession = Depends(get_db),
):
    """Remove resolved by PR marking from a failure."""
    stmt = select(Failure).where(Failure.id == failure_id)
    result = await db.execute(stmt)
    failure = result.scalar_one_or_none()

    if not failure:
        raise HTTPException(status_code=404, detail="Failure not found")

    failure.resolved_by_pr = None
    await db.commit()

    return {"message": "Removed resolved by PR marking", "failure_id": failure_id}
