"""Add another mock build."""
import asyncio
from datetime import datetime, timedelta, UTC

from app.database import async_session_maker, init_db
from app.models import Build, Job, Failure


async def add_build():
    await init_db()

    async with async_session_maker() as session:
        now = datetime.now(UTC)

        build = Build(
            buildkite_build_number=12346,
            build_type="nightly",
            state="failed",
            commit_sha="xyz789abc123",
            branch="main",
            message="[nightly] GPU benchmark suite",
            web_url="https://buildkite.com/vllm/ci/builds/12346",
            triage_status="pending",
            created_at=now - timedelta(hours=1),
            synced_at=now,
        )
        session.add(build)
        await session.flush()

        job1 = Job(
            build_id=build.id,
            buildkite_job_id="job-005-xyz",
            name="benchmark-llama-70b",
            state="failed",
            step_key="benchmark_llama70b",
            web_url="https://buildkite.com/vllm/ci/builds/12346#job-005",
        )
        session.add(job1)

        job2 = Job(
            build_id=build.id,
            buildkite_job_id="job-006-abc",
            name="benchmark-mistral-7b",
            state="passed",
            step_key="benchmark_mistral",
            web_url="https://buildkite.com/vllm/ci/builds/12346#job-006",
        )
        session.add(job2)
        await session.flush()

        failure = Failure(
            job_id=job1.id,
            failure_category="infra",
            failure_type="oom_killed",
            error_signature="OOMKilled:benchmark_large_model",
            error_message="Process killed by OOM killer. GPU memory exhausted during Llama-70B benchmark.",
            root_cause="Insufficient GPU memory for 70B model with current batch size configuration.",
            is_flaky=False,
            log_excerpt="""[2026-02-12 19:45:12] Starting Llama-70B benchmark...
[2026-02-12 19:45:13] Loading model weights...
[2026-02-12 19:47:22] CUDA out of memory. Tried to allocate 2.5 GiB
[2026-02-12 19:47:22] torch.cuda.OutOfMemoryError: CUDA out of memory
[2026-02-12 19:47:23] Process killed (OOM)""",
        )
        session.add(failure)

        await session.commit()
        print(f"Added build #{build.buildkite_build_number}")


if __name__ == "__main__":
    asyncio.run(add_build())
