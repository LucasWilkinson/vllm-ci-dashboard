from pydantic import BaseModel, ConfigDict


class GitHubIssueBase(BaseModel):
    github_issue_number: int
    title: str | None = None
    state: str | None = None
    github_issue_url: str | None = None


class GitHubIssueCreate(BaseModel):
    title: str
    body: str
    labels: list[str] = []


class GitHubIssueResponse(GitHubIssueBase):
    model_config = ConfigDict(from_attributes=True)

    id: int


class LinkIssueRequest(BaseModel):
    github_issue_number: int
    link_type: str = "associated"
