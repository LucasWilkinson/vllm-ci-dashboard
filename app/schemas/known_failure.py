from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.schemas.github import GitHubIssueResponse


class KnownFailureInstance(BaseModel):
    """A single failure instance within a KnownFailure."""
    failure_id: int
    job_id: int
    job_name: str
    job_url: str | None = None
    failing_test: str | list[str] | None = None
    error_message: str | None = None
    log_excerpt: str | None = None


class FailuresByBuild(BaseModel):
    """Failures grouped by build for display in expanded KnownFailure view."""
    build_number: int
    build_url: str | None = None
    commit_sha: str | None = None
    committed_at: datetime | None = None  # git commit time
    created_at: datetime | None = None    # build/triage time
    commits_behind: int = 0
    failures: list[KnownFailureInstance] = []


class BuildRef(BaseModel):
    """Lightweight build reference for KnownFailure first/last seen."""
    build_number: int
    commit_sha: str | None = None
    committed_at: datetime | None = None  # git commit time
    created_at: datetime | None = None
    message: str | None = None


class KnownFailureResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    summary: str | None = None
    match_prompt: str | None = None
    category: str | None = None
    status: str = "open"
    is_flaky: bool = False
    github_issue: GitHubIssueResponse | None = None
    resolved_by_pr: int | None = None
    resolved_by: str | None = None
    resolved_in_build: BuildRef | None = None
    first_seen_build: BuildRef | None = None
    last_seen_build: BuildRef | None = None
    failure_count: int = 0
    affected_jobs: list[str] = []
    failures_by_build: list[FailuresByBuild] = []


class BuildInHistory(BaseModel):
    """A single build within a commit-level history entry."""
    build_number: int
    build_url: str | None = None
    build_type: str | None = None  # "nightly", "daily", or None for normal builds
    status: str  # "not_run", "infra_fail", "other_fail", "fail", "pass", "flaky_pass"


class BuildHistoryEntry(BaseModel):
    """A commit's status in the context of a KnownFailure's history.

    Groups all builds for the same commit together.
    """
    commit_sha: str | None = None
    committed_at: datetime | None = None  # git commit time
    created_at: datetime | None = None    # build/triage time (None if not triaged)
    message: str | None = None
    status: str  # Aggregate: worst status across all builds for this commit
    triaged: bool = True  # False for commits filled from GitHub with no DB builds
    builds: list[BuildInHistory] = []
    failures: list[KnownFailureInstance] = []  # Only populated when status == "fail" or "flaky_pass"


class KnownFailureHistory(BaseModel):
    """Complete commit-by-commit history for a KnownFailure."""
    known_failure_id: int
    title: str
    affected_jobs: list[str]
    affected_tests: list[str] = []
    predates_history: bool
    no_prior_runs: bool = False  # True if affected jobs were never run before first failure
    is_flaky: bool = False
    entries: list[BuildHistoryEntry]  # Ordered newest-first


class KnownFailureUpdate(BaseModel):
    title: str | None = None
    summary: str | None = None
    match_prompt: str | None = None
    category: str | None = None
    status: str | None = None
    is_flaky: bool | None = None


class ResolveRequest(BaseModel):
    resolved_by_pr: int | None = None


class ReassignFailureRequest(BaseModel):
    failure_id: int
    target_known_failure_id: int | None = None  # None = create new
    new_title: str | None = None  # Required if target_known_failure_id is None


class MergeKnownFailuresRequest(BaseModel):
    source_id: int
    target_id: int


class SplitFailuresRequest(BaseModel):
    failure_ids: list[int]
    new_title: str


class LinkIssueToKnownFailureRequest(BaseModel):
    github_issue_number: int
