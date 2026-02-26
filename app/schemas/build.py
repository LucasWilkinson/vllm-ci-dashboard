from datetime import datetime
from pydantic import BaseModel, ConfigDict

from app.schemas.failure import FailureResponse


class JobBase(BaseModel):
    buildkite_job_id: str
    name: str | None = None
    state: str | None = None
    step_key: str | None = None
    web_url: str | None = None
    retry_count: int = 0


class JobResponse(JobBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    build_id: int
    failures: list[FailureResponse] = []


class BuildBase(BaseModel):
    buildkite_build_number: int
    build_type: str | None = None
    state: str | None = None
    commit_sha: str | None = None
    branch: str | None = None
    message: str | None = None
    web_url: str | None = None


class BuildCreate(BuildBase):
    pass


class BuildResponse(BuildBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    triage_status: str
    created_at: datetime | None = None
    synced_at: datetime
    jobs: list[JobResponse] = []


class FailedJobSummary(BaseModel):
    job_id: int
    failure_id: int | None = None
    job_name: str
    step_key: str | None
    job_url: str | None
    failure_category: str | None
    failure_type: str | None
    failing_test: str | list[str] | None = None
    error_message: str | None
    log_excerpt: str | None = None
    flaky_status: str | None = None
    known_failure_id: int | None = None
    known_failure_title: str | None = None
    linked_issue_number: int | None = None
    linked_issue_state: str | None = None
    linked_issue_url: str | None = None


class BuildWithFailures(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    buildkite_build_number: int
    build_type: str | None = None
    state: str | None = None
    commit_sha: str | None = None
    branch: str | None = None
    message: str | None = None
    web_url: str | None = None
    triage_status: str
    created_at: datetime | None = None
    total_jobs: int = 0
    failed_jobs: list[FailedJobSummary] = []


class BuildInTimeline(BaseModel):
    build_number: int
    build_type: str | None = None
    state: str | None = None
    web_url: str | None = None
    triage_status: str
    total_jobs: int = 0
    failed_job_count: int = 0
    passed_job_count: int = 0
    not_run_job_count: int = 0


class CommitTimelineEntry(BaseModel):
    commit_sha: str | None = None
    message: str | None = None
    committed_at: datetime | None = None  # git commit time
    created_at: datetime | None = None    # build/triage time (None if not triaged)
    status: str  # worst aggregate state across builds
    builds: list[BuildInTimeline] = []
    failed_jobs: list[FailedJobSummary] = []
