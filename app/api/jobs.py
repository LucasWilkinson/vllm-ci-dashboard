from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.models import Job
from app.schemas.build import JobResponse
from app.services.buildkite import BuildkiteService

router = APIRouter()


@router.get("/{job_id}", response_model=JobResponse)
async def get_job(job_id: int, db: AsyncSession = Depends(get_db)):
    stmt = (
        select(Job)
        .where(Job.id == job_id)
        .options(selectinload(Job.failures))
    )
    result = await db.execute(stmt)
    job = result.scalar_one_or_none()

    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    return job


@router.post("/{job_id}/retry")
async def retry_job(job_id: int, db: AsyncSession = Depends(get_db)):
    stmt = select(Job).where(Job.id == job_id)
    result = await db.execute(stmt)
    job = result.scalar_one_or_none()

    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    buildkite = BuildkiteService()
    success = await buildkite.retry_job(job.buildkite_job_id)

    if success:
        job.retry_count += 1
        await db.commit()
        return {"message": "Job retry initiated", "retry_count": job.retry_count}
    else:
        raise HTTPException(status_code=500, detail="Failed to retry job")


@router.get("/{job_id}/log")
async def get_job_log(job_id: int, db: AsyncSession = Depends(get_db)):
    stmt = select(Job).where(Job.id == job_id).options(selectinload(Job.build))
    result = await db.execute(stmt)
    job = result.scalar_one_or_none()

    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    buildkite = BuildkiteService()
    log_content = await buildkite.get_job_log(
        job.buildkite_job_id,
        job.build.buildkite_build_number
    )

    return {"job_id": job_id, "log": log_content}
