import asyncio
import json
import logging
from dataclasses import dataclass

from app.config import settings

logger = logging.getLogger(__name__)


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

    async def _run_bk_command(self, *args: str, timeout: int = 60) -> str:
        cmd = ["bk", *args]
        logger.debug(f"Running command: {' '.join(cmd)}")

        import os
        env = os.environ.copy()
        if self.api_token:
            env["BUILDKITE_API_TOKEN"] = self.api_token
            env["BUILDKITE_ORGANIZATION_SLUG"] = self.org

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )

        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            proc.kill()
            raise TimeoutError(f"Command timed out: {' '.join(cmd)}")

        if proc.returncode != 0:
            error_msg = stderr.decode().strip() if stderr else "Unknown error"
            raise RuntimeError(f"bk command failed: {error_msg}")

        return stdout.decode()

    async def list_recent_builds(
        self, limit: int = 20, branch: str | None = None, nightly_daily_only: bool = False
    ) -> list[dict]:
        # If filtering for nightly/daily, fetch more builds and filter
        fetch_limit = limit * 10 if nightly_daily_only else limit
        args = ["build", "list", "-p", self.pipeline, "-o", "json", f"--limit={fetch_limit}"]
        if branch:
            args.extend(["--branch", branch])

        output = await self._run_bk_command(*args)
        builds = json.loads(output) if output.strip() else []

        result = []
        for build in builds:
            build_type = self._detect_build_type(build)

            # Filter for nightly/daily only if requested
            if nightly_daily_only and build_type not in ("nightly", "daily"):
                continue

            result.append({
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
        args = ["build", "view", f"{self.org}/{self.pipeline}/{build_number}", "-o", "json"]
        output = await self._run_bk_command(*args)
        build = json.loads(output)

        jobs = []
        for job in build.get("jobs", []):
            if job.get("type") == "script":
                jobs.append({
                    "id": job.get("id"),
                    "name": job.get("name"),
                    "state": job.get("state"),
                    "step_key": job.get("step_key"),
                    "web_url": job.get("web_url"),
                    "soft_failed": job.get("soft_failed", False),
                })

        build_type = self._detect_build_type(build)
        return {
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

    async def get_job_log(self, job_id: str, build_number: int) -> str:
        args = ["job", "log", job_id, "-p", self.pipeline, "-b", str(build_number)]
        try:
            return await self._run_bk_command(*args, timeout=120)
        except Exception as e:
            logger.error(f"Failed to get job log for {job_id}: {e}")
            return ""

    async def retry_job(self, job_id: str) -> bool:
        args = ["job", "retry", job_id]
        try:
            await self._run_bk_command(*args)
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
