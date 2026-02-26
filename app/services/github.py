import asyncio
import json
import logging
import time
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import settings
from app.models import GitHubIssue, KnownFailure, log_kf_event

logger = logging.getLogger(__name__)

# In-memory caches to avoid repeated gh API calls
_commit_dates_cache: dict[str, str] = {}  # sha → ISO date string
_commits_cache: dict[str, tuple[float, list[dict]]] = {}  # cache_key → (timestamp, commits)
_COMMITS_CACHE_TTL = 120  # seconds


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
        """Sync GitHub issue states. When an issue closes, resolve linked KnownFailures."""
        stmt = select(GitHubIssue).where(
            GitHubIssue.state.in_(["open", "OPEN"])
        )
        result = await self.session.execute(stmt)
        open_issues = result.scalars().all()

        for issue in open_issues:
            try:
                issue_data = await self.get_issue(issue.github_issue_number)
                new_state = issue_data.get("state", issue.state).lower()
                if new_state != issue.state:
                    old_state = issue.state
                    issue.state = new_state
                    logger.info(
                        f"GitHub issue #{issue.github_issue_number} "
                        f"changed: {old_state} → {new_state}"
                    )

                    # When issue closes, resolve all linked open KnownFailures
                    if new_state == "closed":
                        kf_stmt = (
                            select(KnownFailure)
                            .where(KnownFailure.github_issue_id == issue.id)
                            .where(KnownFailure.status == "open")
                        )
                        kf_result = await self.session.execute(kf_stmt)
                        linked_kfs = kf_result.scalars().all()
                        for kf in linked_kfs:
                            kf.status = "resolved"
                            kf.resolved_at = datetime.utcnow()
                            kf.resolved_by = "issue_closed"
                            log_kf_event(
                                self.session, kf.id, "issue_closed",
                                github_issue_number=issue.github_issue_number,
                            )
                            logger.info(
                                f"Resolved KF#{kf.id} '{kf.title}' — "
                                f"linked issue #{issue.github_issue_number} closed"
                            )
            except Exception as e:
                logger.error(f"Failed to sync issue {issue.github_issue_number}: {e}")

    async def list_main_commits(
        self,
        since: str | None = None,
        until: str | None = None,
        per_page: int = 100,
    ) -> list[dict]:
        """List commits on main branch, optionally filtered by date range.

        Uses `gh api` for authenticated access (higher rate limits).

        Args:
            since: ISO 8601 datetime string (e.g., "2026-02-18T00:00:00Z")
            until: ISO 8601 datetime string
            per_page: Max commits to return

        Returns:
            List of {sha, message, date} dicts, newest first.
        """
        cache_key = f"{since}|{until}|{per_page}"
        cached = _commits_cache.get(cache_key)
        if cached:
            ts, commits = cached
            if time.time() - ts < _COMMITS_CACHE_TTL:
                return commits

        path = f"repos/{self.repo}/commits?sha=main&per_page={per_page}"
        if since:
            path += f"&since={since}"
        if until:
            path += f"&until={until}"

        try:
            output = await self._run_gh_command("api", path, timeout=30)
            data = json.loads(output)
        except Exception as e:
            logger.warning(f"Failed to list GitHub commits: {e}")
            # Return stale cache if available
            if cached:
                return cached[1]
            return []

        commits = []
        for item in data:
            sha = item.get("sha", "")
            commit = item.get("commit", {})
            message = commit.get("message", "").split("\n")[0]
            date = commit.get("committer", {}).get("date")
            commits.append({"sha": sha, "message": message, "date": date})

        _commits_cache[cache_key] = (time.time(), commits)
        # Also populate the commit dates cache
        for c in commits:
            if c["sha"] and c.get("date"):
                _commit_dates_cache[c["sha"]] = c["date"]

        return commits

    async def get_commit_dates(self, shas: set[str]) -> dict[str, str]:
        """Fetch committer dates for specific commit SHAs.

        Uses `gh api` for authenticated access (higher rate limits).

        Returns:
            Dict mapping SHA to ISO 8601 date string.
        """
        results: dict[str, str] = {}
        uncached: set[str] = set()

        # Check in-memory cache first
        for sha in shas:
            if sha in _commit_dates_cache:
                results[sha] = _commit_dates_cache[sha]
            else:
                uncached.add(sha)

        if not uncached:
            return results

        async def _fetch_one(sha: str):
            try:
                output = await self._run_gh_command(
                    "api", f"repos/{self.repo}/commits/{sha}",
                    "--jq", ".commit.committer.date",
                    timeout=15,
                )
                date = output.strip()
                if date:
                    results[sha] = date
                    _commit_dates_cache[sha] = date
            except Exception as e:
                logger.debug(f"Failed to get commit date for {sha[:8]}: {e}")

        await asyncio.gather(*[_fetch_one(sha) for sha in uncached])
        return results

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
