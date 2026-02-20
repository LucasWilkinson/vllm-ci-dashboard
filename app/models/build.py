from datetime import datetime
from sqlalchemy import String, Integer, DateTime, ForeignKey, Boolean, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class Build(Base):
    __tablename__ = "builds"

    id: Mapped[int] = mapped_column(primary_key=True)
    buildkite_build_number: Mapped[int] = mapped_column(Integer, nullable=False, unique=True)
    build_type: Mapped[str | None] = mapped_column(String(50))
    state: Mapped[str | None] = mapped_column(String(50))
    commit_sha: Mapped[str | None] = mapped_column(String(40))
    branch: Mapped[str | None] = mapped_column(String(255))
    message: Mapped[str | None] = mapped_column(String(500))
    web_url: Mapped[str | None] = mapped_column(String(500))
    triage_status: Mapped[str] = mapped_column(String(50), default="pending")
    created_at: Mapped[datetime | None] = mapped_column(DateTime)
    buildkite_build_id: Mapped[str | None] = mapped_column(String(100))
    synced_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    jobs: Mapped[list["Job"]] = relationship("Job", back_populates="build", cascade="all, delete-orphan")


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[int] = mapped_column(primary_key=True)
    build_id: Mapped[int] = mapped_column(ForeignKey("builds.id"))
    buildkite_job_id: Mapped[str] = mapped_column(String(100), unique=True)
    name: Mapped[str | None] = mapped_column(String(500))
    state: Mapped[str | None] = mapped_column(String(50))
    step_key: Mapped[str | None] = mapped_column(String(255))
    web_url: Mapped[str | None] = mapped_column(String(500))
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    command: Mapped[str | None] = mapped_column(Text)
    soft_failed: Mapped[bool] = mapped_column(Boolean, default=False)
    retry_pending: Mapped[bool] = mapped_column(Boolean, default=False)
    exit_status: Mapped[int | None] = mapped_column(Integer)
    signal: Mapped[str | None] = mapped_column(String(100))
    signal_reason: Mapped[str | None] = mapped_column(String(100))

    build: Mapped["Build"] = relationship("Build", back_populates="jobs")
    # One job can have multiple distinct failures (different test failures with different root causes)
    failures: Mapped[list["Failure"]] = relationship(
        "Failure", back_populates="job", cascade="all, delete-orphan"
    )
