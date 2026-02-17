import json
from pydantic import BaseModel, ConfigDict, field_validator


class FailureBase(BaseModel):
    failure_category: str | None = None
    failure_type: str | None = None
    failing_test: str | list[str] | None = None
    error_signature: str | None = None
    error_message: str | None = None
    root_cause: str | None = None
    is_flaky: bool | None = False


class FailureResponse(FailureBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    job_id: int
    log_excerpt: str | None = None

    @field_validator("failing_test", mode="before")
    @classmethod
    def parse_failing_test(cls, v):
        """Deserialize JSON list if stored as string."""
        if isinstance(v, str) and v.startswith("["):
            try:
                return json.loads(v)
            except json.JSONDecodeError:
                return v
        return v


class FailureSuggestion(BaseModel):
    github_issue_number: int
    title: str
    state: str
    github_issue_url: str
    similarity_score: float
    match_reason: str
