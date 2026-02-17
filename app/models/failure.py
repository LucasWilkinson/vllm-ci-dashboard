from sqlalchemy import String, Integer, Text, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class Failure(Base):
    __tablename__ = "failures"

    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id"))  # Multiple failures per job allowed
    failure_category: Mapped[str | None] = mapped_column(String(50))
    failure_type: Mapped[str | None] = mapped_column(String(100))
    failing_test: Mapped[str | None] = mapped_column(String(500))
    error_signature: Mapped[str | None] = mapped_column(String(255))
    error_message: Mapped[str | None] = mapped_column(Text)
    root_cause: Mapped[str | None] = mapped_column(Text)
    is_flaky: Mapped[bool | None] = mapped_column(default=False)
    log_excerpt: Mapped[str | None] = mapped_column(Text)
    resolved_by_pr: Mapped[int | None] = mapped_column(Integer)

    job: Mapped["Job"] = relationship("Job", back_populates="failures")
    issue_links: Mapped[list["FailureIssueLink"]] = relationship(
        "FailureIssueLink", back_populates="failure", cascade="all, delete-orphan"
    )


class ErrorSignature(Base):
    __tablename__ = "error_signatures"

    id: Mapped[int] = mapped_column(primary_key=True)
    signature_hash: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    occurrence_count: Mapped[int] = mapped_column(Integer, default=1)
    retry_success_count: Mapped[int] = mapped_column(Integer, default=0)
    associated_issue_id: Mapped[int | None] = mapped_column(ForeignKey("github_issues.id"))

    associated_issue: Mapped["GitHubIssue | None"] = relationship("GitHubIssue")

    @property
    def is_flaky(self) -> bool:
        """Consider flaky if >30% of occurrences succeeded after retry."""
        if self.occurrence_count < 2:
            return False
        return self.retry_success_count / self.occurrence_count >= 0.3
