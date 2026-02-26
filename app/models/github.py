from datetime import datetime

from sqlalchemy import String, Integer, DateTime
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class GitHubIssue(Base):
    __tablename__ = "github_issues"

    id: Mapped[int] = mapped_column(primary_key=True)
    github_issue_number: Mapped[int] = mapped_column(Integer, unique=True)
    title: Mapped[str | None] = mapped_column(String(500))
    state: Mapped[str | None] = mapped_column(String(50))
    github_issue_url: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime | None] = mapped_column(DateTime)
