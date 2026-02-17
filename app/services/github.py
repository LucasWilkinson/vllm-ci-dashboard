import asyncio
import json
import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import GitHubIssue

logger = logging.getLogger(__name__)


class GitHubService:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.repo = settings.github_repo

    async def _run_gh_command(self, *args: str, timeout: int = 60) -> str:
        cmd = ["gh", *args]
        logger.debug(f"Running command: {' '.join(cmd)}")

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            proc.kill()
            raise TimeoutError(f"Command timed out: {' '.join(cmd)}")

        if proc.returncode != 0:
            error_msg = stderr.decode().strip() if stderr else "Unknown error"
            raise RuntimeError(f"gh command failed: {error_msg}")

        return stdout.decode()

    async def create_issue(self, title: str, body: str, labels: list[str] | None = None) -> dict:
        args = ["issue", "create", "--repo", self.repo, "--title", title, "--body", body]
        if labels:
            for label in labels:
                args.extend(["--label", label])

        output = await self._run_gh_command(*args)
        issue_url = output.strip()

        issue_number = int(issue_url.split("/")[-1])
        issue_data = await self.get_issue(issue_number)

        issue = GitHubIssue(
            github_issue_number=issue_number,
            title=title,
            state="open",
            github_issue_url=issue_url,
        )
        self.session.add(issue)
        await self.session.flush()

        return {
            "id": issue.id,
            "github_issue_number": issue_number,
            "title": title,
            "state": "open",
            "github_issue_url": issue_url,
        }

    async def get_issue(self, issue_number: int) -> dict:
        args = [
            "issue", "view", str(issue_number),
            "--repo", self.repo,
            "--json", "number,title,state,url"
        ]
        output = await self._run_gh_command(*args)
        return json.loads(output)

    async def list_open_issues(self, limit: int = 100) -> list[dict]:
        args = [
            "issue", "list",
            "--repo", self.repo,
            "--state", "open",
            "--limit", str(limit),
            "--json", "number,title,state,url"
        ]
        output = await self._run_gh_command(*args)
        return json.loads(output) if output.strip() else []

    async def search_issues(self, query: str, limit: int = 10) -> list[dict]:
        args = [
            "search", "issues",
            "--repo", self.repo,
            query,
            "--limit", str(limit),
            "--json", "number,title,state,url"
        ]
        try:
            output = await self._run_gh_command(*args)
            return json.loads(output) if output.strip() else []
        except Exception as e:
            logger.error(f"Failed to search issues: {e}")
            return []

    async def sync_issue_states(self):
        stmt = select(GitHubIssue).where(GitHubIssue.state == "open")
        result = await self.session.execute(stmt)
        open_issues = result.scalars().all()

        for issue in open_issues:
            try:
                issue_data = await self.get_issue(issue.github_issue_number)
                issue.state = issue_data.get("state", issue.state)
            except Exception as e:
                logger.error(f"Failed to sync issue {issue.github_issue_number}: {e}")

    async def get_or_create_issue(self, issue_number: int) -> GitHubIssue:
        stmt = select(GitHubIssue).where(GitHubIssue.github_issue_number == issue_number)
        result = await self.session.execute(stmt)
        issue = result.scalar_one_or_none()

        if issue:
            return issue

        issue_data = await self.get_issue(issue_number)
        issue = GitHubIssue(
            github_issue_number=issue_data["number"],
            title=issue_data.get("title"),
            state=issue_data.get("state"),
            github_issue_url=issue_data.get("url"),
        )
        self.session.add(issue)
        await self.session.flush()
        return issue
