import asyncio
import logging
from dataclasses import dataclass

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

API_BASE = "https://api.buildkite.com/v2"


async def _request_with_retry(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    max_retries: int = 3,
    **kwargs,
) -> httpx.Response:
    """Make an HTTP request with automatic retry on 429 rate limits."""
    for attempt in range(max_retries + 1):
        resp = await client.request(method, url, **kwargs)
        if resp.status_code != 429 or attempt == max_retries:
            resp.raise_for_status()
            return resp
        # Wait based on RateLimit-Reset header, or default backoff
        reset_seconds = int(resp.headers.get("RateLimit-Reset", "10"))
        wait = max(reset_seconds + 1, 2)
        logger.info(f"Rate limited (429), waiting {wait}s before retry {attempt + 1}/{max_retries}")
        await asyncio.sleep(wait)
    return resp  # unreachable but satisfies type checker


@dataclass
class BuildInfo:
    number: int
    state: str
    branch: str | None
    commit: str | None
    message: str | None
    web_url: str | None
    build_type: str | None
    created_at: str | None
    jobs: list[dict]


@dataclass
class JobInfo:
    id: str
    name: str | None
    state: str
    step_key: str | None
    web_url: str | None


class BuildkiteService:
    def __init__(self):
        self.org = settings.buildkite_org
        self.pipeline = settings.buildkite_pipeline
        self.api_token = settings.buildkite_api_token

    def _headers(self) -> dict[str, str]:
        headers = {"Accept": "application/json"}
        if self.api_token:
            headers["Authorization"] = f"Bearer {self.api_token}"
        return headers

    def _builds_url(self) -> str:
        return f"{API_BASE}/organizations/{self.org}/pipelines/{self.pipeline}/builds"

    async def list_recent_builds(
        self, limit: int = 20, branch: str | None = None, nightly_daily_only: bool = False
    ) -> list[dict]:
        fetch_limit = limit * 10 if nightly_daily_only else limit
        params: dict[str, str | int] = {"per_page": fetch_limit}
        if branch:
            params["branch"] = branch

        async with httpx.AsyncClient(timeout=60) as client:
            resp = await _request_with_retry(client, "GET", self._builds_url(), headers=self._headers(), params=params)
            builds = resp.json()

        result = []
        for build in builds:
            build_type = self._detect_build_type(build)

            if nightly_daily_only and build_type not in ("nightly", "daily"):
                continue

            result.append({
                "id": build.get("id"),
                "number": build.get("number"),
                "state": build.get("state"),
                "branch": build.get("branch"),
                "commit": build.get("commit"),
                "message": build.get("message"),
                "web_url": build.get("web_url"),
                "build_type": build_type,
                "created_at": build.get("created_at"),
            })

            if len(result) >= limit:
                break

        return result

    async def get_build(self, build_number: int) -> dict:
        url = f"{self._builds_url()}/{build_number}"
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await _request_with_retry(client, "GET", url, headers=self._headers())
            build = resp.json()

        jobs = []
        for job in build.get("jobs", []):
            if job.get("type") == "script":
                jobs.append({
                    "id": job.get("id"),
                    "name": job.get("name"),
                    "state": job.get("state"),
                    "step_key": job.get("step_key"),
                    "web_url": job.get("web_url"),
                    "command": job.get("command"),
                    "soft_failed": job.get("soft_failed", False),
                    "exit_status": job.get("exit_status"),
                    "signal": job.get("signal"),
                    "signal_reason": job.get("signal_reason"),
                })

        build_type = self._detect_build_type(build)
        return {
            "id": build.get("id"),
            "number": build.get("number"),
            "state": build.get("state"),
            "branch": build.get("branch"),
            "commit": build.get("commit"),
            "message": build.get("message"),
            "web_url": build.get("web_url"),
            "build_type": build_type,
            "created_at": build.get("created_at"),
            "jobs": jobs,
        }

    async def list_builds_by_commit(self, commit_sha: str, branch: str = "main") -> list[dict]:
        """Look up Buildkite builds for a specific commit SHA."""
        params: dict[str, str | int] = {
            "commit": commit_sha,
            "branch": branch,
            "per_page": 10,
        }
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await _request_with_retry(
                client, "GET", self._builds_url(), headers=self._headers(), params=params
            )
            builds = resp.json()

        result = []
        for build in builds:
            build_type = self._detect_build_type(build)
            result.append({
                "id": build.get("id"),
                "number": build.get("number"),
                "state": build.get("state"),
                "branch": build.get("branch"),
                "commit": build.get("commit"),
                "message": build.get("message"),
                "web_url": build.get("web_url"),
                "build_type": build_type,
                "created_at": build.get("created_at"),
            })
        return result

    async def get_job_log(self, job_id: str, build_number: int) -> str:
        url = (
            f"{API_BASE}/organizations/{self.org}/pipelines/{self.pipeline}"
            f"/builds/{build_number}/jobs/{job_id}/log"
        )
        try:
            async with httpx.AsyncClient(timeout=120) as client:
                resp = await _request_with_retry(
                    client, "GET", url,
                    headers={**self._headers(), "Accept": "text/plain"},
                    follow_redirects=True,
                )
                # The API returns JSON with content field, or raw text depending on Accept header
                if resp.headers.get("content-type", "").startswith("application/json"):
                    data = resp.json()
                    return data.get("content", "")
                return resp.text
        except Exception as e:
            logger.error(f"Failed to get job log for {job_id}: {e}")
            return ""

    async def retry_job(self, job_id: str, build_number: int) -> bool:
        url = (
            f"{API_BASE}/organizations/{self.org}/pipelines/{self.pipeline}"
            f"/builds/{build_number}/jobs/{job_id}/retry"
        )
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await _request_with_retry(client, "PUT", url, headers=self._headers())
                _ = resp  # used for status check in retry helper
            return True
        except Exception as e:
            logger.error(f"Failed to retry job {job_id}: {e}")
            return False

    def _detect_build_type(self, build: dict) -> str | None:
        message = (build.get("message") or "").lower()
        branch = (build.get("branch") or "").lower()

        if "nightly" in message or "nightly" in branch:
            return "nightly"
        elif "daily" in message or "daily" in branch:
            return "daily"
        elif branch == "main":
            return "main"
        return None


class TestEngineService:
    """Client for Buildkite Test Engine (Test Analytics) REST API."""

    ANALYTICS_BASE = "https://api.buildkite.com/v2/analytics"

    def __init__(self):
        self.org = settings.buildkite_org
        self.suite = settings.buildkite_test_suite
        self.api_token = settings.buildkite_api_token

    def _headers(self) -> dict[str, str]:
        headers = {"Accept": "application/json"}
        if self.api_token:
            headers["Authorization"] = f"Bearer {self.api_token}"
        return headers

    def _runs_url(self) -> str:
        return (
            f"{self.ANALYTICS_BASE}/organizations/{self.org}"
            f"/suites/{self.suite}/runs"
        )

    async def get_run_by_build_id(self, buildkite_build_id: str) -> dict | None:
        """Find the Test Engine run associated with a Buildkite build UUID.

        Returns the run dict or None if no run found.
        """
        url = self._runs_url()
        params = {"build_id": buildkite_build_id, "per_page": 1}

        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.get(url, headers=self._headers(), params=params)
                resp.raise_for_status()
                runs = resp.json()

            if runs and len(runs) > 0:
                return runs[0]
            return None
        except Exception as e:
            logger.error(f"Failed to get Test Engine run for build {buildkite_build_id}: {e}")
            return None

    async def get_failed_executions(self, run_id: str) -> list[dict]:
        """Get all failed test executions for a Test Engine run.

        Handles pagination to return the complete list.
        Returns list of {test_name, failure_reason, location, duration, ...}.
        """
        url = f"{self._runs_url()}/{run_id}/failed_executions"
        all_executions = []
        page = 1

        try:
            async with httpx.AsyncClient(timeout=60) as client:
                while True:
                    params = {"per_page": 100, "page": page}
                    resp = await client.get(url, headers=self._headers(), params=params)
                    resp.raise_for_status()
                    executions = resp.json()

                    if not executions:
                        break

                    all_executions.extend(executions)

                    if len(executions) < 100:
                        break
                    page += 1

        except Exception as e:
            logger.error(f"Failed to get failed executions for run {run_id}: {e}")

        return all_executions
