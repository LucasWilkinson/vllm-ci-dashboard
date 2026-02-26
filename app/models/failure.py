import json
from datetime import datetime

from sqlalchemy import String, Integer, Text, ForeignKey, Boolean, DateTime
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class KnownFailure(Base):
    __tablename__ = "known_failures"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(500))
    summary: Mapped[str | None] = mapped_column(Text)
    match_prompt: Mapped[str | None] = mapped_column(Text)
    category: Mapped[str | None] = mapped_column(String(50))  # infra / test
    status: Mapped[str] = mapped_column(String(50), default="open")  # open / resolved
    is_flaky: Mapped[bool] = mapped_column(Boolean, default=False)
    github_issue_id: Mapped[int | None] = mapped_column(ForeignKey("github_issues.id"))
    resolved_by_pr: Mapped[int | None] = mapped_column(Integer)
    first_seen_build_id: Mapped[int | None] = mapped_column(ForeignKey("builds.id"))
    last_seen_build_id: Mapped[int | None] = mapped_column(ForeignKey("builds.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime)
    resolved_by: Mapped[str | None] = mapped_column(String(50))  # "manual" / "auto" / None
    resolved_in_build_id: Mapped[int | None] = mapped_column(ForeignKey("builds.id"))

    github_issue: Mapped["GitHubIssue | None"] = relationship("GitHubIssue")
    first_seen_build: Mapped["Build | None"] = relationship(
        "Build", foreign_keys=[first_seen_build_id]
    )
    last_seen_build: Mapped["Build | None"] = relationship(
        "Build", foreign_keys=[last_seen_build_id]
    )
    resolved_in_build: Mapped["Build | None"] = relationship(
        "Build", foreign_keys=[resolved_in_build_id]
    )
    failures: Mapped[list["Failure"]] = relationship(
        "Failure", back_populates="known_failure"
    )


class Failure(Base):
    __tablename__ = "failures"

    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id"))  # Multiple failures per job allowed
    known_failure_id: Mapped[int | None] = mapped_column(ForeignKey("known_failures.id"))
    failure_category: Mapped[str | None] = mapped_column(String(50))
    failure_type: Mapped[str | None] = mapped_column(String(100))
    failing_test: Mapped[str | None] = mapped_column(String(500))
    error_message: Mapped[str | None] = mapped_column(Text)
    root_cause: Mapped[str | None] = mapped_column(Text)
    is_flaky: Mapped[bool | None] = mapped_column(default=False)
    retry_passed: Mapped[bool] = mapped_column(Boolean, default=False)
    log_excerpt: Mapped[str | None] = mapped_column(Text)

    job: Mapped["Job"] = relationship("Job", back_populates="failures")
    known_failure: Mapped["KnownFailure | None"] = relationship(
        "KnownFailure", back_populates="failures"
    )


class KnownFailureEvent(Base):
    """Audit log for KnownFailure lifecycle events."""
    __tablename__ = "known_failure_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    known_failure_id: Mapped[int] = mapped_column(Integer, index=True)
    event_type: Mapped[str] = mapped_column(String(50))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    details: Mapped[str | None] = mapped_column(Text)  # JSON blob


def log_kf_event(session, known_failure_id: int, event_type: str, **details):
    """Create a KnownFailureEvent. Call before session.commit()."""
    event = KnownFailureEvent(
        known_failure_id=known_failure_id,
        event_type=event_type,
        details=json.dumps(details) if details else None,
    )
    session.add(event)
