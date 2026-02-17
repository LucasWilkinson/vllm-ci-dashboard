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
    failure: FailureResponse | None = None


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


class BuildSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    buildkite_build_number: int
    build_type: str | None = None
    state: str | None = None
    branch: str | None = None
    message: str | None = None
    web_url: str | None = None
    triage_status: str
    created_at: datetime | None = None
    total_jobs: int = 0
    failed_jobs: int = 0
    infra_failures: int = 0
    test_failures: int = 0


class DashboardSummary(BaseModel):
    total_builds: int
    pending_triages: int
    completed_triages: int
    infra_failures_today: int
    test_failures_today: int


class FailingBuildInfo(BaseModel):
    """Info about a build where a test failed."""
    build_number: int
    commit_sha: str | None
    build_url: str | None
    job_url: str | None


class CurrentIssue(BaseModel):
    """A test failure that is still broken (hasn't passed on main since failing)."""
    failure_id: int
    job_id: int
    job_name: str
    job_url: str | None = None
    failing_test: str | list[str] | None
    failure_type: str | None
    error_message: str | None
    error_signature: str | None
    log_excerpt: str | None = None
    first_seen_build: int
    last_seen_build: int
    occurrence_count: int
    is_flaky: bool = False
    flaky_rate: float | None = None
    retry_success_count: int | None = None
    signature_occurrence_count: int | None = None
    linked_issue_number: int | None = None
    linked_issue_url: str | None = None
    resolved_by_pr: int | None = None
    failing_builds: list[FailingBuildInfo] = []


class CurrentIssueGroup(BaseModel):
    """A group of current issues with the same root cause error."""
    error_key: str
    error_message: str | None
    failure_type: str | None
    linked_issue_number: int | None = None
    linked_issue_url: str | None = None
    total_affected_tests: int
    first_seen_build: int
    last_seen_build: int
    issues: list[CurrentIssue]


class FailedJobSummary(BaseModel):
    job_id: int
    failure_id: int | None = None
    job_name: str
    step_key: str | None
    job_url: str | None
    failure_category: str | None
    failure_type: str | None
    failing_test: str | list[str] | None = None
    error_signature: str | None = None
    error_message: str | None
    log_excerpt: str | None = None
    is_flaky: bool
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
