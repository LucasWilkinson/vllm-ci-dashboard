from datetime import datetime

from sqlalchemy import String, Integer, ForeignKey, DateTime
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class GitHubIssue(Base):
    __tablename__ = "github_issues"

    id: Mapped[int] = mapped_column(primary_key=True)
    github_issue_number: Mapped[int] = mapped_column(Integer, unique=True)
    title: Mapped[str | None] = mapped_column(String(500))
    state: Mapped[str | None] = mapped_column(String(50))
    github_issue_url: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime | None] = mapped_column(DateTime)

    failure_links: Mapped[list["FailureIssueLink"]] = relationship(
        "FailureIssueLink", back_populates="github_issue"
    )


class FailureIssueLink(Base):
    __tablename__ = "failure_issue_links"

    id: Mapped[int] = mapped_column(primary_key=True)
    failure_id: Mapped[int] = mapped_column(ForeignKey("failures.id"))
    github_issue_id: Mapped[int] = mapped_column(ForeignKey("github_issues.id"))
    link_type: Mapped[str | None] = mapped_column(String(50))

    failure: Mapped["Failure"] = relationship("Failure", back_populates="issue_links")
    github_issue: Mapped["GitHubIssue"] = relationship("GitHubIssue", back_populates="failure_links")
